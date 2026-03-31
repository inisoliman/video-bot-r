#!/usr/bin/env python3
"""
نظام تنظيف سجل المشاهدة التلقائي
يحذف السجلات القديمة ويحد من عدد السجلات لكل مستخدم
"""

import sys
import os
import logging
import argparse
from datetime import datetime, timedelta

# إضافة مجلد app إلى المسار
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.database import get_pool
from app.config import Config

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class HistoryCleaner:
    """
    مدير تنظيف سجل المشاهدة
    """

    def __init__(self):
        self.config = Config()
        self.pool = get_pool()

    def get_cleanup_settings(self):
        """
        جلب إعدادات التنظيف من قاعدة البيانات
        """
        settings = {
            'days_to_keep': 10,
            'max_records_per_user': 100,
            'enabled': True
        }

        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    for key in settings.keys():
                        cursor.execute(
                            "SELECT setting_value FROM bot_settings WHERE setting_key = %s",
                            (f'cleanup_{key}',)
                        )
                        result = cursor.fetchone()
                        if result:
                            try:
                                if key == 'enabled':
                                    settings[key] = result[0].lower() == 'true'
                                elif key in ['days_to_keep', 'max_records_per_user']:
                                    settings[key] = int(result[0])
                            except (ValueError, AttributeError):
                                pass
        except Exception as e:
            logger.warning(f"Could not load cleanup settings from database: {e}")

        return settings

    def cleanup_old_history(self, days_to_keep=None, max_records_per_user=None, dry_run=False):
        """
        تنظيف سجل المشاهدة القديم

        Args:
            days_to_keep: عدد الأيام المحفوظة (إذا لم يحدد، يستخدم الإعدادات)
            max_records_per_user: الحد الأقصى لكل مستخدم (إذا لم يحدد، يستخدم الإعدادات)
            dry_run: تشغيل تجريبي بدون حذف

        Returns:
            عدد السجلات المحذوفة
        """
        settings = self.get_cleanup_settings()

        if days_to_keep is None:
            days_to_keep = settings['days_to_keep']
        if max_records_per_user is None:
            max_records_per_user = settings['max_records_per_user']

        cutoff_date = datetime.now() - timedelta(days=days_to_keep)

        logger.info(f"Starting history cleanup (days_to_keep={days_to_keep}, max_per_user={max_records_per_user}, dry_run={dry_run})")

        total_deleted = 0

        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # حذف السجلات القديمة
                    if dry_run:
                        cursor.execute("""
                            SELECT COUNT(*) FROM user_history
                            WHERE last_watched < %s
                        """, (cutoff_date,))
                        deleted_old = cursor.fetchone()[0]
                        logger.info(f"Would delete {deleted_old} old records")
                    else:
                        cursor.execute("""
                            DELETE FROM user_history
                            WHERE last_watched < %s
                        """, (cutoff_date,))
                        deleted_old = cursor.rowcount
                        logger.info(f"Deleted {deleted_old} old records")

                    # الحد من عدد السجلات لكل مستخدم
                    if dry_run:
                        cursor.execute("""
                            SELECT COUNT(*) FROM user_history h1
                            WHERE EXISTS (
                                SELECT 1 FROM user_history h2
                                WHERE h2.user_id = h1.user_id
                                GROUP BY h2.user_id
                                HAVING COUNT(*) > %s
                                AND h1.id IN (
                                    SELECT id FROM (
                                        SELECT id,
                                               ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY last_watched DESC) as rn
                                        FROM user_history
                                        WHERE user_id = h2.user_id
                                    ) t
                                    WHERE t.rn > %s
                                )
                            )
                        """, (max_records_per_user, max_records_per_user))
                        deleted_excess = cursor.fetchone()[0]
                        logger.info(f"Would delete {deleted_excess} excess records")
                    else:
                        cursor.execute("""
                            DELETE FROM user_history
                            WHERE id IN (
                                SELECT id FROM (
                                    SELECT id,
                                           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY last_watched DESC) as rn
                                    FROM user_history
                                ) t
                                WHERE t.rn > %s
                            )
                        """, (max_records_per_user,))
                        deleted_excess = cursor.rowcount
                        logger.info(f"Deleted {deleted_excess} excess records")

                    if not dry_run:
                        conn.commit()

                    total_deleted = deleted_old + deleted_excess
                    logger.info(f"History cleanup completed: {total_deleted} records processed")

        except Exception as e:
            logger.error(f"Error during history cleanup: {e}")
            if not dry_run:
                raise

        return total_deleted

    def get_history_stats(self):
        """
        جلب إحصائيات سجل المشاهدة
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # إجمالي السجلات
                    cursor.execute("SELECT COUNT(*) FROM user_history")
                    total_records = cursor.fetchone()[0]

                    # عدد المستخدمين النشطين
                    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM user_history")
                    active_users = cursor.fetchone()[0]

                    # أقدم سجل
                    cursor.execute("SELECT MIN(last_watched) FROM user_history")
                    oldest_record = cursor.fetchone()[0]

                    # أحدث سجل
                    cursor.execute("SELECT MAX(last_watched) FROM user_history")
                    newest_record = cursor.fetchone()[0]

                    # متوسط السجلات لكل مستخدم
                    cursor.execute("""
                        SELECT AVG(record_count) FROM (
                            SELECT COUNT(*) as record_count
                            FROM user_history
                            GROUP BY user_id
                        ) t
                    """)
                    avg_per_user = cursor.fetchone()[0] or 0

                    return {
                        'total_records': total_records,
                        'active_users': active_users,
                        'oldest_record': oldest_record,
                        'newest_record': newest_record,
                        'avg_records_per_user': round(avg_per_user, 2)
                    }

        except Exception as e:
            logger.error(f"Error getting history stats: {e}")
            return None

def main():
    parser = argparse.ArgumentParser(description='History Cleanup Tool')
    parser.add_argument('--days', type=int, help='Days to keep history (default: from settings)')
    parser.add_argument('--max-per-user', type=int, help='Max records per user (default: from settings)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be deleted without actually deleting')
    parser.add_argument('--stats', action='store_true', help='Show history statistics')
    parser.add_argument('--force', action='store_true', help='Force cleanup even if disabled in settings')

    args = parser.parse_args()

    cleaner = HistoryCleaner()

    if args.stats:
        stats = cleaner.get_history_stats()
        if stats:
            print("History Statistics:")
            print(f"  Total records: {stats['total_records']}")
            print(f"  Active users: {stats['active_users']}")
            print(f"  Oldest record: {stats['oldest_record']}")
            print(f"  Newest record: {stats['newest_record']}")
            print(f"  Avg records per user: {stats['avg_records_per_user']}")
        else:
            print("Could not retrieve statistics")
        return

    settings = cleaner.get_cleanup_settings()
    if not settings['enabled'] and not args.force:
        print("History cleanup is disabled in settings. Use --force to override.")
        return

    try:
        deleted = cleaner.cleanup_old_history(
            days_to_keep=args.days,
            max_records_per_user=args.max_per_user,
            dry_run=args.dry_run
        )

        if args.dry_run:
            print(f"Would delete {deleted} records")
        else:
            print(f"Successfully deleted {deleted} records")

    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()