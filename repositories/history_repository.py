"""repositories/history_repository.py"""

from core.db import execute_query
import logging

logger = logging.getLogger(__name__)

def add_to_history(user_id, video_id):
    query = """
        INSERT INTO user_history (user_id, video_id, last_watched)
        VALUES (%s, %s, NOW())
        ON CONFLICT (user_id, video_id) DO UPDATE SET
            last_watched = NOW();
    """
    return execute_query(query, (user_id, video_id), commit=True)

def get_user_history(user_id, page=0, videos_per_page=10):
    offset = page * videos_per_page
    query = """
        SELECT va.* FROM video_archive va
        JOIN user_history uh ON va.id = uh.video_id
        WHERE uh.user_id = %s
        ORDER BY uh.last_watched DESC
        LIMIT %s OFFSET %s;
    """
    count_query = "SELECT COUNT(*) FROM user_history WHERE user_id = %s;"
    videos = execute_query(query, (user_id, videos_per_page, offset), fetch="all")
    total_count = execute_query(count_query, (user_id,), fetch="one")["count"]
    return videos, total_count

def clear_old_history(days=30):
    query = "DELETE FROM user_history WHERE last_watched < NOW() - INTERVAL ‘%s days’;"
    return execute_query(query, (days,), commit=True)
