# ==============================================================================
# db_manager.py (add create_indexes for webhook startup)
# ==============================================================================

import logging

# === Indexes and performance helpers ===
INDEX_QUERIES = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS idx_video_caption_trgm ON video_archive USING gin (caption gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_video_filename_trgm ON video_archive USING gin (file_name gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_video_category ON video_archive (category_id)",
    "CREATE INDEX IF NOT EXISTS idx_video_views_desc ON video_archive (view_count DESC)",
]

def create_indexes():
    """
    إنشاء الفهارس المطلوبة لتحسين الأداء. تُستدعى مرة عند الإقلاع. آمنة للتكرار.
    """
    try:
        conn = get_db_connection()
        if not conn:
            logging.getLogger(__name__).warning("create_indexes: no DB connection available; skipping")
            return
        with conn.cursor() as c:
            for q in INDEX_QUERIES:
                try:
                    c.execute(q)
                except Exception as iqe:
                    logging.getLogger(__name__).warning(f"Index creation warning: {iqe}")
            conn.commit()
        try:
            conn.close()
        except Exception:
            pass
        logging.getLogger(__name__).info("DB indexes ensured successfully.")
    except Exception as e:
        logging.getLogger(__name__).warning(f"create_indexes failed or skipped: {e}")
