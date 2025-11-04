# ==============================================================================
# db_manager.py (patched): يستخدم db_pool + فهارس + كاش بسيط
# ==============================================================================

import logging
import json
from psycopg2.extras import DictCursor
import psycopg2
from functools import lru_cache
from time import time

from db_pool import get_db_connection

logger = logging.getLogger(__name__)

VIDEOS_PER_PAGE = 10
CALLBACK_DELIMITER = "::"

# --- تنفيذ فهارس لتحسين الأداء ---
INDEX_QUERIES = [
    # فهارس نصية للبحث ILIKE
    "CREATE INDEX IF NOT EXISTS idx_video_caption ON video_archive USING gin (caption gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_video_filename ON video_archive USING gin (file_name gin_trgm_ops)",
    # فهارس عامة
    "CREATE INDEX IF NOT EXISTS idx_video_category ON video_archive (category_id)",
    "CREATE INDEX IF NOT EXISTS idx_video_views ON video_archive (view_count DESC)",
]


def create_indexes():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as c:
                # تمكين امتداد pg_trgm للفهارس النصية
                c.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                for q in INDEX_QUERIES:
                    c.execute(q)
            conn.commit()
        logger.info("DB indexes ensured")
    except Exception as e:
        logger.warning(f"Index creation skipped/failed: {e}")


# --- أدوات تنفيذ الاستعلام ---

def execute_query(query, params=None, fetch=None, commit=False):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as c:
                c.execute(query, params)
                if fetch == "one":
                    res = c.fetchone()
                elif fetch == "all":
                    res = c.fetchall()
                else:
                    res = None
                if commit:
                    conn.commit()
                return res if fetch else True
    except psycopg2.Error as e:
        logger.error(f"DB error: {e}", exc_info=True)
        if fetch == "all":
            return []
        return None if fetch else False


# --- كاش بسيط بTTL ---
class TTLCache:
    def __init__(self, ttl_seconds=120):
        self.ttl = ttl_seconds
        self.store = {}

    def get(self, key):
        v = self.store.get(key)
        if not v:
            return None
        value, ts = v
        if time() - ts > self.ttl:
            self.store.pop(key, None)
            return None
        return value

    def set(self, key, value):
        self.store[key] = (value, time())

cache = TTLCache(ttl_seconds=120)


# --- أمثلة استعلامات تستخدم الكاش ---

def get_categories_tree():
    ck = "categories_tree"
    v = cache.get(ck)
    if v is not None:
        return v
    rows = execute_query("SELECT * FROM categories ORDER BY name", fetch="all")
    cache.set(ck, rows)
    return rows


def get_popular_videos():
    ck = "popular_videos"
    v = cache.get(ck)
    if v is not None:
        return v
    most_viewed = execute_query(
        "SELECT * FROM video_archive ORDER BY view_count DESC, id DESC LIMIT 10",
        fetch="all",
    )
    highest_rated = execute_query(
        """
        SELECT v.*, r.avg_rating 
        FROM video_archive v 
        JOIN (
            SELECT video_id, AVG(rating) as avg_rating 
            FROM video_ratings 
            GROUP BY video_id
        ) r ON v.id = r.video_id 
        ORDER BY r.avg_rating DESC, v.view_count DESC 
        LIMIT 10
        """,
        fetch="all",
    )
    res = {"most_viewed": most_viewed, "highest_rated": highest_rated}
    cache.set(ck, res)
    return res


def search_videos(query, page=0, category_id=None, quality=None, status=None):
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

    videos_query = f"SELECT * FROM video_archive WHERE {where_string} ORDER BY id DESC LIMIT %s OFFSET %s"
    final_params_videos = tuple(params + [VIDEOS_PER_PAGE, offset])
    videos = execute_query(videos_query, final_params_videos, fetch="all")

    count_query = f"SELECT COUNT(*) as count FROM video_archive WHERE {where_string}"
    total = execute_query(count_query, tuple(params), fetch="one")

    return videos, (total['count'] if total else 0)
