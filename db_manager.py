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
        'full_path': 'TEXT NOT NULL' # هذا هو العمود الذي سبب المشكلة
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
        'id': 'SERIAL PRIMARY KEY',
        'channel_id': 'TEXT UNIQUE NOT NULL',
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
        # استخدام DictCursor لجعل النتائج قابلة للقراءة كقواميس
        with conn.cursor(cursor_factory=DictCursor) as c:
            c.execute(query, params)
            
            if commit:
                conn.commit()
            
            if fetch == "one":
                result = c.fetchone()
                # يتم تحويل DictRow إلى قاموس عادي لتجنب الأخطاء في باقي الكود
                return dict(result) if result else None 
            elif fetch == "all":
                results = c.fetchall()
                # تحويل صفوف DictRow إلى قائمة قواميس عادية
                return [dict(row) for row in results]
            return True

    except psycopg2.errors.NotNullViolation as e:
        # إذا حدث خطأ NOT NULL (مثل مشكلة full_path)
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

# --- دالة فحص وتصحيح المخطط (Schema) ---
def verify_and_repair_schema():
    conn = get_db_connection()
    if not conn:
        logger.critical("Could not verify schema: No database connection.")
        return
    
    try:
        with conn.cursor() as c:
            for table_name, columns in EXPECTED_SCHEMA.items():
                logger.info(f"Verifying table: {table_name}")
                
                # 1. التحقق من وجود الجدول
                c.execute(f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{table_name}');")
                if not c.fetchone()[0]:
                    logger.warning(f"Table {table_name} missing. Creating...")
                    cols_def = ', '.join([f'{col} {dtype}' for col, dtype in columns.items()])
                    c.execute(f"CREATE TABLE {table_name} ({cols_def})")
                    logger.info(f"Table {table_name} created successfully.")
                    conn.commit()
                    continue

                # 2. التحقق من الأعمدة المفقودة
                for col_name, col_type in columns.items():
                    try:
                        c.execute(f"SELECT data_type FROM information_schema.columns WHERE table_name = '{table_name}' AND column_name = '{col_name}'")
                        if c.fetchone() is None:
                            logger.warning(f"Column {col_name} in {table_name} missing. Adding...")
                            
                            # يجب استثناء قيود المفاتيح الأجنبية عند الإضافة
                            col_def_without_fk = col_type.split('REFERENCES')[0].strip()
                            
                            # استخدام PostgreSQL ALTER TABLE ADD COLUMN
                            c.execute(sql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(
                                sql.Identifier(table_name),
                                sql.Identifier(col_name),
                                sql.SQL(col_def_without_fk)
                            ))
                            logger.info(f"Column {col_name} added to {table_name}.")
                            conn.commit()
                    except Exception as e:
                        logger.error(f"Error adding column {col_name} to {table_name}: {e}")
                        conn.rollback()

    except Exception as e:
        logger.critical(f"FATAL SCHEMA ERROR: {e}")
        if conn: conn.rollback()
    finally:
        if conn:
            conn.close()


# --- دوال التعامل مع المستخدمين والحالة ---

def set_active_category_id(category_id):
    """تخزين آخر تصنيف نشط (مستخدم لتحميل الفيديوهات)."""
    return execute_query("INSERT INTO bot_settings (setting_key, setting_value) VALUES (%s, %s) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value", 
                         ('active_category_id', str(category_id)), commit=True)

def get_active_category_id():
    """الحصول على آخر تصنيف نشط."""
    res = execute_query("SELECT setting_value FROM bot_settings WHERE setting_key = 'active_category_id'", fetch="one")
    return int(res['setting_value']) if res and res.get('setting_value') else None

def add_bot_user(user_id, username, first_name):
    return execute_query("INSERT INTO bot_users (user_id, username, first_name) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING", 
                         (user_id, username, first_name), commit=True)

def get_all_user_ids():
    res = execute_query("SELECT user_id FROM bot_users", fetch="all")
    return [row['user_id'] for row in res] if res else []

def get_subscriber_count():
    res = execute_query("SELECT COUNT(*) FROM bot_users", fetch="one")
    return res['count'] if res else 0

# دوال إدارة حالة المستخدم (User State)
def set_user_state(user_id, state, context):
    context_json = json.dumps(context) if context else None
    return execute_query("INSERT INTO user_states (user_id, state, context) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET state = EXCLUDED.state, context = EXCLUDED.context, last_update = CURRENT_TIMESTAMP", 
                         (user_id, state, context_json), commit=True)

def get_user_state(user_id):
    res = execute_query("SELECT state, context FROM user_states WHERE user_id = %s", (user_id,), fetch="one")
    if res and res.get('context'):
        res['context'] = json.loads(res['context'])
    return res

def clear_user_state(user_id):
    return execute_query("DELETE FROM user_states WHERE user_id = %s", (user_id,), commit=True)

# --- دوال التعامل مع التصنيفات (Categories) ---

def get_category_by_id(category_id):
    """الحصول على بيانات تصنيف معين بواسطة المعرف."""
    # نضمن أن المعرف هو رقم موجب قبل الاستعلام
    if category_id is None or int(category_id) <= 0:
        return None
    return execute_query("SELECT * FROM categories WHERE id = %s", (category_id,), fetch="one")

def get_categories_tree(parent_id=None):
    """الحصول على التصنيفات الفرعية لتصنيف معين."""
    if parent_id is None:
        return execute_query("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY id", fetch="all")
    else:
        # تأكد أننا نمرر ID كـ None إذا كان غير موجود
        return execute_query("SELECT * FROM categories WHERE parent_id = %s ORDER BY id", (parent_id,), fetch="all")

def get_child_categories(category_id):
    """الحصول على التصنيفات الفرعية لتصنيف معين."""
    return execute_query("SELECT * FROM categories WHERE parent_id = %s ORDER BY id", (category_id,), fetch="all")


def add_category(name, parent_id=None):
    """
    [الإصلاح هنا] تضيف تصنيفاً جديداً مع حساب قيمة full_path.
    """
    full_path = name
    
    # إذا كان هناك تصنيف أب
    if parent_id is not None:
        # نحصل على بيانات التصنيف الأب لحساب full_path
        parent_category = get_category_by_id(parent_id)
        
        if parent_category:
            # نستخدم full_path الخاص بالأب ثم نضيف اسم الابن
            parent_path = parent_category.get('full_path') or parent_category['name']
            full_path = f"{parent_path}/{name}"
        else:
            # إذا لم يتم العثور على الأب (هذا هو سبب خطأك، حيث أن get_category_by_id كانت ترجع None
            # والآن تم إصلاح هذا ليتم التعامل مع الحالة بأمان)
            # إذا فشل إيجاد الأب، سنعتبره تصنيف جذري ونضيف خطأ في السجلات
            logger.error(f"Parent category with ID {parent_id} not found when adding child category '{name}'. Adding as root.")
            parent_id = None # نضبط الأب على None إذا لم نعثر عليه
            full_path = name
    
    # يتم الآن تمرير full_path الذي تم حسابه إلى قاعدة البيانات
    query = "INSERT INTO categories (name, parent_id, full_path) VALUES (%s, %s, %s) RETURNING id"
    params = (name, parent_id, full_path)
    
    res = execute_query(query, params, fetch="one", commit=True)
    
    if res and res.get('id'):
        return True, res['id']
    else:
        return False, "Failed to add category"

# --- دوال الإحصائيات (حل مشكلة ImportError) ---

def get_bot_stats():
    """الحصول على إحصائيات عامة للبوت."""
    stats = {}
    stats['video_count'] = (execute_query("SELECT COUNT(*) as count FROM video_archive", fetch="one") or {'count': 0})['count']
    stats['category_count'] = (execute_query("SELECT COUNT(*) as count FROM categories", fetch="one") or {'count': 0})['count']
    stats['total_views'] = (execute_query("SELECT SUM(view_count) as sum FROM video_archive", fetch="one") or {'sum': 0})['sum'] or 0
    stats['total_ratings'] = (execute_query("SELECT COUNT(*) as count FROM video_ratings", fetch="one") or {'count': 0})['count']
    return stats


def delete_category_by_id(category_id):
    """حذف تصنيف معين."""
    return execute_query("DELETE FROM categories WHERE id = %s", (category_id,), commit=True)

# --- دوال التعامل مع الفيديوهات (Videos) ---

def add_video(message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id=None):
    """إضافة فيديو جديد إلى الأرشيف."""
    metadata_json = json.dumps(metadata)
    query = """
    INSERT INTO video_archive (message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """
    params = (message_id, caption, chat_id, file_name, file_id, metadata_json, grouping_key, category_id)
    res = execute_query(query, params, fetch="one", commit=True)
    return res['id'] if res and res.get('id') else None

def get_videos(category_id=None, page=1):
    """الحصول على الفيديوهات حسب التصنيف وترتيبها حسب التاريخ أو المجموعة."""
    offset = (page - 1) * VIDEOS_PER_PAGE
    
    # تحديد شرط التصفية (WHERE clause)
    if category_id is None:
        where_clause = ""
        count_params = ()
        video_params = (VIDEOS_PER_PAGE, offset)
    else:
        where_clause = "WHERE category_id = %s"
        count_params = (category_id,)
        video_params = (category_id, VIDEOS_PER_PAGE, offset)

    # حساب العدد الإجمالي
    total_count_res = execute_query(f"SELECT COUNT(*) FROM video_archive {where_clause}", count_params, fetch="one")
    total_count = total_count_res['count'] if total_count_res else 0

    # جلب الفيديوهات المرتبة
    # يجب الترتيب حسب grouping_key (للمسلسلات) ثم upload_date (للأفلام أو الحلقات داخل المسلسل)
    video_query = f"""
    SELECT va.*, c.name AS category_name, c.full_path AS category_full_path
    FROM video_archive va
    LEFT JOIN categories c ON va.category_id = c.id
    {where_clause}
    ORDER BY va.grouping_key NULLS LAST, va.upload_date DESC
    LIMIT %s OFFSET %s
    """
    
    # تغيير طريقة تمرير المعاملات بناءً على وجود category_id
    if category_id is None:
        videos = execute_query(video_query, video_params, fetch="all")
    else:
        # هنا يجب استخدام نفس عدد المتغيرات (%s) في الاستعلام
        video_query = f"""
        SELECT va.*, c.name AS category_name, c.full_path AS category_full_path
        FROM video_archive va
        LEFT JOIN categories c ON va.category_id = c.id
        WHERE va.category_id = %s
        ORDER BY va.grouping_key NULLS LAST, va.upload_date DESC
        LIMIT %s OFFSET %s
        """
        videos = execute_query(video_query, video_params, fetch="all")
        
    return videos, total_count

def search_videos(query, category_id=None, page=1):
    """البحث في الفيديوهات."""
    offset = (page - 1) * VIDEOS_PER_PAGE
    search_term = f"%{query}%"

    # شرط التصنيف
    category_filter = "AND category_id = %s" if category_id is not None else ""
    
    # المعاملات: [البحث، البحث] + [التصنيف] + [LIMIT, OFFSET]
    base_params = [search_term, search_term]
    if category_id is not None:
        base_params.append(category_id)
    
    count_params = tuple(base_params)
    video_params = tuple(base_params + [VIDEOS_PER_PAGE, offset])

    # حساب العدد الإجمالي
    count_query = f"""
    SELECT COUNT(*) FROM video_archive 
    WHERE caption ILIKE %s OR file_name ILIKE %s {category_filter}
    """
    total_count_res = execute_query(count_query, count_params, fetch="one")
    total_count = total_count_res['count'] if total_count_res else 0

    # جلب الفيديوهات
    video_query = f"""
    SELECT va.*, c.name AS category_name 
    FROM video_archive va
    LEFT JOIN categories c ON va.category_id = c.id
    WHERE va.caption ILIKE %s OR va.file_name ILIKE %s {category_filter}
    ORDER BY upload_date DESC 
    LIMIT %s OFFSET %s
    """
    videos = execute_query(video_query, video_params, fetch="all")
        
    return videos, total_count

def get_popular_videos():
    """الحصول على الفيديوهات الأكثر شعبية والأعلى تقييماً."""
    most_viewed = execute_query("SELECT va.*, c.name AS category_name FROM video_archive va LEFT JOIN categories c ON va.category_id = c.id ORDER BY view_count DESC LIMIT 10", fetch="all")
    highest_rated = execute_query("""
        SELECT v.*, r.avg_rating 
        FROM video_archive v 
        JOIN (SELECT video_id, AVG(rating) as avg_rating FROM video_ratings GROUP BY video_id) r 
        ON v.id = r.video_id 
        ORDER BY r.avg_rating DESC, v.view_count DESC LIMIT 10
    """, fetch="all")
    return {"most_viewed": most_viewed, "highest_rated": highest_rated}

def get_random_video(category_id=None):
    """الحصول على فيديو عشوائي."""
    where_clause = "WHERE category_id = %s" if category_id is not None else ""
    params = (category_id,) if category_id is not None else ()
    query = f"""
    SELECT va.*, c.name AS category_name FROM video_archive va 
    LEFT JOIN categories c ON va.category_id = c.id
    {where_clause}
    ORDER BY RANDOM() LIMIT 1
    """
    return execute_query(query, params, fetch="one")

def increment_video_view_count(video_id):
    """زيادة عداد مشاهدة الفيديو."""
    return execute_query("UPDATE video_archive SET view_count = view_count + 1 WHERE id = %s", (video_id,), commit=True)

# --- دوال التعامل مع التقييمات ---

def set_user_video_rating(video_id, user_id, rating):
    """إضافة أو تحديث تقييم المستخدم لفيديو."""
    query = "INSERT INTO video_ratings (video_id, user_id, rating) VALUES (%s, %s, %s) ON CONFLICT (video_id, user_id) DO UPDATE SET rating = EXCLUDED.rating, rating_date = CURRENT_TIMESTAMP"
    params = (video_id, user_id, rating)
    return execute_query(query, params, commit=True)

def get_user_video_rating(video_id, user_id):
    """الحصول على تقييم مستخدم معين لفيديو."""
    res = execute_query("SELECT rating FROM video_ratings WHERE video_id = %s AND user_id = %s", (video_id, user_id), fetch="one")
    return res['rating'] if res else None

def get_video_rating_stats(video_id):
    """الحصول على متوسط التقييم وعدد المقيمين لفيديو."""
    query = "SELECT AVG(rating) AS avg, COUNT(rating) AS count FROM video_ratings WHERE video_id = %s"
    return execute_query(query, (video_id,), fetch="one")

# --- دوال إضافية (إدارة القنوات والفيديوهات) ---

def add_required_channel(channel_id, channel_name):
    query = "INSERT INTO required_channels (channel_id, channel_name) VALUES (%s, %s) ON CONFLICT (channel_id) DO UPDATE SET channel_name = EXCLUDED.channel_name"
    params = (channel_id, channel_name)
    return execute_query(query, params, commit=True)

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
    # لا نحتاج لحذف الفيديوهات هنا يدوياً بفضل ON DELETE SET NULL في تعريف الجدول
    # execute_query("DELETE FROM video_archive WHERE category_id = %s", (category_id,), commit=True)
    execute_query("DELETE FROM categories WHERE id = %s", (category_id,), commit=True)
    return True

def move_videos_from_category(old_category_id, new_category_id):
    return execute_query("UPDATE video_archive SET category_id = %s WHERE category_id = %s", (new_category_id, old_category_id), commit=True)
