#!/usr/bin/env python3
# ==============================================================================
# ملف: db_manager_v3.py
# الوصف: طبقة توافق (Compatibility Layer) - تربط الكود القديم بالجديد
# هذا الملف يمكن أن يحل محل db_manager.py القديم
# ==============================================================================

# Re-export كل شيء من الـ Repositories الجديدة

from bot.database.connection import execute_query, get_db_connection, verify_and_repair_schema
from bot.database.repositories.video_repo import (
    VideoRepository, clear_search_cache, VIDEOS_PER_PAGE
)
from bot.database.repositories.user_repo import UserRepository
from bot.database.repositories.category_repo import CategoryRepository
from bot.database.repositories.comment_repo import CommentRepository
from bot.database.repositories.settings_repo import SettingsRepository

# ==========================================
# دوال التوافق (Backward Compatibility)
# كل دالة تُحوّل الاستدعاء القديم للـ Repository الجديد
# ==========================================

# --- Videos ---
add_video = VideoRepository.add
get_video_by_id = VideoRepository.get_by_id
get_videos = VideoRepository.get_by_category
get_random_video = VideoRepository.get_random
increment_video_view_count = VideoRepository.increment_views
search_videos = VideoRepository.search
search_videos_for_inline = VideoRepository.search_for_inline
get_popular_videos = VideoRepository.get_popular
delete_videos_by_ids = VideoRepository.delete_by_ids
move_videos_from_category = VideoRepository.move_from_category
move_videos_bulk = VideoRepository.move_bulk
update_video_thumbnail = VideoRepository.update_thumbnail
get_videos_without_thumbnail = VideoRepository.get_without_thumbnail

# --- Ratings ---
add_video_rating = VideoRepository.add_rating
get_video_rating_stats = VideoRepository.get_rating_stats
get_user_video_rating = VideoRepository.get_user_rating

# --- Favorites ---
add_to_favorites = VideoRepository.add_favorite
remove_from_favorites = VideoRepository.remove_favorite
is_in_favorites = VideoRepository.is_favorite
get_user_favorites = VideoRepository.get_favorites

# --- History ---
add_to_history = VideoRepository.add_to_history
get_user_history = VideoRepository.get_history

# --- Users ---
add_bot_user = UserRepository.add
get_all_user_ids = UserRepository.get_all_ids
get_subscriber_count = UserRepository.get_count
delete_bot_user = UserRepository.delete

# --- User State ---
set_user_state = UserRepository.set_state
get_user_state = UserRepository.get_state
clear_user_state = UserRepository.clear_state

# --- Categories ---
get_category_by_id = CategoryRepository.get_by_id
add_category = CategoryRepository.add
get_categories_tree = CategoryRepository.get_all
get_child_categories = CategoryRepository.get_children
delete_category_and_contents = CategoryRepository.delete_with_contents
delete_category_by_id = CategoryRepository.delete_by_id

# --- Comments ---
add_comment = CommentRepository.add
get_comment_by_id = CommentRepository.get_by_id
get_all_comments = CommentRepository.get_all
get_user_comments = CommentRepository.get_by_user
reply_to_comment = CommentRepository.reply
mark_comment_read = CommentRepository.mark_read
delete_comment = CommentRepository.delete
delete_all_comments = CommentRepository.delete_all
delete_user_comments = CommentRepository.delete_by_user
delete_old_comments = CommentRepository.delete_old
get_unread_comments_count = CommentRepository.get_unread_count
get_comment_count_for_video = CommentRepository.get_video_count
get_comments_stats = CommentRepository.get_stats

# --- Settings ---
get_active_category_id = SettingsRepository.get_active_category_id
set_active_category_id = SettingsRepository.set_active_category_id
add_required_channel = SettingsRepository.add_channel
remove_required_channel = SettingsRepository.remove_channel
get_required_channels = SettingsRepository.get_channels

# --- Stats ---
def get_bot_stats():
    stats = VideoRepository.get_stats()
    stats['category_count'] = CategoryRepository.get_count()
    return stats
