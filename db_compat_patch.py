#!/usr/bin/env python3
# ==============================================================================
# ملف: db_compat_patch.py
# الوصف: طبقة توافق - يُستخدَم لاستيراد الدوال المشتركة من db_manager فقط
# ملاحظة: هذا الملف لا يحتوي على أي دوال مكررة - كل شيء يُستورَد من db_manager
# ==============================================================================

"""
طبقة توافق لضمان عدم تضارب الاستيرادات.
جميع الدوال يتم استيرادها مباشرة من db_manager لتجنب التكرار.
"""

# استيراد الدوال المشتركة من db_manager للاستخدام الخارجي
from db_manager import (
    execute_query, get_db_connection, get_connection_pool,
    add_bot_user, get_random_video, increment_video_view_count,
    get_categories_tree, add_video, get_category_by_id, get_videos,
    get_child_categories, search_videos, get_popular_videos,
    get_bot_stats, get_subscriber_count, get_all_user_ids,
    add_to_favorites, is_video_favorite, remove_from_favorites,
    get_user_favorites, add_to_history, get_user_history,
    add_video_rating, get_user_video_rating, get_video_rating_stats,
    add_required_channel, remove_required_channel, get_required_channels,
    move_videos_bulk, delete_videos_by_ids, get_video_by_id,
    delete_category_and_contents, move_videos_from_category, delete_category_by_id,
    set_user_state, get_user_state, clear_user_state,
    VIDEOS_PER_PAGE, CALLBACK_DELIMITER, admin_steps, user_last_search,
    ensure_schema, verify_and_repair_schema,
    EXPECTED_SCHEMA, DB_CONFIG, DB_POOL_MIN, DB_POOL_MAX,
)
