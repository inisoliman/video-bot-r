#!/usr/bin/env python3
# ==============================================================================
# ملف: db_manager.py (محسّن ومصحح)
# الوصف: إدارة قاعدة البيانات مع connection pooling
# ==============================================================================

import psycopg2
from psycopg2 import sql
from psycopg2.extras import DictCursor
import os
import logging
import json
import time
from functools import lru_cache

# Import connection pool functions from db_pool module
from db_pool import get_db_connection, get_connection_pool

logger = logging.getLogger(__name__)

# إعدادات الـ Pool المحسنة - نُستخدم في db_pool
DB_POOL_MIN = int(os.environ.get('DB_POOL_MIN', '2'))  # زيادة الحد الأدنى
DB_POOL_MAX = int(os.environ.get('DB_POOL_MAX', '20'))

VIDEOS_PER_PAGE = 10
CALLBACK_DELIMITER = "::"
admin_steps = {}
user_last_search = {}

# Cache للبحث السريع (يحفظ آخر 100 بحث لمدة 60 ثانية)
_search_cache = {}
_search_cache_ttl = 60  # ثانية


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
        'grouping_key': 'TEXT',
        'thumbnail_file_id': 'TEXT',
        'content_type': 'TEXT DEFAULT NULL',  # VIDEO or DOCUMENT
        # عنوان عرض يعدّله الأدمن - لا يغيّر caption/file_name/file_id
        'display_title': 'TEXT DEFAULT NULL'
    },
    'required_channels': {
        'channel_id': 'BIGINT PRIMARY KEY',
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
        'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
        # --- إضافات تراكمية (additive) لنظام المحادثات ---
        'thread_updated_at': 'TIMESTAMP',
        'member_read_at': 'TIMESTAMP',
        'admin_read_at': 'TIMESTAMP'
    }
}

# جدول رسائل المحادثة (إضافي فقط - لا يعدّل أو يحذف أعمدة قديمة)
COMMENT_THREAD_TABLE = 'video_comment_messages'
COMMENT_THREAD_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS video_comment_messages (
        id SERIAL PRIMARY KEY,
        comment_id INTEGER NOT NULL REFERENCES video_comments(id) ON DELETE CASCADE,
        author_role TEXT NOT NULL,
        author_id BIGINT,
        body TEXT NOT NULL,
        origin TEXT NOT NULL DEFAULT 'thread',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""


def _thread_schema_statements():
    """DDL إضافي متكرر التنفيذ للمحادثات - يُنفَّذ بعد إنشاء الجدول الأب video_comments."""
    return [
        (COMMENT_THREAD_TABLE, COMMENT_THREAD_TABLE_DDL),
        (
            'video_comment_messages_idx',
            "CREATE INDEX IF NOT EXISTS video_comment_messages_comment_idx "
            "ON video_comment_messages (comment_id, id)"
        ),
    ]


LEGACY_ORIGIN_ORIGINAL = 'legacy_original'
LEGACY_ORIGIN_REPLY = 'legacy_reply'


def ensure_thread_legacy_unique_index(cursor):
    """
    فهرس فريد جزئي (comment_id, origin) يمنع تكرار التاريخ القديم عند أي إعادة تشغيل.

    إن تعذّرت إضافته (بيانات قديمة مكررة لا تمنع التهيئة) يستمر الاستيراد لأن حارس
    NOT EXISTS+ON CONFLICT DO NOTHING يبقيه بلا تكرار.
    """
    try:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS video_comment_messages_legacy_uq "
            "ON video_comment_messages (comment_id, origin) WHERE origin <> 'thread'"
        )
        return True
    except Exception as index_err:
        logger.warning("Legacy unique index unavailable (%s) - import guard still applies", index_err)
        return False


