#!/usr/bin/env python3
"""
إصلاح معرفات الملفات المفقودة في قاعدة البيانات
يبحث عن الفيديوهات بدون file_id ويحاول استرجاعها من الرسائل المحفوظة
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

class FileIdFixer:
    """
    مصلح معرفات الملفات المفقودة
    """

    def __init__(self):
        self.config = Config()
        self.pool = get_pool()
        self.bot = telebot.TeleBot(self.config.BOT_TOKEN)

    def get_videos_without_file_id(self) -> List[Dict]:
        """
        جلب الفيديوهات بدون file_id

        Returns:
            قائمة بالفيديوهات المفقودة
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT id, message_id, channel_username, title
                        FROM videos
                        WHERE file_id IS NULL OR file_id = ''
                        ORDER BY created_at DESC
                    """)
                    videos = cursor.fetchall()

                    return [{
                        'id': row[0],
                        'message_id': row[1],
                        'channel_username': row[2],
                        'title': row[3]
                    } for row in videos]

        except Exception as e:
            logger.error(f"Error getting videos without file_id: {e}")
            return []

    def get_file_id_from_message(self, channel_username: str, message_id: int) -> Optional[str]:
        """
        استرجاع file_id من رسالة محددة

        Args:
            channel_username: اسم القناة
            message_id: معرف الرسالة

        Returns:
            file_id إذا تم العثور عليه
        """
        try:
            # الحصول على معرف القناة
            chat_id = f"@{channel_username}"

            # جلب الرسالة
            message = self.bot.forward_message(
                chat_id=self.config.ADMIN_ID,  # إرسال إلى الإدمن للتحقق
                from_chat_id=chat_id,
                message_id=message_id
            )

            if message.video:
                file_id = message.video.file_id
                logger.info(f"Found file_id for message {message_id}: {file_id}")
                return file_id
            else:
                logger.warning(f"Message {message_id} does not contain a video")
                return None

        except Exception as e:
            logger.error(f"Error getting file_id from message {message_id}: {e}")
            return None

    def update_video_file_id(self, video_id: int, file_id: str) -> bool:
        """
        تحديث file_id للفيديو

        Args:
            video_id: معرف الفيديو
            file_id: المعرف الجديد

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
                    """, (file_id, video_id))
                    conn.commit()

                    logger.info(f"Updated file_id for video {video_id}")
                    return True

        except Exception as e:
            logger.error(f"Error updating file_id for video {video_id}: {e}")
            return False

    def fix_missing_file_ids(self, dry_run: bool = False, limit: Optional[int] = None) -> Dict[str, int]:
        """
        إصلاح جميع معرفات الملفات المفقودة

        Args:
            dry_run: تشغيل تجريبي
            limit: حد أقصى لعدد الفيديوهات المعالجة

        Returns:
            إحصائيات العملية
        """
        videos = self.get_videos_without_file_id()

        if limit:
            videos = videos[:limit]

        stats = {
            'total': len(videos),
            'fixed': 0,
            'failed': 0,
            'skipped': 0
        }

        logger.info(f"Found {stats['total']} videos without file_id")

        for i, video in enumerate(videos, 1):
            logger.info(f"Processing video {i}/{stats['total']}: {video['title'][:50]}...")

            # استرجاع file_id من الرسالة
            file_id = self.get_file_id_from_message(
                video['channel_username'],
                video['message_id']
            )

            if file_id:
                if dry_run:
                    logger.info(f"Would update video {video['id']} with file_id: {file_id}")
                    stats['fixed'] += 1
                else:
                    if self.update_video_file_id(video['id'], file_id):
                        stats['fixed'] += 1
                    else:
                        stats['failed'] += 1
            else:
                logger.warning(f"Could not retrieve file_id for video {video['id']}")
                stats['failed'] += 1

            # انتظار لتجنب حظر API
            time.sleep(1)

        logger.info(f"File ID fix completed: {stats}")
        return stats

    def validate_file_ids(self) -> Dict[str, int]:
        """
        التحقق من صحة file_ids الموجودة

        Returns:
            إحصائيات التحقق
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # جلب جميع الفيديوهات مع file_id
                    cursor.execute("""
                        SELECT id, file_id, title
                        FROM videos
                        WHERE file_id IS NOT NULL AND file_id != ''
                    """)
                    videos = cursor.fetchall()

                    stats = {
                        'total': len(videos),
                        'valid': 0,
                        'invalid': 0
                    }

                    for video in videos:
                        video_id, file_id, title = video

                        try:
                            # محاولة الحصول على معلومات الملف
                            file_info = self.bot.get_file(file_id)
                            if file_info:
                                stats['valid'] += 1
                            else:
                                stats['invalid'] += 1
                                logger.warning(f"Invalid file_id for video {video_id}: {title[:50]}...")
                        except Exception as e:
                            stats['invalid'] += 1
                            logger.warning(f"Error validating file_id for video {video_id}: {e}")

                        # انتظار لتجنب حظر API
                        time.sleep(0.5)

                    logger.info(f"File ID validation completed: {stats}")
                    return stats

        except Exception as e:
            logger.error(f"Error during file ID validation: {e}")
            return {'total': 0, 'valid': 0, 'invalid': 0}

def main():
    parser = argparse.ArgumentParser(description='File ID Fixer Tool')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be fixed without actually fixing')
    parser.add_argument('--limit', type=int, help='Limit number of videos to process')
    parser.add_argument('--validate', action='store_true', help='Validate existing file_ids instead of fixing missing ones')

    args = parser.parse_args()

    fixer = FileIdFixer()

    try:
        if args.validate:
            stats = fixer.validate_file_ids()
            print("Validation Results:")
            print(f"  Total file_ids: {stats['total']}")
            print(f"  Valid: {stats['valid']}")
            print(f"  Invalid: {stats['invalid']}")
        else:
            stats = fixer.fix_missing_file_ids(dry_run=args.dry_run, limit=args.limit)
            print("Fix Results:")
            print(f"  Total videos without file_id: {stats['total']}")
            print(f"  Fixed: {stats['fixed']}")
            print(f"  Failed: {stats['failed']}")
            print(f"  Skipped: {stats['skipped']}")

    except Exception as e:
        logger.error(f"Operation failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()