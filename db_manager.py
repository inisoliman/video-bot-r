#!/usr/bin/env python3
# ==============================================================================
# ملف: db_manager.py (محسّن ومصحح)
# الوصف: إدارة قاعدة البيانات مع connection pooling
# ==============================================================================

import psycopg2
from psycopg2 import sql
from psycopg2.extras import DictCursor
import os
from urllib.parse import urlparse
import logging
import json

logger = logging.getLogger(__name__)

# [تعديل] يجب التأكد من أن هذا الملف لديك هو آخر نسخة كاملة
# تتضمن EXPECTED_SCHEMA بكل الجداول (favorites, history, user_states)
# وإلا ستفشل إضافات المفضلة. 

try:
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if not DATABASE_URL: raise ValueError("DATABASE_URL not set.")
    result = urlparse(DATABASE_URL)
    DB_CONFIG = {
        'user': result.username, 'password': result.password,
        'host': result.hostname, 'port': result.port, 'dbname': result.path[1:]
    }
except Exception as e:
    logger.critical(f"FATAL: Could not parse DATABASE_URL. Error: {e}")
    exit()

# إنشاء connection pool
import psycopg2.pool
import threading
from contextlib import contextmanager

_connection_pool = None
_pool_lock = threading.Lock()

VIDEOS_PER_PAGE = 10
CALLBACK_DELIMITER = "::"
admin_steps = {}
user_last_search = {}

# --- هيكل قاعدة البيانات المتوقع (مصدر الحقيقة) ---
EXPECTED_SCHEMA = {
    'video_archive': {
        'id': 'SERIAL PRIMARY KEY',
        'message_id': 'BIGINT UNIQUE',
        'caption': 'TEXT',
        'chat_id': 'BIGINT',
        'file_name': 'TEXT',
        'file_id': 'TEXT',
        'category_id': 'INTEGER REFERENCES categories(id) ON DELETE SET NULL',
        'metadata': 'JSONB',
        'view_count': 'INTEGER DEFAULT 0',
        'upload_date': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
        'grouping_key': 'TEXT'
    },
    'required_channels': {
        'id': 'SERIAL PRIMARY KEY',
        'channel_id': 'TEXT UNIQUE',
        'channel_name': 'TEXT'
    },
    'categories': {
        'id': 'SERIAL PRIMARY KEY',
        'name': 'TEXT NOT NULL',
        'parent_id': 'INTEGER REFERENCES categories(id) ON DELETE CASCADE',
        'full_path': 'TEXT NOT NULL'
    },
    'bot_users': {
        'user_id': 'BIGINT PRIMARY KEY',
        'username': 'TEXT',
        'first_name': 'TEXT',
        'join_date': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
    },
    'bot_settings': {
        'setting_key': 'TEXT PRIMARY KEY',
        'setting_value': 'TEXT'
    },
    'video_ratings': {
        'id': 'SERIAL PRIMARY KEY',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'user_id': 'BIGINT',
        'rating': 'INTEGER',
        '_UNIQUE_CONSTRAINT': 'UNIQUE(video_id, user_id)'
    },
    'user_states': {
        'user_id': 'BIGINT PRIMARY KEY',
        'state': 'TEXT',
        'context': 'JSONB'
    },
    'user_favorites': {
        'id': 'SERIAL PRIMARY KEY',
        'user_id': 'BIGINT',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'date_added': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
        '_UNIQUE_CONSTRAINT': 'UNIQUE(user_id, video_id)'
    },
    'user_history': {
        'id': 'SERIAL PRIMARY KEY',
        'user_id': 'BIGINT',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'last_watched': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
        '_UNIQUE_CONSTRAINT': 'UNIQUE(user_id, video_id)'
    },
    'video_comments': {
        'id': 'SERIAL PRIMARY KEY',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'user_id': 'BIGINT NOT NULL',
        'username': 'TEXT',
        'comment_text': 'TEXT NOT NULL',
        'admin_reply': 'TEXT',
        'is_read': 'BOOLEAN DEFAULT FALSE',
        'replied_at': 'TIMESTAMP',
        'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
    }
}


def get_connection_pool():
    """إنشاء أو إرجاع connection pool"""
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

