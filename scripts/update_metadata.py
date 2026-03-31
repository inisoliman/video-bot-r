# update_metadata.py

import psycopg2
from psycopg2.extras import DictCursor
import os
from urllib.parse import urlparse
import logging
import json
import time
import telebot

# استيراد المحلل الذكي الجديد من utils
from app.utils import extract_video_metadata
from app.config import settings
from app.database import get_pool

logger = logging.getLogger(__name__)

def get_videos_for_metadata_update():
    """جلب الفيديوهات التي تحتاج تحديث metadata"""
    pool = get_pool()
    with pool.getconn() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, caption, file_name, metadata
                FROM video_archive
                WHERE metadata IS NULL OR metadata = '{}' OR metadata->>'series_name' IS NULL
                ORDER BY id
            """)
            return [dict(zip(['id', 'caption', 'file_name', 'metadata'], row)) for row in cursor.fetchall()]

def update_video_metadata(video_id, metadata):
    """تحديث metadata للفيديو"""
    pool = get_pool()
    with pool.getconn() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE video_archive
                SET metadata = %s
                WHERE id = %s
            """, (json.dumps(metadata), video_id))
            conn.commit()
            return cursor.rowcount > 0

def run_update_and_report_progress(bot, chat_id, message_id):
    """
    Fetches all videos, re-parses their metadata using the new intelligent parser,
    and updates the database, reporting progress back to the admin.
    """
    try:
        logger.info("🚀 Starting metadata update...")

        # جلب الفيديوهات
        videos = get_videos_for_metadata_update()

        if not videos:
            bot.edit_message_text("✅ جميع الفيديوهات لديها metadata محدث!", chat_id, message_id)
            return

        total_videos = len(videos)
        updated_count = 0
        last_edit_time = 0

        bot.edit_message_text(
            f"🔄 بدء تحديث metadata...\n\n📊 إجمالي الفيديوهات: {total_videos}\n✅ تم التحديث: 0",
            chat_id, message_id
        )

        for i, video in enumerate(videos, 1):
            try:
                # استخراج metadata الجديد
                new_metadata = extract_video_metadata(video['caption'] or "", video['file_name'] or "")

                # تحديث قاعدة البيانات
                if update_video_metadata(video['id'], new_metadata):
                    updated_count += 1
                else:
                    logger.error(f"Failed to update metadata for video {video['id']}")

                # تحديث التقدم كل 10 فيديوهات أو كل 5 ثوان
                current_time = time.time()
                if i % 10 == 0 or current_time - last_edit_time > 5:
                    progress_text = f"🔄 تحديث metadata...\n\n📊 إجمالي الفيديوهات: {total_videos}\n✅ تم التحديث: {updated_count}\n📈 التقدم: {i}/{total_videos}"
                    bot.edit_message_text(progress_text, chat_id, message_id)
                    last_edit_time = current_time

            except Exception as e:
                logger.error(f"Error processing video {video['id']}: {e}")
                continue

        # تقرير نهائي
        final_text = f"🎉 تم الانتهاء من تحديث metadata!\n\n📊 إجمالي الفيديوهات: {total_videos}\n✅ تم التحديث بنجاح: {updated_count}\n❌ فشل: {total_videos - updated_count}"
        bot.edit_message_text(final_text, chat_id, message_id)

        logger.info(f"Metadata update completed: {updated_count}/{total_videos} videos updated")

    except Exception as e:
        error_text = f"❌ خطأ في تحديث metadata: {str(e)}"
        bot.edit_message_text(error_text, chat_id, message_id)
        logger.error(f"Metadata update failed: {e}", exc_info=True)

def main():
    """تشغيل السكريبت مباشرة"""
    try:
        logger.info("🚀 Starting metadata update script...")

        # إنشاء بوت للتقارير (اختياري)
        bot = telebot.TeleBot(settings.BOT_TOKEN)

        # تشغيل التحديث بدون تقارير
        videos = get_videos_for_metadata_update()
        updated_count = 0

        for video in videos:
            try:
                new_metadata = extract_video_metadata(video['caption'] or "", video['file_name'] or "")
                if update_video_metadata(video['id'], new_metadata):
                    updated_count += 1
                    if updated_count % 100 == 0:
                        logger.info(f"Updated {updated_count} videos...")
            except Exception as e:
                logger.error(f"Error processing video {video['id']}: {e}")
                continue

        logger.info(f"🎉 Metadata update completed! Updated {updated_count}/{len(videos)} videos")

    except Exception as e:
        logger.error(f"❌ Script failed: {e}", exc_info=True)

if __name__ == "__main__":
    main()