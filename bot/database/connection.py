#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/database/connection.py
# الوصف: إدارة اتصالات قاعدة البيانات مع Connection Pooling
# ==============================================================================

import logging
import os
import threading
from contextlib import contextmanager
from typing import Optional, Any, List, Dict

import psycopg2
import psycopg2.pool
from psycopg2.extras import DictCursor
from psycopg2 import sql

from bot.core.config import settings

logger = logging.getLogger(__name__)

# --- Connection Pool Singleton ---
_connection_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()
_pool_pid: Optional[int] = None  # PID of the process that created the pool


def get_connection_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """إنشاء أو إرجاع connection pool (Thread-safe, Fork-safe Singleton)"""
    global _connection_pool, _pool_pid
    current_pid = os.getpid()

    # كشف fork(): إذا كان الـ Pool أُنشئ في process آخر (Master)
    # يجب إعادة إنشائه لأن اتصالات SSL لا تنجو من fork()
    if _connection_pool is not None and _pool_pid != current_pid:
        logger.info(f"🔄 Pool created in PID {_pool_pid}, now in PID {current_pid} — recreating (fork detected)")
        try:
            _connection_pool.closeall()
        except Exception:
            pass
        _connection_pool = None

    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                try:
                    _connection_pool = psycopg2.pool.ThreadedConnectionPool(
                        settings.db.pool_min,
                        settings.db.pool_max,
                        **settings.db.connection_params
                    )
                    _pool_pid = current_pid
                    logger.info(
                        f"✅ Database connection pool created "
                        f"(min={settings.db.pool_min}, max={settings.db.pool_max}, pid={current_pid})"
                    )
                except Exception as e:
                    logger.critical(f"❌ Failed to create connection pool: {e}")
                    raise
    return _connection_pool


@contextmanager
def get_db_connection():
    """
    Context manager للحصول على اتصال من الـ pool.

    Usage:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
    """
    pool = get_connection_pool()
    conn = None
    try:
        conn = pool.getconn()
        if conn is None:
            raise psycopg2.OperationalError("Could not get connection from pool")
        yield conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise
    finally:
        if conn is not None:
            pool.putconn(conn)


def execute_query(
    query: str,
    params: tuple = None,
    fetch: Optional[str] = None,
    commit: bool = False
) -> Any:
    """
    تنفيذ استعلام SQL مع استخدام connection pool.

    Args:
        query: جملة SQL
        params: معاملات الاستعلام
        fetch: "one" أو "all" أو None
        commit: هل يجب عمل commit

    Returns:
        نتيجة الاستعلام حسب نوع الـ fetch
    """
    result = None
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cursor:
                cursor.execute(query, params)

                if fetch == "one":
                    result = cursor.fetchone()
                elif fetch == "all":
                    result = cursor.fetchall()

                if commit:
                    conn.commit()
                    if fetch is None:
                        result = True

    except psycopg2.Error as e:
        logger.error(f"Database query failed: {e}", exc_info=True)
        if fetch == "all":
            return []
        return None if fetch else False

    return result


# ==============================================================================
# Schema Management
# ==============================================================================

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
        'content_type': 'TEXT DEFAULT NULL'
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
        'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
    }
}


def verify_and_repair_schema():
    """التحقق من صحة هيكل قاعدة البيانات وإصلاحه"""
    logger.info("🔍 Verifying database schema...")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                for table_name, columns in EXPECTED_SCHEMA.items():
                    # إنشاء الجدول إذا لم يكن موجوداً
                    try:
                        cursor.execute(
                            sql.SQL("CREATE TABLE IF NOT EXISTS {} (id SERIAL PRIMARY KEY)").format(
                                sql.Identifier(table_name)
                            )
                        )
                    except psycopg2.ProgrammingError:
                        conn.rollback()

                    # التحقق من الأعمدة
                    for col_name, col_def in columns.items():
                        if col_name.startswith('_') or col_name == 'id':
                            continue

                        cursor.execute("""
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = %s AND column_name = %s
                        """, (table_name, col_name))

                        if cursor.fetchone() is None:
                            logger.warning(f"Adding column '{col_name}' to '{table_name}'")
                            try:
                                alter = sql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(
                                    sql.Identifier(table_name),
                                    sql.Identifier(col_name),
                                    sql.SQL(col_def)
                                )
                                cursor.execute(alter)
                            except Exception as e:
                                logger.error(f"Error adding column {col_name}: {e}")
                                conn.rollback()

                    # إضافة قيود UNIQUE
                    if '_UNIQUE_CONSTRAINT' in columns:
                        constraint_def = columns['_UNIQUE_CONSTRAINT']
                        constraint_name = f"{table_name}_unique_constraint"
                        cursor.execute("""
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE table_name = %s AND constraint_name = %s
                        """, (table_name, constraint_name))

                        if cursor.fetchone() is None:
                            try:
                                cursor.execute(sql.SQL(
                                    f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} {constraint_def}"
                                ))
                            except Exception as e:
                                logger.error(f"Error adding constraint: {e}")
                                conn.rollback()

                conn.commit()
                logger.info("✅ Database schema verification completed")

    except Exception as e:
        logger.error(f"Schema verification error: {e}", exc_info=True)
        raise


def close_pool():
    """إغلاق الـ connection pool"""
    global _connection_pool
    if _connection_pool:
        _connection_pool.closeall()
        _connection_pool = None
        logger.info("Database connection pool closed")
