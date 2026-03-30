
# services/video_service.py

import logging
import time
from repositories import video_repository, rating_repository
from config.config import Config

logger = logging.getLogger(__name__)

# Cache for search results
_search_cache = {}

def _get_cache_key(query, page, category_id, quality, status):
    """Generates a unique key for the search cache."""
    return f"{query}:{page}:{category_id}:{quality}:{status}"

def _get_cached_search(cache_key):
    """Retrieves search results from cache if valid."""
    if cache_key in _search_cache:
        cached_result, cached_time = _search_cache[cache_key]
        if time.time() - cached_time < Config.SEARCH_CACHE_TTL:
            logger.debug(f"Cache hit for search: {cache_key[:30]}...")
            return cached_result
    return None

def _set_cached_search(cache_key, result):
    """Stores search results in cache."""
    _search_cache[cache_key] = (result, time.time())

def add_new_video(message_id, chat_id, caption, file_name, file_id, category_id, metadata, grouping_key, thumbnail_file_id, content_type):
    return video_repository.add_video(message_id, chat_id, caption, file_name, file_id, category_id, metadata, grouping_key, thumbnail_file_id, content_type)

def get_video_details(video_id):
    return video_repository.get_video_by_id(video_id)

def get_paginated_videos(category_id=None, page=0):
    return video_repository.get_videos(category_id, page)

def search_videos_with_filters(query, page=0, category_id=None, quality=None, status=None):
    cache_key = _get_cache_key(query, page, category_id, quality, status)
    cached_result = _get_cached_search(cache_key)
    if cached_result:
        return cached_result

    videos, total_count = video_repository.search_videos(query, page, category_id, quality, status)
    result = (videos, total_count)
    _set_cached_search(cache_key, result)
    return result

def get_random_video_suggestion():
    return video_repository.get_random_video()

def record_video_view(video_id, user_id):
    video_repository.increment_video_view_count(video_id)
    # Assuming add_to_history is handled by a separate history service or directly in handler

def get_popular_and_highest_rated_videos():
    return video_repository.get_popular_videos()

def get_bulk_video_ratings(video_ids):
    return rating_repository.get_videos_ratings_bulk(video_ids)

def update_video_thumbnail_file_id(video_id, thumbnail_file_id):
    return video_repository.update_video_thumbnail(video_id, thumbnail_file_id)

def get_videos_without_thumbnails(limit=20):
    return video_repository.get_videos_without_thumbnail(limit)

def update_video_metadata_info(video_id, metadata):
    return video_repository.update_video_metadata(video_id, metadata)

def get_video_file_identifier(video_id):
    return video_repository.get_video_file_id(video_id)

def update_video_file_identifier(video_id, new_file_id):
    return video_repository.update_video_file_id(video_id, new_file_id)

def get_videos_with_missing_file_identifiers(limit=100):
    return video_repository.get_videos_with_missing_file_id(limit)

def get_videos_with_missing_metadata_info(limit=100):
    return video_repository.get_videos_with_missing_metadata(limit)

def get_total_videos():
    return video_repository.get_total_videos_count()

def get_videos_in_category(category_id, page=0):
    return video_repository.get_videos_by_category(category_id, page)

def get_video_count_in_category(category_id):
    return video_repository.get_videos_count_by_category(category_id)

def move_video_to_new_category(video_id, new_category_id):
    return video_repository.move_video_to_category(video_id, new_category_id)

def bulk_move_videos_to_category(video_ids, new_category_id):
    return video_repository.move_videos_bulk(video_ids, new_category_id)
