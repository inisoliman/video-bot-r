# ==============================================================================
# db_manager.py (كامل - متوافق مع النسخة الاحتياطية وhandlers)
# ==============================================================================

import psycopg2
import psycopg2.pool
from psycopg2 import sql
from psycopg2.extras import DictCursor
import os
from urllib.parse import urlparse
import logging
import json
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# --- إعداد قاعدة البيانات ---
try:
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if not DATABASE_URL: 
        raise ValueError("DATABASE_URL not set.")
    result = urlparse(DATABASE_URL)
    DB_CONFIG = {
        'user': result.username, 
        'password': result.password,
        'host': result.hostname, 
        'port': result.port, 
        'dbname': result.path[1:]
    }
except Exception as e:
    logger.critical(f"FATAL: Could not parse DATABASE_URL. Error: {e}")
    exit()

VIDEOS_PER_PAGE = 10
CALLBACK_DELIMITER = "::"
admin_steps = {}
user_last_search = {}

# --- Connection Pool ---
_connection_pool = None
_pool_lock = threading.Lock()

def get_connection_pool():
    global _connection_pool
    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                try:
                    _connection_pool = psycopg2.pool.ThreadedConnectionPool(
                        1, 20,  # min and max connections
                        **DB_CONFIG
                    )
                    logger.info("Database connection pool created successfully")
                except Exception as e:
                    logger.error(f"Failed to create connection pool: {e}")
                    raise
    return _connection_pool

@contextmanager
def get_db_connection():
    """Context manager للحصول على اتصال من pool"""
    pool = get_connection_pool()
    conn = None
    try:
        conn = pool.getconn()
        if conn:
            yield conn
        else:
            raise psycopg2.OperationalError("Could not get connection from pool")
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        if conn:
            pool.putconn(conn)
        raise
    finally:
        if conn:
            pool.putconn(conn)

def execute_query(query, params=None, fetch=None, commit=False):
    result = None
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as c:
                c.execute(query, params)
                if fetch == "one": 
                    result = c.fetchone()
                elif fetch == "all": 
                    result = c.fetchall()
                if commit:
                    conn.commit()
                    if fetch is None: 
                        result = True
    except psycopg2.Error as e:
        logger.error(f"Database query failed. Error: {e}", exc_info=True)
        if fetch == "all": 
            return []
        return None if fetch else False
    return result

