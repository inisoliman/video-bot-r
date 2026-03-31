#!/usr/bin/env python3
"""
سكريبت لاستخراج وحفظ thumbnails للفيديوهات القديمة.

هذا السكريبت يُنفذ مرة واحدة فقط بعد إضافة ميزة inline query.
يقوم باستخراج thumbnail_file_id من الفيديوهات وحفظه في قاعدة البيانات.
"""

import os
import sys
import time
import logging
import telebot

# إضافة المسار الحالي للـ path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import get_pool

# إعداد logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# إنشاء البوت
bot = telebot.TeleBot(settings.BOT_TOKEN)

def get_videos_without_thumbnail(limit=1000):
    """جلب الفيديوهات بدون thumbnail"""
    pool = get_pool()
    with pool.getconn() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, message_id, chat_id, file_id
                FROM video_archive
                WHERE (thumbnail_file_id IS NULL OR thumbnail_file_id = '')
                  AND message_id IS NOT NULL
                  AND chat_id IS NOT NULL
                LIMIT %s
            """, (limit,))
            return [dict(zip(['id', 'message_id', 'chat_id', 'file_id'], row)) for row in cursor.fetchall()]

def update_video_thumbnail(video_id, thumbnail_file_id):
    """تحديث thumbnail للفيديو"""
    pool = get_pool()
    with pool.getconn() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE video_archive
                SET thumbnail_file_id = %s
                WHERE id = %s
            """, (thumbnail_file_id, video_id))
            conn.commit()
            return cursor.rowcount > 0

def extract_thumbnail_for_video(video):
    """
    استخراج thumbnail لفيديو واحد.

    Args:
        video: dict مع بيانات الفيديو

    Returns:
        thumbnail_file_id أو None
    """
    try:
        # جلب الرسالة من القناة
        message = bot.forward_message(
            chat_id=video['chat_id'],
            from_chat_id=video['chat_id'],
            message_id=video['message_id']
        )

        # حذف الرسالة المعاد توجيهها
        try:
            bot.delete_message(video['chat_id'], message.message_id)
        except:
            pass

        # استخراج thumbnail
        if message.video and message.video.thumb:
            return message.video.thumb.file_id

    except Exception as e:
        logger.error(f"Error extracting thumbnail for video {video['id']}: {e}")

    return None

def update_thumbnails_batch():
    """
    تحديث thumbnails لجميع الفيديوهات بدون thumbnail.
    """
    try:
        logger.info("🚀 Starting thumbnail update...")

        # جلب الفيديوهات بدون thumbnail
        videos = get_videos_without_thumbnail()

        if not videos:
            logger.info("✅ All videos already have thumbnails!")
            return 0

        logger.info(f"📊 Found {len(videos)} videos without thumbnails")

        total_updated = 0
        failed_count = 0

        for i, video in enumerate(videos, 1):
            logger.info(f"Processing {i}/{len(videos)}: Video ID {video['id']}")

            thumbnail_id = extract_thumbnail_for_video(video)

            if thumbnail_id:
                if update_video_thumbnail(video['id'], thumbnail_id):
                    total_updated += 1
                    logger.info(f"✅ Updated video {video['id']}")
                else:
                    failed_count += 1
                    logger.error(f"❌ Failed to update database for video {video['id']}")
            else:
                failed_count += 1
                logger.warning(f"⚠️ No thumbnail found for video {video['id']}")

            # تأخير لتجنب rate limiting
            time.sleep(0.5)

        logger.info(f"\n📊 Summary:")
        logger.info(f"  ✅ Success: {total_updated}")
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
        logger.info("Update Thumbnails Script")
        logger.info("="*50)

        # التحقق من أن المستخدم admin
        if not any(uid in settings.admin_ids for uid in [int(os.getenv("ADMIN_ID", 0))]):
            logger.error("❌ This script can only be run by admins")
            sys.exit(1)

        # تحديث thumbnails
        total = update_thumbnails_batch()

        logger.info(f"\n🎉 Completed! Total videos updated: {total}")

    except KeyboardInterrupt:
        logger.info("\n⚠️ Script interrupted by user")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()