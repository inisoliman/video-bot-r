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
    'required_channels': {
        'channel_id': 'TEXT PRIMARY KEY',
        'channel_name': 'TEXT'
    },
    'video_ratings': {
        'id': 'SERIAL PRIMARY KEY',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'user_id': 'BIGINT',
        'rating': 'INTEGER NOT NULL',
        'rating_date': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
    },
    'user_states': {
        'user_id': 'BIGINT PRIMARY KEY',
        'state': 'TEXT NOT NULL',
        'context': 'JSONB',
        'last_update': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
    },
    # --- [جدول جديد] المفضلة ---
    'user_favorites': {
        'id': 'SERIAL PRIMARY KEY',
        'user_id': 'BIGINT',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'added_date': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
        'UNIQUE_CONSTRAINT': 'UNIQUE (user_id, video_id)' # ضمان عدم تكرار الفيديو في المفضلة
    },
    # --- [جدول جديد] سجل المشاهدة ---
    'user_history': {
        'id': 'SERIAL PRIMARY KEY',
        'user_id': 'BIGINT',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'last_viewed': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
        'UNIQUE_CONSTRAINT': 'UNIQUE (user_id, video_id)' # تحديث السجل بدلاً من إضافة صف جديد
    }
}

def get_db_connection():
    """تأسيس اتصال قاعدة البيانات"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return None

def execute_query(query, params=None, fetch="none", commit=False):
    """تنفيذ استعلام قاعدة بيانات مع التعامل مع الأخطاء."""
    conn = get_db_connection()
    if conn is None:
        return None

    result = None
    try:
        with conn.cursor(cursor_factory=DictCursor) as c:
            # [تعديل] التعامل مع UNIQUE_CONSTRAINT عند إنشاء الجداول الجديدة
            if 'UNIQUE_CONSTRAINT' in query:
                # هذا جزء خاص بإنشاء القيود، يتم تجاهله في التنفيذ العادي
                pass 
            
            c.execute(query, params)
            
            if commit:
                conn.commit()
            
            if fetch == "one":
                result = c.fetchone()
                return dict(result) if result else None
            elif fetch == "all":
                results = c.fetchall()
                return [dict(row) for row in results]
            return True

    except psycopg2.errors.NotNullViolation as e:
        logger.error(f"Database query failed. Error: {e}", exc_info=True)
        if conn: conn.rollback()
        return None
    except Exception as e:
        logger.error(f"Database query failed. Error: {e}", exc_info=True)
        if conn: conn.rollback()
        return None
    finally:
        if conn:
            conn.close()

# --- دالة فحص وتصحيح المخطط (Schema) [مهمة لإضافة الجداول الجديدة] ---
def verify_and_repair_schema():
    conn = get_db_connection()
    if not conn:
        logger.critical("Could not verify schema: No database connection.")
        return
    
    try:
        with conn.cursor() as c:
            for table_name, columns in EXPECTED_SCHEMA.items():
                logger.info(f"Verifying table: {table_name}")
                
                # 1. التحقق من وجود الجدول وإنشاءه إذا لم يكن موجودًا
                c.execute(f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{table_name}');")
                if not c.fetchone()[0]:
                    logger.warning(f"Table {table_name} missing. Creating...")
                    
                    # تنقية تعريف الأعمدة من القيود الخاصة مثل UNIQUE_CONSTRAINT
                    cols_def = ', '.join([f'{col} {dtype}' for col, dtype in columns.items() if col != 'UNIQUE_CONSTRAINT'])
                    
                    # إضافة القيود (مثل UNIQUE) بشكل منفصل بعد إنشاء الجدول
                    unique_constraint = columns.get('UNIQUE_CONSTRAINT', '')
                    if unique_constraint:
                        # يتم إنشاء الجدول بدون القيود أولاً
                        c.execute(f"CREATE TABLE {table_name} ({cols_def})")
                        conn.commit()
                        logger.info(f"Table {table_name} created successfully.")
                        
                        # ثم إضافة القيد الفريد إذا كان موجودًا
                        constraint_name = f'uc_{table_name}'
                        try:
                            c.execute(f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} {unique_constraint}")
                            conn.commit()
                            logger.info(f"Added UNIQUE constraint to {table_name}.")
                        except Exception as e:
                            logger.warning(f"Failed to add UNIQUE constraint to {table_name}: {e}")
                            conn.rollback()
                    else:
                        c.execute(f"CREATE TABLE {table_name} ({cols_def})")
                        conn.commit()
                        logger.info(f"Table {table_name} created successfully.")
                    
                    continue # ننتقل للجدول التالي بعد الإنشاء الكامل

                # 2. التحقق من الأعمدة المفقودة (نفس المنطق السابق)
                for col_name, col_type in columns.items():
                    if col_name == 'UNIQUE_CONSTRAINT': continue
                    col_def_stripped = col_type.split('PRIMARY KEY')[0].split('REFERENCES')[0].split('UNIQUE')[0].strip()
                    
                    try:
                        c.execute(f"SELECT data_type FROM information_schema.columns WHERE table_name = '{table_name}' AND column_name = '{col_name}'")
                        if c.fetchone() is None:
                            logger.warning(f"Column {col_name} in {table_name} missing. Adding...")
                            
                            c.execute(sql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(
                                sql.Identifier(table_name),
                                sql.Identifier(col_name),
                                sql.SQL(col_def_stripped)
                            ))
                            logger.info(f"Column {col_name} added to {table_name}.")
                            conn.commit()
                    except Exception as e:
                        if 'already exists' in str(e).lower() or 'multiple primary keys' in str(e).lower():
                            logger.warning(f"Skipping redundant column/constraint addition for {col_name} in {table_name}.")
                            conn.rollback() 
                        else:
                            logger.error(f"Error adding column {col_name} to {table_name}: {e}")
                            conn.rollback()
                            
            conn.commit()
            logger.info("Schema verification and repair process completed successfully.")

    except Exception as e:
        logger.critical(f"FATAL SCHEMA ERROR: {e}")
        if conn: conn.rollback()
    finally:
        if conn:
            conn.close()

# ==============================================================================
# دوال إدارة الفيديوهات والتصنيفات
# ==============================================================================
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
    
def get_categories_tree():
    return execute_query("SELECT * FROM categories ORDER BY name", fetch="all")

def get_child_categories(parent_id):
    if parent_id is None:
        return execute_query("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY name", fetch="all")
    return execute_query("SELECT * FROM categories WHERE parent_id = %s ORDER BY name", (parent_id,), fetch="all")

def get_category_by_id(category_id):
    if category_id is None or (isinstance(category_id, str) and not category_id.isdigit()):
        return None
    return execute_query("SELECT * FROM categories WHERE id = %s", (category_id,), fetch="one")

def add_category(name, parent_id=None):
    full_path = name
    if parent_id is not None:
        parent_category = get_category_by_id(parent_id)
        if parent_category and parent_category.get('full_path'):
            full_path = f"{parent_category['full_path']}/{name}"
        elif parent_category:
             # Fallback if parent exists but somehow lost full_path
            full_path = f"{parent_category['name']}/{name}"
        else:
            # If parent_id is provided but category not found, treat as root to avoid NullViolation
            full_path = name 
    
    query = "INSERT INTO categories (name, parent_id, full_path) VALUES (%s, %s, %s) RETURNING id"
    params = (name, parent_id, full_path)
    
    res = execute_query(query, params, fetch="one", commit=True)
    return (True, res['id']) if res else (False, "Failed to add category")

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

# --- دوال المفضلة (Favorites) ---

def is_video_favorite(user_id, video_id):
    """التحقق مما إذا كان الفيديو مفضلاً لدى المستخدم."""
    query = "SELECT 1 FROM user_favorites WHERE user_id = %s AND video_id = %s"
    return execute_query(query, (user_id, video_id), fetch="one") is not None

def add_to_favorites(user_id, video_id):
    """إضافة فيديو إلى المفضلة."""
    query = "INSERT INTO user_favorites (user_id, video_id) VALUES (%s, %s) ON CONFLICT (user_id, video_id) DO NOTHING"
    return execute_query(query, (user_id, video_id), commit=True)

def remove_from_favorites(user_id, video_id):
    """إزالة فيديو من المفضلة."""
    query = "DELETE FROM user_favorites WHERE user_id = %s AND video_id = %s"
    return execute_query(query, (user_id, video_id), commit=True)

def get_user_favorites(user_id, page=0):
    """جلب قائمة المفضلة للمستخدم مع التصفح."""
    offset = page * VIDEOS_PER_PAGE
    videos_query = """
        SELECT va.* FROM video_archive va
        JOIN user_favorites uf ON va.id = uf.video_id
        WHERE uf.user_id = %s
        ORDER BY uf.added_date DESC
        LIMIT %s OFFSET %s
    """
    total_query = "SELECT COUNT(*) as count FROM user_favorites WHERE user_id = %s"
    
    videos = execute_query(videos_query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    total = execute_query(total_query, (user_id,), fetch="one")
    
    return videos, total['count'] if total else 0

# --- دوال سجل المشاهدة (Watch History) ---

def add_to_history(user_id, video_id):
    """إضافة/تحديث سجل المشاهدة. يستخدم ON CONFLICT لتحديث التاريخ فقط."""
    query = """
        INSERT INTO user_history (user_id, video_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id, video_id) DO UPDATE SET
            last_viewed = CURRENT_TIMESTAMP
    """
    return execute_query(query, (user_id, video_id), commit=True)

def get_user_history(user_id, page=0):
    """جلب سجل المشاهدة للمستخدم مع التصفح."""
    offset = page * VIDEOS_PER_PAGE
    videos_query = """
        SELECT va.* FROM video_archive va
        JOIN user_history uh ON va.id = uh.video_id
        WHERE uh.user_id = %s
        ORDER BY uh.last_viewed DESC
        LIMIT %s OFFSET %s
    """
    total_query = "SELECT COUNT(*) as count FROM user_history WHERE user_id = %s"
    
    videos = execute_query(videos_query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    total = execute_query(total_query, (user_id,), fetch="one")
    
    return videos, total['count'] if total else 0

# --- دوال إدارة الحالة (State Management) [في نهاية الملف] ---
def set_user_state(user_id, state, context=None):
    """حفظ حالة المستخدم في قاعدة البيانات."""
    context_json = json.dumps(context) if context else None
    query = """
        INSERT INTO user_states (user_id, state, context)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            state = EXCLUDED.state,
            context = EXCLUDED.context,
            last_update = CURRENT_TIMESTAMP
        RETURNING user_id
    """
    params = (user_id, state, context_json)
    return execute_query(query, params, commit=True)

def get_user_state(user_id):
    """جلب حالة المستخدم من قاعدة البيانات."""
    query = "SELECT user_id, state, context FROM user_states WHERE user_id = %s"
    return execute_query(query, (user_id,), fetch="one")

def clear_user_state(user_id):
    """مسح حالة المستخدم من قاعدة البيانات."""
    query = "DELETE FROM user_states WHERE user_id = %s"
    return execute_query(query, (user_id,), commit=True)
