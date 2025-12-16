#!/usr/bin/env python3
# ==============================================================================
# ملف: db_optimizer.py (مصَحَّح)
# الوصف: أداة تحسين أداء قاعدة البيانات - إنشاء فهارس CONCURRENTLY خارج المعاملات + إصلاح تقارير الأداء
# ==============================================================================

import os
import psycopg2
from psycopg2.extras import DictCursor
from urllib.parse import urlparse
import logging
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_db_config():
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL not set')
    r = urlparse(DATABASE_URL)
    return {
        'user': r.username,
        'password': r.password,
        'host': r.hostname,
        'port': r.port,
        'dbname': r.path[1:],
    }


def connect(autocommit=False):
    try:
        cfg = get_db_config()
        conn = psycopg2.connect(**cfg)
        conn.set_session(autocommit=autocommit)
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to DB: {e}")
        return None


def check_index_exists(conn, index_name: str) -> bool:
    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute("SELECT 1 FROM pg_indexes WHERE indexname = %s", (index_name,))
        return cur.fetchone() is not None


def create_index_concurrently(sql_stmt: str, index_name: str) -> bool:
    """ينفذ CREATE INDEX CONCURRENTLY خارج أي معاملة (autocommit)."""
    try:
        conn = connect(autocommit=True)
        try:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                if check_index_exists(conn, index_name):
                    logger.info(f"✅ الفهرس {index_name} موجود بالفعل")
                    return True
                logger.info(f"🔄 إنشاء الفهرس {index_name}...")
                t0 = time.time()
                cur.execute(sql_stmt)
                logger.info(f"✅ تم إنشاء الفهرس {index_name} في {(time.time()-t0):.2f}s")
                return True
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"❌ فشل في إنشاء الفهرس {index_name}: {e}")
        return False


def enable_pg_trgm():
    try:
        conn = connect(autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                logger.info("✅ تم تفعيل امتداد pg_trgm للبحث المحسن")
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"⚠️ لم يتم تفعيل pg_trgm: {e}")


def check_database_performance():
    logger.info("📊 فحص أداء قاعدة البيانات...")
    try:
        conn = connect(autocommit=True)
        try:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                # إحصائيات الجداول الصحيحة عبر pg_stat_user_tables + pg_class
                cur.execute(
                    """
                    SELECT c.relname AS table_name,
                           st.n_live_tup AS live_rows,
                           st.n_dead_tup AS dead_rows
                    FROM pg_stat_user_tables st
                    JOIN pg_class c ON c.oid = st.relid
                    ORDER BY st.n_live_tup DESC
                    """
                )
                for row in cur.fetchall()[:10]:
                    logger.info(f"   📋 {row['table_name']}: {row['live_rows']:,} حي, {row['dead_rows']:,} ميت")

                # إحصائيات الفهارس عبر pg_stat_user_indexes + pg_class
                cur.execute(
                    """
                    SELECT c.relname AS table_name,
                           i.relname AS index_name,
                           idx.idx_tup_read,
                           idx.idx_tup_fetch
                    FROM pg_stat_user_indexes idx
                    JOIN pg_class c ON c.oid = idx.relid
                    JOIN pg_class i ON i.oid = idx.indexrelid
                    ORDER BY idx.idx_tup_read DESC
                    LIMIT 10
                    """
                )
                for row in cur.fetchall():
                    logger.info(f"   🔍 {row['index_name']} على {row['table_name']}: {row['idx_tup_read']:,} قراءة")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"❌ خطأ في فحص الأداء: {e}")


def optimize_database_performance():
    logger.info("🚀 بدء عملية تحسين أداء قاعدة البيانات...")
    enable_pg_trgm()

    indexes = [
        # video_archive
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_category_id ON video_archive(category_id)", "idx_video_archive_category_id"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_view_count_desc ON video_archive(view_count DESC)", "idx_video_archive_view_count_desc"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_upload_date_desc ON video_archive(upload_date DESC)", "idx_video_archive_upload_date_desc"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_message_id ON video_archive(message_id)", "idx_video_archive_message_id"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_caption_trgm ON video_archive USING gin (caption gin_trgm_ops)", "idx_video_archive_caption_trgm"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_filename_trgm ON video_archive USING gin (file_name gin_trgm_ops)", "idx_video_archive_filename_trgm"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_archive_grouping_key ON video_archive(grouping_key)", "idx_video_archive_grouping_key"),
        # categories
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_categories_parent_id ON categories(parent_id)", "idx_categories_parent_id"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_categories_name ON categories(name)", "idx_categories_name"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_categories_full_path ON categories(full_path)", "idx_categories_full_path"),
        # user_favorites
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_favorites_user_id ON user_favorites(user_id)", "idx_user_favorites_user_id"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_favorites_video_id ON user_favorites(video_id)", "idx_user_favorites_video_id"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_favorites_date_added_desc ON user_favorites(date_added DESC)", "idx_user_favorites_date_added_desc"),
        # user_history
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_history_user_id ON user_history(user_id)", "idx_user_history_user_id"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_history_last_watched_desc ON user_history(last_watched DESC)", "idx_user_history_last_watched_desc"),
        # video_ratings
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_ratings_video_id ON video_ratings(video_id)", "idx_video_ratings_video_id"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_ratings_user_id ON video_ratings(user_id)", "idx_video_ratings_user_id"),
        # bot_users
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bot_users_join_date_desc ON bot_users(join_date DESC)", "idx_bot_users_join_date_desc"),
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bot_users_username ON bot_users(username)", "idx_bot_users_username"),
        # user_states
        ("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_user_states_state ON user_states(state)", "idx_user_states_state"),
    ]

    ok = 0
    fail = 0
    for sql_stmt, idx_name in indexes:
        if create_index_concurrently(sql_stmt, idx_name):
            ok += 1
        else:
            fail += 1
        time.sleep(0.2)

    # ANALYZE بعد الإنشاء
    try:
        conn = connect(autocommit=True)
        try:
            with conn.cursor() as cur:
                logger.info("📊 تحديث إحصائيات قاعدة البيانات...")
                cur.execute("ANALYZE")
                logger.info("✅ تم تحديث إحصائيات قاعدة البيانات")
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"⚠️ لم يتم تحديث الإحصائيات: {e}")

    logger.info("\n🎉 تم الانتهاء من تحسين قاعدة البيانات!")
    logger.info(f"✅ فهارس تم إنشاؤها بنجاح: {ok}")
    logger.info(f"❌ فهارس فشل إنشاؤها: {fail}")
    logger.info(f"📊 إجمالي الفهارس المعالجة: {len(indexes)}")
    return ok > 0


optimization_lock = False

def main():
    global optimization_lock
    if optimization_lock:
        logger.warning("Optimization already running")
        return False
        
    optimization_lock = True
    try:
        logger.info("🔧 بدء عملية تحسين أداء قاعدة البيانات...")
        check_database_performance()
        result = optimize_database_performance()
        logger.info("\n📊 فحص الأداء بعد التحسين:")
        check_database_performance()
        return result
    except Exception as e:
        logger.error(f"Optimization failed: {e}")
        return False
    finally:
        optimization_lock = False

if __name__ == "__main__":
    main()
