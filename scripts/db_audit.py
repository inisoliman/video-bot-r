#!/usr/bin/env python3
"""
تدقيق شامل لقاعدة البيانات
يفحص سلامة البيانات ويبلغ عن المشاكل
"""

import sys
import os
import logging
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Any
from collections import defaultdict

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

class DatabaseAuditor:
    """
    مدقق قاعدة البيانات
    """

    def __init__(self):
        self.config = Config()
        self.pool = get_pool()
        self.issues = []

    def log_issue(self, severity: str, category: str, message: str, details: Any = None):
        """
        تسجيل مشكلة في التدقيق

        Args:
            severity: خطورة المشكلة (error, warning, info)
            category: فئة المشكلة
            message: رسالة المشكلة
            details: تفاصيل إضافية
        """
        issue = {
            'severity': severity,
            'category': category,
            'message': message,
            'details': details,
            'timestamp': datetime.now()
        }
        self.issues.append(issue)
        logger.log(
            getattr(logging, severity.upper()) if hasattr(logging, severity.upper()) else logging.INFO,
            f"[{category}] {message}"
        )

    def audit_table_counts(self) -> Dict[str, int]:
        """
        تدقيق عدد السجلات في كل جدول

        Returns:
            عدد السجلات لكل جدول
        """
        tables = ['videos', 'users', 'categories', 'user_history', 'comments',
                 'favorites', 'ratings', 'bot_settings']

        counts = {}
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    for table in tables:
                        cursor.execute(f"SELECT COUNT(*) FROM {table}")
                        count = cursor.fetchone()[0]
                        counts[table] = count

                        if count == 0 and table in ['videos', 'users']:
                            self.log_issue('warning', 'empty_tables',
                                         f"Table '{table}' is empty", {'table': table})

        except Exception as e:
            self.log_issue('error', 'audit_error',
                         f"Error counting records in tables: {e}")

        return counts

    def audit_data_integrity(self) -> Dict[str, Any]:
        """
        تدقيق سلامة البيانات والعلاقات

        Returns:
            نتائج تدقيق السلامة
        """
        integrity_issues = {
            'orphaned_records': [],
            'missing_references': [],
            'invalid_data': []
        }

        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # فيديوهات بدون فئة صحيحة
                    cursor.execute("""
                        SELECT v.id, v.title, v.category_id
                        FROM videos v
                        LEFT JOIN categories c ON v.category_id = c.id
                        WHERE v.category_id IS NOT NULL AND c.id IS NULL
                    """)
                    orphaned_videos = cursor.fetchall()
                    if orphaned_videos:
                        integrity_issues['orphaned_records'].extend([{
                            'table': 'videos',
                            'id': row[0],
                            'title': row[1],
                            'invalid_category_id': row[2]
                        } for row in orphaned_videos])
                        self.log_issue('error', 'data_integrity',
                                     f"Found {len(orphaned_videos)} videos with invalid category references")

                    # سجل مشاهدة لفيديوهات غير موجودة
                    cursor.execute("""
                        SELECT uh.id, uh.video_id, uh.user_id
                        FROM user_history uh
                        LEFT JOIN videos v ON uh.video_id = v.id
                        WHERE v.id IS NULL
                    """)
                    orphaned_history = cursor.fetchall()
                    if orphaned_history:
                        integrity_issues['orphaned_records'].extend([{
                            'table': 'user_history',
                            'id': row[0],
                            'invalid_video_id': row[1],
                            'user_id': row[2]
                        } for row in orphaned_history])
                        self.log_issue('error', 'data_integrity',
                                     f"Found {len(orphaned_history)} history records for non-existent videos")

                    # تعليقات لفيديوهات غير موجودة
                    cursor.execute("""
                        SELECT c.id, c.video_id, c.user_id
                        FROM comments c
                        LEFT JOIN videos v ON c.video_id = v.id
                        WHERE v.id IS NULL
                    """)
                    orphaned_comments = cursor.fetchall()
                    if orphaned_comments:
                        integrity_issues['orphaned_records'].extend([{
                            'table': 'comments',
                            'id': row[0],
                            'invalid_video_id': row[1],
                            'user_id': row[2]
                        } for row in orphaned_comments])
                        self.log_issue('error', 'data_integrity',
                                     f"Found {len(orphaned_comments)} comments for non-existent videos")

                    # مفضلات لفيديوهات غير موجودة
                    cursor.execute("""
                        SELECT f.id, f.video_id, f.user_id
                        FROM favorites f
                        LEFT JOIN videos v ON f.video_id = v.id
                        WHERE v.id IS NULL
                    """)
                    orphaned_favorites = cursor.fetchall()
                    if orphaned_favorites:
                        integrity_issues['orphaned_records'].extend([{
                            'table': 'favorites',
                            'id': row[0],
                            'invalid_video_id': row[1],
                            'user_id': row[2]
                        } for row in orphaned_favorites])
                        self.log_issue('error', 'data_integrity',
                                     f"Found {len(orphaned_favorites)} favorites for non-existent videos")

                    # تقييمات لفيديوهات غير موجودة
                    cursor.execute("""
                        SELECT r.id, r.video_id, r.user_id
                        FROM ratings r
                        LEFT JOIN videos v ON r.video_id = v.id
                        WHERE v.id IS NULL
                    """)
                    orphaned_ratings = cursor.fetchall()
                    if orphaned_ratings:
                        integrity_issues['orphaned_records'].extend([{
                            'table': 'ratings',
                            'id': row[0],
                            'invalid_video_id': row[1],
                            'user_id': row[2]
                        } for row in orphaned_ratings])
                        self.log_issue('error', 'data_integrity',
                                     f"Found {len(orphaned_ratings)} ratings for non-existent videos")

                    # فيديوهات بدون file_id أو message_id
                    cursor.execute("""
                        SELECT COUNT(*) FROM videos
                        WHERE (file_id IS NULL OR file_id = '') AND message_id IS NULL
                    """)
                    videos_without_ids = cursor.fetchone()[0]
                    if videos_without_ids > 0:
                        integrity_issues['invalid_data'].append({
                            'issue': 'videos_without_ids',
                            'count': videos_without_ids,
                            'description': 'Videos without both file_id and message_id'
                        })
                        self.log_issue('warning', 'data_integrity',
                                     f"Found {videos_without_ids} videos without file_id or message_id")

        except Exception as e:
            self.log_issue('error', 'audit_error',
                         f"Error during data integrity audit: {e}")

        return integrity_issues

    def audit_performance_metrics(self) -> Dict[str, Any]:
        """
        تدقيق مقاييس الأداء

        Returns:
            مقاييس الأداء
        """
        metrics = {}

        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # متوسط حجم الفيديوهات لكل فئة
                    cursor.execute("""
                        SELECT c.name, COUNT(v.id) as video_count, AVG(LENGTH(v.title)) as avg_title_length
                        FROM categories c
                        LEFT JOIN videos v ON c.id = v.category_id
                        GROUP BY c.id, c.name
                        ORDER BY video_count DESC
                    """)
                    category_stats = cursor.fetchall()
                    metrics['category_stats'] = [{
                        'category': row[0],
                        'video_count': row[1],
                        'avg_title_length': round(row[2] or 0, 2)
                    } for row in category_stats]

                    # نشاط المستخدمين
                    cursor.execute("""
                        SELECT
                            COUNT(DISTINCT uh.user_id) as active_users,
                            COUNT(uh.id) as total_watches,
                            AVG(uh.watch_count) as avg_watch_count
                        FROM user_history uh
                        WHERE uh.last_watched >= CURRENT_DATE - INTERVAL '30 days'
                    """)
                    activity_stats = cursor.fetchone()
                    metrics['user_activity'] = {
                        'active_users_30d': activity_stats[0],
                        'total_watches_30d': activity_stats[1],
                        'avg_watch_count': round(activity_stats[2] or 0, 2)
                    }

                    # إحصائيات التقييمات
                    cursor.execute("""
                        SELECT
                            COUNT(*) as total_ratings,
                            AVG(rating) as avg_rating,
                            MIN(rating) as min_rating,
                            MAX(rating) as max_rating
                        FROM ratings
                    """)
                    rating_stats = cursor.fetchone()
                    metrics['rating_stats'] = {
                        'total_ratings': rating_stats[0],
                        'avg_rating': round(rating_stats[1] or 0, 2),
                        'min_rating': rating_stats[2],
                        'max_rating': rating_stats[3]
                    }

        except Exception as e:
            self.log_issue('error', 'audit_error',
                         f"Error during performance metrics audit: {e}")

        return metrics

    def audit_database_health(self) -> Dict[str, Any]:
        """
        تدقيق صحة قاعدة البيانات

        Returns:
            حالة قاعدة البيانات
        """
        health = {
            'connection_status': 'unknown',
            'table_status': {},
            'index_status': {}
        }

        try:
            with self.pool.getconn() as conn:
                health['connection_status'] = 'connected'

                with conn.cursor() as cursor:
                    # فحص الجداول
                    cursor.execute("""
                        SELECT tablename
                        FROM pg_tables
                        WHERE schemaname = 'public'
                    """)
                    tables = [row[0] for row in cursor.fetchall()]

                    expected_tables = ['videos', 'users', 'categories', 'user_history',
                                     'comments', 'favorites', 'ratings', 'bot_settings']

                    for table in expected_tables:
                        if table in tables:
                            health['table_status'][table] = 'exists'
                        else:
                            health['table_status'][table] = 'missing'
                            self.log_issue('error', 'database_health',
                                         f"Required table '{table}' is missing")

                    # فحص الفهارس
                    cursor.execute("""
                        SELECT indexname, tablename
                        FROM pg_indexes
                        WHERE schemaname = 'public'
                    """)
                    indexes = cursor.fetchall()
                    index_dict = {row[1]: row[0] for row in indexes}

                    # فهارس مهمة متوقعة
                    expected_indexes = {
                        'videos': ['videos_pkey', 'idx_videos_category_id', 'idx_videos_channel'],
                        'user_history': ['user_history_pkey', 'idx_user_history_user_id'],
                        'users': ['users_pkey'],
                        'categories': ['categories_pkey']
                    }

                    for table, expected_idxs in expected_indexes.items():
                        if table in health['table_status'] and health['table_status'][table] == 'exists':
                            table_indexes = [idx for tbl, idx in index_dict.items() if tbl == table]
                            health['index_status'][table] = {
                                'existing': table_indexes,
                                'missing': [idx for idx in expected_idxs if idx not in table_indexes]
                            }

                            if health['index_status'][table]['missing']:
                                self.log_issue('warning', 'database_health',
                                             f"Missing indexes on table '{table}': {health['index_status'][table]['missing']}")

        except Exception as e:
            health['connection_status'] = 'error'
            self.log_issue('error', 'database_health',
                         f"Database health check failed: {e}")

        return health

    def generate_report(self) -> Dict[str, Any]:
        """
        إنشاء تقرير التدقيق الكامل

        Returns:
            التقرير الكامل
        """
        logger.info("Starting database audit...")

        report = {
            'timestamp': datetime.now(),
            'table_counts': self.audit_table_counts(),
            'data_integrity': self.audit_data_integrity(),
            'performance_metrics': self.audit_performance_metrics(),
            'database_health': self.audit_database_health(),
            'issues': self.issues,
            'summary': {}
        }

        # إنشاء ملخص
        severity_counts = defaultdict(int)
        category_counts = defaultdict(int)

        for issue in self.issues:
            severity_counts[issue['severity']] += 1
            category_counts[issue['category']] += 1

        report['summary'] = {
            'total_issues': len(self.issues),
            'severity_breakdown': dict(severity_counts),
            'category_breakdown': dict(category_counts),
            'audit_status': 'completed'
        }

        logger.info(f"Audit completed with {len(self.issues)} issues found")
        return report

    def print_report(self, report: Dict[str, Any]):
        """
        طباعة تقرير التدقيق

        Args:
            report: تقرير التدقيق
        """
        print("=== تقرير تدقيق قاعدة البيانات ===")
        print(f"تاريخ التدقيق: {report['timestamp']}")
        print()

        # عدد السجلات
        print("عدد السجلات في كل جدول:")
        for table, count in report['table_counts'].items():
            print(f"  {table}: {count}")
        print()

        # ملخص المشاكل
        summary = report['summary']
        print(f"إجمالي المشاكل: {summary['total_issues']}")
        if summary['severity_breakdown']:
            print("توزيع الخطورة:")
            for severity, count in summary['severity_breakdown'].items():
                print(f"  {severity}: {count}")
        print()

        # المشاكل التفصيلية
        if self.issues:
            print("المشاكل المكتشفة:")
            for issue in self.issues[:10]:  # أول 10 مشاكل
                print(f"  [{issue['severity'].upper()}] {issue['category']}: {issue['message']}")
            if len(self.issues) > 10:
                print(f"  ... و {len(self.issues) - 10} مشاكل أخرى")
        else:
            print("لم يتم العثور على مشاكل!")

        # مقاييس الأداء
        metrics = report['performance_metrics']
        if metrics.get('user_activity'):
            activity = metrics['user_activity']
            print("\nنشاط المستخدمين (آخر 30 يوم):")
            print(f"  المستخدمون النشطون: {activity['active_users_30d']}")
            print(f"  إجمالي المشاهدات: {activity['total_watches_30d']}")
            print(f"  متوسط المشاهدات: {activity['avg_watch_count']}")

def main():
    parser = argparse.ArgumentParser(description='Database Audit Tool')
    parser.add_argument('--output', help='Save report to JSON file')
    parser.add_argument('--quiet', action='store_true', help='Suppress detailed output')

    args = parser.parse_args()

    auditor = DatabaseAuditor()
    report = auditor.generate_report()

    if not args.quiet:
        auditor.print_report(report)

    if args.output:
        import json
        with open(args.output, 'w', encoding='utf-8') as f:
            # تحويل datetime إلى string للـ JSON
            json_report = json.dumps(report, default=str, ensure_ascii=False, indent=2)
            f.write(json_report)
        print(f"\nتم حفظ التقرير في: {args.output}")

    # إرجاع كود خطأ إذا كانت هناك أخطاء خطيرة
    error_count = report['summary']['severity_breakdown'].get('error', 0)
    if error_count > 0:
        print(f"\nتحذير: تم العثور على {error_count} أخطاء خطيرة!")
        sys.exit(1)

if __name__ == '__main__':
    main()