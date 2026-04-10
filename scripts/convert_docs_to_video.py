# scripts/convert_docs_to_video.py
"""
سكربت تحويل المستندات (Documents) إلى فيديوهات حقيقية.
الآلية: تنزيل الملف من تليجرام ثم إعادة رفعه كفيديو باستخدام send_video.
"""

import time
import logging
import io
from db_manager import execute_query
from telebot.apihelper import ApiTelegramException

logger = logging.getLogger(__name__)

# الحد الأقصى لحجم الملف الذي يمكن للبوت تنزيله (20MB)
MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024

def get_document_videos():
    """جلب كل الفيديوهات المسجلة كمستندات مع تفاصيلها"""
    sql = """
        SELECT id, message_id, chat_id, file_id, caption, file_name, content_type, thumbnail_file_id
        FROM video_archive 
        WHERE content_type = 'DOCUMENT' OR content_type IS NULL
        ORDER BY id ASC
    """
    return execute_query(sql, fetch="all") or []


def convert_single_video(bot, video_record, admin_id, channel_id):
    """
    تحويل مستند واحد إلى فيديو حقيقي.
    
    الخطوات:
    1. تنزيل الملف من تليجرام
    2. إعادة رفعه كفيديو باستخدام send_video
    3. تحديث قاعدة البيانات بالبيانات الجديدة
    4. حذف الرسالة القديمة من القناة (اختياري)
    
    Returns:
        'success' | 'too_large' | 'error'
    """
    video_id = video_record['id']
    old_file_id = video_record['file_id']
    caption = video_record.get('caption', '')
    
    try:
        # 1. فحص حجم الملف
        file_info = bot.get_file(old_file_id)
        if file_info.file_size and file_info.file_size > MAX_DOWNLOAD_SIZE:
            logger.warning(f"Video {video_id}: File too large ({file_info.file_size} bytes), skipping")
            return 'too_large'
        
        # 2. تنزيل الملف
        downloaded_file = bot.download_file(file_info.file_path)
        
        # 3. إعادة رفعه كفيديو إلى الأدمن أولاً (للحصول على file_id جديد)
        video_bytes = io.BytesIO(downloaded_file)
        video_bytes.name = video_record.get('file_name', 'video.mp4')
        
        sent_msg = bot.send_video(
            admin_id,
            video_bytes,
            caption=caption[:1024] if caption else None,
            supports_streaming=True
        )
        
        if not sent_msg.video:
            logger.error(f"Video {video_id}: send_video did not return a video object")
            try:
                bot.delete_message(admin_id, sent_msg.message_id)
            except:
                pass
            return 'error'
        
        # 4. استخراج البيانات الجديدة
        new_file_id = sent_msg.video.file_id
        new_thumb_id = sent_msg.video.thumb.file_id if sent_msg.video.thumb else None
        
        # 5. تحديث قاعدة البيانات
        update_sql = """
            UPDATE video_archive 
            SET file_id = %s,
                content_type = 'VIDEO',
                thumbnail_file_id = COALESCE(%s, thumbnail_file_id)
            WHERE id = %s
        """
        execute_query(update_sql, (new_file_id, new_thumb_id, video_id), commit=True)
        
        # 6. حذف الرسالة المؤقتة من الأدمن
        try:
            bot.delete_message(admin_id, sent_msg.message_id)
        except:
            pass
        
        logger.info(f"✅ Video {video_id}: Converted to real VIDEO with thumb={new_thumb_id is not None}")
        return 'success'
        
    except ApiTelegramException as e:
        error_str = str(e).lower()
        if "file is too big" in error_str or "file_size" in error_str:
            return 'too_large'
        if "flood" in error_str:
            time.sleep(35)
        logger.error(f"API error converting video {video_id}: {e}")
        return 'error'
    except Exception as e:
        logger.error(f"Error converting video {video_id}: {e}")
        return 'error'


def run_convert_all_docs(bot, chat_id, message_id):
    """تحويل كل المستندات إلى فيديوهات (يعمل في الخلفية)"""
    
    bot.edit_message_text("🔍 جارِ البحث عن الملفات المسجلة كمستندات...", chat_id, message_id)
    
    docs = get_document_videos()
    if not docs:
        bot.edit_message_text("✅ لا يوجد أي ملفات مسجلة كمستندات! الأرشيف نظيف تماماً.", chat_id, message_id)
        return
    
    total = len(docs)
    success_count = 0
    too_large_count = 0
    error_count = 0
    start_time = time.time()
    
    from config import ADMIN_IDS, CHANNEL_ID
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
    
    if not admin_id:
        bot.edit_message_text("❌ لم يتم العثور على معرف أدمن.", chat_id, message_id)
        return
    
    bot.edit_message_text(
        f"🔄 بدء تحويل {total} ملف إلى فيديوهات حقيقية...\n"
        f"⚠️ الملفات أكبر من 20MB سيتم تجاوزها (يجب رفعها يدوياً).",
        chat_id, message_id
    )
    
    too_large_ids = []
    
    for i, doc in enumerate(docs):
        result = convert_single_video(bot, doc, admin_id, CHANNEL_ID)
        
        if result == 'success':
            success_count += 1
        elif result == 'too_large':
            too_large_count += 1
            too_large_ids.append(str(doc['id']))
        else:
            error_count += 1
        
        # تحديث التقدم كل 5 ملفات
        if (i + 1) % 5 == 0 or (i + 1) == total:
            elapsed = time.time() - start_time
            progress = ((i + 1) / total) * 100
            status = (
                f"🔄 جارِ التحويل... ({i + 1}/{total})\n"
                f"✅ نجاح: {success_count}\n"
                f"📦 كبير جداً: {too_large_count}\n"
                f"❌ فشل: {error_count}\n"
                f"📊 التقدم: {progress:.0f}%\n"
                f"🕒 {int(elapsed)} ثانية"
            )
            try:
                bot.edit_message_text(status, chat_id, message_id)
            except:
                pass
        
        # تأخير لتجنب Flood
        time.sleep(2.0)
    
    # التقرير النهائي
    final = (
        f"✅ اكتملت عملية التحويل!\n\n"
        f"• إجمالي: {total}\n"
        f"• ✅ تم تحويله لفيديو: {success_count}\n"
        f"• 📦 كبير جداً (>20MB): {too_large_count}\n"
        f"• ❌ فشل: {error_count}\n"
    )
    
    if too_large_ids:
        final += f"\n⚠️ الملفات الكبيرة (تحتاج رفع يدوي):\n"
        final += f"IDs: {', '.join(too_large_ids[:20])}"
        if len(too_large_ids) > 20:
            final += f"\n... و{len(too_large_ids) - 20} ملف آخر"
    
    bot.send_message(chat_id, final)
