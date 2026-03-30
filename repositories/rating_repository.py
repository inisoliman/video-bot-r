
# repositories/rating_repository.py

from core.db import execute_query
import logging

logger = logging.getLogger(__name__)

def add_video_rating(video_id, user_id, rating):
    query = """
        INSERT INTO video_ratings (video_id, user_id, rating)
        VALUES (%s, %s, %s)
        ON CONFLICT (video_id, user_id) DO UPDATE SET
            rating = EXCLUDED.rating;
    """
    return execute_query(query, (video_id, user_id, rating), commit=True)

def get_user_video_rating(video_id, user_id):
    query = "SELECT rating FROM video_ratings WHERE video_id = %s AND user_id = %s;"
    result = execute_query(query, (video_id, user_id), fetch="one")
    return result["rating"] if result else None

def get_video_rating_stats(video_id):
    query = "SELECT AVG(rating) as avg, COUNT(rating) as count FROM video_ratings WHERE video_id = %s;"
    return execute_query(query, (video_id,), fetch="one")
