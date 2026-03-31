#!/usr/bin/env python3
"""
سكريبت لاستخراج thumbnails من الفيديوهات الموجودة في القناة.

هذا الحل يعمل لأن:
1. الفيديوهات موجودة في القناة مع صورها المصغرة
2. نجلب الرسائل مباشرة من القناة
3. نستخرج thumbnail_file_id من message.video.thumb
4. نحفظه في قاعدة البيانات
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

def extract_thumbnails_from_channel():
    """
    استخراج thumbnails من القناة للفيديوهات التي لا تملك thumbnail_file_id.
    """
    try:
        logger.info("🚀 Starting thumbnail extraction from channel...")

        # جلب الفيديوهات بدون thumbnail
        videos = get_videos_without_thumbnail(limit=1000)

        if not videos:
            logger.info("✅ All videos already have thumbnails!")
            return 0

        logger.info(f"📊 Found {len(videos)} videos without thumbnails")

        total_updated = 0
        failed_count = 0

        for i, video in enumerate(videos, 1):
            try:
                logger.info(f"Processing {i}/{len(videos)}: Video ID {video['id']}, Message ID {video['message_id']}")

                # التحقق من وجود message_id و chat_id
                if not video.get('message_id') or not video.get('chat_id'):
                    logger.warning(f"⚠️ Video {video['id']} missing message_id or chat_id, skipping")
                    failed_count += 1
                    continue

                # جلب الرسالة من القناة
                try:
                    message = bot.forward_message(
                        chat_id=video['chat_id'],
                        from_chat_id=video['chat_id'],
                        message_id=video['message_id']
                    )

                    # حذف الرسالة المعاد توجيهها فوراً
                    try:
                        bot.delete_message(video['chat_id'], message.message_id)
                    except:
                        pass

                except Exception as e:
                    logger.info(f"Forward failed, trying direct fetch for video {video['id']}")
                    logger.warning(f"⚠️ Cannot fetch message {video['message_id']} for video {video['id']}: {e}")
                    failed_count += 1
                    continue

                # استخراج thumbnail
                if message.video and message.video.thumb:
                    thumbnail_id = message.video.thumb.file_id

                    # حفظ في قاعدة البيانات
                    if update_video_thumbnail(video['id'], thumbnail_id):
                        total_updated += 1
                        logger.info(f"✅ Updated video {video['id']} ({total_updated}/{len(videos)})")
                    else:
                        failed_count += 1
                        logger.error(f"❌ Failed to update database for video {video['id']}")
                else:
                    logger.warning(f"⚠️ No thumbnail found in message for video {video['id']}")
                    failed_count += 1

                # تأخير بسيط لتجنب rate limiting
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"❌ Error processing video {video['id']}: {e}")
                failed_count += 1
                continue

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
        logger.info("Channel Thumbnail Extraction Script")
        logger.info("="*50)

        # استخراج thumbnails
        total = extract_thumbnails_from_channel()

        logger.info(f"\n🎉 Completed! Total videos updated: {total}")

    except KeyboardInterrupt:
        logger.info("\n⚠️ Script interrupted by user")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()