def _legacy_import_statements():
    """
    استيراد التعليق الأصلي (comment_text) والرد القديم (admin_reply) مرة واحدة لكل محادثة.

    لا يعدّل ولا يحذف أي صف قديم. يحافظ على النص والهوية والوقت الأصليين، ولا يمس
    رسائل المحادثة الجديدة (origin='thread').
    """
    return [
        (
            LEGACY_ORIGIN_ORIGINAL,
            """
            INSERT INTO video_comment_messages (comment_id, author_role, author_id, body, origin, created_at)
            SELECT c.id, 'member', c.user_id, c.comment_text, %s, COALESCE(c.created_at, CURRENT_TIMESTAMP)
            FROM video_comments c
            WHERE c.comment_text IS NOT NULL AND c.comment_text <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM video_comment_messages m
                  WHERE m.comment_id = c.id AND m.origin = %s
              )
            ON CONFLICT DO NOTHING
            """,
        ),
        (
            LEGACY_ORIGIN_REPLY,
            """
            INSERT INTO video_comment_messages (comment_id, author_role, author_id, body, origin, created_at)
            SELECT c.id, 'admin', NULL, c.admin_reply, %s, COALESCE(c.replied_at, c.created_at, CURRENT_TIMESTAMP)
            FROM video_comments c
            WHERE c.admin_reply IS NOT NULL AND c.admin_reply <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM video_comment_messages m
                  WHERE m.comment_id = c.id AND m.origin = %s
              )
            ON CONFLICT DO NOTHING
            """,
        ),
    ]


def import_legacy_comment_history(cursor):
    """
    تنفيذ استيراد التاريخ القديم على مؤشر داخل معاملة المستدعي.

    Returns:
        dict: import_original, import_reply (صفوف أُدخلت في هذه الدعوة)
    """
    outcome = {'import_original': 0, 'import_reply': 0}

    for origin, statement in _legacy_import_statements():
        cursor.execute(statement, (origin, origin))
        inserted = max(cursor.rowcount or 0, 0)
        if origin == LEGACY_ORIGIN_ORIGINAL:
            outcome['import_original'] = inserted
        else:
            outcome['import_reply'] = inserted

    if outcome['import_original'] or outcome['import_reply']:
        logger.info(
            "Imported legacy comment history: originals=%s replies=%s",
            outcome['import_original'], outcome['import_reply']
        )
    return outcome


def _existing_columns(cursor, table_name):
    """أعمدة الجدول الحقيقية من information_schema (set قد يكون فارغاً إن تعذّرت القراءة)."""
    _render_and_execute(cursor, "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                        (table_name,))
    rows = cursor.fetchall() or []
    columns = set()
    for row in rows:
        if isinstance(row, dict):
            columns.add(row.get('column_name'))
        elif isinstance(row, (list, tuple)):
            columns.add(row[0])
    return columns


def quote_identifier(name):
    """
    تهريب آمن لهوية جدول/عمود كما يفعل psycopg2.sql.Identifier، بدون اتصال حقيقي.
    الأسماء تأتي دائماً من ثوابت داخلية لا من مدخلات المستخدم.
    """
    text = str(name)
    if not text:
        raise ValueError("empty identifier")
    if "\x00" in text:
        raise ValueError("NUL in identifier")
    return '"' + text.replace('"', '""') + '"'


def _render_and_execute(cursor, statement, params=None):
    """
    تنفيذ عبارة DDL مكوَّنة: psycopg2.sql.Composable تُحوَّل إلى نص (أسماء مهربة).
    """
    if hasattr(statement, 'as_string'):
        statement = statement.as_string(cursor)
    return cursor.execute(statement, params)


def _run_additive_schema(cursor):
    """
    كل التغييرات الإضافية في المعاملة الحالية: جداول الأب first (ترتيب EXPECTED_SCHEMA)،
    ثم الأعمدة الإضافية، ثم الجدول الفرعي video_comment_messages (مفتاح أجنبي بعد الأب)،
    ثم الفهرس الفريد، ثم استيراد التاريخ القديم. أي خطأ يرفع للمستدعي لـ rollback ذري.
    """
    for table_name, columns in EXPECTED_SCHEMA.items():
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS " + quote_identifier(table_name) + " (id SERIAL PRIMARY KEY)"
        )
        logger.info("Checking table: %s", table_name)

        existing = _existing_columns(cursor, table_name)
        for column_name, column_definition in columns.items():
            if column_name.startswith('_') or column_name == 'id':
                continue
            if column_name in existing:
                continue
            logger.warning("Column '%s' not found in table '%s'. Adding it now.", column_name, table_name)
            cursor.execute(
                "ALTER TABLE " + quote_identifier(table_name)
                + " ADD COLUMN IF NOT EXISTS " + quote_identifier(column_name)
                + " " + column_definition
            )
            logger.info("Successfully added column '%s' to '%s'.", column_name, table_name)

        if '_UNIQUE_CONSTRAINT' in columns:
            constraint_definition = columns['_UNIQUE_CONSTRAINT']
            constraint_name = f"{table_name}_unique_constraint"
            cursor.execute("""
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = %s AND constraint_name = %s
            """, (table_name, constraint_name))
            if cursor.fetchone() is None:
                logger.info("Adding UNIQUE constraint to %s", table_name)
                cursor.execute(
                    "ALTER TABLE " + quote_identifier(table_name)
                    + " ADD CONSTRAINT " + quote_identifier(constraint_name)
                    + " " + constraint_definition
                )

    # الجدول الفرعي بعد الجدول الأب (foreign key)
    for object_name, statement in _thread_schema_statements():
        cursor.execute(statement)
        logger.info("Thread schema ensured: %s", object_name)

    ensure_thread_legacy_unique_index(cursor)
    return import_legacy_comment_history(cursor)


