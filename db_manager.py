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
    'categories': {
        'id': 'SERIAL PRIMARY KEY',
        'name': 'VARCHAR(255) NOT NULL',
        'parent_id': 'INTEGER REFERENCES categories(id) ON DELETE CASCADE'
    },
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
        'upload_date': 'TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP',
        'grouping_key': 'TEXT'
    },
    'video_ratings': {
        'id': 'SERIAL PRIMARY KEY',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'user_id': 'BIGINT',
        'rating': 'INTEGER CHECK (rating >= 1 AND rating <= 5)',
        '_unique_rating': 'UNIQUE (video_id, user_id)'
    },
    'bot_users': {
        'user_id': 'BIGINT PRIMARY KEY',
        'username': 'VARCHAR(255)',
        'first_name': 'VARCHAR(255)',
        'join_date': 'TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP'
    },
    'bot_settings': {
        'setting_key': 'VARCHAR(255) PRIMARY KEY',
        'setting_value': 'TEXT'
    },
    'required_channels': {
        'channel_id': 'VARCHAR(255) PRIMARY KEY',
        'channel_name': 'VARCHAR(255) NOT NULL'
    },
    'user_favorites': {
        'id': 'SERIAL PRIMARY KEY',
        'user_id': 'BIGINT REFERENCES bot_users(user_id) ON DELETE CASCADE',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'added_date': 'TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP',
        '_unique_favorite': 'UNIQUE (user_id, video_id)'
    },
    'user_watch_history': {
        'id': 'SERIAL PRIMARY KEY',
        'user_id': 'BIGINT REFERENCES bot_users(user_id) ON DELETE CASCADE',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'last_watched_date': 'TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP',
        '_unique_history': 'UNIQUE (user_id, video_id)'
    },
    'user_states': {
        'user_id': 'BIGINT PRIMARY KEY REFERENCES bot_users(user_id) ON DELETE CASCADE',
        'state': 'VARCHAR(255) NOT NULL',
        'context': 'JSONB'
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
            # First, create tables if they don't exist
            for table_name, columns in EXPECTED_SCHEMA.items():
                c.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table_name,))
                if c.fetchone() is None:
                    logger.warning(f"Table '{table_name}' not found. Creating it now.")
                    cols_sql = ", ".join([f"{col_name} {col_def}" for col_name, col_def in columns.items()])
                    create_table_query = sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(table_name), sql.SQL(cols_sql))
                    c.execute(create_table_query)
                    logger.info(f"Table '{table_name}' created successfully.")
                else:
                    # If table exists, check for missing columns
                    for column_name, column_definition in columns.items():
                        if column_name.startswith('_'): # Skip constraints defined as columns
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
            
            # After schema is stable, create indexes
            create_indexes()

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

# ==============================================================================
# دالة بحث مطورة لتدعم الفلاتر المتقدمة
# ==============================================================================
def search_videos(query, page=0, category_id=None, quality=None, status=None, 
                 date_from=None, date_to=None, duration_min=None, duration_max=None,
                 file_size_min=None, file_size_max=None, sort_by='upload_date', sort_order='DESC'):
    """
    Advanced video search with multiple filters.
    
    Args:
        query: Search term for caption and filename
        page: Page number for pagination
        category_id: Filter by category
        quality: Filter by video quality (e.g., '720p', '1080p')
        status: Filter by video status
        date_from: Filter videos uploaded after this date (YYYY-MM-DD)
        date_to: Filter videos uploaded before this date (YYYY-MM-DD)
        duration_min: Minimum video duration in seconds
        duration_max: Maximum video duration in seconds
        file_size_min: Minimum file size in bytes
        file_size_max: Maximum file size in bytes
        sort_by: Sort field ('upload_date', 'view_count', 'rating')
        sort_order: Sort order ('ASC' or 'DESC')
    """
    offset = page * VIDEOS_PER_PAGE
    search_term = f"%{query}%"

    # بناء جملة WHERE بشكل ديناميكي
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
    if date_from:
        where_clauses.append("upload_date >= %s")
        params.append(date_from)
    if date_to:
        where_clauses.append("upload_date <= %s")
        params.append(date_to)
    if duration_min:
        where_clauses.append("CAST(metadata->>'duration' AS INTEGER) >= %s")
        params.append(duration_min)
    if duration_max:
        where_clauses.append("CAST(metadata->>'duration' AS INTEGER) <= %s")
        params.append(duration_max)
    if file_size_min:
        where_clauses.append("CAST(metadata->>'file_size' AS BIGINT) >= %s")
        params.append(file_size_min)
    if file_size_max:
        where_clauses.append("CAST(metadata->>'file_size' AS BIGINT) <= %s")
        params.append(file_size_max)

    where_string = " AND ".join(where_clauses)

    # بناء جملة ORDER BY
    valid_sort_fields = {'upload_date': 'upload_date', 'view_count': 'view_count', 'rating': 'avg_rating'}
    sort_field = valid_sort_fields.get(sort_by, 'upload_date')
    sort_order = 'DESC' if sort_order.upper() == 'DESC' else 'ASC'
    
    if sort_by == 'rating':
        # استعلام مع التقييمات
        videos_query = f"""
            SELECT v.*, COALESCE(r.avg_rating, 0) as avg_rating
            FROM video_archive v
            LEFT JOIN (
                SELECT video_id, AVG(rating) as avg_rating
                FROM video_ratings
                GROUP BY video_id
            ) r ON v.id = r.video_id
            WHERE {where_string}
            ORDER BY {sort_field} {sort_order}, v.id DESC
            LIMIT %s OFFSET %s
        """
    else:
        videos_query = f"""
            SELECT * FROM video_archive 
            WHERE {where_string} 
            ORDER BY {sort_field} {sort_order}, id DESC 
            LIMIT %s OFFSET %s
        """
    
    final_params_videos = tuple(params + [VIDEOS_PER_PAGE, offset])
    videos = execute_query(videos_query, final_params_videos, fetch="all")

    # استعلام جلب العدد الإجمالي
    count_query = f"SELECT COUNT(*) as count FROM video_archive WHERE {where_string}"
    final_params_count = tuple(params)
    total = execute_query(count_query, final_params_count, fetch="one")

    return videos, total['count'] if total else 0


