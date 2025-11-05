#!/usr/bin/env python3
# ==============================================================================
# ملف: db_optimizer.py
# الوصف: أداة تحسين أداء قاعدة البيانات - إضافة فهارس محسنة
# الاستخدام: python db_optimizer.py
# ==============================================================================

import os
import psycopg2
from psycopg2.extras import DictCursor
from urllib.parse import urlparse
import logging
from datetime import datetime
import time

# إعداد نظام التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_db_config():
    """الحصول على إعدادات قاعدة البيانات من متغيرات البيئة"""
    try:
        DATABASE_URL = os.environ.get('DATABASE_URL')
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL not set.")
        
        result = urlparse(DATABASE_URL)
        return {
            'user': result.username,
            'password': result.password,
            'host': result.hostname,
            'port': result.port,
            'dbname': result.path[1:]
        }
    except Exception as e:
        logger.error(f"Could not parse DATABASE_URL: {e}")
        return None

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    config = get_db_config()
    if not config:
        return None
    
    try:
        return psycopg2.connect(**config)
    except psycopg2.OperationalError as e:
        logger.error(f"Database connection failed: {e}")
        return None

def check_index_exists(cur, index_name):
    """التحقق من وجود فهرس معين"""
    cur.execute("""
        SELECT 1 FROM pg_indexes 
        WHERE indexname = %s
    """, (index_name,))
    return cur.fetchone() is not None

def create_index_safely(cur, index_sql, index_name):
    """إنشاء فهرس بشكل آمن مع معالجة الأخطاء"""
    try:
        if check_index_exists(cur, index_name):
            logger.info(f"✅ الفهرس {index_name} موجود بالفعل")
            return True
        
        logger.info(f"🔄 إنشاء الفهرس {index_name}...")
        start_time = time.time()
        
        cur.execute(index_sql)
        
        end_time = time.time()
        logger.info(f"✅ تم إنشاء الفهرس {index_name} بنجاح في {end_time - start_time:.2f} ثانية")
        return True
        
    except psycopg2.Error as e:
        logger.error(f"❌ فشل في إنشاء الفهرس {index_name}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ خطأ غير متوقع في إنشاء الفهرس {index_name}: {e}")
        return False

def enable_pg_trgm_extension(cur):
    """تفعيل امتداد pg_trgm للبحث المحسن"""
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        logger.info("✅ تم تفعيل امتداد pg_trgm للبحث المحسن")
        return True
    except psycopg2.Error as e:
        logger.warning(f"⚠️ لم يتم تفعيل pg_trgm: {e}")
        return False

