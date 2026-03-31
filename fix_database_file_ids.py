#!/usr/bin/env python3
"""
سكريبت لإصلاح قاعدة البيانات: جلب file_id و thumbnail من القناة.

المشكلة: الفيديوهات في قاعدة البيانات بدون file_id
الحل: نجلب الرسائل من القناة ونحدث file_id و thumbnail_file_id
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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# قراءة المتغيرات من البيئة
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN not found in environment variables")
    sys.exit(1)

if not CHANNEL_ID:
    logger.error("❌ CHANNEL_ID not found in environment variables")
    sys.exit(1)

# إنشاء البوت
bot = telebot.TeleBot(BOT_TOKEN)

def fix_database_file_ids():
    """
    إصلاح قاعدة البيانات بجلب file_id و thumbnail من القناة.
    """
    try:
        logger.info("🚀 Starting database fix: fetching file_id and thumbnails from channel...")
        
        # جلب جميع الفيديوهات من قاعدة البيانات
        sql = """
            SELECT id, message_id, chat_id, file_id, thumbnail_file_id
            FROM video_archive
            WHERE message_id IS NOT NULL AND chat_id IS NOT NULL
            ORDER BY id DESC
            LIMIT 2000
        """
        videos = db.execute_query(sql, fetch="all")
        
        if not videos:
            logger.info("✅ No videos found in database!")
            return 0
        
        logger.info(f"📊 Found {len(videos)} videos in database")
        
        total_updated = 0
        failed_count = 0
        skipped_count = 0
        
        for i, video in enumerate(videos, 1):
            try:
                logger.info(f"Processing {i}/{len(videos)}: Video ID {video['id']}, Message ID {video['message_id']}")
                
                # إذا كان file_id موجود بالفعل، نتخطى
                if video.get('file_id') and video.get('thumbnail_file_id'):
                    logger.info(f"⏭️ Video {video['id']} already has file_id and thumbnail, skipping")
                    skipped_count += 1
                    continue
                
                # جلب الرسالة من القناة باستخدام forward
                try:
                    # نحاول forward للبوت نفسه (Saved Messages)
                    # هذا يعمل لأننا نرسل لمكان مختلف
                    admin_ids = os.getenv("ADMIN_IDS", "").split(",")
                    if admin_ids and admin_ids[0]:
                        admin_id = int(admin_ids[0])
                    else:
                        logger.error("No admin ID found")
                        continue
                    
                    # نسخ الرسالة للأدمن
                    forwarded = bot.forward_message(
                        chat_id=admin_id,
                        from_chat_id=video['chat_id'],
                        message_id=video['message_id']
                    )
                    
                    # استخراج file_id و thumbnail
                    if forwarded.video:
                        new_file_id = forwarded.video.file_id
                        new_thumbnail_id = forwarded.video.thumb.file_id if forwarded.video.thumb else None
                        
                        # تحديث قاعدة البيانات
                        update_sql = """
                            UPDATE video_archive
                            SET file_id = %s, thumbnail_file_id = %s
                            WHERE id = %s
                        """
                        db.execute_query(update_sql, (new_file_id, new_thumbnail_id, video['id']))
                        
                        total_updated += 1
                        logger.info(f"✅ Updated video {video['id']}: file_id={bool(new_file_id)}, thumbnail={bool(new_thumbnail_id)}")
                    else:
                        logger.warning(f"⚠️ Forwarded message for video {video['id']} is not a video")
                        failed_count += 1
                    
                    # حذف الرسالة المعاد توجيهها
                    try:
                        bot.delete_message(admin_id, forwarded.message_id)
                    except:
                        pass
                    
                except Exception as e:
                    logger.error(f"❌ Error forwarding message for video {video['id']}: {e}")
                    failed_count += 1
                    continue
                
                # تأخير بسيط
                time.sleep(0.3)
                
            except Exception as e:
                logger.error(f"❌ Error processing video {video['id']}: {e}")
                failed_count += 1
                continue
        
        logger.info(f"\n📊 Summary:")
        logger.info(f"  ✅ Updated: {total_updated}")
        logger.info(f"  ⏭️ Skipped: {skipped_count}")
        logger.info(f"  ❌ Failed: {failed_count}")
        logger.info(f"  📝 Total: {len(videos)}")
        
        return total_updated
        
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        return 0

def main():
    """الدالة الرئيسية"""
    try:
        logger.info("="*50)
        logger.info("Database Fix Script - Fetch file_id from Channel")
        logger.info("="*50)
        
        # التحقق من الاتصال بقاعدة البيانات
        logger.info("🔌 Connecting to database...")
        db.ensure_schema()
        logger.info("✅ Database connected")
        
        # إصلاح قاعدة البيانات
        total = fix_database_file_ids()
        
        logger.info(f"\n🎉 Completed! Total videos updated: {total}")
        
    except KeyboardInterrupt:
        logger.info("\n⚠️ Script interrupted by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
