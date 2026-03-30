
# services/favorite_service.py

import logging
from repositories import favorite_repository

logger = logging.getLogger(__name__)

def add_video_to_favorites(user_id, video_id):
    return favorite_repository.add_to_favorites(user_id, video_id)

def remove_video_from_favorites(user_id, video_id):
    return favorite_repository.remove_from_favorites(user_id, video_id)

def check_if_video_is_favorite(user_id, video_id):
    return favorite_repository.is_video_favorite(user_id, video_id)

def get_user_favorite_videos(user_id, page=0, videos_per_page=10):
    return favorite_repository.get_user_favorites(user_id, page, videos_per_page)