def _rollback_quietly(conn):
    try:
        conn.rollback()
    except Exception as rollback_error:
        logger.error("Schema rollback failed: %s", rollback_error)


def verify_and_repair_schema():
    """
    تهيئة المخطط الإضافي بشكل ذري: الأب أولاً ثم الفرعي ثم التاريخ القديم، أو تراجع كامل.

    تكرار الدعوة آمن: كل عبارة idempotent والاستيراد لا يكرر صفوفاً.
    """
    logger.info("Verifying and repairing database schema...")

    with get_db_connection() as conn:
        try:
            with conn.cursor() as c:
                outcome = _run_additive_schema(c)
            conn.commit()
            logger.info(
                "Schema verification and repair completed. legacy originals=%s replies=%s",
                outcome['import_original'], outcome['import_reply'],
            )
            return outcome
        except Exception as schema_err:
            _rollback_quietly(conn)
            logger.error("Schema verification failed, rolled back: %s", schema_err, exc_info=True)
            raise


def execute_query(query, params=None, fetch=None, commit=False):
    """تنفيذ استعلام SQL مع استخدام connection pool"""
    result = None
    try:
        with get_db_connection() as conn:
            try:
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
                try:
                    conn.rollback()
                except Exception as rollback_error:
                    logger.error(f"Database rollback failed: {rollback_error}")
                logger.error(f"Database query failed. Error: {e}", exc_info=True)
                if fetch == "all": 
                    return []
                return None if fetch else False
    except psycopg2.Error as e:
        logger.error(f"Database connection failed. Error: {e}", exc_info=True)
        if fetch == "all": 
            return []
        return None if fetch else False
    return result


def row_count(row, key='count', default=0):
    """
    قراءة عدّاد من صف استعلام بأمان (DictRow أو tuple أو None).

    يستخدمها كل عدّاد في هذا الملف حتى لا يرفع صفٌّ ناقص KeyError في طبقة العرض.
    """
    if not row:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        try:
            value = row[0]
        except (KeyError, IndexError, TypeError):
            return default
    return default if value is None else value


# ==============================================================================
# دالة بحث مطورة لتدعم الفلاتر المتقدمة - مع Caching
# ==============================================================================

def _get_cache_key(query, page, category_id, quality, status):
    """إنشاء مفتاح فريد للـ cache"""
    return f"{query}:{page}:{category_id}:{quality}:{status}"

def _get_cached_search(cache_key):
    """جلب نتيجة البحث من الـ cache إذا كانت صالحة"""
    if cache_key in _search_cache:
        cached_result, cached_time = _search_cache[cache_key]
        if time.time() - cached_time < _search_cache_ttl:
            logger.debug(f"Cache hit for search: {cache_key[:30]}...")
            return cached_result
        else:
            # انتهت صلاحية الـ cache
            del _search_cache[cache_key]
    return None

def _set_cached_search(cache_key, result):
    """حفظ نتيجة البحث في الـ cache"""
    # تنظيف الـ cache إذا كبر جداً
    if len(_search_cache) > 100:
        # حذف أقدم نصف
        oldest_keys = sorted(_search_cache.keys(), key=lambda k: _search_cache[k][1])[:50]
        for key in oldest_keys:
            del _search_cache[key]
    
    _search_cache[cache_key] = (result, time.time())

def clear_search_cache():
    """مسح كل الـ cache للبحث"""
    global _search_cache
    _search_cache = {}
    logger.info("Search cache cleared")

