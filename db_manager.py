# ==============================================================================
# تحديثات على db_manager.py للاستفادة من الفهارس وإضافة / إصلاحات طفيفة
# ==============================================================================

import psycopg2
from psycopg2 import sql
from psycopg2.extras import DictCursor
import os
from urllib.parse import urlparse
import logging
import json

logger = logging.getLogger(__name__)

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

VIDEOS_PER_PAGE = 10
CALLBACK_DELIMITER = "::"
admin_steps = {}
user_last_search = {}

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
    }
}


def get_db_connection():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        logger.error(f"Database connection failed: {e}")
        return None


def verify_and_repair_schema():
    logger.info("Verifying and repairing database schema...")
    conn = get_db_connection()
    if not conn:
        logger.critical("Cannot verify schema, no DB connection.")
        return

    try:
        with conn.cursor() as c:
            for table_name, columns in EXPECTED_SCHEMA.items():
                create_table_query = sql.SQL(f"CREATE TABLE IF NOT EXISTS {table_name} (id SERIAL PRIMARY KEY)")
                try:
                    c.execute(create_table_query)
                except psycopg2.ProgrammingError:
                    pass

                logger.info(f"Checking table: {table_name}")
                for column_name, column_definition in columns.items():
                    if column_name.startswith('_'):
                        continue

                    c.execute("""
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = %s AND column_name = %s
                    """, (table_name, column_name))

                    if c.fetchone() is None:
                        if column_name == 'id':
                            # تحقق من وجود مفتاح أساسي بدلاً من محاولة إضافة id كل مرة
                            c.execute("""
                                SELECT kcu.column_name
                                FROM information_schema.table_constraints tc
                                JOIN information_schema.key_column_usage kcu 
                                  ON tc.constraint_name = kcu.constraint_name
                                WHERE tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'
                            """, (table_name,))
                            if c.fetchone():
                                logger.warning(f"Skipping redundant creation of id in {table_name}.")
                                continue
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
        if conn: conn.rollback()
    finally:
        if conn: conn.close()


# ===================== تحسين البحث =====================

def search_videos(query, page=0, category_id=None, quality=None, status=None, order_by="recent"):
    offset = page * VIDEOS_PER_PAGE
    params = []

    where_parts = []
    if query:
        # يدعم pg_trgm عبر ILIKE + فهارس GIN التي أضفناها
        where_parts.append("(caption ILIKE %s OR file_name ILIKE %s)")
        like = f"%{query}%"
        params += [like, like]

    if category_id:
        where_parts.append("category_id = %s")
        params.append(category_id)

    if quality:
        where_parts.append("metadata->>'quality_resolution' = %s")
        params.append(quality)

    if status:
        where_parts.append("metadata->>'status' = %s")
        params.append(status)

    where_sql = " AND ".join(where_parts) if where_parts else "TRUE"

    # ترتيب يستفيد من الفهارس الجديدة
    if order_by == "popular":
        order_sql = "ORDER BY view_count DESC, id DESC"
    elif order_by == "recent":
        order_sql = "ORDER BY upload_date DESC, id DESC"
    else:
        order_sql = "ORDER BY id DESC"

    videos_sql = f"""
        SELECT * FROM video_archive
        WHERE {where_sql}
        {order_sql}
        LIMIT %s OFFSET %s
    """
    videos_params = tuple(params + [VIDEOS_PER_PAGE, offset])
    videos = execute_query(videos_sql, videos_params, fetch="all")

    count_sql = f"SELECT COUNT(*) as count FROM video_archive WHERE {where_sql}"
    total = execute_query(count_sql, tuple(params), fetch="one")
    return videos, total['count'] if total else 0


def execute_query(query, params=None, fetch=None, commit=False):
    conn = get_db_connection()
    if not conn:
        if fetch == "all": return []
        return None if fetch else False

    result = None
    try:
        with conn.cursor(cursor_factory=DictCursor) as c:
            c.execute(query, params)
            if fetch == "one": result = c.fetchone()
            elif fetch == "all": result = c.fetchall()
            if commit:
                conn.commit()
                if fetch is None: result = True
    except psycopg2.Error as e:
        logger.error(f"Database query failed. Error: {e}", exc_info=True)
        if conn: conn.rollback()
        if fetch == "all": return []
        return None if fetch else False
    finally:
        if conn: conn.close()
    return result