def get_videos_by_date_range(days_back=7, page=0):
    """Gets videos uploaded within the specified number of days."""
    offset = page * VIDEOS_PER_PAGE
    query = """
        SELECT * FROM video_archive 
        WHERE upload_date >= CURRENT_DATE - INTERVAL '%s days'
        ORDER BY upload_date DESC 
        LIMIT %s OFFSET %s
    """
    videos = execute_query(query, (days_back, VIDEOS_PER_PAGE, offset), fetch="all")
    
    count_query = """
        SELECT COUNT(*) as count FROM video_archive 
        WHERE upload_date >= CURRENT_DATE - INTERVAL '%s days'
    """
    total = execute_query(count_query, (days_back,), fetch="one")
    
    return videos, total['count'] if total else 0


def get_video_metadata_options():
    """Gets available metadata options for filtering."""
    quality_query = """
        SELECT DISTINCT metadata->>'quality_resolution' as quality
        FROM video_archive 
        WHERE metadata->>'quality_resolution' IS NOT NULL
        ORDER BY quality
    """
    
    status_query = """
        SELECT DISTINCT metadata->>'status' as status
        FROM video_archive 
        WHERE metadata->>'status' IS NOT NULL
        ORDER BY status
    """
    
    qualities = execute_query(quality_query, fetch="all")
    statuses = execute_query(status_query, fetch="all")
    
    return {
        'qualities': [q['quality'] for q in qualities if q['quality']],
        'statuses': [s['status'] for s in statuses if s['status']]
    }

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

def get_categories_tree():
    return execute_query("SELECT * FROM categories ORDER BY name", fetch="all")

def get_child_categories(parent_id):
    if parent_id is None:
        return execute_query("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY name", fetch="all")
    return execute_query("SELECT * FROM categories WHERE parent_id = %s ORDER BY name", (parent_id,), fetch="all")

def get_category_by_id(category_id):
    return execute_query("SELECT * FROM categories WHERE id = %s", (category_id,), fetch="one")

def add_category(name, parent_id=None):
    res = execute_query("INSERT INTO categories (name, parent_id) VALUES (%s, %s) RETURNING id", (name, parent_id), fetch="one", commit=True)
    return (True, res) if res else (False, "Failed to add category")

def get_videos(category_id, page=0):
    offset = page * VIDEOS_PER_PAGE
    videos = execute_query("SELECT * FROM video_archive WHERE category_id = %s ORDER BY id DESC LIMIT %s OFFSET %s", (category_id, VIDEOS_PER_PAGE, offset), fetch="all")
    total = execute_query("SELECT COUNT(*) as count FROM video_archive WHERE category_id = %s", (category_id,), fetch="one")
    return videos, total['count'] if total else 0

def increment_video_view_count(video_id):
    return execute_query("UPDATE video_archive SET view_count = view_count + 1 WHERE id = %s", (video_id,), commit=True)

def get_video_by_message_id(message_id):
    return execute_query("SELECT * FROM video_archive WHERE message_id = %s", (message_id,), fetch="one")

