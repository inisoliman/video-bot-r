# handlers/user_handlers.py (patched imports)

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging

# أولاً نحاول الاستيراد من db_manager، وإن فشل نستخدم طبقة التوافق
try:
    from db_manager import (
        add_bot_user, get_popular_videos, search_videos,
        get_random_video, increment_video_view_count, get_categories_tree, add_video,
        get_active_category_id, get_user_favorites, get_user_history, add_to_history
    )
except Exception:
    from db_compat_patch import (
        add_bot_user, get_random_video, increment_video_view_count, get_categories_tree, add_video
    )
    from db_manager import (
        get_popular_videos, search_videos,
        get_active_category_id, get_user_favorites, get_user_history, add_to_history
    )

from .helpers import (
    main_menu, create_paginated_keyboard,
    create_video_action_keyboard, user_last_search, generate_grouping_key,
    check_subscription, list_videos
)
from utils import extract_video_metadata
from state_manager import (
    set_user_waiting_for_input, States, get_user_waiting_context, 
    clear_user_waiting_state, state_handler 
)

logger = logging.getLogger(__name__)

# باقي الملف بدون تغيير ...
