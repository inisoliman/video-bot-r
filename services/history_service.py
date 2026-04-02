
# services/history_service.py

import logging
from repositories import history_repository

logger = logging.getLogger(__name__)

def add_video_to_history(user_id, video_id):
    return history_repository.add_to_history(user_id, video_id)

def get_user_video_history(user_id, page=0, videos_per_page=10):
    return history_repository.get_user_history(user_id, page, videos_per_page)

def clean_old_user_history(days=30):
    return history_repository.clear_old_history(days)
