
# services/rating_service.py

import logging
from repositories import rating_repository

logger = logging.getLogger(__name__)

def add_or_update_video_rating(video_id, user_id, rating):
    return rating_repository.add_video_rating(video_id, user_id, rating)

def get_user_rating_for_video(video_id, user_id):
    return rating_repository.get_user_video_rating(video_id, user_id)

def get_video_overall_rating_stats(video_id):
    return rating_repository.get_video_rating_stats(video_id)
