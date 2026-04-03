# Patch: restore missing functions needed by handlers
import psycopg2
from psycopg2.extras import DictCursor
from urllib.parse import urlparse
import os, logging, json

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')
r = urlparse(DATABASE_URL)
DB_CONFIG = {'user': r.username, 'password': r.password, 'host': r.hostname, 'port': r.port, 'dbname': r.path[1:]}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

def execute_query(query, params=None, fetch=None, commit=False):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=DictCursor) as c:
            c.execute(query, params)
            if commit:
                conn.commit()
            if fetch == 'one':
                return c.fetchone()
            if fetch == 'all':
                return c.fetchall()
            return True if commit else None
    except Exception as e:
        logger.error(f"DB error: {e}")
        if conn: conn.rollback()
        return [] if fetch == 'all' else None
    finally:
        if conn: conn.close()

# Restored functions

def add_bot_user(user_id, username, first_name):
    return execute_query(
        "INSERT INTO bot_users (user_id, username, first_name) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
        (user_id, username, first_name), commit=True)

def get_random_video():
    return execute_query("SELECT * FROM video_archive ORDER BY RANDOM() LIMIT 1", fetch='one')

def increment_video_view_count(video_id):
    return execute_query("UPDATE video_archive SET view_count = view_count + 1 WHERE id = %s", (video_id,), commit=True)

def get_categories_tree():
    # Return all categories ordered; filtering is handled at UI level
    return execute_query("SELECT * FROM categories ORDER BY name", fetch='all')

def add_video(message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id=None):
    metadata_json = json.dumps(metadata)
    row = execute_query(
        """
        INSERT INTO video_archive (message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (message_id) DO UPDATE SET caption=EXCLUDED.caption,file_name=EXCLUDED.file_name,file_id=EXCLUDED.file_id,metadata=EXCLUDED.metadata,grouping_key=EXCLUDED.grouping_key,category_id=EXCLUDED.category_id
        RETURNING id
        """,
        (message_id, caption, chat_id, file_name, file_id, metadata_json, grouping_key, category_id), fetch='one', commit=True)
    return row['id'] if row else None