def search_videos(query, page=0, category_id=None, quality=None, status=None):
    # التحقق من الـ cache أولاً
    cache_key = _get_cache_key(query, page, category_id, quality, status)
    cached = _get_cached_search(cache_key)
    if cached:
        return cached
    
    offset = page * VIDEOS_PER_PAGE
    search_term = f"%{query}%"

    # بناء جملة WHERE بشكل ديناميكي (يشمل العنوان المعدّل display_title)
    where_clauses = ["(caption ILIKE %s OR file_name ILIKE %s OR display_title ILIKE %s)"]
    params = [search_term, search_term, search_term]

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

    # حفظ النتيجة في الـ cache
    result = (videos, row_count(total))
    _set_cached_search(cache_key, result)
    
    return result

def add_video(message_id, caption, chat_id, file_name, file_id, metadata, grouping_key=None, category_id=None, thumbnail_file_id=None, content_type='VIDEO'):
    """إضافة فيديو جديد للأرشيف مع البيانات الكاملة"""
    metadata_json = json.dumps(metadata) if metadata else None
    query = """
        INSERT INTO video_archive (message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id, thumbnail_file_id, content_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (message_id) DO UPDATE SET
            caption = EXCLUDED.caption,
            file_name = EXCLUDED.file_name,
            file_id = EXCLUDED.file_id,
            metadata = EXCLUDED.metadata,
            grouping_key = EXCLUDED.grouping_key,
            category_id = EXCLUDED.category_id,
            thumbnail_file_id = COALESCE(EXCLUDED.thumbnail_file_id, video_archive.thumbnail_file_id),
            content_type = EXCLUDED.content_type
        RETURNING id
    """
    params = (message_id, caption, chat_id, file_name, file_id, metadata_json, grouping_key, category_id, thumbnail_file_id, content_type)
    result = execute_query(query, params, fetch="one", commit=True)
    return result['id'] if result and 'id' in result else (result[0] if result else None)

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
    return videos, row_count(total)

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
    return row_count(res)

def get_bot_stats():
    stats = {}
    stats['video_count'] = (execute_query("SELECT COUNT(*) as count FROM video_archive", fetch="one") or {'count': 0})['count']
    stats['category_count'] = (execute_query("SELECT COUNT(*) as count FROM categories", fetch="one") or {'count': 0})['count']
    stats['total_views'] = (execute_query("SELECT COALESCE(SUM(view_count), 0) as sum FROM video_archive", fetch="one") or {'sum': 0})['sum']
    stats['total_ratings'] = (execute_query("SELECT COUNT(*) as count FROM video_ratings", fetch="one") or {'count': 0})['count']
    return stats

def add_required_channel(channel_id, channel_name):
    return execute_query("INSERT INTO required_channels (channel_id, channel_name) VALUES (%s, %s) ON CONFLICT(channel_id) DO NOTHING", (int(channel_id), channel_name), commit=True)

def remove_required_channel(channel_id):
    return execute_query("DELETE FROM required_channels WHERE channel_id = %s", (int(channel_id),), commit=True)

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
    return videos, row_count(total)

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
    return videos, row_count(total)

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

def _thread_bind(c, query, params=None, fetch=None):
    """تنفيذ استعلام المحادثة داخل معاملة واحدة (يمرَّر المؤشر من perform_transaction)."""
    c.execute(query, params)
    if fetch == 'one':
        return c.fetchone()
    if fetch == 'all':
        return c.fetchall()
    return True


