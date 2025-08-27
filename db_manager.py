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
                logger.info(f"Checking table: {table_name}")
                for column_name, column_definition in columns.items():
                    if column_name.startswith('_'):
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
                        c.execute(alter_query)
                        logger.info(f"Successfully added column '{column_name}' to '{table_name}'.")
            conn.commit()
            logger.info("Schema verification and repair process completed successfully.")
    except psycopg2.Error as e:
        logger.error(f"Schema verification error: {e}", exc_info=True)
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

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

def search_videos(query, page=0, category_id=None, quality=None, status=None):
    offset = page * VIDEOS_PER_PAGE
    search_term = f"%{query}%" if query else "%"
    base_query = """
        SELECT * FROM video_archive
        WHERE (caption ILIKE %s OR file_name ILIKE %s OR metadata->>'series_name' ILIKE %s)
    """
    params = [search_term, search_term, search_term]
    if category_id:
        base_query += " AND category_id = %s"
        params.append(category_id)
    if quality:
        base_query += " AND metadata->>'quality_resolution' = %s"
        params.append(quality)
    if status:
        base_query += " AND metadata->>'status' = %s"
        params.append(status)
    base_query += " ORDER BY upload_date DESC LIMIT %s OFFSET %s"
    params.extend([VIDEOS_PER_PAGE, offset])
    
    videos = execute_query(base_query, params, fetch="all")
    
    count_query = """
        SELECT COUNT(*) as count FROM video_archive
        WHERE (caption ILIKE %s OR file_name ILIKE %s OR metadata->>'series_name' ILIKE %s)
    """
    count_params = [search_term, search_term, search_term]
    if category_id:
        count_query += " AND category_id = %s"
        count_params.append(category_id)
    if quality:
        count_query += " AND metadata->>'quality_resolution' = %s"
        count_params.append(quality)
    if status:
        count_query += " AND metadata->>'status' = %s"
        count_params.append(status)
    total_count = execute_query(count_query, count_params, fetch="one")['count']
    
    return videos, total_count

def set_active_category(category_id):
    return execute_query("INSERT INTO settings (setting_key, setting_value) VALUES ('active_category_id', %s) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value", (str(category_id),), commit=True)

def add_video_rating(video_id, user_id, rating):
    return execute_query("INSERT INTO video_ratings (video_id, user_id, rating) VALUES (%s, %s, %s) ON CONFLICT (video_id, user_id) DO UPDATE SET rating = EXCLUDED.rating", (video_id, user_id, rating), commit=True)

def get_video_rating_stats(video_id):
    return execute_query("SELECT AVG(rating) as avg, COUNT(id) as count FROM video_ratings WHERE video_id = %s", (video_id,), fetch="one")

def get_user_video_rating(video_id, user_id):
    res = execute_query("SELECT rating FROM video_ratings WHERE video_id = %s AND user_id = %s", (video_id, user_id), fetch="one")
    return res['rating'] if res else None

def get_popular_videos():
    most_viewed = execute_query("SELECT * FROM video_archive ORDER BY view_count DESC, id DESC LIMIT 10", fetch="all")
    highest_rated = execute_query("SELECT v.*, r.avg_rating FROM video_archive v JOIN (SELECT video_id, AVG(rating) as avg_rating FROM video_ratings GROUP BY video_id) r ON v.id = r.video_id ORDER BY r.avg_rating DESC, v.view_count DESC LIMIT 10", fetch="all")
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
    stats['total_views'] = (execute_query("SELECT SUM(view_count) as sum FROM video_archive", fetch="one") or {'sum': 0})['sum'] or 0
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
