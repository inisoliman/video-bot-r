#!/usr/bin/env python3
"""
جلب وتحليل سجل المشاهدة للمستخدمين
يعرض إحصائيات المشاهدة وأنماط الاستخدام
"""

import sys
import os
import logging
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Optional
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

class HistoryFetcher:
    """
    مجلب بيانات سجل المشاهدة
    """

    def __init__(self):
        self.config = Config()
        self.pool = get_pool()

    def get_user_history(self, user_id: Optional[int] = None, days: Optional[int] = None,
                        limit: Optional[int] = None) -> List[Dict]:
        """
        جلب سجل مشاهدة مستخدم محدد أو جميع المستخدمين

        Args:
            user_id: معرف المستخدم (None لجميع المستخدمين)
            days: عدد الأيام الماضية
            limit: حد أقصى لعدد السجلات

        Returns:
            قائمة بسجل المشاهدة
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    query = """
                        SELECT uh.user_id, uh.video_id, uh.last_watched, uh.watch_count,
                               v.title, v.channel_username, v.category_id, c.name as category_name
                        FROM user_history uh
                        JOIN videos v ON uh.video_id = v.id
                        LEFT JOIN categories c ON v.category_id = c.id
                    """

                    conditions = []
                    params = []

                    if user_id:
                        conditions.append("uh.user_id = %s")
                        params.append(user_id)

                    if days:
                        cutoff_date = datetime.now() - timedelta(days=days)
                        conditions.append("uh.last_watched >= %s")
                        params.append(cutoff_date)

                    if conditions:
                        query += " WHERE " + " AND ".join(conditions)

                    query += " ORDER BY uh.last_watched DESC"

                    if limit:
                        query += " LIMIT %s"
                        params.append(limit)

                    cursor.execute(query, params)
                    records = cursor.fetchall()

                    return [{
                        'user_id': row[0],
                        'video_id': row[1],
                        'last_watched': row[2],
                        'watch_count': row[3],
                        'video_title': row[4],
                        'channel_username': row[5],
                        'category_id': row[6],
                        'category_name': row[7]
                    } for row in records]

        except Exception as e:
            logger.error(f"Error getting user history: {e}")
            return []

    def get_user_stats(self, user_id: Optional[int] = None, days: Optional[int] = None) -> Dict:
        """
        جلب إحصائيات المستخدم

        Args:
            user_id: معرف المستخدم
            days: عدد الأيام الماضية

        Returns:
            إحصائيات المستخدم
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    conditions = []
                    params = []

                    if user_id:
                        conditions.append("user_id = %s")
                        params.append(user_id)

                    if days:
                        cutoff_date = datetime.now() - timedelta(days=days)
                        conditions.append("last_watched >= %s")
                        params.append(cutoff_date)

                    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

                    # إجمالي المشاهدات
                    cursor.execute(f"SELECT COUNT(*) FROM user_history{where_clause}", params)
                    total_watches = cursor.fetchone()[0]

                    # عدد الفيديوهات المميزة
                    cursor.execute(f"SELECT COUNT(DISTINCT video_id) FROM user_history{where_clause}", params)
                    unique_videos = cursor.fetchone()[0]

                    # عدد المستخدمين النشطين
                    if not user_id:
                        cursor.execute(f"SELECT COUNT(DISTINCT user_id) FROM user_history{where_clause}", params)
                        active_users = cursor.fetchone()[0]
                    else:
                        active_users = 1

                    # متوسط المشاهدات لكل فيديو
                    cursor.execute(f"""
                        SELECT AVG(watch_count) FROM user_history{where_clause}
                    """, params)
                    avg_watches_per_video = cursor.fetchone()[0] or 0

                    # الفئات الأكثر مشاهدة
                    cursor.execute(f"""
                        SELECT c.name, COUNT(*) as watch_count
                        FROM user_history uh
                        JOIN videos v ON uh.video_id = v.id
                        LEFT JOIN categories c ON v.category_id = c.id
                        {where_clause}
                        GROUP BY c.id, c.name
                        ORDER BY watch_count DESC
                        LIMIT 10
                    """, params)
                    top_categories = cursor.fetchall()

                    # القنوات الأكثر مشاهدة
                    cursor.execute(f"""
                        SELECT v.channel_username, COUNT(*) as watch_count
                        FROM user_history uh
                        JOIN videos v ON uh.video_id = v.id
                        {where_clause}
                        GROUP BY v.channel_username
                        ORDER BY watch_count DESC
                        LIMIT 10
                    """, params)
                    top_channels = cursor.fetchall()

                    return {
                        'total_watches': total_watches,
                        'unique_videos': unique_videos,
                        'active_users': active_users,
                        'avg_watches_per_video': round(avg_watches_per_video, 2),
                        'top_categories': [{'name': row[0] or 'Uncategorized', 'count': row[1]} for row in top_categories],
                        'top_channels': [{'username': row[0], 'count': row[1]} for row in top_channels]
                    }

        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return {}

    def get_popular_videos(self, days: Optional[int] = None, limit: int = 20) -> List[Dict]:
        """
        جلب الفيديوهات الأكثر مشاهدة

        Args:
            days: عدد الأيام الماضية
            limit: حد أقصى للنتائج

        Returns:
            قائمة بالفيديوهات الشائعة
        """
        try:
            with self.pool.getconn() as conn:
                with conn.cursor() as cursor:
                    query = """
                        SELECT v.id, v.title, v.channel_username, COUNT(*) as watch_count,
                               SUM(uh.watch_count) as total_views, MAX(uh.last_watched) as last_watched
                        FROM videos v
                        JOIN user_history uh ON v.id = uh.video_id
                    """

                    params = []
                    if days:
                        cutoff_date = datetime.now() - timedelta(days=days)
                        query += " WHERE uh.last_watched >= %s"
                        params.append(cutoff_date)

                    query += """
                        GROUP BY v.id, v.title, v.channel_username
                        ORDER BY watch_count DESC, total_views DESC
                        LIMIT %s
                    """
                    params.append(limit)

                    cursor.execute(query, params)
                    videos = cursor.fetchall()

                    return [{
                        'id': row[0],
                        'title': row[1],
                        'channel_username': row[2],
                        'watch_count': row[3],
                        'total_views': row[4],
                        'last_watched': row[5]
                    } for row in videos]

        except Exception as e:
            logger.error(f"Error getting popular videos: {e}")
            return []

    def export_history(self, filename: str, user_id: Optional[int] = None, days: Optional[int] = None) -> bool:
        """
        تصدير سجل المشاهدة إلى ملف JSON

        Args:
            filename: اسم الملف
            user_id: معرف المستخدم
            days: عدد الأيام الماضية

        Returns:
            نجاح العملية
        """
        try:
            history = self.get_user_history(user_id, days)
            stats = self.get_user_stats(user_id, days)

            export_data = {
                'export_timestamp': datetime.now().isoformat(),
                'user_id': user_id,
                'days_filter': days,
                'stats': stats,
                'history': history
            }

            import json
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2, default=str)

            logger.info(f"Exported {len(history)} history records to {filename}")
            return True

        except Exception as e:
            logger.error(f"Error exporting history: {e}")
            return False