def _comment_with_video_select(where_clause, order_clause=''):
    return f"""
        SELECT c.*, v.caption as video_caption, v.file_name as video_name,
               v.display_title as display_title,
               (v.id IS NULL) as video_missing
        FROM video_comments c
        LEFT JOIN video_archive v ON c.video_id = v.id
        {where_clause}
        {order_clause}
    """


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
    
    where_clause = "WHERE c.is_read = FALSE" if unread_only else ""
    
    comments_query = _comment_with_video_select(where_clause, "ORDER BY c.created_at DESC") + "LIMIT %s OFFSET %s"
    comments = execute_query(comments_query, (VIDEOS_PER_PAGE, offset), fetch="all")
    
    count_query = f"SELECT COUNT(*) as count FROM video_comments c {where_clause}"
    total = execute_query(count_query, fetch="one")
    
    return comments, row_count(total)

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
    
    comments_query = _comment_with_video_select(
        "WHERE c.user_id = %s",
        "ORDER BY COALESCE(c.thread_updated_at, c.created_at) DESC, c.id DESC"
    ) + " LIMIT %s OFFSET %s"
    comments = execute_query(comments_query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    
    total = execute_query("SELECT COUNT(*) as count FROM video_comments WHERE user_id = %s", (user_id,), fetch="one")
    
    return comments, row_count(total)

def get_comment_by_id(comment_id):
    """
    جلب تعليق بواسطة رقمه.
    
    Args:
        comment_id: رقم التعليق
    
    Returns:
        بيانات التعليق أو None
    """
    query = _comment_with_video_select("WHERE c.id = %s")
    return execute_query(query, (comment_id,), fetch="one")

def reply_to_comment(comment_id, admin_reply):
    """
    الرد على تعليق من قبل الأدمن (آخر رد - للتوافق مع النظام القديم).
    
    Args:
        comment_id: رقم التعليق
        admin_reply: نص الرد
    
    Returns:
        True إذا نجح، False إذا فشل
    """
    query = """
        UPDATE video_comments 
        SET admin_reply = %s, replied_at = CURRENT_TIMESTAMP, is_read = TRUE,
            thread_updated_at = CURRENT_TIMESTAMP
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
    return row_count(result)

def get_video_comments_count(video_id):
    """
    جلب عدد التعليقات على فيديو معين.
    
    Args:
        video_id: رقم الفيديو
    
    Returns:
        عدد التعليقات
    """
    result = execute_query("SELECT COUNT(*) as count FROM video_comments WHERE video_id = %s", (video_id,), fetch="one")
    return row_count(result)

# ==============================================================================
# دوال الحذف الجماعي للتعليقات
# ==============================================================================

THREAD_PAGE_SIZE = 10  # عدد رسائل المحادثة في الصفحة الواحدة
ROLE_MEMBER = 'member'
ROLE_ADMIN = 'admin'
THREAD_ORIGIN = 'thread'
MAX_THREAD_BODY = 2000  # حد أقصى لنص الرسالة المحفوظ


def append_thread_message(comment_id, author_role, author_id, body):
    """
    إضافة رسالة جديدة إلى محادثة تعليق (بدون أي تعديل على Comment_text أو admin_reply).

    Args:
        comment_id: رقم التعليق (الاستفسار)
        author_role: 'member' أو 'admin'
        author_id: رقم كاتب الرسالة (None للأدمن القديم)
        body: نص الرسالة (يُقطع إلى MAX_THREAD_BODY)

    Returns:
        رقم الرسالة الجديدة أو None عند الفشل
    """
    if author_role not in (ROLE_MEMBER, ROLE_ADMIN):
        logger.error("Invalid thread author_role: %s", author_role)
        return None
    text = _clip_thread_body(body)
    if not text:
        return None
    return _thread_write(comment_id, author_role, author_id, text)


def _clip_thread_body(body):
    if body is None:
        return ''
    text = str(body)
    return text[:MAX_THREAD_BODY]


def _thread_write(comment_id, author_role, author_id, text):
    with get_db_connection() as conn:
        try:
            with conn.cursor(cursor_factory=DictCursor) as c:
                c.execute("SELECT 1 FROM video_comments WHERE id = %s", (comment_id,))
                if c.fetchone() is None:
                    return None
                c.execute(
                    """
                    INSERT INTO video_comment_messages (comment_id, author_role, author_id, body, origin)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (comment_id, author_role, author_id, text, THREAD_ORIGIN)
                )
                row = c.fetchone()
                c.execute("UPDATE video_comments SET thread_updated_at = CURRENT_TIMESTAMP WHERE id = %s", (comment_id,))
                conn.commit()
                if not row:
                    return None
                return row['id'] if hasattr(row, 'keys') else row[0]
        except psycopg2.Error as e:
            try:
                conn.rollback()
            except Exception as rollback_error:
                logger.error(f"Database rollback failed: {rollback_error}")
            logger.error(f"Thread write failed. Error: {e}", exc_info=True)
            return None


def get_thread_messages(comment_id, page=0, page_size=THREAD_PAGE_SIZE, order='asc'):
    """
    جلب رسائل المحادثة مرتبة (الأقدم أولاً افتراضياً).

    Returns:
        tuple: (قائمة الرسائل، العدد الإجمالي)
    """
    page = max(int(page or 0), 0)
    page_size = max(int(page_size or THREAD_PAGE_SIZE), 1)
    offset = page * page_size
    order_sql = 'DESC' if str(order).lower() == 'desc' else 'ASC'
    rows = execute_query(
        f"""
        SELECT id, comment_id, author_role, author_id, body, origin, created_at
        FROM video_comment_messages
        WHERE comment_id = %s
        ORDER BY id {order_sql}
        LIMIT %s OFFSET %s
        """,
        (comment_id, page_size, offset),
        fetch='all'
    ) or []
    total = execute_query(
        "SELECT COUNT(*) as count FROM video_comment_messages WHERE comment_id = %s",
        (comment_id,),
        fetch='one'
    )
    return rows, row_count(total)


