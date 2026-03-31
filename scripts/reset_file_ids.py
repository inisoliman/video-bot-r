#!/usr/bin/env python3
"""
إعادة تعيين معرفات الملفات في قاعدة البيانات
يستخدم لتحديث file_ids القديمة أو إعادة التحقق منها
"""

import sys
import os
import logging
import argparse
import time
from typing import List, Dict, Optional

# إضافة مجلد app إلى المسار
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.database import get_pool
from app.config import Config
import telebot

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class FileIdResetter:
    """
    معيد تعيين معرفات الملفات
    """

    def __init__(self):
        self.config = Config()
        self.pool = get_pool()
        self.bot = telebot.TeleBot(self.config.BOT_TOKEN)

    def get_videos_for_reset(self, channel_filter: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
        """
        جلب الفيديوهات لإعادة التعيين

        Args:
            channel_filter: تصفية حسب اسم القناة
            limit: حد أقصى لعدد الفيديوهات

        Returns:
            قائمة بالفيديوهات
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    query = """
                        SELECT id, message_id, channel_username, title, file_id
                        FROM videos
                        WHERE message_id IS NOT NULL
                    """

                    params = []
                    if channel_filter:
                        query += " AND channel_username = %s"
                        params.append(channel_filter)

                    query += " ORDER BY created_at DESC"

                    if limit:
                        query += " LIMIT %s"
                        params.append(limit)

                    cursor.execute(query, params)
                    videos = cursor.fetchall()

                    return [{
                        'id': row[0],
                        'message_id': row[1],
                        'channel_username': row[2],
                        'title': row[3],
                        'current_file_id': row[4]
                    } for row in videos]

        except Exception as e:
            logger.error(f"Error getting videos for reset: {e}")
            return []

    def get_fresh_file_id(self, channel_username: str, message_id: int) -> Optional[str]:
        """
        الحصول على file_id جديد من الرسالة

        Args:
            channel_username: اسم القناة
            message_id: معرف الرسالة

        Returns:
            file_id جديد
        """
        try:
            # الحصول على معرف القناة
            chat_id = f"@{channel_username}"

            # إرسال الرسالة إلى الإدمن للتحقق من وجود الفيديو
            message = self.bot.forward_message(
                chat_id=self.config.ADMIN_ID,
                from_chat_id=chat_id,
                message_id=message_id
            )

            if message.video:
                file_id = message.video.file_id
                return file_id
            else:
                logger.warning(f"Message {message_id} in @{channel_username} does not contain a video")
                return None

        except Exception as e:
            logger.error(f"Error getting fresh file_id from @{channel_username}:{message_id}: {e}")
            return None

    def update_file_id(self, video_id: int, new_file_id: str) -> bool:
        """
        تحديث file_id للفيديو

        Args:
            video_id: معرف الفيديو
            new_file_id: المعرف الجديد

        Returns:
            نجاح العملية
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE videos
                        SET file_id = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (new_file_id, video_id))
                    conn.commit()

                    logger.info(f"Updated file_id for video {video_id}: {new_file_id}")
                    return True

        except Exception as e:
            logger.error(f"Error updating file_id for video {video_id}: {e}")
            return False

    def reset_file_ids(self, channel_filter: Optional[str] = None, limit: Optional[int] = None,
                      dry_run: bool = False, force: bool = False) -> Dict[str, int]:
        """
        إعادة تعيين معرفات الملفات

        Args:
            channel_filter: تصفية حسب القناة
            limit: حد أقصى للمعالجة
            dry_run: تشغيل تجريبي
            force: إجبار التحديث حتى لو كان file_id موجود

        Returns:
            إحصائيات العملية
        """
        videos = self.get_videos_for_reset(channel_filter, limit)

        stats = {
            'total': len(videos),
            'updated': 0,
            'skipped': 0,
            'failed': 0,
            'unchanged': 0
        }

        logger.info(f"Found {stats['total']} videos to process")

        for i, video in enumerate(videos, 1):
            logger.info(f"Processing video {i}/{stats['total']}: {video['title'][:50]}...")

            # تخطي إذا كان file_id موجود ولم يتم إجبار التحديث
            if video['current_file_id'] and not force:
                logger.debug(f"Skipping video {video['id']} - already has file_id")
                stats['skipped'] += 1
                continue

            # الحصول على file_id جديد
            new_file_id = self.get_fresh_file_id(
                video['channel_username'],
                video['message_id']
            )

            if new_file_id:
                # التحقق من التغيير
                if new_file_id == video['current_file_id']:
                    logger.debug(f"File ID unchanged for video {video['id']}")
                    stats['unchanged'] += 1
                else:
                    if dry_run:
                        logger.info(f"Would update video {video['id']}: {video['current_file_id']} -> {new_file_id}")
                        stats['updated'] += 1
                    else:
                        if self.update_file_id(video['id'], new_file_id):
                            stats['updated'] += 1
                        else:
                            stats['failed'] += 1
            else:
                logger.warning(f"Could not get file_id for video {video['id']}")
                stats['failed'] += 1

            # انتظار لتجنب حظر API
            time.sleep(1)

        logger.info(f"File ID reset completed: {stats}")
        return stats

    def backup_file_ids(self, filename: str) -> bool:
        """
        عمل نسخة احتياطية من file_ids الحالية

        Args:
            filename: اسم ملف النسخة الاحتياطية

        Returns:
            نجاح العملية
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT id, file_id, channel_username, message_id, title
                        FROM videos
                        WHERE file_id IS NOT NULL AND file_id != ''
                        ORDER BY id
                    """)
                    videos = cursor.fetchall()

                    import json
                    backup_data = {
                        'timestamp': time.time(),
                        'videos': [{
                            'id': row[0],
                            'file_id': row[1],
                            'channel_username': row[2],
                            'message_id': row[3],
                            'title': row[4]
                        } for row in videos]
                    }

                    with open(filename, 'w', encoding='utf-8') as f:
                        json.dump(backup_data, f, ensure_ascii=False, indent=2)

                    logger.info(f"Backed up {len(videos)} file_ids to {filename}")
                    return True

        except Exception as e:
            logger.error(f"Error creating backup: {e}")
            return False

def main():
    parser = argparse.ArgumentParser(description='File ID Reset Tool')
    parser.add_argument('--channel', help='Filter by channel username')
    parser.add_argument('--limit', type=int, help='Limit number of videos to process')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be updated without actually updating')
    parser.add_argument('--force', action='store_true', help='Force update even if file_id already exists')
    parser.add_argument('--backup', help='Create backup of current file_ids to specified file')

    args = parser.parse_args()

    resetter = FileIdResetter()

    try:
        if args.backup:
            if resetter.backup_file_ids(args.backup):
                print(f"Backup created: {args.backup}")
            else:
                print("Backup failed")
                sys.exit(1)
            return

        stats = resetter.reset_file_ids(
            channel_filter=args.channel,
            limit=args.limit,
            dry_run=args.dry_run,
            force=args.force
        )

        print("Reset Results:")
        print(f"  Total videos processed: {stats['total']}")
        print(f"  Updated: {stats['updated']}")
        print(f"  Skipped: {stats['skipped']}")
        print(f"  Failed: {stats['failed']}")
        print(f"  Unchanged: {stats['unchanged']}")

    except Exception as e:
        logger.error(f"Operation failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()