def verify_and_repair_schema():
    logger.info("Verifying and repairing database schema...")
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as c:
                for table_name, columns in EXPECTED_SCHEMA.items():
                    create_table_query = sql.SQL(f"CREATE TABLE IF NOT EXISTS {table_name} (id SERIAL PRIMARY KEY)")
                    try:
                        c.execute(create_table_query)
                    except psycopg2.ProgrammingError:
                        pass 

                    logger.info(f"Checking table: {table_name}")
                    for column_name, column_definition in columns.items():
                        # تجاهل الأعمدة الخاصة والـ id (يتم إنشاؤه تلقائياً)
                        if column_name.startswith('_') or column_name == 'id':
                            continue 
                            
                        c.execute("""
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = %s AND column_name = %s
                        """, (table_name, column_name))
                        
                        if c.fetchone() is None:
                            logger.warning(f"Column '{column_name}' not found in table '{table_name}'. Adding it now.")

                            alter_query = sql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(
                                sql.Identifier(table_name),
                                sql.Identifier(column_name),
                                sql.SQL(column_definition)
                            )
                            try:
                                c.execute(alter_query)
                                logger.info(f"Successfully added column '{column_name}' to '{table_name}'.")
                            except Exception as add_err:
                                logger.error(f"Error adding column {column_name} to {table_name}: {add_err}")
                    
                    # إضافة قيود UNIQUE إذا كانت محددة
                    if '_UNIQUE_CONSTRAINT' in columns:
                        constraint_definition = columns['_UNIQUE_CONSTRAINT']
                        constraint_name = f"{table_name}_unique_constraint"
                        c.execute("""
                            SELECT 1 FROM information_schema.table_constraints 
                            WHERE table_name = %s AND constraint_name = %s
                        """, (table_name, constraint_name))
                        if c.fetchone() is None:
                            logger.info(f"Adding UNIQUE constraint to {table_name}")
                            try:
                                c.execute(sql.SQL(f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} {constraint_definition}"))
                            except Exception as const_err:
                                logger.error(f"Error adding constraint to {table_name}: {const_err}")

                conn.commit()
                logger.info("Schema verification and repair process completed successfully.")
    except psycopg2.Error as e:
        logger.error(f"Schema verification error: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error in schema verification: {e}", exc_info=True)
        raise


def execute_query(query, params=None, fetch=None, commit=False):
    """تنفيذ استعلام SQL مع استخدام connection pool"""
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

# ==============================================================================
# دالة بحث مطورة لتدعم الفلاتر المتقدمة
# ==============================================================================
def search_videos(query, page=0, category_id=None, quality=None, status=None):
    offset = page * VIDEOS_PER_PAGE
    search_term = f"%{query}%"

    # بناء جملة WHERE بشكل ديناميكي
    where_clauses = ["(caption ILIKE %s OR file_name ILIKE %s)"]
    params = [search_term, search_term]

    if category_id:
        where_clauses.append("category_id = %s")
        params.append(category_id)
    if quality:
        # البحث داخل حقل JSON
        where_clauses.append("metadata->>'quality_resolution' = %s")
        params.append(quality)
    if status:
        # البحث داخل حقل JSON
        where_clauses.append("metadata->>'status' = %s")
        params.append(status)

    where_string = " AND ".join(where_clauses)

    # استعلام جلب الفيديوهات
    videos_query = f"SELECT * FROM video_archive WHERE {where_string} ORDER BY id DESC LIMIT %s OFFSET %s"
    final_params_videos = tuple(params + [VIDEOS_PER_PAGE, offset])
    videos = execute_query(videos_query, final_params_videos, fetch="all")

    # استعلام جلب العدد الإجمالي
    count_query = f"SELECT COUNT(*) as count FROM video_archive WHERE {where_string}"
    final_params_count = tuple(params)
    total = execute_query(count_query, final_params_count, fetch="one")

    return videos, total['count'] if total else 0

def add_video(message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id=None):
    metadata_json = json.dumps(metadata)
    query = """
        INSERT INTO video_archive (message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (message_id) DO UPDATE SET
            caption = EXCLUDED.caption,
            file_name = EXCLUDED.file_name,
            file_id = EXCLUDED.file_id,
            metadata = EXCLUDED.metadata,
            grouping_key = EXCLUDED.grouping_key,
            category_id = EXCLUDED.category_id
        RETURNING id
    """
    params = (message_id, caption, chat_id, file_name, file_id, metadata_json, grouping_key, category_id)
    result = execute_query(query, params, fetch="one", commit=True)
    return result['id'] if result else None

# [إصلاح] إضافة دالة get_category_by_id قبل دالة add_category
def get_category_by_id(category_id):
    """جلب تصنيف بواسطة معرفه (ID)."""
    return execute_query("SELECT * FROM categories WHERE id = %s", (category_id,), fetch="one")

def add_category(name, parent_id=None):
    """
    Adds a new category to the database, including the full_path.
    """
    full_path = name
    if parent_id is not None:
        parent_category = get_category_by_id(parent_id)
        if parent_category and parent_category.get('full_path'):
            full_path = f"{parent_category['full_path']}/{name}"
        elif parent_category:
            # معالجة بيانات قديمة لا تحتوي على full_path
            full_path = f"{parent_category['name']}/{name}" 

    query = "INSERT INTO categories (name, parent_id, full_path) VALUES (%s, %s, %s) RETURNING id"
    params = (name, parent_id, full_path)
    
    res = execute_query(query, params, fetch="one", commit=True)
    return (True, res) if res else (False, "Failed to add category")

def get_categories_tree():
    """جلب جميع التصنيفات الرئيسية (parent_id IS NULL)."""
    # [إصلاح] تغيير الاستعلام لكي يجلب جميع التصنيفات الرئيسية
    return execute_query("SELECT * FROM categories WHERE parent_id IS NULL OR parent_id IS NOT NULL ORDER BY name", fetch="all")

def get_child_categories(parent_id):
    """جلب التصنيفات الفرعية لتصنيف معين."""
    if parent_id is None:
         # [إصلاح] التأكد من جلب التصنيفات الرئيسية (parent_id IS NULL)
         return execute_query("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY name", fetch="all")

    return execute_query("SELECT * FROM categories WHERE parent_id = %s ORDER BY name", (parent_id,), fetch="all")


def get_videos(category_id, page=0):
    offset = page * VIDEOS_PER_PAGE
    videos = execute_query("SELECT * FROM video_archive WHERE category_id = %s ORDER BY id DESC LIMIT %s OFFSET %s", (category_id, VIDEOS_PER_PAGE, offset), fetch="all")
    total = execute_query("SELECT COUNT(*) as count FROM video_archive WHERE category_id = %s", (category_id,), fetch="one")
    return videos, total['count'] if total else 0

def increment_video_view_count(video_id):
    """دالة زيادة عداد المشاهدات."""
    return execute_query("UPDATE video_archive SET view_count = view_count + 1 WHERE id = %s", (video_id,), commit=True)

def get_video_by_message_id(message_id):
    """دالة جلب فيديو بمعرف الرسالة."""
    return execute_query("SELECT * FROM video_archive WHERE message_id = %s", (message_id,), fetch="one")

def get_active_category_id():
    res = execute_query("SELECT setting_value FROM bot_settings WHERE setting_key = 'active_category_id'", fetch="one")
    return int(res['setting_value']) if res and res['setting_value'].isdigit() else None

def set_active_category_id(category_id):
    return execute_query("INSERT INTO bot_settings (setting_key, setting_value) VALUES ('active_category_id', %s) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value", (str(category_id),), commit=True)

def add_video_rating(video_id, user_id, rating):
    return execute_query("INSERT INTO video_ratings (video_id, user_id, rating) VALUES (%s, %s, %s) ON CONFLICT (video_id, user_id) DO UPDATE SET rating = EXCLUDED.rating", (video_id, user_id, rating), commit=True)

def get_video_rating_stats(video_id):
    return execute_query("SELECT AVG(rating) as avg, COUNT(id) as count FROM video_ratings WHERE video_id = %s", (video_id,), fetch="one")

def get_user_video_rating(video_id, user_id):
    res = execute_query("SELECT rating FROM video_ratings WHERE video_id = %s AND user_id = %s", (video_id, user_id), fetch="one")
    return res['rating'] if res else None

def get_videos_ratings_bulk(video_ids):
    """
    جلب تقييمات متعددة دفعة واحدة لتحسين الأداء (حل مشكلة N+1)
    
    Args:
        video_ids: قائمة بأرقام الفيديوهات
    
    Returns:
        dict: قاموس {video_id: avg_rating}
    """
    if not video_ids:
        return {}
    
    query = """
        SELECT video_id, AVG(rating) as avg_rating, COUNT(*) as count
        FROM video_ratings
        WHERE video_id = ANY(%s)
        GROUP BY video_id
    """
    
    results = execute_query(query, (video_ids,), fetch="all")
    
    if not results:
        return {}
    
    return {
        row['video_id']: {
            'avg': float(row['avg_rating']) if row['avg_rating'] else 0,
            'count': row['count']
        }
        for row in results
    }


# ============================================
# [إصلاح] دالة get_popular_videos - إزالة القوس والـ r المكرر
# ============================================
def get_popular_videos():
    most_viewed = execute_query(
        "SELECT * FROM video_archive ORDER BY view_count DESC, id DESC LIMIT 10", 
        fetch="all"
    )
    
    # [إصلاح] إزالة القوس الإضافي والـ r المكرر
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
        fetch="all"
    )
    
    return {"most_viewed": most_viewed, "highest_rated": highest_rated}