def get_active_category_id():
    res = execute_query("SELECT setting_value FROM bot_settings WHERE setting_key = 'active_category_id'", fetch="one")
    return int(res['setting_value']) if res else None

def set_active_category_id(category_id):
    return execute_query("INSERT INTO bot_settings (setting_key, setting_value) VALUES ('active_category_id', %s) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value", (str(category_id),), commit=True)

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


def set_user_state(user_id, state, context=None):
    """Sets or updates the state for a given user."""
    # Ensure the user exists in bot_users first
    execute_query(
        "INSERT INTO bot_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
        (user_id,),
        commit=True
    )
    query = """
        INSERT INTO user_states (user_id, state, context)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE
        SET state = EXCLUDED.state, context = EXCLUDED.context;
    """
    params = (user_id, state, json.dumps(context) if context else None)
    execute_query(query, params, commit=True)
    logger.info(f"Set state for user {user_id} to '{state}' with context: {context}")


def get_user_state(user_id):
    """Retrieves the state for a given user."""
    query = "SELECT state, context FROM user_states WHERE user_id = %s"
    return execute_query(query, (user_id,), fetch="one")


def clear_user_state(user_id):
    """Clears the state for a given user."""
    query = "DELETE FROM user_states WHERE user_id = %s"
    execute_query(query, (user_id,), commit=True)
    logger.info(f"Cleared state for user {user_id}")


def add_to_favorites(user_id, video_id):
    """Adds a video to user's favorites."""
    # Ensure the user exists in bot_users first
    execute_query(
        "INSERT INTO bot_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
        (user_id,),
        commit=True
    )
    query = """
        INSERT INTO user_favorites (user_id, video_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id, video_id) DO NOTHING
    """
    return execute_query(query, (user_id, video_id), commit=True)


def remove_from_favorites(user_id, video_id):
    """Removes a video from user's favorites."""
    query = "DELETE FROM user_favorites WHERE user_id = %s AND video_id = %s"
    return execute_query(query, (user_id, video_id), commit=True)


