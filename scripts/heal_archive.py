# scripts/heal_archive.py

import time
import logging
import threading
from db_manager import execute_query, update_video_thumbnail
from telebot.apihelper import ApiTelegramException

from handlers.telegram_rate_limit import sleep_after_429

logger = logging.getLogger(__name__)

def run_heal_archive(bot, chat_id, message_id):
    """
    يقوم بفحص الأرشيف بالكامل وجلب الصور المصغرة المفقودة وتصحيح أنواع الملفات.
    """
    bot.edit_message_text("🔍 بدء عملية الفحص والإصلاح الشامل للأرشيف...", chat_id, message_id)
    
    # 1. جلب الفيديوهات التي تحتاج لإصلاح
    sql = """
        SELECT id, message_id, chat_id 
        FROM video_archive 
        WHERE thumbnail_file_id IS NULL OR content_type = 'DOCUMENT' OR content_type IS NULL
        ORDER BY id DESC
    """
    
    try:
        videos_to_fix = execute_query(sql, fetch="all")
    except Exception as e:
        logger.error(f"Error fetching videos to fix: {e}")
        bot.edit_message_text(f"❌ خطأ في قاعدة البيانات: {e}", chat_id, message_id)
        return

    if not videos_to_fix:
        bot.edit_message_text("✅ الأرشيف سليم تماماً! كل الفيديوهات تحتوي على صور مصغرة.", chat_id, message_id)
        return

    total = len(videos_to_fix)
    fixed_count = 0
    failed_count = 0
    start_time = time.time()
    
    # جلب معرف أحد المشرفين لإجراء عملية الـ Forward
    from config import ADMIN_IDS
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
    
    if not admin_id:
        bot.edit_message_text("❌ لم يتم العثور على معرف أدمن لإجراء عملية الإصلاح.", chat_id, message_id)
        return

    # تقدم العملية: مسودة إن أمكن وإلا تعديل الرسالة (لا يغيّر نتيجة الإصلاح)
    from handlers.maintenance_progress import create as create_progress
    progress = create_progress(bot, chat_id, message_id)

    for i, video in enumerate(videos_to_fix):
        video_db_id = video['id']
        msg_id = video['message_id']
        channel_id = video['chat_id']
        
        try:
            # 2. إعادة توجيه الرسالة للأدمن لاستخراج البيانات الحقيقية
            forwarded = bot.forward_message(admin_id, channel_id, msg_id)
            
            thumb_id = None
            content_type = 'DOCUMENT'
            new_file_id = None
            
            if forwarded.video:
                content_type = 'VIDEO'
                new_file_id = forwarded.video.file_id
                if forwarded.video.thumb:
                    thumb_id = forwarded.video.thumb.file_id
            elif forwarded.document:
                content_type = 'DOCUMENT'
                new_file_id = forwarded.document.file_id
                if forwarded.document.thumb:
                    thumb_id = forwarded.document.thumb.file_id
            
            # 3. تحديث قاعدة البيانات
            update_sql = """
                UPDATE video_archive 
                SET thumbnail_file_id = %s, 
                    content_type = %s,
                    file_id = COALESCE(%s, file_id)
                WHERE id = %s
            """
            execute_query(update_sql, (thumb_id, content_type, new_file_id, video_db_id), commit=True)
            
            # حذف الرسالة الموجهة فوراً
            try:
                bot.delete_message(admin_id, forwarded.message_id)
            except:
                pass
                
            fixed_count += 1
            
        except ApiTelegramException as e:
            logger.error(f"Error healing video {video_db_id}: {e}")
            failed_count += 1
            # 429 رسمي: ننتظر retry_after المعلن (لا إعادة محاولة للفيديو الفاشل).
            # القيمة الغائبة تترك البديل القديم لنص 'flood' كما كان تماماً.
            if not sleep_after_429(e, time.sleep) and "flood" in str(e).lower():
                time.sleep(35) 
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            failed_count += 1

        # 4. تحديث رسالة التقدم كل 10 فيديوهات
        if (i + 1) % 10 == 0 or (i + 1) == total:
            elapsed = time.time() - start_time
            progress_pct = ((i + 1) / total) * 100
            status_text = (
                f"⏳ جارِ إصلاح الأرشيف... ({i + 1}/{total})\n"
                f"✅ تم إصلاح: {fixed_count}\n"
                f"❌ فشل: {failed_count}\n"
                f"📊 التقدم: {progress_pct:.1f}%\n"
                f"🕒 الوقت المنقضي: {int(elapsed)} ثانية"
            )
            progress.update(status_text)
        
        # تأخير بسيط لتجنب الـ Flood
        time.sleep(1.0) # زيادة التأخير قليلاً للأمان

    final_text = (
        f"✅ اكتملت عملية الإصلاح الشامل!\n\n"
        f"• إجمالي ما تم فحصه: {total}\n"
        f"• نجاح: {fixed_count}\n"
        f"• فشل: {failed_count}\n"
        f"🎉 الآن ستظهر جميع الفيديوهات بصورها المصغرة في البحث."
    )
    bot.send_message(chat_id, final_text)

    # قائمة متابعة اختيارية بعد التقرير الناجح: لا تُغيّر النتيجة ولا النص أبداً
    from handlers.maintenance_checklists import send_followup_checklist
    send_followup_checklist(bot, chat_id, 'heal_archive')
