#!/usr/bin/env python3
"""
محسن أداء قاعدة البيانات
يقوم بتحليل وتحسين الأداء من خلال الفهارس والإحصائيات
"""

import sys
import os
import logging
import argparse
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any

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

class DatabaseOptimizer:
    """
    محسن أداء قاعدة البيانات
    """

    def __init__(self):
        self.config = Config()
        self.pool = get_pool()

    def analyze_query_performance(self) -> Dict[str, Any]:
        """
        تحليل أداء الاستعلامات الشائعة

        Returns:
            تقرير أداء الاستعلامات
        """
        performance_report = {
            'slow_queries': [],
            'table_scans': [],
            'missing_indexes': [],
            'recommendations': []
        }

        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # تحليل الجداول الكبيرة
                    cursor.execute("""
                        SELECT schemaname, tablename, n_tup_ins, n_tup_upd, n_tup_del
                        FROM pg_stat_user_tables
                        ORDER BY n_tup_ins + n_tup_upd + n_tup_del DESC
                        LIMIT 10
                    """)
                    active_tables = cursor.fetchall()

                    for schema, table, inserts, updates, deletes in active_tables:
                        total_operations = inserts + updates + deletes
                        if total_operations > 1000:  # جداول نشطة
                            performance_report['recommendations'].append({
                                'type': 'active_table',
                                'table': table,
                                'operations': total_operations,
                                'suggestion': f"Consider optimizing queries for high-activity table: {table}"
                            })

                    # تحليل الفهارس غير المستخدمة
                    cursor.execute("""
                        SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read, idx_tup_fetch
                        FROM pg_stat_user_indexes
                        WHERE idx_scan = 0 AND idx_tup_read = 0 AND idx_tup_fetch = 0
                        ORDER BY pg_relation_size(indexrelid) DESC
                    """)
                    unused_indexes = cursor.fetchall()

                    for schema, table, index, scans, reads, fetches in unused_indexes:
                        index_size = self.get_index_size(index)
                        if index_size > 1024 * 1024:  # أكبر من 1MB
                            performance_report['recommendations'].append({
                                'type': 'unused_index',
                                'table': table,
                                'index': index,
                                'size_mb': round(index_size / (1024 * 1024), 2),
                                'suggestion': f"Consider dropping unused index: {index} ({table})"
                            })

        except Exception as e:
            logger.error(f"Error analyzing query performance: {e}")

        return performance_report

    def get_index_size(self, index_name: str) -> int:
        """
        جلب حجم الindex

        Args:
            index_name: اسم الindex

        Returns:
            حجم الindex بالبايت
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT pg_relation_size(indexrelid)
                        FROM pg_stat_user_indexes
                        WHERE indexname = %s
                    """, (index_name,))
                    result = cursor.fetchone()
                    return result[0] if result else 0
        except Exception:
            return 0

    def optimize_table_statistics(self):
        """
        تحسين إحصائيات الجداول
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # تحديث إحصائيات جميع الجداول
                    cursor.execute("ANALYZE")
                    conn.commit()

                    logger.info("Updated table statistics for all tables")

        except Exception as e:
            logger.error(f"Error updating table statistics: {e}")

    def create_recommended_indexes(self) -> List[str]:
        """
        إنشاء الفهارس الموصى بها

        Returns:
            قائمة الفهارس المُنشأة
        """
        recommended_indexes = [
            # فهارس البحث الشائعة
            ("idx_videos_title_search", "videos", "CREATE INDEX idx_videos_title_search ON videos USING gin (to_tsvector('arabic', title))"),
            ("idx_videos_channel_search", "videos", "CREATE INDEX idx_videos_channel_search ON videos (channel_username varchar_pattern_ops)"),

            # فهارس الأداء للاستعلامات المركبة
            ("idx_user_history_composite", "user_history", "CREATE INDEX idx_user_history_composite ON user_history (user_id, last_watched DESC)"),
            ("idx_comments_composite", "comments", "CREATE INDEX idx_comments_composite ON comments (video_id, created_at DESC)"),

            # فهارس فريدة لمنع التكرار
            ("idx_favorites_unique", "favorites", "CREATE UNIQUE INDEX idx_favorites_unique ON favorites (user_id, video_id)"),
            ("idx_ratings_unique", "ratings", "CREATE UNIQUE INDEX idx_ratings_unique ON ratings (user_id, video_id)"),

            # فهارس للأعمدة المستخدمة في الترتيب
            ("idx_videos_popular", "videos", "CREATE INDEX idx_videos_popular ON videos (view_count DESC, created_at DESC)"),
            ("idx_videos_recent", "videos", "CREATE INDEX idx_videos_recent ON videos (created_at DESC)"),
        ]

        created_indexes = []

        for index_name, table, create_sql in recommended_indexes:
            try:
                with self.pool.getconn() as conn:
                    with conn.cursor() as cursor:
                        # فحص إذا كان الindex موجود
                        cursor.execute("""
                            SELECT 1 FROM pg_indexes
                            WHERE indexname = %s
                        """, (index_name,))

                        if not cursor.fetchone():
                            cursor.execute(create_sql)
                            conn.commit()
                            created_indexes.append(index_name)
                            logger.info(f"Created recommended index: {index_name}")
                        else:
                            logger.debug(f"Index {index_name} already exists")

            except Exception as e:
                logger.error(f"Error creating index {index_name}: {e}")

        return created_indexes

    def vacuum_and_reindex(self, full_vacuum: bool = False):
        """
        تنظيف وإعادة بناء الفهارس

        Args:
            full_vacuum: استخدام VACUUM FULL
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    tables = ['videos', 'users', 'categories', 'user_history',
                             'comments', 'favorites', 'ratings', 'bot_settings']

                    for table in tables:
                        try:
                            if full_vacuum:
                                logger.info(f"Running VACUUM FULL on {table}")
                                cursor.execute(f"VACUUM FULL {table}")
                            else:
                                logger.info(f"Running VACUUM on {table}")
                                cursor.execute(f"VACUUM {table}")

                            # إعادة بناء الفهارس
                            logger.info(f"Reindexing {table}")
                            cursor.execute(f"REINDEX TABLE {table}")

                        except Exception as e:
                            logger.error(f"Error optimizing table {table}: {e}")

                    conn.commit()
                    logger.info("Completed vacuum and reindex operations")

        except Exception as e:
            logger.error(f"Error during vacuum/reindex: {e}")

    def optimize_connection_pool(self) -> Dict[str, Any]:
        """
        تحسين إعدادات connection pool

        Returns:
            توصيات التحسين
        """
        recommendations = {
            'pool_size': 'Consider increasing pool size for high traffic',
            'max_connections': 'Monitor max connections vs active connections',
            'connection_timeout': 'Adjust connection timeout based on usage patterns'
        }

        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # فحص الاتصالات النشطة
                    cursor.execute("""
                        SELECT count(*) as active_connections
                        FROM pg_stat_activity
                        WHERE state = 'active'
                    """)
                    active = cursor.fetchone()[0]

                    # فحص الحد الأقصى للاتصالات
                    cursor.execute("SHOW max_connections")
                    max_conn = int(cursor.fetchone()[0])

                    if active > max_conn * 0.8:  # أكثر من 80%
                        recommendations['urgent'] = f"High connection usage: {active}/{max_conn}"

        except Exception as e:
            logger.error(f"Error analyzing connection pool: {e}")

        return recommendations

    def generate_optimization_report(self) -> Dict[str, Any]:
        """
        إنشاء تقرير التحسين الكامل

        Returns:
            التقرير الكامل
        """
        logger.info("Starting database optimization analysis...")

        report = {
            'timestamp': datetime.now(),
            'performance_analysis': self.analyze_query_performance(),
            'connection_pool': self.optimize_connection_pool(),
            'optimizations_applied': [],
            'recommendations': []
        }

        # تطبيق التحسينات
        try:
            # تحديث الإحصائيات
            logger.info("Updating table statistics...")
            self.optimize_table_statistics()
            report['optimizations_applied'].append('table_statistics_updated')

            # إنشاء الفهارس الموصى بها
            logger.info("Creating recommended indexes...")
            created_indexes = self.create_recommended_indexes()
            if created_indexes:
                report['optimizations_applied'].append({
                    'type': 'indexes_created',
                    'indexes': created_indexes
                })

            # تنظيف الجداول
            logger.info("Running vacuum on tables...")
            self.vacuum_and_reindex()
            report['optimizations_applied'].append('vacuum_completed')

        except Exception as e:
            logger.error(f"Error during optimization: {e}")
            report['optimizations_applied'].append({'error': str(e)})

        # تجميع التوصيات
        all_recommendations = []

        # من تحليل الأداء
        perf_rec = report['performance_analysis'].get('recommendations', [])
        all_recommendations.extend(perf_rec)

        # من connection pool
        pool_rec = report['connection_pool']
        for key, value in pool_rec.items():
            if key != 'urgent':
                all_recommendations.append({'type': key, 'suggestion': value})

        if 'urgent' in pool_rec:
            all_recommendations.append({
                'type': 'urgent',
                'suggestion': pool_rec['urgent'],
                'priority': 'high'
            })

        report['recommendations'] = all_recommendations

        logger.info(f"Optimization completed. {len(all_recommendations)} recommendations generated.")
        return report

    def print_report(self, report: Dict[str, Any]):
        """
        طباعة تقرير التحسين

        Args:
            report: تقرير التحسين
        """
        print("=== تقرير تحسين قاعدة البيانات ===")
        print(f"تاريخ التحسين: {report['timestamp']}")
        print()

        # التحسينات المطبقة
        optimizations = report['optimizations_applied']
        print(f"التحسينات المطبقة ({len(optimizations)}):")
        for opt in optimizations:
            if isinstance(opt, str):
                print(f"  ✓ {opt.replace('_', ' ').title()}")
            elif isinstance(opt, dict) and 'indexes' in opt:
                print(f"  ✓ Created {len(opt['indexes'])} indexes: {', '.join(opt['indexes'])}")
            elif isinstance(opt, dict) and 'error' in opt:
                print(f"  ✗ Error: {opt['error']}")

        print()

        # التوصيات
        recommendations = report['recommendations']
        if recommendations:
            print(f"التوصيات ({len(recommendations)}):")
            urgent = [r for r in recommendations if r.get('priority') == 'high']
            normal = [r for r in recommendations if r.get('priority') != 'high']

            for rec in urgent:
                print(f"  🔥 {rec.get('suggestion', rec)}")

            for rec in normal:
                print(f"  💡 {rec.get('suggestion', rec)}")
        else:
            print("لا توجد توصيات إضافية!")

def main():
    parser = argparse.ArgumentParser(description='Database Optimizer Tool')
    parser.add_argument('--full-vacuum', action='store_true', help='Use VACUUM FULL (more aggressive but locks tables)')
    parser.add_argument('--output', help='Save optimization report to JSON file')
    parser.add_argument('--quiet', action='store_true', help='Suppress detailed output')

    args = parser.parse_args()

    optimizer = DatabaseOptimizer()

    if args.full_vacuum:
        logger.info("Running full vacuum as requested")
        optimizer.vacuum_and_reindex(full_vacuum=True)

    report = optimizer.generate_optimization_report()

    if not args.quiet:
        optimizer.print_report(report)

    if args.output:
        import json
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(report, f, default=str, ensure_ascii=False, indent=2)
        print(f"\nتم حفظ التقرير في: {args.output}")

if __name__ == '__main__':
    main()