# === Bootstrap Schema ===
def bootstrap_schema():
    """إنشاء الجداول المطلوبة إذا لم تكن موجودة (متوافق مع backup.sql)"""
    schemas = [
        # جدول الإعدادات
        """CREATE TABLE IF NOT EXISTS botsettings (
            settingkey text PRIMARY KEY,
            settingvalue text
        )""",
        
        # جدول المستخدمين
        """CREATE TABLE IF NOT EXISTS botusers (
            userid bigint PRIMARY KEY,
            username text,
            firstname text,
            joindate timestamp DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # جدول القنوات المطلوبة
        """CREATE TABLE IF NOT EXISTS requiredchannels (
            id SERIAL PRIMARY KEY,
            channelid text UNIQUE,
            channelname text
        )""",
        
        # جدول التصنيفات
        """CREATE TABLE IF NOT EXISTS categories (
            id SERIAL PRIMARY KEY,
            name text NOT NULL,
            parentid integer REFERENCES categories(id) ON DELETE CASCADE,
            fullpath text NOT NULL
        )""",
        
        # جدول الفيديوهات
        """CREATE TABLE IF NOT EXISTS videoarchive (
            id SERIAL PRIMARY KEY,
            messageid bigint UNIQUE,
            caption text,
            chatid bigint,
            filename text,
            fileid text,
            categoryid integer REFERENCES categories(id) ON DELETE SET NULL,
            metadata jsonb DEFAULT '{}'::jsonb,
            viewcount integer DEFAULT 0,
            uploaddate timestamp DEFAULT CURRENT_TIMESTAMP,
            groupingkey text
        )""",
        
        # جدول التقييمات
        """CREATE TABLE IF NOT EXISTS videoratings (
            id SERIAL PRIMARY KEY,
            videoid integer REFERENCES videoarchive(id) ON DELETE CASCADE,
            userid bigint NOT NULL,
            rating integer,
            createdat timestamp DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(videoid, userid)
        )""",
        
        # جدول حالة المستخدمين
        """CREATE TABLE IF NOT EXISTS userstates (
            userid bigint PRIMARY KEY,
            state text NOT NULL,
            context jsonb,
            lastupdate timestamp DEFAULT CURRENT_TIMESTAMP
        )""",
        
        # جدول المفضلة
        """CREATE TABLE IF NOT EXISTS userfavorites (
            id SERIAL PRIMARY KEY,
            userid bigint,
            videoid integer REFERENCES videoarchive(id) ON DELETE CASCADE,
            dateadded timestamp DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(userid, videoid)
        )""",
        
        # جدول تاريخ المشاهدة
        """CREATE TABLE IF NOT EXISTS userhistory (
            id SERIAL PRIMARY KEY,
            userid bigint,
            videoid integer REFERENCES videoarchive(id) ON DELETE CASCADE,
            lastwatched timestamp DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(userid, videoid)
        )"""
    ]
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as c:
                for schema in schemas:
                    c.execute(schema)
            conn.commit()
        logger.info("Bootstrap schema completed")
    except Exception as e:
        logger.error(f"Bootstrap schema failed: {e}")

# === Indexes ===
INDEX_QUERIES = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS idx_video_caption_trgm ON videoarchive USING gin (caption gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_video_filename_trgm ON videoarchive USING gin (filename gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_video_category ON videoarchive (categoryid)",
    "CREATE INDEX IF NOT EXISTS idx_video_views_desc ON videoarchive (viewcount DESC)",
]

def create_indexes():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as c:
                for q in INDEX_QUERIES:
                    try:
                        c.execute(q)
                    except Exception as iqe:
                        logger.warning(f"Index creation warning: {iqe}")
            conn.commit()
        logger.info("DB indexes ensured successfully")
    except Exception as e:
        logger.warning(f"create_indexes failed or skipped: {e}")

def verify_and_repair_schema():
    """دالة للتوافق مع الكود القديم"""
    bootstrap_schema()

# === البحث المحسن ===
def search_videos(query, page=0, category_id=None, quality=None, status=None):
    offset = page * VIDEOS_PER_PAGE
    search_term = f"%{query}%"

    where_clauses = ["(caption ILIKE %s OR filename ILIKE %s)"]
    params = [search_term, search_term]

    if category_id:
        where_clauses.append("categoryid = %s")
        params.append(category_id)
    if quality:
        where_clauses.append("metadata->>'quality_resolution' = %s")
        params.append(quality)
    if status:
        where_clauses.append("metadata->>'status' = %s")
        params.append(status)

    where_string = " AND ".join(where_clauses)

    videos_query = f"SELECT * FROM videoarchive WHERE {where_string} ORDER BY id DESC LIMIT %s OFFSET %s"
    final_params_videos = tuple(params + [VIDEOS_PER_PAGE, offset])
    videos = execute_query(videos_query, final_params_videos, fetch="all")

    count_query = f"SELECT COUNT(*) as count FROM videoarchive WHERE {where_string}"
    final_params_count = tuple(params)
    total = execute_query(count_query, final_params_count, fetch="one")

    return videos, total['count'] if total else 0

# === دوال البوت الأساسية ===
def add_bot_user(user_id, username, first_name):
    return execute_query("INSERT INTO botusers (userid, username, firstname) VALUES (%s, %s, %s) ON CONFLICT (userid) DO NOTHING", (user_id, username, first_name), commit=True)

def get_all_user_ids():
    res = execute_query("SELECT userid FROM botusers", fetch="all")
    return [r['userid'] for r in res] if res else []

def get_subscriber_count():
    res = execute_query("SELECT COUNT(*) as count FROM botusers", fetch="one")
    return res['count'] if res else 0

# === إدارة الفيديوهات ===
def add_video(message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id=None):
    metadata_json = json.dumps(metadata or {})
    query = """
        INSERT INTO videoarchive (messageid, caption, chatid, filename, fileid, metadata, groupingkey, categoryid)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (messageid) DO UPDATE SET
            caption = EXCLUDED.caption,
            filename = EXCLUDED.filename,
            fileid = EXCLUDED.fileid,
            metadata = EXCLUDED.metadata,
            groupingkey = EXCLUDED.groupingkey,
            categoryid = EXCLUDED.categoryid
        RETURNING id
    """
    params = (message_id, caption, chat_id, file_name, file_id, metadata_json, grouping_key, category_id)
    result = execute_query(query, params, fetch="one", commit=True)
    return result['id'] if result else None

def get_video_by_id(video_id):
    return execute_query("SELECT * FROM videoarchive WHERE id = %s", (video_id,), fetch="one")

def get_video_by_message_id(message_id):
    return execute_query("SELECT * FROM videoarchive WHERE messageid = %s", (message_id,), fetch="one")

def increment_video_view_count(video_id):
    return execute_query("UPDATE videoarchive SET viewcount = viewcount + 1 WHERE id = %s", (video_id,), commit=True)

def get_random_video():
    return execute_query("SELECT * FROM videoarchive ORDER BY RANDOM() LIMIT 1", fetch="one")

def get_videos(category_id, page=0):
    offset = page * VIDEOS_PER_PAGE
    videos = execute_query("SELECT * FROM videoarchive WHERE categoryid = %s ORDER BY id DESC LIMIT %s OFFSET %s", (category_id, VIDEOS_PER_PAGE, offset), fetch="all")
    total = execute_query("SELECT COUNT(*) as count FROM videoarchive WHERE categoryid = %s", (category_id,), fetch="one")
    return videos, total['count'] if total else 0

def move_video_to_category(video_id, new_category_id):
    return execute_query("UPDATE videoarchive SET categoryid = %s WHERE id = %s", (new_category_id, video_id), commit=True)

def delete_videos_by_ids(video_ids):
    if not video_ids: 
        return 0
    res = execute_query("DELETE FROM videoarchive WHERE id = ANY(%s) RETURNING id", (video_ids,), fetch="all", commit=True)
    return len(res) if isinstance(res, list) else 0

def move_videos_from_category(old_category_id, new_category_id):
    return execute_query("UPDATE videoarchive SET categoryid = %s WHERE categoryid = %s", (new_category_id, old_category_id), commit=True)

def move_videos_bulk(video_ids, new_category_id):
    if not video_ids:
        return 0
    query = "UPDATE videoarchive SET categoryid = %s WHERE id = ANY(%s) RETURNING id"
    result = execute_query(query, (new_category_id, video_ids), fetch='all', commit=True)
    return len(result) if isinstance(result, list) else 0

# === إدارة التصنيفات ===
def get_category_by_id(category_id):
    return execute_query("SELECT * FROM categories WHERE id = %s", (category_id,), fetch="one")

def add_category(name, parent_id=None):
    full_path = name
    if parent_id is not None:
        parent_category = get_category_by_id(parent_id)
        if parent_category and parent_category.get('fullpath'):
            full_path = f"{parent_category['fullpath']}/{name}"
        elif parent_category:
            full_path = f"{parent_category['name']}/{name}" 

    query = "INSERT INTO categories (name, parentid, fullpath) VALUES (%s, %s, %s) RETURNING id"
    params = (name, parent_id, full_path)
    
    res = execute_query(query, params, fetch="one", commit=True)
    return (True, res) if res else (False, "Failed to add category")

def get_categories_tree():
    return execute_query("SELECT * FROM categories ORDER BY name", fetch="all")

def get_child_categories(parent_id):
    if parent_id is None:
         return execute_query("SELECT * FROM categories WHERE parentid IS NULL ORDER BY name", fetch="all")
    return execute_query("SELECT * FROM categories WHERE parentid = %s ORDER BY name", (parent_id,), fetch="all")

def delete_category_and_contents(category_id):
    execute_query("DELETE FROM videoarchive WHERE categoryid = %s", (category_id,), commit=True)
    execute_query("DELETE FROM categories WHERE id = %s", (category_id,), commit=True)
    return True

def delete_category_by_id(category_id):
    return execute_query("DELETE FROM categories WHERE id = %s", (category_id,), commit=True)

# === إدارة القنوات المطلوبة ===
def add_required_channel(channel_id, channel_name):
    return execute_query("INSERT INTO requiredchannels (channelid, channelname) VALUES (%s, %s) ON CONFLICT(channelid) DO NOTHING", (str(channel_id), channel_name), commit=True)

def remove_required_channel(channel_id):
    return execute_query("DELETE FROM requiredchannels WHERE channelid = %s", (str(channel_id),), commit=True)

def get_required_channels():
    return execute_query("SELECT * FROM requiredchannels", fetch="all")

# === إعدادات البوت ===
def get_active_category_id():
    res = execute_query("SELECT settingvalue FROM botsettings WHERE settingkey = 'activecategoryid'", fetch="one")
    return int(res['settingvalue']) if res and (res['settingvalue'] or '').isdigit() else None

def set_active_category_id(category_id):
    return execute_query("INSERT INTO botsettings (settingkey, settingvalue) VALUES ('activecategoryid', %s) ON CONFLICT (settingkey) DO UPDATE SET settingvalue = EXCLUDED.settingvalue", (str(category_id),), commit=True)

# === التقييمات ===
def add_video_rating(video_id, user_id, rating):
    return execute_query("INSERT INTO videoratings (videoid, userid, rating) VALUES (%s, %s, %s) ON CONFLICT (videoid, userid) DO UPDATE SET rating = EXCLUDED.rating", (video_id, user_id, rating), commit=True)

def get_video_rating_stats(video_id):
    return execute_query("SELECT AVG(rating) as avg, COUNT(id) as count FROM videoratings WHERE videoid = %s", (video_id,), fetch="one")

def get_user_video_rating(video_id, user_id):
    res = execute_query("SELECT rating FROM videoratings WHERE videoid = %s AND userid = %s", (video_id, user_id), fetch="one")
    return res['rating'] if res else None

# === الفيديوهات الشائعة ===
def get_popular_videos():
    most_viewed = execute_query(
        "SELECT * FROM videoarchive ORDER BY viewcount DESC, id DESC LIMIT 10", 
        fetch="all"
    )
    
    highest_rated = execute_query(
        """
        SELECT v.*, r.avg_rating 
        FROM videoarchive v 
        JOIN (
            SELECT videoid, AVG(rating) as avg_rating 
            FROM videoratings 
            GROUP BY videoid
        ) r ON v.id = r.videoid 
        ORDER BY r.avg_rating DESC, v.viewcount DESC 
        LIMIT 10
        """, 
        fetch="all"
    )
    
    return {"most_viewed": most_viewed, "highest_rated": highest_rated}

# === حالة المستخدم ===
def set_user_state(user_id: int, state: str, context: dict = None):
    context_json = json.dumps(context) if context else None
    query = "INSERT INTO userstates (userid, state, context) VALUES (%s, %s, %s) ON CONFLICT (userid) DO UPDATE SET state = EXCLUDED.state, context = EXCLUDED.context"
    return execute_query(query, (user_id, state, context_json), commit=True)

def get_user_state(user_id: int):
    return execute_query("SELECT state, context FROM userstates WHERE userid = %s", (user_id,), fetch="one")

def clear_user_state(user_id: int):
    return execute_query("DELETE FROM userstates WHERE userid = %s", (user_id,), commit=True)

# === المفضلة ===
def is_video_favorite(user_id, video_id):
    res = execute_query("SELECT 1 FROM userfavorites WHERE userid = %s AND videoid = %s", (user_id, video_id), fetch="one")
    return bool(res)

def add_to_favorites(user_id, video_id):
    return execute_query("INSERT INTO userfavorites (userid, videoid) VALUES (%s, %s) ON CONFLICT (userid, videoid) DO NOTHING", (user_id, video_id), commit=True)

def remove_from_favorites(user_id, video_id):
    return execute_query("DELETE FROM userfavorites WHERE userid = %s AND videoid = %s", (user_id, video_id), commit=True)

def get_user_favorites(user_id, page=0):
    offset = page * VIDEOS_PER_PAGE
    videos_query = """
        SELECT v.* FROM videoarchive v
        JOIN userfavorites f ON v.id = f.videoid
        WHERE f.userid = %s
        ORDER BY f.dateadded DESC
        LIMIT %s OFFSET %s
    """
    videos = execute_query(videos_query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    total = execute_query("SELECT COUNT(*) as count FROM userfavorites WHERE userid = %s", (user_id,), fetch="one")
    return videos, total['count'] if total else 0

# === تاريخ المشاهدة ===
def add_to_history(user_id, video_id):
    query = """
        INSERT INTO userhistory (userid, videoid, lastwatched) 
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (userid, videoid) DO UPDATE SET lastwatched = CURRENT_TIMESTAMP
    """
    return execute_query(query, (user_id, video_id), commit=True)

def get_user_history(user_id, page=0):
    offset = page * VIDEOS_PER_PAGE
    videos_query = """
        SELECT v.* FROM videoarchive v
        JOIN userhistory h ON v.id = h.videoid
        WHERE h.userid = %s
        ORDER BY h.lastwatched DESC
        LIMIT %s OFFSET %s
    """
    videos = execute_query(videos_query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    total = execute_query("SELECT COUNT(*) as count FROM userhistory WHERE userid = %s", (user_id,), fetch="one")
    return videos, total['count'] if total else 0

# === إدارة المستخدمين ===
def delete_bot_user(user_id):
    execute_query("DELETE FROM userstates WHERE userid = %s", (user_id,), commit=True)
    execute_query("DELETE FROM userfavorites WHERE userid = %s", (user_id,), commit=True)
    execute_query("DELETE FROM userhistory WHERE userid = %s", (user_id,), commit=True)
    execute_query("DELETE FROM videoratings WHERE userid = %s", (user_id,), commit=True)
    return execute_query("DELETE FROM botusers WHERE userid = %s", (user_id,), commit=True)

# === الإحصائيات ===
def get_bot_stats():
    stats = {}
    stats['video_count'] = (execute_query("SELECT COUNT(*) as count FROM videoarchive", fetch="one") or {'count': 0})['count']
    stats['category_count'] = (execute_query("SELECT COUNT(*) as count FROM categories", fetch="one") or {'count': 0})['count']
    stats['total_views'] = (execute_query("SELECT COALESCE(SUM(viewcount), 0) as sum FROM videoarchive", fetch="one") or {'sum': 0})['sum']
    stats['total_ratings'] = (execute_query("SELECT COUNT(*) as count FROM videoratings", fetch="one") or {'count': 0})['count']
    stats['user_count'] = (execute_query("SELECT COUNT(*) as count FROM botusers", fetch="one") or {'count': 0})['count']
    return stats