def get_latest_thread_message(comment_id):
    result = execute_query(
        """
        SELECT id, comment_id, author_role, author_id, body, origin, created_at
        FROM video_comment_messages
        WHERE comment_id = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (comment_id,),
        fetch='one'
    )
    return result


def get_thread_message_count(comment_id):
    result = execute_query(
        "SELECT COUNT(*) as count FROM video_comment_messages WHERE comment_id = %s",
        (comment_id,),
        fetch='one'
    )
    return row_count(result)


def get_thread_owner_id(comment_id):
    row = execute_query("SELECT user_id FROM video_comments WHERE id = %s", (comment_id,), fetch='one')
    return row['user_id'] if row else None


def reply_in_thread(comment_id, admin_id, body):
    """
    حفظ رد الأدمن في سجل المحادثة وتحديث admin_reply (آخر رد) في معاملة واحدة.

    الترتيب: آخر رد + رسالة المحادثة تُثبّت معاً، ثم تُرسل الإشعار من طبقة المعالجات.
    الاستدعاء المتكرر لنفس النص بعد إشعار فاشل لا يكرر الرسالة المحفوظة.

    Returns:
        dict: status ('ok' | 'already_answered' | 'missing' | 'error'), message_id
    """
    text = _clip_thread_body(body)
    if not text:
        return {'status': 'missing', 'message_id': None}
    with get_db_connection() as conn:
        try:
            with conn.cursor(cursor_factory=DictCursor) as c:
                c.execute("SELECT 1 FROM video_comments WHERE id = %s FOR UPDATE", (comment_id,))
                if c.fetchone() is None:
                    conn.rollback()
                    return {'status': 'missing', 'message_id': None}
                c.execute(
                    """
                    UPDATE video_comments
                    SET admin_reply = %s, replied_at = CURRENT_TIMESTAMP,
                        is_read = TRUE, thread_updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (text, comment_id)
                )
                c.execute(
                    """
                    INSERT INTO video_comment_messages (comment_id, author_role, author_id, body, origin)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (comment_id, ROLE_ADMIN, admin_id, text, THREAD_ORIGIN)
                )
                row = c.fetchone()
                conn.commit()
                return {'status': 'ok', 'message_id': (row['id'] if hasattr(row, 'keys') else row[0]) if row else None}
        except psycopg2.Error as e:
            try:
                conn.rollback()
            except Exception as rollback_error:
                logger.error(f"Database rollback failed: {rollback_error}")
            logger.error(f"Reply persistence failed. Error: {e}", exc_info=True)
            return {'status': 'error', 'message_id': None}


def mark_thread_preferred(comment_id, role):
    """تحديث علامات القراءة لطرف واحد (member/admin) أو thread_updated_at."""
    if role == ROLE_MEMBER:
        query = "UPDATE video_comments SET member_read_at = CURRENT_TIMESTAMP WHERE id = %s"
    elif role == ROLE_ADMIN:
        query = "UPDATE video_comments SET admin_read_at = CURRENT_TIMESTAMP, is_read = TRUE WHERE id = %s"
    else:
        query = "UPDATE video_comments SET thread_updated_at = CURRENT_TIMESTAMP WHERE id = %s"
    return execute_query(query, (comment_id,), commit=True)


def mark_comment_read_for_admin(comment_id):
    """تعليم التعليق كمقروء للأدمن دون المساس برسائل المحادثة."""
    return execute_query(
        "UPDATE video_comments SET is_read = TRUE, admin_read_at = CURRENT_TIMESTAMP WHERE id = %s",
        (comment_id,),
        commit=True
    )


def set_display_title(video_id, display_title):
    """
    حفظ عنوان عرض يعدّله الأدمن (لا يغيّر caption/file_name/file_id أو القناة).

    Args:
        video_id: رقم الفيديو
        display_title: العنوان الجديد (None لاستعادة العنوان الأصلي)

    Returns:
        True إذا تم التحديث
    """
    row = execute_query(
        "UPDATE video_archive SET display_title = %s WHERE id = %s RETURNING id",
        (display_title, video_id),
        fetch='one',
        commit=True
    )
    if row:
        clear_search_cache()
        return True
    return False


def get_video_title_source(video_id):
    """جلب بيانات العنوان الحالية لفيديو (للمعاينة والتحقق من الحقوق عند الحفظ)."""
    return execute_query(
        "SELECT id, caption, file_name, display_title FROM video_archive WHERE id = %s",
        (video_id,),
        fetch='one'
    )



def delete_all_comments():
    """
    حذف جميع التعليقات من قاعدة البيانات.
    
    Returns:
        عدد التعليقات المحذوفة
    """
    result = execute_query("DELETE FROM video_comments RETURNING id", fetch="all", commit=True)
    return len(result) if result else 0

def delete_user_comments(user_id):
    """
    حذف جميع تعليقات مستخدم معين.
    
    Args:
        user_id: رقم المستخدم
    
    Returns:
        عدد التعليقات المحذوفة
    """
    result = execute_query("DELETE FROM video_comments WHERE user_id = %s RETURNING id", (user_id,), fetch="all", commit=True)
    return len(result) if result else 0

def delete_old_comments(days=30):
    """
    حذف التعليقات القديمة (أقدم من عدد معين من الأيام).
    
    Args:
        days: عدد الأيام (افتراضي 30)
    
    Returns:
        عدد التعليقات المحذوفة
    """
    query = """
        DELETE FROM video_comments 
        WHERE created_at < NOW() - make_interval(days => %s)
        RETURNING id
    """
    result = execute_query(query, (days,), fetch="all", commit=True)
    return len(result) if result else 0

def get_comments_stats():
    """
    جلب إحصائيات التعليقات.
    
    Returns:
        dict مع الإحصائيات
    """
    query = """
        SELECT 
            COUNT(*) as total_comments,
            COUNT(CASE WHEN is_read = FALSE THEN 1 END) as unread_comments,
            COUNT(CASE WHEN admin_reply IS NOT NULL THEN 1 END) as replied_comments,
            COUNT(DISTINCT user_id) as unique_users
        FROM video_comments
    """
    return execute_query(query, fetch="one")

# ==============================================================================
# دوال Inline Query
# ==============================================================================

def search_videos_for_inline(query, offset=0, limit=50):
    """
    بحث محسّن للـ inline query مع دعم البحث في العنوان والوصف والتصنيف ودعم الصفحات (Pagination).
    
    Args:
        query: نص البحث
        offset: بداية النتائج (للتمرير)
        limit: عدد النتائج (افتراضي 50)
    
    Returns:
        قائمة الفيديوهات مع المعلومات الكاملة
    """
    if not query or query.strip() == "":
        # إذا كان البحث فارغاً، نعرض الفيديوهات الأكثر مشاهدة
        sql = """
            SELECT 
                v.id, v.file_id, v.caption, v.file_name, v.display_title, v.view_count,
                v.thumbnail_file_id, v.chat_id, v.message_id, v.content_type,
                c.name as category_name,
                COALESCE(AVG(r.rating), 0) as avg_rating,
                COUNT(r.rating) as rating_count
            FROM video_archive v
            LEFT JOIN categories c ON v.category_id = c.id
            LEFT JOIN video_ratings r ON v.id = r.video_id
            WHERE v.file_id IS NOT NULL 
              AND LENGTH(v.file_id) >= 20
            GROUP BY v.id, v.file_id, v.caption, v.file_name, v.display_title, v.view_count,
                     v.thumbnail_file_id, v.chat_id, v.message_id, v.content_type, c.name
            ORDER BY v.view_count DESC, avg_rating DESC
            LIMIT %s OFFSET %s
        """
        return execute_query(sql, (limit, offset), fetch="all")
    
    # بحث ذكي (Smart Search) مع 4 مستويات للأهمية:
    # 1. تطابق تام (Exact Match)
    # 2. يبدأ بـ (Starts With)
    # 3. كلمة كاملة (Full Word)
    # 4. يحتوي على (Contains)
    sql = """
        SELECT 
            v.id, v.file_id, v.caption, v.file_name, v.display_title, v.view_count,
            v.thumbnail_file_id, v.chat_id, v.message_id, v.content_type,
            c.name as category_name,
            COALESCE(AVG(r.rating), 0) as avg_rating,
            COUNT(r.rating) as rating_count
        FROM video_archive v
        LEFT JOIN categories c ON v.category_id = c.id
        LEFT JOIN video_ratings r ON v.id = r.video_id
        WHERE 
            v.file_id IS NOT NULL 
            AND LENGTH(v.file_id) >= 20
            AND (
                v.caption ILIKE %s OR 
                v.file_name ILIKE %s OR 
                v.display_title ILIKE %s OR
                c.name ILIKE %s
            )
        GROUP BY v.id, v.file_id, v.caption, v.file_name, v.display_title, v.view_count,
                 v.thumbnail_file_id, v.chat_id, v.message_id, v.content_type, c.name
        ORDER BY 
            CASE 
                WHEN v.caption ILIKE %s OR v.display_title ILIKE %s THEN 0       -- تطابق تام (الأهم)
                WHEN v.file_name ILIKE %s THEN 0
                
                WHEN v.caption ILIKE %s OR v.display_title ILIKE %s THEN 1       -- يبدأ بـ
                WHEN v.file_name ILIKE %s THEN 1
                
                WHEN v.caption ILIKE %s OR v.display_title ILIKE %s THEN 2       -- كلمة كاملة (محاطة بمسافات)
                WHEN v.file_name ILIKE %s THEN 2
                
                ELSE 3                               -- مجرد احتواء (الأقل أهمية)
            END,
            v.view_count DESC, 
            avg_rating DESC
        LIMIT %s OFFSET %s
    """
    
    # تحضير الأنماط
    exact_pattern = query                   # يوسف
    starts_with_pattern = f"{query}%"       # يوسف%
    word_pattern = f"% {query} %"           # % يوسف %
    contains_pattern = f"%{query}%"         # %يوسف%
    
    return execute_query(sql, (
        contains_pattern, contains_pattern, contains_pattern, contains_pattern,  # WHERE (للفلترة العامة)
        
        exact_pattern, exact_pattern, exact_pattern,           # Case 0: Exact (caption / display_title / file_name)
        starts_with_pattern, starts_with_pattern, starts_with_pattern, # Case 1: Starts With
        word_pattern, word_pattern, word_pattern,             # Case 2: Word Match
        
        limit, offset                           # Limit & Offset
    ), fetch="all")

def update_video_thumbnail(video_id, thumbnail_file_id):
    """
    تحديث thumbnail_file_id للفيديو.
    
    Args:
        video_id: رقم الفيديو
        thumbnail_file_id: معرف الصورة المصغرة من Telegram
    
    Returns:
        True إذا نجح التحديث، False إذا فشل
    """
    sql = "UPDATE video_archive SET thumbnail_file_id = %s WHERE id = %s"
    result = execute_query(sql, (thumbnail_file_id, video_id), commit=True)
    return result is not None

def get_videos_without_thumbnail(limit=20):
    """
    جلب الفيديوهات التي لا تملك thumbnail_file_id.
    
    Args:
        limit: عدد الفيديوهات (افتراضي 20)
    
    Returns:
        قائمة الفيديوهات
    """
    sql = """
        SELECT id, file_id, caption, file_name, chat_id, message_id
        FROM video_archive
        WHERE thumbnail_file_id IS NULL
        ORDER BY upload_date DESC
        LIMIT %s
    """
    return execute_query(sql, (limit,), fetch="all")

# --- إدارة إعدادات البوت (Bot Settings) ---

def set_bot_setting(key, value):
    """حفظ أو تحديث إعداد في جدول bot_settings"""
    sql_query = """
    INSERT INTO bot_settings (setting_key, setting_value)
    VALUES (%s, %s)
    ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value
    """
    return execute_query(sql_query, (key, value), commit=True) is not None

def get_bot_setting(key, default=None):
    """جلب قيمة إعداد من جدول bot_settings"""
    sql_query = "SELECT setting_value FROM bot_settings WHERE setting_key = %s"
    result = execute_query(sql_query, (key,), fetch="one")
    return result[0] if result else default

def set_default_thumbnail(file_id):
    """تعيين الصورة المصغرة الافتراضية للبوت"""
    return set_bot_setting('default_thumbnail_file_id', file_id)

def get_default_thumbnail():
    """جلب الصورة المصغرة الافتراضية للبوت"""
    return get_bot_setting('default_thumbnail_file_id')

# Alias للتوافق مع السكريبات الخارجية
def ensure_schema():
    """Alias لـ verify_and_repair_schema - للتوافق مع السكريبات الخارجية"""
    return verify_and_repair_schema()
