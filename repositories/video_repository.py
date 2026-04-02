
# repositories/video_repository.py

from core.db import execute_query
import logging
import json
from config.constants import VIDEOS_PER_PAGE

logger = logging.getLogger(__name__)

def add_video(message_id, chat_id, caption, file_name, file_id, category_id, metadata, grouping_key, thumbnail_file_id, content_type):
    query = """
        INSERT INTO video_archive (
            message_id, chat_id, caption, file_name, file_id, category_id, metadata, grouping_key, thumbnail_file_id, content_type
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
    """
    params = (
        message_id, chat_id, caption, file_name, file_id, category_id,
        json.dumps(metadata) if metadata else None, grouping_key, thumbnail_file_id, content_type
    )
    result = execute_query(query, params, fetch="one", commit=True)
    return result["id"] if result else None

def get_video_by_id(video_id):
    query = "SELECT * FROM video_archive WHERE id = %s;"
    return execute_query(query, (video_id,), fetch="one")

def get_videos(category_id=None, page=0):
    offset = page * VIDEOS_PER_PAGE
    if category_id:
        query = "SELECT * FROM video_archive WHERE category_id = %s ORDER BY upload_date DESC LIMIT %s OFFSET %s;"
        count_query = "SELECT COUNT(*) FROM video_archive WHERE category_id = %s;"
        videos = execute_query(query, (category_id, VIDEOS_PER_PAGE, offset), fetch="all")
        total_count = execute_query(count_query, (category_id,), fetch="one")["count"]
    else:
        query = "SELECT * FROM video_archive ORDER BY upload_date DESC LIMIT %s OFFSET %s;"
        count_query = "SELECT COUNT(*) FROM video_archive;"
        videos = execute_query(query, (VIDEOS_PER_PAGE, offset), fetch="all")
        total_count = execute_query(count_query, fetch="one")["count"]
    return videos, total_count

def search_videos(query, page=0, category_id=None, quality=None, status=None):
    offset = page * VIDEOS_PER_PAGE
    sql_query = "SELECT * FROM video_archive WHERE caption ILIKE %s OR file_name ILIKE %s"
    count_sql_query = "SELECT COUNT(*) FROM video_archive WHERE caption ILIKE %s OR file_name ILIKE %s"
    params = [f"%{query}%", f"%{query}%"]

    if category_id and category_id != "all":
        sql_query += " AND category_id = %s"
        count_sql_query += " AND category_id = %s"
        params.append(category_id)
    
    if quality:
        sql_query += " AND metadata->>‘quality_resolution’ = %s"
        count_sql_query += " AND metadata->>‘quality_resolution’ = %s"
        params.append(quality)
    
    if status:
        sql_query += " AND metadata->>‘status’ = %s"
        count_sql_query += " AND metadata->>‘status’ = %s"
        params.append(status)

    sql_query += " ORDER BY upload_date DESC LIMIT %s OFFSET %s;"
    params.extend([VIDEOS_PER_PAGE, offset])

    videos = execute_query(sql_query, tuple(params), fetch="all")
    total_count = execute_query(count_sql_query, tuple(params[:-2]), fetch="one")["count"]
    return videos, total_count

def get_random_video():
    query = "SELECT * FROM video_archive ORDER BY RANDOM() LIMIT 1;"
    return execute_query(query, fetch="one")

def increment_video_view_count(video_id):
    query = "UPDATE video_archive SET view_count = view_count + 1 WHERE id = %s;"
    return execute_query(query, (video_id,), commit=True)

def get_popular_videos():
    # Fetch most viewed
    most_viewed_query = "SELECT * FROM video_archive ORDER BY view_count DESC LIMIT 10;"
    most_viewed = execute_query(most_viewed_query, fetch="all")

    # Fetch highest rated (requires joining with video_ratings and calculating average)
    highest_rated_query = """
        SELECT va.*, AVG(vr.rating) as avg_rating
        FROM video_archive va
        JOIN video_ratings vr ON va.id = vr.video_id
        GROUP BY va.id
        ORDER BY avg_rating DESC, COUNT(vr.rating) DESC
        LIMIT 10;
    """
    highest_rated = execute_query(highest_rated_query, fetch="all")

    return {
        "most_viewed": most_viewed if most_viewed else [],
        "highest_rated": highest_rated if highest_rated else []
    }

def get_videos_ratings_bulk(video_ids):
    if not video_ids:
        return {}
    query = """
        SELECT video_id, AVG(rating) as avg_rating, COUNT(rating) as rating_count
        FROM video_ratings
        WHERE video_id = ANY(%s)
        GROUP BY video_id;
    """
    results = execute_query(query, (list(video_ids),), fetch="all")
    return {r["video_id"]: {"avg": r["avg_rating"], "count": r["rating_count"]} for r in results} if results else {}

def update_video_thumbnail(video_id, thumbnail_file_id):
    query = "UPDATE video_archive SET thumbnail_file_id = %s WHERE id = %s;"
    return execute_query(query, (thumbnail_file_id, video_id), commit=True)

def get_videos_without_thumbnail(limit=20):
    query = "SELECT id, message_id, chat_id, file_id, file_name, caption, metadata FROM video_archive WHERE thumbnail_file_id IS NULL LIMIT %s;"
    return execute_query(query, (limit,), fetch="all")

def update_video_metadata(video_id, metadata):
    query = "UPDATE video_archive SET metadata = %s WHERE id = %s;"
    return execute_query(query, (json.dumps(metadata), video_id), commit=True)

def get_video_file_id(video_id):
    query = "SELECT file_id FROM video_archive WHERE id = %s;"
    result = execute_query(query, (video_id,), fetch="one")
    return result["file_id"] if result else None

def update_video_file_id(video_id, new_file_id):
    query = "UPDATE video_archive SET file_id = %s WHERE id = %s;"
    return execute_query(query, (new_file_id, video_id), commit=True)

def get_videos_with_missing_file_id(limit=100):
    query = "SELECT id, message_id, chat_id, file_name, caption FROM video_archive WHERE file_id IS NULL LIMIT %s;"
    return execute_query(query, (limit,), fetch="all")

def get_videos_with_missing_metadata(limit=100):
    query = "SELECT id, message_id, chat_id, file_id, file_name, caption FROM video_archive WHERE metadata IS NULL OR metadata = ‘{}’::jsonb LIMIT %s;"
    return execute_query(query, (limit,), fetch="all")

def get_total_videos_count():
    query = "SELECT COUNT(*) FROM video_archive;"
    result = execute_query(query, fetch="one")
    return result["count"] if result else 0

def get_videos_by_category(category_id, page=0):
    offset = page * VIDEOS_PER_PAGE
    query = "SELECT * FROM video_archive WHERE category_id = %s ORDER BY upload_date DESC LIMIT %s OFFSET %s;"
    return execute_query(query, (category_id, VIDEOS_PER_PAGE, offset), fetch="all")

def get_videos_count_by_category(category_id):
    query = "SELECT COUNT(*) FROM video_archive WHERE category_id = %s;"
    result = execute_query(query, (category_id,), fetch="one")
    return result["count"] if result else 0

def move_video_to_category(video_id, new_category_id):
    query = "UPDATE video_archive SET category_id = %s WHERE id = %s;"
    return execute_query(query, (new_category_id, video_id), commit=True)

def move_videos_bulk(video_ids, new_category_id):
    if not video_ids:
        return 0
    query = "UPDATE video_archive SET category_id = %s WHERE id = ANY(%s);"
    return execute_query(query, (new_category_id, list(video_ids)), commit=True)
