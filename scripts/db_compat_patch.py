#!/usr/bin/env python3
"""
تصحيحات توافق قاعدة البيانات
يطبق التحديثات والإصلاحات على قاعدة البيانات
"""

import sys
import os
import logging
import argparse
from datetime import datetime

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

class DatabaseCompatPatcher:
    """
    مصلح توافق قاعدة البيانات
    """

    def __init__(self):
        self.config = Config()
        self.pool = get_pool()
        self.applied_patches = []

    def get_applied_patches(self) -> set:
        """
        جلب التصحيحات المطبقة مسبقاً

        Returns:
            مجموعة التصحيحات المطبقة
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT patch_name FROM db_patches
                        WHERE applied_at IS NOT NULL
                    """)
                    patches = cursor.fetchall()
                    return {row[0] for row in patches}
        except Exception:
            # الجدول غير موجود بعد
            return set()

    def record_patch(self, patch_name: str, description: str):
        """
        تسجيل تطبيق تصحيح

        Args:
            patch_name: اسم التصحيح
            description: وصف التصحيح
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO db_patches (patch_name, description, applied_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (patch_name) DO UPDATE SET
                            applied_at = EXCLUDED.applied_at,
                            description = EXCLUDED.description
                    """, (patch_name, description, datetime.now()))
                    conn.commit()

            self.applied_patches.append(patch_name)
            logger.info(f"Applied patch: {patch_name}")

        except Exception as e:
            logger.error(f"Error recording patch {patch_name}: {e}")

    def create_patches_table(self):
        """
        إنشاء جدول تسجيل التصحيحات
        """
        patch_name = "create_patches_table"

        if patch_name in self.get_applied_patches():
            logger.info("Patches table already exists")
            return

        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS db_patches (
                            patch_name VARCHAR(255) PRIMARY KEY,
                            description TEXT,
                            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    conn.commit()

            self.record_patch(patch_name, "Create db_patches table to track applied patches")
            logger.info("Created patches tracking table")

        except Exception as e:
            logger.error(f"Error creating patches table: {e}")

    def add_missing_indexes(self):
        """
        إضافة الفهارس المفقودة لتحسين الأداء
        """
        indexes = [
            ("idx_videos_category_id", "videos", "category_id"),
            ("idx_videos_channel", "videos", "channel_username"),
            ("idx_videos_created_at", "videos", "created_at"),
            ("idx_user_history_user_id", "user_history", "user_id"),
            ("idx_user_history_video_id", "user_history", "video_id"),
            ("idx_user_history_last_watched", "user_history", "last_watched"),
            ("idx_comments_video_id", "comments", "video_id"),
            ("idx_comments_user_id", "comments", "user_id"),
            ("idx_favorites_user_id", "favorites", "user_id"),
            ("idx_favorites_video_id", "favorites", "video_id"),
            ("idx_ratings_video_id", "ratings", "video_id"),
            ("idx_ratings_user_id", "ratings", "user_id"),
        ]

        for index_name, table, column in indexes:
            if index_name in self.get_applied_patches():
                continue

            try:
                with self.pool.getconn() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(f"""
                            CREATE INDEX IF NOT EXISTS {index_name}
                            ON {table} ({column})
                        """)
                        conn.commit()

                self.record_patch(index_name, f"Create index {index_name} on {table}({column})")
                logger.info(f"Created index: {index_name}")

            except Exception as e:
                logger.error(f"Error creating index {index_name}: {e}")

    def fix_data_types(self):
        """
        إصلاح أنواع البيانات وإضافة الأعمدة المفقودة
        """
        patches = [
            ("add_video_metadata_columns", """
                ALTER TABLE videos
                ADD COLUMN IF NOT EXISTS duration INTEGER,
                ADD COLUMN IF NOT EXISTS file_size BIGINT,
                ADD COLUMN IF NOT EXISTS resolution VARCHAR(20),
                ADD COLUMN IF NOT EXISTS bitrate INTEGER
            """, "Add metadata columns to videos table"),

            ("add_user_preferences", """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS preferences JSONB DEFAULT '{}',
                ADD COLUMN IF NOT EXISTS last_activity TIMESTAMP,
                ADD COLUMN IF NOT EXISTS language VARCHAR(10) DEFAULT 'ar'
            """, "Add user preferences and activity tracking"),

            ("add_video_stats", """
                ALTER TABLE videos
                ADD COLUMN IF NOT EXISTS view_count INTEGER DEFAULT 0,
                ADD COLUMN IF NOT EXISTS like_count INTEGER DEFAULT 0,
                ADD COLUMN IF NOT EXISTS comment_count INTEGER DEFAULT 0
            """, "Add video statistics columns"),

            ("add_bot_settings_defaults", """
                INSERT INTO bot_settings (setting_key, setting_value)
                VALUES
                    ('cleanup_enabled', 'true'),
                    ('cleanup_days_to_keep', '10'),
                    ('cleanup_max_per_user', '100'),
                    ('max_video_size_mb', '50'),
                    ('thumbnail_quality', '85')
                ON CONFLICT (setting_key) DO NOTHING
            """, "Add default bot settings"),
        ]

        for patch_name, sql, description in patches:
            if patch_name in self.get_applied_patches():
                continue

            try:
                with self.pool.getconn() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(sql)
                        conn.commit()

                self.record_patch(patch_name, description)
                logger.info(f"Applied data fix: {patch_name}")

            except Exception as e:
                logger.error(f"Error applying data fix {patch_name}: {e}")

    def clean_orphaned_records(self):
        """
        تنظيف السجلات اليتيمة (orphaned records)
        """
        patch_name = "clean_orphaned_records"

        if patch_name in self.get_applied_patches():
            logger.info("Orphaned records cleanup already applied")
            return

        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # حذف سجل المشاهدة للفيديوهات غير الموجودة
                    cursor.execute("""
                        DELETE FROM user_history
                        WHERE video_id NOT IN (SELECT id FROM videos)
                    """)
                    deleted_history = cursor.rowcount

                    # حذف التعليقات للفيديوهات غير الموجودة
                    cursor.execute("""
                        DELETE FROM comments
                        WHERE video_id NOT IN (SELECT id FROM videos)
                    """)
                    deleted_comments = cursor.rowcount

                    # حذف المفضلات للفيديوهات غير الموجودة
                    cursor.execute("""
                        DELETE FROM favorites
                        WHERE video_id NOT IN (SELECT id FROM videos)
                    """)
                    deleted_favorites = cursor.rowcount

                    # حذف التقييمات للفيديوهات غير الموجودة
                    cursor.execute("""
                        DELETE FROM ratings
                        WHERE video_id NOT IN (SELECT id FROM videos)
                    """)
                    deleted_ratings = cursor.rowcount

                    conn.commit()

                    total_deleted = deleted_history + deleted_comments + deleted_favorites + deleted_ratings

                    if total_deleted > 0:
                        logger.info(f"Cleaned up {total_deleted} orphaned records")
                        self.record_patch(patch_name, f"Cleaned {total_deleted} orphaned records")
                    else:
                        logger.info("No orphaned records found")
                        self.record_patch(patch_name, "No orphaned records to clean")

        except Exception as e:
            logger.error(f"Error cleaning orphaned records: {e}")

    def update_video_stats(self):
        """
        تحديث إحصائيات الفيديوهات
        """
        patch_name = "update_video_stats"

        if patch_name in self.get_applied_patches():
            logger.info("Video stats update already applied")
            return

        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # تحديث عدد المشاهدات
                    cursor.execute("""
                        UPDATE videos
                        SET view_count = COALESCE((
                            SELECT SUM(watch_count)
                            FROM user_history
                            WHERE video_id = videos.id
                        ), 0)
                    """)

                    # تحديث عدد التعليقات
                    cursor.execute("""
                        UPDATE videos
                        SET comment_count = COALESCE((
                            SELECT COUNT(*)
                            FROM comments
                            WHERE video_id = videos.id
                        ), 0)
                    """)

                    # تحديث عدد الإعجابات (من التقييمات المرتفعة)
                    cursor.execute("""
                        UPDATE videos
                        SET like_count = COALESCE((
                            SELECT COUNT(*)
                            FROM ratings
                            WHERE video_id = videos.id AND rating >= 4
                        ), 0)
                    """)

                    conn.commit()

                    logger.info("Updated video statistics")
                    self.record_patch(patch_name, "Updated view_count, comment_count, and like_count for all videos")

        except Exception as e:
            logger.error(f"Error updating video stats: {e}")

    def add_constraints(self):
        """
        إضافة القيود (constraints) لضمان سلامة البيانات
        """
        constraints = [
            ("fk_videos_category_id", """
                ALTER TABLE videos
                ADD CONSTRAINT fk_videos_category_id
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            """, "Add foreign key constraint for videos.category_id"),

            ("fk_user_history_video_id", """
                ALTER TABLE user_history
                ADD CONSTRAINT fk_user_history_video_id
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            """, "Add foreign key constraint for user_history.video_id"),

            ("fk_comments_video_id", """
                ALTER TABLE comments
                ADD CONSTRAINT fk_comments_video_id
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            """, "Add foreign key constraint for comments.video_id"),

            ("fk_favorites_video_id", """
                ALTER TABLE favorites
                ADD CONSTRAINT fk_favorites_video_id
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            """, "Add foreign key constraint for favorites.video_id"),

            ("fk_ratings_video_id", """
                ALTER TABLE ratings
                ADD CONSTRAINT fk_ratings_video_id
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            """, "Add foreign key constraint for ratings.video_id"),
        ]

        for constraint_name, sql, description in constraints:
            if constraint_name in self.get_applied_patches():
                continue

            try:
                with self.pool.getconn() as conn:
                    with conn.cursor() as cursor:
                        # فحص إذا كان القيد موجود مسبقاً
                        cursor.execute("""
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE constraint_name = %s
                        """, (constraint_name,))

                        if not cursor.fetchone():
                            cursor.execute(sql)
                            conn.commit()
                            self.record_patch(constraint_name, description)
                            logger.info(f"Added constraint: {constraint_name}")
                        else:
                            logger.info(f"Constraint {constraint_name} already exists")

            except Exception as e:
                logger.error(f"Error adding constraint {constraint_name}: {e}")

    def apply_all_patches(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        تطبيق جميع التصحيحات

        Args:
            dry_run: تشغيل تجريبي بدون تطبيق

        Returns:
            تقرير التصحيحات المطبقة
        """
        logger.info("Starting database compatibility patches...")

        if dry_run:
            logger.info("DRY RUN MODE - No changes will be made")

        # تطبيق التصحيحات بالترتيب
        patches = [
            ("create_patches_table", "Create patches tracking table", self.create_patches_table),
            ("add_missing_indexes", "Add performance indexes", self.add_missing_indexes),
            ("fix_data_types", "Fix data types and add columns", self.fix_data_types),
            ("clean_orphaned_records", "Clean orphaned records", self.clean_orphaned_records),
            ("update_video_stats", "Update video statistics", self.update_video_stats),
            ("add_constraints", "Add data integrity constraints", self.add_constraints),
        ]

        applied = []
        skipped = []
        errors = []

        for patch_name, description, patch_func in patches:
            try:
                if not dry_run:
                    patch_func()
                else:
                    logger.info(f"Would apply: {patch_name} - {description}")

                if patch_name in self.applied_patches or dry_run:
                    applied.append({'name': patch_name, 'description': description})
                else:
                    skipped.append({'name': patch_name, 'reason': 'Already applied'})

            except Exception as e:
                errors.append({'name': patch_name, 'error': str(e)})
                logger.error(f"Failed to apply patch {patch_name}: {e}")

        report = {
            'timestamp': datetime.now(),
            'dry_run': dry_run,
            'applied_patches': applied,
            'skipped_patches': skipped,
            'errors': errors,
            'total_applied': len(applied),
            'total_errors': len(errors)
        }

        logger.info(f"Patch application completed: {len(applied)} applied, {len(errors)} errors")
        return report

def main():
    parser = argparse.ArgumentParser(description='Database Compatibility Patcher')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be patched without applying changes')
    parser.add_argument('--output', help='Save patch report to JSON file')

    args = parser.parse_args()

    patcher = DatabaseCompatPatcher()
    report = patcher.apply_all_patches(dry_run=args.dry_run)

    print("=== تقرير تصحيحات قاعدة البيانات ===")
    print(f"تاريخ التشغيل: {report['timestamp']}")
    print(f"الوضع التجريبي: {'نعم' if report['dry_run'] else 'لا'}")
    print(f"التصحيحات المطبقة: {report['total_applied']}")
    print(f"الأخطاء: {report['total_errors']}")
    print()

    if report['applied_patches']:
        print("التصحيحات المطبقة:")
        for patch in report['applied_patches']:
            print(f"  ✓ {patch['name']}: {patch['description']}")

    if report['errors']:
        print("\nالأخطاء:")
        for error in report['errors']:
            print(f"  ✗ {error['name']}: {error['error']}")

    if args.output:
        import json
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(report, f, default=str, ensure_ascii=False, indent=2)
        print(f"\nتم حفظ التقرير في: {args.output}")

    if report['total_errors'] > 0:
        sys.exit(1)

if __name__ == '__main__':
    main()