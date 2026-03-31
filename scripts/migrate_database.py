#!/usr/bin/env python3
"""
مهاجر قاعدة البيانات
ينفذ migrations لتحديث هيكل قاعدة البيانات
"""

import sys
import os
import logging
import argparse
import importlib.util
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

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

class DatabaseMigrator:
    """
    مهاجر قاعدة البيانات
    """

    def __init__(self):
        self.config = Config()
        self.pool = get_pool()
        self.migrations_dir = Path(__file__).parent / 'migrations'

    def ensure_migrations_table(self):
        """
        إنشاء جدول migrations إذا لم يكن موجوداً
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS schema_migrations (
                            version VARCHAR(255) PRIMARY KEY,
                            name VARCHAR(255) NOT NULL,
                            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            checksum VARCHAR(255)
                        )
                    """)
                    conn.commit()
                    logger.info("Ensured migrations table exists")

        except Exception as e:
            logger.error(f"Error creating migrations table: {e}")
            raise

    def get_applied_migrations(self) -> Dict[str, Dict[str, Any]]:
        """
        جلب المهاجرات المطبقة

        Returns:
            قاموس بالمهاجرات المطبقة
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT version, name, applied_at, checksum
                        FROM schema_migrations
                        ORDER BY version
                    """)
                    migrations = cursor.fetchall()

                    return {
                        row[0]: {
                            'name': row[1],
                            'applied_at': row[2],
                            'checksum': row[3]
                        }
                        for row in migrations
                    }

        except Exception as e:
            logger.error(f"Error getting applied migrations: {e}")
            return {}

    def get_pending_migrations(self) -> List[Dict[str, Any]]:
        """
        جلب المهاجرات المعلقة

        Returns:
            قائمة بالمهاجرات المعلقة
        """
        if not self.migrations_dir.exists():
            logger.info("No migrations directory found")
            return []

        applied = self.get_applied_migrations()
        pending = []

        # قراءة ملفات المهاجرات
        migration_files = sorted(self.migrations_dir.glob('*.py'))

        for migration_file in migration_files:
            version = migration_file.stem

            if version not in applied:
                # تحميل معلومات المهجرة
                spec = importlib.util.spec_from_file_location(f"migration_{version}", migration_file)
                module = importlib.util.module_from_spec(spec)

                try:
                    spec.loader.exec_module(module)

                    if hasattr(module, 'Migration'):
                        migration_class = module.Migration
                        migration_info = {
                            'version': version,
                            'name': getattr(migration_class, 'name', f'Migration {version}'),
                            'file': migration_file,
                            'class': migration_class
                        }
                        pending.append(migration_info)

                except Exception as e:
                    logger.error(f"Error loading migration {version}: {e}")

        return sorted(pending, key=lambda x: x['version'])

    def apply_migration(self, migration: Dict[str, Any]) -> bool:
        """
        تطبيق مهاجرة واحدة

        Args:
            migration: معلومات المهجرة

        Returns:
            نجاح التطبيق
        """
        version = migration['version']
        name = migration['name']
        migration_class = migration['class']

        logger.info(f"Applying migration {version}: {name}")

        try:
            # إنشاء instance من المهجرة
            mig_instance = migration_class()

            # تطبيق المهجرة
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    if hasattr(mig_instance, 'up'):
                        mig_instance.up(cursor)
                    else:
                        raise AttributeError("Migration class must have 'up' method")

                    # تسجيل المهجرة كمطبقة
                    checksum = getattr(mig_instance, 'checksum', '')
                    cursor.execute("""
                        INSERT INTO schema_migrations (version, name, checksum)
                        VALUES (%s, %s, %s)
                    """, (version, name, checksum))

                    conn.commit()

            logger.info(f"Successfully applied migration {version}")
            return True

        except Exception as e:
            logger.error(f"Error applying migration {version}: {e}")
            return False

    def rollback_migration(self, migration: Dict[str, Any]) -> bool:
        """
        التراجع عن مهاجرة

        Args:
            migration: معلومات المهجرة

        Returns:
            نجاح التراجع
        """
        version = migration['version']
        name = migration['name']
        migration_class = migration['class']

        logger.info(f"Rolling back migration {version}: {name}")

        try:
            mig_instance = migration_class()

            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    if hasattr(mig_instance, 'down'):
                        mig_instance.down(cursor)
                    else:
                        logger.warning(f"Migration {version} has no down method, skipping")

                    # حذف تسجيل المهجرة
                    cursor.execute("""
                        DELETE FROM schema_migrations
                        WHERE version = %s
                    """, (version,))

                    conn.commit()

            logger.info(f"Successfully rolled back migration {version}")
            return True

        except Exception as e:
            logger.error(f"Error rolling back migration {version}: {e}")
            return False

    def migrate(self, target_version: str = None, dry_run: bool = False) -> Dict[str, Any]:
        """
        تنفيذ المهاجرات

        Args:
            target_version: الإصدار المستهدف
            dry_run: تشغيل تجريبي

        Returns:
            تقرير المهاجرات
        """
        self.ensure_migrations_table()

        pending = self.get_pending_migrations()

        if not pending:
            logger.info("No pending migrations")
            return {'status': 'no_pending', 'applied': [], 'failed': []}

        # تحديد المهاجرات المراد تطبيقها
        if target_version:
            target_index = None
            for i, mig in enumerate(pending):
                if mig['version'] == target_version:
                    target_index = i + 1
                    break

            if target_index is None:
                raise ValueError(f"Target migration {target_version} not found")

            pending = pending[:target_index]

        applied = []
        failed = []

        logger.info(f"Applying {len(pending)} migrations...")

        for migration in pending:
            if dry_run:
                logger.info(f"Would apply: {migration['version']} - {migration['name']}")
                applied.append(migration)
            else:
                if self.apply_migration(migration):
                    applied.append(migration)
                else:
                    failed.append(migration)
                    break  # توقف عند أول فشل

        status = 'completed' if not failed else 'failed'
        if dry_run:
            status = 'dry_run'

        report = {
            'status': status,
            'applied': [{'version': m['version'], 'name': m['name']} for m in applied],
            'failed': [{'version': m['version'], 'name': m['name']} for m in failed],
            'total_applied': len(applied),
            'total_failed': len(failed)
        }

        logger.info(f"Migration {status}: {len(applied)} applied, {len(failed)} failed")
        return report

    def rollback(self, steps: int = 1, dry_run: bool = False) -> Dict[str, Any]:
        """
        التراجع عن المهاجرات

        Args:
            steps: عدد الخطوات للتراجع
            dry_run: تشغيل تجريبي

        Returns:
            تقرير التراجع
        """
        applied_migrations = self.get_applied_migrations()

        if not applied_migrations:
            logger.info("No migrations to rollback")
            return {'status': 'no_migrations', 'rolled_back': [], 'failed': []}

        # جلب آخر N مهاجرات
        sorted_versions = sorted(applied_migrations.keys(), reverse=True)
        rollback_versions = sorted_versions[:steps]

        rolled_back = []
        failed = []

        logger.info(f"Rolling back {len(rollback_versions)} migrations...")

        for version in rollback_versions:
            migration_info = applied_migrations[version]

            # إعادة إنشاء كائن المهجرة
            migration_file = self.migrations_dir / f"{version}.py"

            if not migration_file.exists():
                logger.error(f"Migration file {version}.py not found")
                failed.append({'version': version, 'name': migration_info['name']})
                continue

            spec = importlib.util.spec_from_file_location(f"migration_{version}", migration_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            migration = {
                'version': version,
                'name': migration_info['name'],
                'class': module.Migration
            }

            if dry_run:
                logger.info(f"Would rollback: {version} - {migration_info['name']}")
                rolled_back.append(migration)
            else:
                if self.rollback_migration(migration):
                    rolled_back.append(migration)
                else:
                    failed.append(migration)
                    break

        status = 'completed' if not failed else 'failed'
        if dry_run:
            status = 'dry_run'

        report = {
            'status': status,
            'rolled_back': [{'version': m['version'], 'name': m['name']} for m in rolled_back],
            'failed': [{'version': m['version'], 'name': m['name']} for m in failed],
            'total_rolled_back': len(rolled_back),
            'total_failed': len(failed)
        }

        logger.info(f"Rollback {status}: {len(rolled_back)} rolled back, {len(failed)} failed")
        return report

    def status(self) -> Dict[str, Any]:
        """
        عرض حالة المهاجرات

        Returns:
            حالة المهاجرات
        """
        applied = self.get_applied_migrations()
        pending = self.get_pending_migrations()

        return {
            'applied_count': len(applied),
            'pending_count': len(pending),
            'applied': [{'version': v, 'name': info['name'], 'applied_at': info['applied_at']}
                       for v, info in applied.items()],
            'pending': [{'version': m['version'], 'name': m['name']} for m in pending]
        }

def main():
    parser = argparse.ArgumentParser(description='Database Migration Tool')
    parser.add_argument('command', choices=['migrate', 'rollback', 'status'],
                       help='Migration command')
    parser.add_argument('--target', help='Target migration version for migrate')
    parser.add_argument('--steps', type=int, default=1,
                       help='Number of steps to rollback (default: 1)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be done without making changes')
    parser.add_argument('--output', help='Save migration report to JSON file')

    args = parser.parse_args()

    migrator = DatabaseMigrator()

    try:
        if args.command == 'migrate':
            report = migrator.migrate(target_version=args.target, dry_run=args.dry_run)

        elif args.command == 'rollback':
            report = migrator.rollback(steps=args.steps, dry_run=args.dry_run)

        elif args.command == 'status':
            status_info = migrator.status()
            print("=== حالة المهاجرات ===")
            print(f"المطبقة: {status_info['applied_count']}")
            print(f"المعلقة: {status_info['pending_count']}")
            print()

            if status_info['applied']:
                print("المطبقة:")
                for mig in status_info['applied'][-5:]:  # آخر 5
                    print(f"  ✓ {mig['version']}: {mig['name']} ({mig['applied_at']})")

            if status_info['pending']:
                print("المعلقة:")
                for mig in status_info['pending'][:5]:  # أول 5
                    print(f"  ○ {mig['version']}: {mig['name']}")

            return

        # طباعة التقرير
        print("=== تقرير المهاجرات ===")
        print(f"الحالة: {report['status']}")
        print(f"المطبقة: {report.get('total_applied', 0)}")
        print(f"الفاشلة: {report.get('total_failed', 0)}")

        if report.get('applied'):
            print("\nالمطبقة:")
            for mig in report['applied']:
                print(f"  ✓ {mig['version']}: {mig['name']}")

        if report.get('failed'):
            print("\nالفاشلة:")
            for mig in report['failed']:
                print(f"  ✗ {mig['version']}: {mig['name']}")

        if args.output:
            import json
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(report, f, default=str, ensure_ascii=False, indent=2)
            print(f"\nتم حفظ التقرير في: {args.output}")

        if report.get('total_failed', 0) > 0:
            sys.exit(1)

    except Exception as e:
        logger.error(f"Migration command failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()