def print_stats(stats: Dict):
    """طباعة الإحصائيات"""
    print("\n=== إحصائيات المشاهدة ===")
    print(f"إجمالي المشاهدات: {stats.get('total_watches', 0)}")
    print(f"الفيديوهات المميزة: {stats.get('unique_videos', 0)}")
    print(f"المستخدمون النشطون: {stats.get('active_users', 0)}")
    print(f"متوسط المشاهدات لكل فيديو: {stats.get('avg_watches_per_video', 0)}")

    if stats.get('top_categories'):
        print("\nالفئات الأكثر مشاهدة:")
        for cat in stats['top_categories'][:5]:
            print(f"  {cat['name']}: {cat['count']}")

    if stats.get('top_channels'):
        print("\nالقنوات الأكثر مشاهدة:")
        for ch in stats['top_channels'][:5]:
            print(f"  @{ch['username']}: {ch['count']}")

def print_history(history: List[Dict], limit: int = 10):
    """طباعة سجل المشاهدة"""
    print(f"\n=== سجل المشاهدة (آخر {min(limit, len(history))} سجل) ===")
    for record in history[:limit]:
        print(f"[{record['last_watched']}] {record['video_title'][:50]}... "
              f"(مشاهدات: {record['watch_count']}) - @{record['channel_username']}")

def main():
    parser = argparse.ArgumentParser(description='History Fetch Tool')
    parser.add_argument('--user-id', type=int, help='Filter by user ID')
    parser.add_argument('--days', type=int, help='Filter by last N days')
    parser.add_argument('--limit', type=int, default=20, help='Limit number of results')
    parser.add_argument('--export', help='Export history to JSON file')
    parser.add_argument('--popular', action='store_true', help='Show popular videos instead of user history')
    parser.add_argument('--stats-only', action='store_true', help='Show only statistics')

    args = parser.parse_args()

    fetcher = HistoryFetcher()

    try:
        if args.popular:
            videos = fetcher.get_popular_videos(args.days, args.limit)
            print("
=== الفيديوهات الأكثر مشاهدة ===")
            for video in videos:
                print(f"{video['watch_count']} مشاهدات - {video['title'][:50]}... "
                      f"(إجمالي المشاهدات: {video['total_views']}) - @{video['channel_username']}")

        elif args.stats_only:
            stats = fetcher.get_user_stats(args.user_id, args.days)
            print_stats(stats)

        else:
            history = fetcher.get_user_history(args.user_id, args.days, args.limit)
            stats = fetcher.get_user_stats(args.user_id, args.days)

            print_stats(stats)
            print_history(history, args.limit)

        if args.export:
            if fetcher.export_history(args.export, args.user_id, args.days):
                print(f"\nتم التصدير إلى: {args.export}")
            else:
                print("\nفشل في التصدير")

    except Exception as e:
        logger.error(f"Operation failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()