def get_user_favorites(user_id, page=0):
    """Gets user's favorite videos with pagination."""
    offset = page * VIDEOS_PER_PAGE
    query = """
        SELECT v.*, f.added_date
        FROM video_archive v
        JOIN user_favorites f ON v.id = f.video_id
        WHERE f.user_id = %s
        ORDER BY f.added_date DESC
        LIMIT %s OFFSET %s
    """
    videos = execute_query(query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    
    count_query = "SELECT COUNT(*) as count FROM user_favorites WHERE user_id = %s"
    total = execute_query(count_query, (user_id,), fetch="one")
    
    return videos, total['count'] if total else 0


def is_video_favorite(user_id, video_id):
    """Checks if a video is in user's favorites."""
    query = "SELECT 1 FROM user_favorites WHERE user_id = %s AND video_id = %s"
    result = execute_query(query, (user_id, video_id), fetch="one")
    return result is not None


def add_to_watch_history(user_id, video_id):
    """Adds or updates a video in user's watch history."""
    # Ensure the user exists in bot_users first
    execute_query(
        "INSERT INTO bot_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
        (user_id,),
        commit=True
    )
    query = """
        INSERT INTO user_watch_history (user_id, video_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id, video_id) DO UPDATE
        SET last_watched_date = CURRENT_TIMESTAMP
    """
    return execute_query(query, (user_id, video_id), commit=True)


def get_user_watch_history(user_id, page=0):
    """Gets user's watch history with pagination."""
    offset = page * VIDEOS_PER_PAGE
    query = """
        SELECT v.*, h.last_watched_date
        FROM video_archive v
        JOIN user_watch_history h ON v.id = h.video_id
        WHERE h.user_id = %s
        ORDER BY h.last_watched_date DESC
        LIMIT %s OFFSET %s
    """
    videos = execute_query(query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    
    count_query = "SELECT COUNT(*) as count FROM user_watch_history WHERE user_id = %s"
    total = execute_query(count_query, (user_id,), fetch="one")
    
    return videos, total['count'] if total else 0


def get_last_watched_video(user_id):
    """Gets the last video watched by the user."""
    query = """
        SELECT v.*, h.last_watched_date
        FROM video_archive v
        JOIN user_watch_history h ON v.id = h.video_id
        WHERE h.user_id = %s
        ORDER BY h.last_watched_date DESC
        LIMIT 1
    """
    return execute_query(query, (user_id,), fetch="one")


def get_recommended_videos(user_id, limit=10):
    """Gets recommended videos for a user based on their history and ratings."""
    # Simple recommendation based on:
    # 1. Categories the user watches frequently
    # 2. Highly rated videos in those categories
    # 3. Popular videos overall
    
    query = """
        WITH user_categories AS (
            SELECT v.category_id, COUNT(*) as watch_count
            FROM user_watch_history h
            JOIN video_archive v ON h.video_id = v.id
            WHERE h.user_id = %s AND v.category_id IS NOT NULL
            GROUP BY v.category_id
            ORDER BY watch_count DESC
            LIMIT 3
        ),
        category_recommendations AS (
            SELECT DISTINCT v.*, 
                   COALESCE(r.avg_rating, 0) as avg_rating,
                   v.view_count
            FROM video_archive v
            LEFT JOIN (
                SELECT video_id, AVG(rating) as avg_rating
                FROM video_ratings
                GROUP BY video_id
            ) r ON v.id = r.video_id
            WHERE v.category_id IN (SELECT category_id FROM user_categories)
            AND v.id NOT IN (
                SELECT video_id FROM user_watch_history WHERE user_id = %s
            )
        ),
        popular_recommendations AS (
            SELECT DISTINCT v.*,
                   COALESCE(r.avg_rating, 0) as avg_rating,
                   v.view_count
            FROM video_archive v
            LEFT JOIN (
                SELECT video_id, AVG(rating) as avg_rating
                FROM video_ratings
                GROUP BY video_id
            ) r ON v.id = r.video_id
            WHERE v.id NOT IN (
                SELECT video_id FROM user_watch_history WHERE user_id = %s
            )
            ORDER BY v.view_count DESC, r.avg_rating DESC
            LIMIT %s
        )
        SELECT * FROM category_recommendations
        UNION ALL
        SELECT * FROM popular_recommendations
        ORDER BY avg_rating DESC, view_count DESC
        LIMIT %s
    """
    return execute_query(query, (user_id, user_id, user_id, limit, limit), fetch="all")

def create_indexes():
    """Create indexes for the database to improve performance."""
    logger.info("Creating database indexes if they don't exist...")
    
    # List of indexes to create: (table, index_name, columns, type)
    indexes = [
        ('video_archive', 'idx_video_archive_category_id', '(category_id)', 'BTREE'),
        ('video_archive', 'idx_video_archive_view_count', '(view_count DESC)', 'BTREE'),
        ('video_archive', 'idx_video_archive_upload_date', '(upload_date DESC)', 'BTREE'),
        ('video_archive', 'idx_video_archive_grouping_key', '(grouping_key)', 'BTREE'),
        ('video_archive', 'idx_video_archive_metadata', '(metadata)', 'GIN'), # For JSONB
        ('categories', 'idx_categories_parent_id', '(parent_id)', 'BTREE'),
        ('video_ratings', 'idx_video_ratings_video_user', '(video_id, user_id)', 'BTREE'),
        ('user_favorites', 'idx_user_favorites_user_video', '(user_id, video_id)', 'BTREE'),
        ('user_watch_history', 'idx_user_watch_history_user_video', '(user_id, video_id)', 'BTREE'),
        ('video_archive', 'idx_video_archive_caption_trgm', 'USING gin(caption gin_trgm_ops)', ''), # For ILIKE
        ('video_archive', 'idx_video_archive_filename_trgm', 'USING gin(file_name gin_trgm_ops)', '') # For ILIKE
    ]

    conn = get_db_connection()
    if not conn:
        logger.error("Cannot create indexes, no DB connection.")
        return

    try:
        with conn.cursor() as c:
            # Enable pg_trgm extension for faster ILIKE searches
            c.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            logger.info("Ensured pg_trgm extension is enabled.")

            for table, index_name, columns, index_type in indexes:
                # Check if the index already exists
                c.execute("SELECT 1 FROM pg_class WHERE relname = %s;", (index_name,))
                if c.fetchone():
                    # logger.info(f"Index '{index_name}' already exists.")
                    continue
                
                logger.info(f"Creating index '{index_name}' on table '{table}'.")
                if index_type: # Handle BTREE and GIN
                    query = sql.SQL("CREATE INDEX {} ON {} USING {} {}").format(
                        sql.Identifier(index_name),
                        sql.Identifier(table),
                        sql.SQL(index_type),
                        sql.SQL(columns)
                    )
                else: # Handle custom definitions like gin_trgm_ops
                    query = sql.SQL("CREATE INDEX {} ON {} {}").format(
                        sql.Identifier(index_name),
                        sql.Identifier(table),
                        sql.SQL(columns)
                    )
                c.execute(query)
                logger.info(f"Index '{index_name}' created successfully.")
            conn.commit()
            logger.info("Finished creating indexes.")
    except psycopg2.Error as e:
        logger.error(f"Index creation failed: {e}", exc_info=True)
        if conn: conn.rollback()
    finally:
        if conn: conn.close()
