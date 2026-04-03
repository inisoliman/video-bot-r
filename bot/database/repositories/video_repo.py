#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/database/repositories/video_repo.py
# الوصف: عمليات CRUD للفيديوهات والتقييمات والمفضلة والسجل
# ==============================================================================

import json
import time
import logging
from typing import Optional, List, Dict, Tuple, Any

from bot.database.connection import execute_query
from bot.core.config import settings

logger = logging.getLogger(__name__)

# --- Search Cache ---
_search_cache: Dict[str, Tuple[Any, float]] = {}
_CACHE_TTL = settings.cache.search_cache_time
_MAX_CACHE = settings.cache.max_cache_entries
VIDEOS_PER_PAGE = settings.pagination.videos_per_page


def _cache_key(query, page, category_id, quality, status):
    return f"{query}:{page}:{category_id}:{quality}:{status}"


def _get_cached(key):
    if key in _search_cache:
        result, ts = _search_cache[key]
        if time.time() - ts < _CACHE_TTL:
            return result
        del _search_cache[key]
    return None


def _set_cached(key, result):
    if len(_search_cache) > _MAX_CACHE:
        oldest = sorted(_search_cache, key=lambda k: _search_cache[k][1])[:_MAX_CACHE // 2]
        for k in oldest:
            del _search_cache[k]
    _search_cache[key] = (result, time.time())


def clear_search_cache():
    """مسح ذاكرة البحث المؤقتة"""
    global _search_cache
    _search_cache = {}
    logger.info("Search cache cleared")


class VideoRepository:
    """Repository لعمليات الفيديو"""

    # --- CRUD ---
    @staticmethod
    def add(message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id=None):
        """إضافة أو تحديث فيديو"""
        metadata_json = json.dumps(metadata)
        query = """
            INSERT INTO video_archive (message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE SET
                caption = EXCLUDED.caption, file_name = EXCLUDED.file_name,
                file_id = EXCLUDED.file_id, metadata = EXCLUDED.metadata,
                grouping_key = EXCLUDED.grouping_key, category_id = EXCLUDED.category_id
            RETURNING id
        """
        result = execute_query(query, (message_id, caption, chat_id, file_name, file_id, metadata_json, grouping_key, category_id), fetch="one", commit=True)
        return result['id'] if result else None

    @staticmethod
    def get_by_id(video_id: int):
        return execute_query("SELECT * FROM video_archive WHERE id = %s", (video_id,), fetch="one")

    @staticmethod
    def get_by_message_id(message_id: int):
        return execute_query("SELECT * FROM video_archive WHERE message_id = %s", (message_id,), fetch="one")

    @staticmethod
    def get_by_category(category_id: int, page: int = 0):
        offset = page * VIDEOS_PER_PAGE
        videos = execute_query(
            "SELECT * FROM video_archive WHERE category_id = %s ORDER BY id DESC LIMIT %s OFFSET %s",
            (category_id, VIDEOS_PER_PAGE, offset), fetch="all"
        )
        total = execute_query(
            "SELECT COUNT(*) as count FROM video_archive WHERE category_id = %s",
            (category_id,), fetch="one"
        )
        return videos, total['count'] if total else 0

    @staticmethod
    def get_random():
        return execute_query("SELECT * FROM video_archive ORDER BY RANDOM() LIMIT 1", fetch="one")

    @staticmethod
    def increment_views(video_id: int):
        return execute_query("UPDATE video_archive SET view_count = view_count + 1 WHERE id = %s", (video_id,), commit=True)

    @staticmethod
    def move_to_category(video_id: int, new_category_id: int):
        return execute_query("UPDATE video_archive SET category_id = %s WHERE id = %s", (new_category_id, video_id), commit=True)

    @staticmethod
    def move_bulk(video_ids: List[int], new_category_id: int) -> int:
        if not video_ids:
            return 0
        result = execute_query(
            "UPDATE video_archive SET category_id = %s WHERE id = ANY(%s) RETURNING id",
            (new_category_id, video_ids), fetch='all', commit=True
        )
        return len(result) if isinstance(result, list) else 0

    @staticmethod
    def delete_by_ids(video_ids: List[int]) -> int:
        if not video_ids:
            return 0
        result = execute_query(
            "DELETE FROM video_archive WHERE id = ANY(%s) RETURNING id",
            (video_ids,), fetch="all", commit=True
        )
        return len(result) if isinstance(result, list) else 0

    @staticmethod
    def move_from_category(old_id: int, new_id: int):
        return execute_query("UPDATE video_archive SET category_id = %s WHERE category_id = %s", (new_id, old_id), commit=True)

    @staticmethod
    def update_thumbnail(video_id: int, thumbnail_file_id: str):
        result = execute_query("UPDATE video_archive SET thumbnail_file_id = %s WHERE id = %s", (thumbnail_file_id, video_id), commit=True)
        return result is not None

    @staticmethod
    def get_without_thumbnail(limit: int = 20):
        return execute_query("""
            SELECT id, file_id, caption, file_name, chat_id, message_id
            FROM video_archive WHERE thumbnail_file_id IS NULL
            ORDER BY upload_date DESC LIMIT %s
        """, (limit,), fetch="all")

    # --- Search ---
    @staticmethod
    def search(query, page=0, category_id=None, quality=None, status=None):
        """بحث في الفيديوهات مع كاش"""
        key = _cache_key(query, page, category_id, quality, status)
        cached = _get_cached(key)
        if cached:
            return cached

        offset = page * VIDEOS_PER_PAGE
        search_term = f"%{query}%"

        where_clauses = ["(caption ILIKE %s OR file_name ILIKE %s)"]
        params = [search_term, search_term]

        if category_id:
            where_clauses.append("category_id = %s")
            params.append(category_id)
        if quality:
            where_clauses.append("metadata->>'quality_resolution' = %s")
            params.append(quality)
        if status:
            where_clauses.append("metadata->>'status' = %s")
            params.append(status)

        where_string = " AND ".join(where_clauses)

        videos = execute_query(
            f"SELECT * FROM video_archive WHERE {where_string} ORDER BY id DESC LIMIT %s OFFSET %s",
            tuple(params + [VIDEOS_PER_PAGE, offset]), fetch="all"
        )
        total = execute_query(
            f"SELECT COUNT(*) as count FROM video_archive WHERE {where_string}",
            tuple(params), fetch="one"
        )

        result = (videos, total['count'] if total else 0)
        _set_cached(key, result)
        return result

    @staticmethod
    def search_for_inline(query: str, offset: int = 0, limit: int = 50):
        """بحث محسّن للـ inline query"""
        if not query or query.strip() == "":
            sql = """
                SELECT v.id, v.file_id, v.caption, v.file_name, v.view_count,
                    v.thumbnail_file_id, v.chat_id, v.message_id, v.content_type,
                    c.name as category_name,
                    COALESCE(AVG(r.rating), 0) as avg_rating, COUNT(r.rating) as rating_count
                FROM video_archive v
                LEFT JOIN categories c ON v.category_id = c.id
                LEFT JOIN video_ratings r ON v.id = r.video_id
                WHERE v.file_id IS NOT NULL AND LENGTH(v.file_id) >= 20
                GROUP BY v.id, v.file_id, v.caption, v.file_name, v.view_count,
                         v.thumbnail_file_id, v.chat_id, v.message_id, v.content_type, c.name
                ORDER BY v.view_count DESC, avg_rating DESC
                LIMIT %s OFFSET %s
            """
            return execute_query(sql, (limit, offset), fetch="all")

        contains = f"%{query}%"
        exact = query
        starts = f"{query}%"
        word = f"% {query} %"

        sql = """
            SELECT v.id, v.file_id, v.caption, v.file_name, v.view_count,
                v.thumbnail_file_id, v.chat_id, v.message_id, v.content_type,
                c.name as category_name,
                COALESCE(AVG(r.rating), 0) as avg_rating, COUNT(r.rating) as rating_count
            FROM video_archive v
            LEFT JOIN categories c ON v.category_id = c.id
            LEFT JOIN video_ratings r ON v.id = r.video_id
            WHERE v.file_id IS NOT NULL AND LENGTH(v.file_id) >= 20
                AND (v.caption ILIKE %s OR v.file_name ILIKE %s OR c.name ILIKE %s)
            GROUP BY v.id, v.file_id, v.caption, v.file_name, v.view_count,
                     v.thumbnail_file_id, v.chat_id, v.message_id, v.content_type, c.name
            ORDER BY
                CASE
                    WHEN v.caption ILIKE %s THEN 0
                    WHEN v.file_name ILIKE %s THEN 0
                    WHEN v.caption ILIKE %s THEN 1
                    WHEN v.file_name ILIKE %s THEN 1
                    WHEN v.caption ILIKE %s THEN 2
                    WHEN v.file_name ILIKE %s THEN 2
                    ELSE 3
                END,
                v.view_count DESC, avg_rating DESC
            LIMIT %s OFFSET %s
        """
        return execute_query(sql, (
            contains, contains, contains,
            exact, exact, starts, starts, word, word,
            limit, offset
        ), fetch="all")

    # --- Popular ---
    @staticmethod
    def get_popular():
        most_viewed = execute_query(
            "SELECT * FROM video_archive ORDER BY view_count DESC, id DESC LIMIT 10", fetch="all"
        )
        highest_rated = execute_query("""
            SELECT v.*, r.avg_rating
            FROM video_archive v
            JOIN (SELECT video_id, AVG(rating) as avg_rating FROM video_ratings GROUP BY video_id) r
            ON v.id = r.video_id
            ORDER BY r.avg_rating DESC, v.view_count DESC LIMIT 10
        """, fetch="all")
        return {"most_viewed": most_viewed, "highest_rated": highest_rated}

    # --- Ratings ---
    @staticmethod
    def add_rating(video_id: int, user_id: int, rating: int):
        return execute_query(
            "INSERT INTO video_ratings (video_id, user_id, rating) VALUES (%s, %s, %s) ON CONFLICT (video_id, user_id) DO UPDATE SET rating = EXCLUDED.rating",
            (video_id, user_id, rating), commit=True
        )

    @staticmethod
    def get_rating_stats(video_id: int):
        return execute_query(
            "SELECT AVG(rating) as avg, COUNT(id) as count FROM video_ratings WHERE video_id = %s",
            (video_id,), fetch="one"
        )

    @staticmethod
    def get_user_rating(video_id: int, user_id: int):
        res = execute_query(
            "SELECT rating FROM video_ratings WHERE video_id = %s AND user_id = %s",
            (video_id, user_id), fetch="one"
        )
        return res['rating'] if res else None

    @staticmethod
    def get_ratings_bulk(video_ids: List[int]) -> Dict:
        if not video_ids:
            return {}
        results = execute_query("""
            SELECT video_id, AVG(rating) as avg_rating, COUNT(*) as count
            FROM video_ratings WHERE video_id = ANY(%s) GROUP BY video_id
        """, (video_ids,), fetch="all")
        if not results:
            return {}
        return {
            row['video_id']: {
                'avg': float(row['avg_rating']) if row['avg_rating'] else 0,
                'count': row['count']
            } for row in results
        }

    # --- Favorites ---
    @staticmethod
    def is_favorite(user_id: int, video_id: int) -> bool:
        res = execute_query(
            "SELECT 1 FROM user_favorites WHERE user_id = %s AND video_id = %s",
            (user_id, video_id), fetch="one"
        )
        return bool(res)

    @staticmethod
    def add_favorite(user_id: int, video_id: int):
        return execute_query(
            "INSERT INTO user_favorites (user_id, video_id) VALUES (%s, %s) ON CONFLICT (user_id, video_id) DO NOTHING",
            (user_id, video_id), commit=True
        )

    @staticmethod
    def remove_favorite(user_id: int, video_id: int):
        return execute_query(
            "DELETE FROM user_favorites WHERE user_id = %s AND video_id = %s",
            (user_id, video_id), commit=True
        )

    @staticmethod
    def get_favorites(user_id: int, page: int = 0):
        offset = page * VIDEOS_PER_PAGE
        videos = execute_query("""
            SELECT v.* FROM video_archive v
            JOIN user_favorites f ON v.id = f.video_id
            WHERE f.user_id = %s ORDER BY f.date_added DESC
            LIMIT %s OFFSET %s
        """, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
        total = execute_query(
            "SELECT COUNT(*) as count FROM user_favorites WHERE user_id = %s",
            (user_id,), fetch="one"
        )
        return videos, total['count'] if total else 0

    # --- History ---
    @staticmethod
    def add_to_history(user_id: int, video_id: int):
        return execute_query("""
            INSERT INTO user_history (user_id, video_id, last_watched)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, video_id) DO UPDATE SET last_watched = CURRENT_TIMESTAMP
        """, (user_id, video_id), commit=True)

    @staticmethod
    def get_history(user_id: int, page: int = 0):
        offset = page * VIDEOS_PER_PAGE
        videos = execute_query("""
            SELECT v.* FROM video_archive v
            JOIN user_history h ON v.id = h.video_id
            WHERE h.user_id = %s ORDER BY h.last_watched DESC
            LIMIT %s OFFSET %s
        """, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
        total = execute_query(
            "SELECT COUNT(*) as count FROM user_history WHERE user_id = %s",
            (user_id,), fetch="one"
        )
        return videos, total['count'] if total else 0

    # --- Stats ---
    @staticmethod
    def get_stats():
        stats = {}
        stats['video_count'] = (execute_query("SELECT COUNT(*) as count FROM video_archive", fetch="one") or {'count': 0})['count']
        stats['total_views'] = (execute_query("SELECT COALESCE(SUM(view_count), 0) as sum FROM video_archive", fetch="one") or {'sum': 0})['sum']
        stats['total_ratings'] = (execute_query("SELECT COUNT(*) as count FROM video_ratings", fetch="one") or {'count': 0})['count']
        return stats
