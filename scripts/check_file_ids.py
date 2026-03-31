#!/usr/bin/env python3
"""
فحص صحة معرفات الملفات في قاعدة البيانات
يتحقق من وجود الفيديوهات وصحة file_ids
"""

import sys
import os
import logging
import argparse
import time
from typing import List, Dict, Tuple

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

class FileIdChecker:
    """
    فاحص صحة معرفات الملفات
    """

    def __init__(self):
        self.config = Config()
        self.pool = get_pool()
        self.bot = telebot.TeleBot(self.config.BOT_TOKEN)

    def get_videos_to_check(self, channel_filter: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
        """
        جلب الفيديوهات للفحص

        Args:
            channel_filter: تصفية حسب القناة
            limit: حد أقصى للنتائج

        Returns:
            قائمة بالفيديوهات
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    query = """
                        SELECT id, file_id, title, channel_username, message_id
                        FROM videos
                        WHERE file_id IS NOT NULL AND file_id != ''
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
                        'file_id': row[1],
                        'title': row[2],
                        'channel_username': row[3],
                        'message_id': row[4]
                    } for row in videos]

        except Exception as e:
            logger.error(f"Error getting videos to check: {e}")
            return []

    def check_file_id(self, file_id: str) -> Tuple[bool, Optional[str]]:
        """
        فحص صحة file_id واحد

        Args:
            file_id: معرف الملف

        Returns:
            (صحيح/خطأ, رسالة الخطأ إن وجدت)
        """
        try:
            # محاولة الحصول على معلومات الملف
            file_info = self.bot.get_file(file_id)

            if file_info and file_info.file_path:
                return True, None
            else:
                return False, "File info not available"

        except telebot.apihelper.ApiException as e:
            if "file is not found" in str(e).lower():
                return False, "File not found"
            elif "file is too big" in str(e).lower():
                return False, "File too big"
            else:
                return False, f"API Error: {e}"
        except Exception as e:
            return False, f"Unexpected error: {e}"

    def check_all_file_ids(self, channel_filter: Optional[str] = None, limit: Optional[int] = None,
                          fix_invalid: bool = False) -> Dict[str, any]:
        """
        فحص جميع معرفات الملفات

        Args:
            channel_filter: تصفية حسب القناة
            limit: حد أقصى للنتائج
            fix_invalid: محاولة إصلاح المعرفات غير الصحيحة

        Returns:
            إحصائيات الفحص
        """
        videos = self.get_videos_to_check(channel_filter, limit)

        stats = {
            'total': len(videos),
            'valid': 0,
            'invalid': 0,
            'fixed': 0,
            'errors': []
        }

        logger.info(f"Checking {stats['total']} file_ids")

        for i, video in enumerate(videos, 1):
            logger.info(f"Checking video {i}/{stats['total']}: {video['title'][:50]}...")

            is_valid, error_msg = self.check_file_id(video['file_id'])

            if is_valid:
                stats['valid'] += 1
                logger.debug(f"Valid file_id for video {video['id']}")
            else:
                stats['invalid'] += 1
                error_info = {
                    'video_id': video['id'],
                    'title': video['title'],
                    'file_id': video['file_id'],
                    'channel': video['channel_username'],
                    'error': error_msg
                }
                stats['errors'].append(error_info)
                logger.warning(f"Invalid file_id for video {video['id']}: {error_msg}")

                # محاولة الإصلاح إذا طلب ذلك
                if fix_invalid and video['message_id']:
                    logger.info(f"Attempting to fix file_id for video {video['id']}")
                    new_file_id = self.get_fresh_file_id(video['channel_username'], video['message_id'])
                    if new_file_id and new_file_id != video['file_id']:
                        if self.update_file_id(video['id'], new_file_id):
                            stats['fixed'] += 1
                            logger.info(f"Fixed file_id for video {video['id']}")
                        else:
                            logger.error(f"Failed to update file_id for video {video['id']}")

            # انتظار لتجنب حظر API
            time.sleep(0.5)

        logger.info(f"File ID check completed: {stats['valid']} valid, {stats['invalid']} invalid")
        return stats

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
            chat_id = f"@{channel_username}"

            # إرسال الرسالة للتحقق
            message = self.bot.forward_message(
                chat_id=self.config.ADMIN_ID,
                from_chat_id=chat_id,
                message_id=message_id
            )

            if message.video:
                return message.video.file_id
            else:
                return None

        except Exception as e:
            logger.error(f"Error getting fresh file_id: {e}")
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
                    return True

        except Exception as e:
            logger.error(f"Error updating file_id: {e}")
            return False

    def get_missing_file_ids(self) -> Dict[str, int]:
        """
        جلب إحصائيات المعرفات المفقودة

        Returns:
            إحصائيات المعرفات المفقودة
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # إجمالي الفيديوهات
                    cursor.execute("SELECT COUNT(*) FROM videos")
                    total_videos = cursor.fetchone()[0]

                    # الفيديوهات بدون file_id
                    cursor.execute("""
                        SELECT COUNT(*) FROM videos
                        WHERE file_id IS NULL OR file_id = ''
                    """)
                    missing_file_ids = cursor.fetchone()[0]

                    # الفيديوهات بدون message_id
                    cursor.execute("""
                        SELECT COUNT(*) FROM videos
                        WHERE message_id IS NULL
                    """)
                    missing_message_ids = cursor.fetchone()[0]

                    # الفيديوهات القابلة للإصلاح
                    cursor.execute("""
                        SELECT COUNT(*) FROM videos
                        WHERE (file_id IS NULL OR file_id = '') AND message_id IS NOT NULL
                    """)
                    fixable = cursor.fetchone()[0]

                    return {
                        'total_videos': total_videos,
                        'missing_file_ids': missing_file_ids,
                        'missing_message_ids': missing_message_ids,
                        'fixable': fixable,
                        'percentage_missing': round((missing_file_ids / total_videos) * 100, 2) if total_videos > 0 else 0
                    }

        except Exception as e:
            logger.error(f"Error getting missing file_ids stats: {e}")
            return {}

def print_stats(stats: Dict):
    """طباعة الإحصائيات"""
    print("\n=== إحصائيات معرفات الملفات ===")
    print(f"إجمالي الفيديوهات: {stats.get('total', 0)}")
    print(f"المعرفات الصحيحة: {stats.get('valid', 0)}")
    print(f"المعرفات غير الصحيحة: {stats.get('invalid', 0)}")
    print(f"المعرفات المصلحة: {stats.get('fixed', 0)}")

    if stats.get('errors'):
        print(f"\nأخطاء ({len(stats['errors'])}):")
        for error in stats['errors'][:5]:  # أول 5 أخطاء فقط
            print(f"  فيديو {error['video_id']}: {error['error']}")

def print_missing_stats(stats: Dict):
    """طباعة إحصائيات المعرفات المفقودة"""
    print("\n=== إحصائيات المعرفات المفقودة ===")
    print(f"إجمالي الفيديوهات: {stats.get('total_videos', 0)}")
    print(f"بدون file_id: {stats.get('missing_file_ids', 0)}")
    print(f"بدون message_id: {stats.get('missing_message_ids', 0)}")
    print(f"قابلة للإصلاح: {stats.get('fixable', 0)}")
    print(f"نسبة المفقودة: {stats.get('percentage_missing', 0)}%")

def main():
    parser = argparse.ArgumentParser(description='File ID Check Tool')
    parser.add_argument('--channel', help='Filter by channel username')
    parser.add_argument('--limit', type=int, help='Limit number of videos to check')
    parser.add_argument('--fix', action='store_true', help='Attempt to fix invalid file_ids')
    parser.add_argument('--missing', action='store_true', help='Show missing file_ids statistics instead of checking validity')

    args = parser.parse_args()

    checker = FileIdChecker()

    try:
        if args.missing:
            stats = checker.get_missing_file_ids()
            print_missing_stats(stats)
        else:
            stats = checker.check_all_file_ids(
                channel_filter=args.channel,
                limit=args.limit,
                fix_invalid=args.fix
            )
            print_stats(stats)

    except Exception as e:
        logger.error(f"Operation failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()