def optimize_database_performance():
    """تحسين أداء قاعدة البيانات بإضافة فهارس محسنة"""
    conn = get_db_connection()
    if not conn:
        logger.error("❌ فشل الاتصال بقاعدة البيانات")
        return False
    
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            logger.info("🚀 بدء عملية تحسين أداء قاعدة البيانات...")
            
            # تفعيل امتداد pg_trgm
            enable_pg_trgm_extension(cur)
            
            # قائمة الفهارس المحسنة
            optimization_indexes = [
                # فهارس جدول video_archive
                {
                    'name': 'idx_video_archive_category_id',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_category_id ON video_archive(category_id)',
                    'description': 'فهرس للبحث السريع بالتصنيف'
                },
                {
                    'name': 'idx_video_archive_view_count_desc',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_view_count_desc ON video_archive(view_count DESC)',
                    'description': 'فهرس للفيديوهات الأكثر مشاهدة'
                },
                {
                    'name': 'idx_video_archive_upload_date_desc',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_upload_date_desc ON video_archive(upload_date DESC)',
                    'description': 'فهرس للفيديوهات الأحدث'
                },
                {
                    'name': 'idx_video_archive_message_id',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_message_id ON video_archive(message_id)',
                    'description': 'فهرس للبحث برقم الرسالة'
                },
                {
                    'name': 'idx_video_archive_caption_trgm',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_caption_trgm ON video_archive USING gin (caption gin_trgm_ops)',
                    'description': 'فهرس البحث النصي المحسن للعناوين'
                },
                {
                    'name': 'idx_video_archive_filename_trgm',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_filename_trgm ON video_archive USING gin (file_name gin_trgm_ops)',
                    'description': 'فهرس البحث النصي المحسن لأسماء الملفات'
                },
                {
                    'name': 'idx_video_archive_grouping_key',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_grouping_key ON video_archive(grouping_key)',
                    'description': 'فهرس للبحث بمفتاح التجميع'
                },
                
                # فهارس جدول categories
                {
                    'name': 'idx_categories_parent_id',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_categories_parent_id ON categories(parent_id)',
                    'description': 'فهرس للتصنيفات الفرعية'
                },
                {
                    'name': 'idx_categories_name',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_categories_name ON categories(name)',
                    'description': 'فهرس لأسماء التصنيفات'
                },
                {
                    'name': 'idx_categories_full_path',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_categories_full_path ON categories(full_path)',
                    'description': 'فهرس للمسار الكامل للتصنيف'
                },
                
                # فهارس جدول user_favorites
                {
                    'name': 'idx_user_favorites_user_id',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_favorites_user_id ON user_favorites(user_id)',
                    'description': 'فهرس لمفضلات المستخدم'
                },
                {
                    'name': 'idx_user_favorites_video_id',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_favorites_video_id ON user_favorites(video_id)',
                    'description': 'فهرس للفيديوهات المفضلة'
                },
                {
                    'name': 'idx_user_favorites_date_added_desc',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_favorites_date_added_desc ON user_favorites(date_added DESC)',
                    'description': 'فهرس لآخر المفضلات المضافة'
                },
                
                # فهارس جدول user_history
                {
                    'name': 'idx_user_history_user_id',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_history_user_id ON user_history(user_id)',
                    'description': 'فهرس لتاريخ المستخدم'
                },
                {
                    'name': 'idx_user_history_last_watched_desc',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_history_last_watched_desc ON user_history(last_watched DESC)',
                    'description': 'فهرس لآخر المشاهدات'
                },
                
                # فهارس جدول video_ratings
                {
                    'name': 'idx_video_ratings_video_id',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_ratings_video_id ON video_ratings(video_id)',
                    'description': 'فهرس لتقييمات الفيديو'
                },
                {
                    'name': 'idx_video_ratings_user_id',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_ratings_user_id ON video_ratings(user_id)',
                    'description': 'فهرس لتقييمات المستخدم'
                },
                
                # فهارس جدول bot_users
                {
                    'name': 'idx_bot_users_join_date_desc',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bot_users_join_date_desc ON bot_users(join_date DESC)',
                    'description': 'فهرس لآخر المشتركين'
                },
                {
                    'name': 'idx_bot_users_username',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bot_users_username ON bot_users(username)',
                    'description': 'فهرس لأسماء المستخدمين'
                },
                
                # فهارس جدول user_states
                {
                    'name': 'idx_user_states_state',
                    'sql': 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_states_state ON user_states(state)',
                    'description': 'فهرس لحالات المستخدمين'
                }
            ]
            
            successful_indexes = 0
            failed_indexes = 0
            
            # إنشاء الفهارس
            for index_info in optimization_indexes:
                logger.info(f"📋 {index_info['description']}")
                
                if create_index_safely(cur, index_info['sql'], index_info['name']):
                    successful_indexes += 1
                else:
                    failed_indexes += 1
                
                # فترة انتظار قصيرة بين الفهارس
                time.sleep(0.5)
            
            # تحديث إحصائيات قاعدة البيانات
            logger.info("📊 تحديث إحصائيات قاعدة البيانات...")
            try:
                cur.execute("ANALYZE")
                logger.info("✅ تم تحديث إحصائيات قاعدة البيانات")
            except Exception as e:
                logger.warning(f"⚠️ لم يتم تحديث الإحصائيات: {e}")
            
            # الالتزام بالتغييرات
            conn.commit()
            
            # تقرير النتائج
            logger.info(f"\n🎉 تم الانتهاء من تحسين قاعدة البيانات!")
            logger.info(f"✅ فهارس تم إنشاؤها بنجاح: {successful_indexes}")
            logger.info(f"❌ فهارس فشل إنشاؤها: {failed_indexes}")
            logger.info(f"📊 إجمالي الفهارس المعالجة: {len(optimization_indexes)}")
            
            return successful_indexes > 0
            
    except Exception as e:
        logger.error(f"❌ خطأ في عملية التحسين: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def check_database_performance():
    """فحص أداء قاعدة البيانات الحالي"""
    conn = get_db_connection()
    if not conn:
        logger.error("❌ فشل الاتصال بقاعدة البيانات")
        return
    
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            logger.info("📊 فحص أداء قاعدة البيانات...")
            
            # إحصائيات الجداول
            cur.execute("""
                SELECT 
                    schemaname,
                    tablename,
                    n_tup_ins as inserts,
                    n_tup_upd as updates,
                    n_tup_del as deletes,
                    n_live_tup as live_rows,
                    n_dead_tup as dead_rows
                FROM pg_stat_user_tables
                ORDER BY n_live_tup DESC
            """)
            
            tables_stats = cur.fetchall()
            
            logger.info("\n📈 إحصائيات الجداول:")
            for stat in tables_stats:
                logger.info(f"   📋 {stat['tablename']}: {stat['live_rows']:,} سجل حي, {stat['dead_rows']:,} سجل ميت")
            
            # إحصائيات الفهارس
            cur.execute("""
                SELECT 
                    schemaname,
                    tablename,
                    indexname,
                    idx_tup_read,
                    idx_tup_fetch
                FROM pg_stat_user_indexes
                WHERE idx_tup_read > 0
                ORDER BY idx_tup_read DESC
                LIMIT 10
            """)
            
            index_stats = cur.fetchall()
            
            logger.info("\n🔍 أكثر الفهارس استخداماً:")
            for stat in index_stats:
                logger.info(f"   📊 {stat['indexname']}: {stat['idx_tup_read']:,} قراءة")
    
    except Exception as e:
        logger.error(f"❌ خطأ في فحص الأداء: {e}")
    finally:
        if conn:
            conn.close()

def main():
    """الدالة الرئيسية"""
    logger.info("🔧 بدء عملية تحسين أداء قاعدة البيانات...")
    
    # فحص الأداء الحالي
    check_database_performance()
    
    # تحسين الأداء
    success = optimize_database_performance()
    
    if success:
        logger.info("\n✅ تم تحسين قاعدة البيانات بنجاح!")
        logger.info("🚀 الآن البوت سيعمل بأداء أفضل وسرعة أكبر")
    else:
        logger.error("\n❌ فشل في تحسين قاعدة البيانات")
        logger.info("🔍 تحقق من اللوج أعلاه لمعرفة التفاصيل")
    
    # فحص الأداء بعد التحسين
    logger.info("\n📊 فحص الأداء بعد التحسين:")
    check_database_performance()

if __name__ == "__main__":
    main()