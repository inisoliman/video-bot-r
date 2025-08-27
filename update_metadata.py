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
from utils import extract_video_metadata 

logger = logging.getLogger(__name__)

def get_db_connection():
    """Establish and return a database connection."""
    try:
        DATABASE_URL = os.environ.get('DATABASE_URL')
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL environment variable not set.")

        result = urlparse(DATABASE_URL)
        DB_CONFIG = {
            'user': result.username,
            'password': result.password,
            'host': result.hostname,
            'port': result.port,
            'dbname': result.path[1:]
        }
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return None

def run_update_and_report_progress(bot, chat_id, message_id):
    """
    Fetches all videos, re-parses their metadata using the new intelligent parser,
    and updates the database, reporting progress back to the admin.
    """
    conn = get_db_connection()
    if not conn:
        bot.edit_message_text("❌ فشل الاتصال بقاعدة البيانات.", chat_id, message_id)
        return

    updated_count = 0
    total_videos = 0
    last_edit_time = 0

    try:
        with conn.cursor(cursor_factory=DictCursor) as c:
            logger.info("Fetching all videos from the database for metadata rebuild...")
            c.execute("SELECT id, caption, file_name FROM video_archive")
            videos = c.fetchall()
            total_videos = len(videos)

            if not videos:
                bot.edit_message_text("✅ لا توجد فيديوهات في قاعدة البيانات لتحديثها.", chat_id, message_id)
                return

            for i, video in enumerate(videos):
                # استدعاء المحلل الذكي الجديد
                # نمرر الكابشن واسم الملف للحصول على أفضل نتيجة
                new_metadata = extract_video_metadata(video['caption'], video['file_name'])

                # تحويل القاموس إلى صيغة JSON لحفظه
                metadata_json = json.dumps(new_metadata)

                # تحديث قاعدة البيانات بالبيانات الجديدة (يستبدل القديمة بالكامل)
                c.execute("UPDATE video_archive SET metadata = %s WHERE id = %s", (metadata_json, video['id']))
                updated_count += 1

                # تحديث الرسالة كل 1.5 ثانية لإظهار التقدم
                current_time = time.time()
                if current_time - last_edit_time > 1.5 or (i + 1) == total_videos:
                    try:
                        progress = ((i + 1) / total_videos) * 100
                        bot.edit_message_text(f"⏳ جارِ إعادة بناء البيانات... ({i + 1}/{total_videos}) - {progress:.0f}%", chat_id, message_id)
                        last_edit_time = current_time
                    except telebot.apihelper.ApiTelegramException as e:
                        if 'message is not modified' in e.description:
                            continue # تجاهل الخطأ إذا لم تتغير الرسالة
                        else:
                            logger.error(f"Error editing progress message: {e}")

            conn.commit()
            bot.edit_message_text(f"✅ اكتملت إعادة بناء البيانات بنجاح!\n\n- تم فحص وتحديث: {updated_count} فيديو.", chat_id, message_id)

    except Exception as e:
        logger.error(f"An error occurred during the update process: {e}", exc_info=True)
        if conn:
            conn.rollback()
        bot.edit_message_text(f"❌ حدث خطأ أثناء التحديث: {e}", chat_id, message_id)
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    # هذا الجزء للتوضيح فقط، السكريبت مصمم ليتم استدعاؤه من البوت
    logger.info("This script is intended to be called from the main bot via the admin panel.")