def add_bot_user(user_id, username, first_name):
    execute_query("INSERT INTO bot_users (user_id, username, first_name) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, username, first_name), commit=True)

def get_all_user_ids():
    res = execute_query("SELECT user_id FROM bot_users", fetch="all")
    return [r['user_id'] for r in res] if res else []

def get_subscriber_count():
    res = execute_query("SELECT COUNT(*) as count FROM bot_users", fetch="one")
    return res['count'] if res else 0

def get_bot_stats():
    stats = {}
    stats['video_count'] = (execute_query("SELECT COUNT(*) as count FROM video_archive", fetch="one") or {'count': 0})['count']
    stats['category_count'] = (execute_query("SELECT COUNT(*) as count FROM categories", fetch="one") or {'count': 0})['count']
    stats['total_views'] = (execute_query("SELECT COALESCE(SUM(view_count), 0) as sum FROM video_archive", fetch="one") or {'sum': 0})['sum']
    stats['total_ratings'] = (execute_query("SELECT COUNT(*) as count FROM video_ratings", fetch="one") or {'count': 0})['count']
    return stats

def add_required_channel(channel_id, channel_name):
    return execute_query("INSERT INTO required_channels (channel_id, channel_name) VALUES (%s, %s) ON CONFLICT(channel_id) DO NOTHING", (str(channel_id), channel_name), commit=True)

def remove_required_channel(channel_id):
    return execute_query("DELETE FROM required_channels WHERE channel_id = %s", (str(channel_id),), commit=True)

def get_required_channels():
    return execute_query("SELECT * FROM required_channels", fetch="all")

def get_video_by_id(video_id):
    return execute_query("SELECT * FROM video_archive WHERE id = %s", (video_id,), fetch="one")

def move_video_to_category(video_id, new_category_id):
    return execute_query("UPDATE video_archive SET category_id = %s WHERE id = %s", (new_category_id, video_id), commit=True)

def delete_videos_by_ids(video_ids):
    if not video_ids: return 0
    res = execute_query("DELETE FROM video_archive WHERE id = ANY(%s) RETURNING id", (video_ids,), fetch="all", commit=True)
    return len(res) if isinstance(res, list) else 0

def delete_category_and_contents(category_id):
    execute_query("DELETE FROM video_archive WHERE category_id = %s", (category_id,), commit=True)
    execute_query("DELETE FROM categories WHERE id = %s", (category_id,), commit=True)
    return True

def move_videos_from_category(old_category_id, new_category_id):
    return execute_query("UPDATE video_archive SET category_id = %s WHERE category_id = %s", (new_category_id, old_category_id), commit=True)

def delete_category_by_id(category_id):
    return execute_query("DELETE FROM categories WHERE id = %s", (category_id,), commit=True)

def get_random_video():
    """Fetches a single random video from the database."""
    query = "SELECT * FROM video_archive ORDER BY RANDOM() LIMIT 1"
    return execute_query(query, fetch="one")

# --- دوال إدارة حالة المستخدم (State Management) ---
def set_user_state(user_id: int, state: str, context: dict = None):
    context_json = json.dumps(context) if context else None
    query = "INSERT INTO user_states (user_id, state, context) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET state = EXCLUDED.state, context = EXCLUDED.context"
    return execute_query(query, (user_id, state, context_json), commit=True)

def get_user_state(user_id: int):
    return execute_query("SELECT state, context FROM user_states WHERE user_id = %s", (user_id,), fetch="one")

def clear_user_state(user_id: int):
    return execute_query("DELETE FROM user_states WHERE user_id = %s", (user_id,), commit=True)

# --- دوال المفضلة وسجل المشاهدة ---
def is_video_favorite(user_id, video_id):
    res = execute_query("SELECT 1 FROM user_favorites WHERE user_id = %s AND video_id = %s", (user_id, video_id), fetch="one")
    return bool(res)

def add_to_favorites(user_id, video_id):
    return execute_query("INSERT INTO user_favorites (user_id, video_id) VALUES (%s, %s) ON CONFLICT (user_id, video_id) DO NOTHING", (user_id, video_id), commit=True)

def remove_from_favorites(user_id, video_id):
    return execute_query("DELETE FROM user_favorites WHERE user_id = %s AND video_id = %s", (user_id, video_id), commit=True)

def get_user_favorites(user_id, page=0):
    offset = page * VIDEOS_PER_PAGE
    videos_query = """
        SELECT v.* FROM video_archive v
        JOIN user_favorites f ON v.id = f.video_id
        WHERE f.user_id = %s
        ORDER BY f.date_added DESC
        LIMIT %s OFFSET %s
    """
    videos = execute_query(videos_query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    total = execute_query("SELECT COUNT(*) as count FROM user_favorites WHERE user_id = %s", (user_id,), fetch="one")
    return videos, total['count'] if total else 0

def add_to_history(user_id, video_id):
    query = """
        INSERT INTO user_history (user_id, video_id, last_watched) 
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, video_id) DO UPDATE SET last_watched = CURRENT_TIMESTAMP
    """
    return execute_query(query, (user_id, video_id), commit=True)

def get_user_history(user_id, page=0):
    offset = page * VIDEOS_PER_PAGE
    videos_query = """
        SELECT v.* FROM video_archive v
        JOIN user_history h ON v.id = h.video_id
        WHERE h.user_id = %s
        ORDER BY h.last_watched DESC
        LIMIT %s OFFSET %s
    """
    videos = execute_query(videos_query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    total = execute_query("SELECT COUNT(*) as count FROM user_history WHERE user_id = %s", (user_id,), fetch="one")
    return videos, total['count'] if total else 0

# --- دالة حذف المشترك (لحل خطأ البث 403) ---
def delete_bot_user(user_id):
    """حذف المستخدم من جدول المشتركين."""
    execute_query("DELETE FROM user_states WHERE user_id = %s", (user_id,), commit=True)
    execute_query("DELETE FROM user_favorites WHERE user_id = %s", (user_id,), commit=True)
    execute_query("DELETE FROM user_history WHERE user_id = %s", (user_id,), commit=True)
    execute_query("DELETE FROM video_ratings WHERE user_id = %s", (user_id,), commit=True)
    return execute_query("DELETE FROM bot_users WHERE user_id = %s", (user_id,), commit=True)

def move_videos_bulk(video_ids, new_category_id):
    """
    نقل مجموعة من الفيديوهات إلى تصنيف جديد دفعة واحدة.

    Args:
        video_ids: قائمة بأرقام الفيديوهات
        new_category_id: رقم التصنيف الجديد

    Returns:
        عدد الفيديوهات التي تم نقلها بنجاح
    """
    if not video_ids:
        return 0

    query = "UPDATE video_archive SET category_id = %s WHERE id = ANY(%s) RETURNING id"
    result = execute_query(query, (new_category_id, video_ids), fetch='all', commit=True)
    return len(result) if isinstance(result, list) else 0

# ==============================================================================
# دوال نظام التعليقات الخاصة (Private Comments System)
# ==============================================================================

def add_comment(video_id, user_id, username, comment_text):
    """
    إضافة تعليق جديد من المستخدم على فيديو.
    
    Args:
        video_id: رقم الفيديو
        user_id: رقم المستخدم
        username: اسم المستخدم
        comment_text: نص التعليق
    
    Returns:
        رقم التعليق إذا نجح، None إذا فشل
    """
    query = """
        INSERT INTO video_comments (video_id, user_id, username, comment_text)
        VALUES (%s, %s, %s, %s)
        RETURNING id
    """
    result = execute_query(query, (video_id, user_id, username, comment_text), fetch="one", commit=True)
    return result['id'] if result else None

def get_all_comments(page=0, unread_only=False):
    """
    جلب جميع التعليقات للأدمن (مع pagination).
    
    Args:
        page: رقم الصفحة
        unread_only: إذا كان True، يجلب التعليقات غير المقروءة فقط
    
    Returns:
        tuple: (قائمة التعليقات، العدد الإجمالي)
    """
    offset = page * VIDEOS_PER_PAGE
    
    where_clause = "WHERE is_read = FALSE" if unread_only else ""
    
    comments_query = f"""
        SELECT c.*, v.caption as video_caption, v.file_name as video_name
        FROM video_comments c
        JOIN video_archive v ON c.video_id = v.id
        {where_clause}
        ORDER BY c.created_at DESC
        LIMIT %s OFFSET %s
    """
    comments = execute_query(comments_query, (VIDEOS_PER_PAGE, offset), fetch="all")
    
    count_query = f"SELECT COUNT(*) as count FROM video_comments {where_clause}"
    total = execute_query(count_query, fetch="one")
    
    return comments, total['count'] if total else 0

def get_user_comments(user_id, page=0):
    """
    جلب تعليقات مستخدم معين (للمستخدم لرؤية تعليقاته والردود عليها).
    
    Args:
        user_id: رقم المستخدم
        page: رقم الصفحة
    
    Returns:
        tuple: (قائمة التعليقات، العدد الإجمالي)
    """
    offset = page * VIDEOS_PER_PAGE
    
    comments_query = """
        SELECT c.*, v.caption as video_caption, v.file_name as video_name
        FROM video_comments c
        JOIN video_archive v ON c.video_id = v.id
        WHERE c.user_id = %s
        ORDER BY c.created_at DESC
        LIMIT %s OFFSET %s
    """
    comments = execute_query(comments_query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    
    total = execute_query("SELECT COUNT(*) as count FROM video_comments WHERE user_id = %s", (user_id,), fetch="one")
    
    return comments, total['count'] if total else 0

def get_comment_by_id(comment_id):
    """
    جلب تعليق بواسطة رقمه.
    
    Args:
        comment_id: رقم التعليق
    
    Returns:
        بيانات التعليق أو None
    """
    query = """
        SELECT c.*, v.caption as video_caption, v.file_name as video_name
        FROM video_comments c
        JOIN video_archive v ON c.video_id = v.id
        WHERE c.id = %s
    """
    return execute_query(query, (comment_id,), fetch="one")

def reply_to_comment(comment_id, admin_reply):
    """
    الرد على تعليق من قبل الأدمن.
    
    Args:
        comment_id: رقم التعليق
        admin_reply: نص الرد
    
    Returns:
        True إذا نجح، False إذا فشل
    """
    query = """
        UPDATE video_comments 
        SET admin_reply = %s, replied_at = CURRENT_TIMESTAMP, is_read = TRUE
        WHERE id = %s
    """
    return execute_query(query, (admin_reply, comment_id), commit=True)

def mark_comment_read(comment_id):
    """
    تعليم التعليق كمقروء.
    
    Args:
        comment_id: رقم التعليق
    
    Returns:
        True إذا نجح، False إذا فشل
    """
    return execute_query("UPDATE video_comments SET is_read = TRUE WHERE id = %s", (comment_id,), commit=True)

def delete_comment(comment_id):
    """
    حذف تعليق (للأدمن فقط).
    
    Args:
        comment_id: رقم التعليق
    
    Returns:
        True إذا نجح، False إذا فشل
    """
    return execute_query("DELETE FROM video_comments WHERE id = %s", (comment_id,), commit=True)

def get_unread_comments_count():
    """
    جلب عدد التعليقات غير المقروءة.
    
    Returns:
        عدد التعليقات غير المقروءة
    """
    result = execute_query("SELECT COUNT(*) as count FROM video_comments WHERE is_read = FALSE", fetch="one")
    return result['count'] if result else 0

def get_video_comments_count(video_id):
    """
    جلب عدد التعليقات على فيديو معين.
    
    Args:
        video_id: رقم الفيديو
    
    Returns:
        عدد التعليقات
    """
    result = execute_query("SELECT COUNT(*) as count FROM video_comments WHERE video_id = %s", (video_id,), fetch="one")
    return result['count'] if result else 0
