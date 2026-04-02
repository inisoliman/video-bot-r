#!/usr/bin/env python3
"""
الحل الاحترافي النهائي: جلب file_id و thumbnail من القناة
باستخدام message_id و chat_id فقط!
"""

import os
import sys
import time
import logging
import telebot

# إضافة المسار الحالي للـ path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_manager as db

# إعداد logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# قراءة المتغيرات من البيئة
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_IDS = os.getenv("ADMIN_IDS", "").split(",")

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN not found")
    sys.exit(1)

if not ADMIN_IDS or not ADMIN_IDS[0]:
    logger.error("❌ ADMIN_IDS not found")
    sys.exit(1)

admin_id = int(ADMIN_IDS[0])

# إنشاء البوت
bot = telebot.TeleBot(BOT_TOKEN)

def fix_videos_from_channel():
    """
    الحل الاحترافي: جلب file_id و thumbnail من القناة مباشرة
    """
    try:
        logger.info("="*60)
        logger.info("🚀 بدء الإصلاح الاحترافي...")
        logger.info("="*60)
        
        # جلب الفيديوهات بدون file_id أو بدون thumbnail
        sql = """
            SELECT id, message_id, chat_id, file_id, thumbnail_file_id, caption
            FROM video_archive
            WHERE message_id IS NOT NULL 
              AND chat_id IS NOT NULL
              AND (file_id IS NULL OR thumbnail_file_id IS NULL)
            ORDER BY id ASC
            LIMIT 100
        """
        videos = db.execute_query(sql, fetch="all")
        
        if not videos:
            logger.info("✅ جميع الفيديوهات لديها file_id و thumbnail!")
            return 0
        
        logger.info(f"📊 وجدت {len(videos)} فيديو تحتاج إصلاح")
        
        total_updated = 0
        failed_count = 0
        
        for i, video in enumerate(videos, 1):
            try:
                logger.info(f"\n[{i}/{len(videos)}] معالجة فيديو ID: {video['id']}")
                
                # جلب الرسالة من القناة باستخدام copy_message
                # هذا يعمل لأننا نرسل لمكان مختلف (الأدمن)
                try:
                    # نسخ الرسالة للأدمن
                    copied = bot.copy_message(
                        chat_id=admin_id,
                        from_chat_id=video['chat_id'],
                        message_id=video['message_id']
                    )
                    
                    # الآن نجلب الرسالة المنسوخة
                    # لكن copy_message لا يعيد الرسالة الكاملة!
                    # الحل: نستخدم forward ثم نحذفها
                    
                    forwarded = bot.forward_message(
                        chat_id=admin_id,
                        from_chat_id=video['chat_id'],
                        message_id=video['message_id']
                    )
                    
                    # استخراج البيانات
                    if forwarded.video:
                        new_file_id = forwarded.video.file_id
                        new_thumbnail_id = forwarded.video.thumb.file_id if forwarded.video.thumb else None
                        
                        # تحديث قاعدة البيانات
                        update_sql = """
                            UPDATE video_archive
                            SET file_id = COALESCE(%s, file_id),
                                thumbnail_file_id = COALESCE(%s, thumbnail_file_id)
                            WHERE id = %s
                        """
                        db.execute_query(update_sql, (new_file_id, new_thumbnail_id, video['id']))
                        
                        total_updated += 1
                        logger.info(f"  ✅ تم التحديث: file_id={bool(new_file_id)}, thumbnail={bool(new_thumbnail_id)}")
                    else:
                        logger.warning(f"  ⚠️ الرسالة ليست فيديو!")
                        failed_count += 1
                    
                    # حذف الرسائل المنسوخة/المعاد توجيهها
                    try:
                        bot.delete_message(admin_id, copied.message_id)
                    except:
                        pass
                    
                    try:
                        bot.delete_message(admin_id, forwarded.message_id)
                    except:
                        pass
                    
                except telebot.apihelper.ApiTelegramException as e:
                    if "message to forward not found" in str(e).lower() or "message to copy not found" in str(e).lower():
                        logger.warning(f"  ⚠️ الرسالة محذوفة من القناة")
                        failed_count += 1
                    else:
                        logger.error(f"  ❌ خطأ Telegram: {e}")
                        failed_count += 1
                    continue
                
                # تأخير بسيط لتجنب rate limiting
                time.sleep(0.3)
                
            except Exception as e:
                logger.error(f"  ❌ خطأ في معالجة الفيديو {video['id']}: {e}")
                failed_count += 1
                continue
        
        logger.info("\n" + "="*60)
        logger.info("📊 ملخص النتائج:")
        logger.info(f"  ✅ تم التحديث: {total_updated}")
        logger.info(f"  ❌ فشل: {failed_count}")
        logger.info(f"  📝 المجموع: {len(videos)}")
        logger.info("="*60)
        
        return total_updated
        
    except Exception as e:
        logger.error(f"❌ خطأ فادح: {e}", exc_info=True)
        return 0

def main():
    """الدالة الرئيسية"""
    try:
        # التحقق من الاتصال بقاعدة البيانات
        logger.info("🔌 الاتصال بقاعدة البيانات...")
        db.ensure_schema()
        logger.info("✅ تم الاتصال بنجاح\n")
        
        # إصلاح الفيديوهات
        total = fix_videos_from_channel()
        
        if total > 0:
            logger.info(f"\n🎉 تم! إصلاح {total} فيديو")
            logger.info("💡 شغّل السكريبت مرة أخرى لإصلاح المزيد")
        else:
            logger.info("\n✅ لا توجد فيديوهات تحتاج إصلاح!")
        
    except KeyboardInterrupt:
        logger.info("\n⚠️ تم الإيقاف بواسطة المستخدم")
    except Exception as e:
        logger.error(f"❌ خطأ فادح: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
