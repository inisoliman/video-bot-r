"""repositories/favorite_repository.py"""

from core.db import execute_query
import logging

logger = logging.getLogger(__name__)

def add_to_favorites(user_id, video_id):
    query = """
        INSERT INTO user_favorites (user_id, video_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id, video_id) DO NOTHING;
    """
    return execute_query(query, (user_id, video_id), commit=True)

def remove_from_favorites(user_id, video_id):
    query = "DELETE FROM user_favorites WHERE user_id = %s AND video_id = %s;"
    return execute_query(query, (user_id, video_id), commit=True)

def is_video_favorite(user_id, video_id):
    query = "SELECT 1 FROM user_favorites WHERE user_id = %s AND video_id = %s;"
    return execute_query(query, (user_id, video_id), fetch="one") is not None

def get_user_favorites(user_id, page=0, videos_per_page=10):
    offset = page * videos_per_page
    query = """
        SELECT va.* FROM video_archive va
        JOIN user_favorites uf ON va.id = uf.video_id
        WHERE uf.user_id = %s
        ORDER BY uf.date_added DESC
        LIMIT %s OFFSET %s;
    """
    count_query = "SELECT COUNT(*) FROM user_favorites WHERE user_id = %s;"
    videos = execute_query(query, (user_id, videos_per_page, offset), fetch="all")
    total_count = execute_query(count_query, (user_id,), fetch="one")["count"]
    return videos, total_count
