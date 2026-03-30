
# utils/telegram_utils.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import math
import re
import logging

from config.constants import (
    VIDEOS_PER_PAGE, CALLBACK_DELIMITER, EMOJI_FOLDER, EMOJI_LEAF, EMOJI_DIAMOND,
    EMOJI_BACK, EMOJI_SEARCH, EMOJI_STAR, EMOJI_FIRE, EMOJI_EYE, EMOJI_FILM,
    EMOJI_FAVORITE, EMOJI_HISTORY, EMOJI_COMMENT, EMOJI_CHECK, EMOJI_UNSUBSCRIBE,
    EMOJI_ERROR, EMOJI_WARNING, EMOJI_ADMIN, EMOJI_SEASON, EMOJI_EPISODE,
    PARSE_MODE_HTML, PARSE_MODE_MARKDOWN_V2
)
from services import video_service, category_service, favorite_service, rating_service

logger = logging.getLogger(__name__)

def format_duration(seconds):
    if not seconds or not isinstance(seconds, (int, float)): return ""
    secs = int(seconds)
    mins, secs = divmod(secs, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02}:{mins:02}:{secs:02}" if hours > 0 else f"{mins:02}:{secs:02}"

def format_video_display_info(video):
    metadata = video.get("metadata") or {}
    series_name = metadata.get("series_name")
    season = metadata.get("season_number")
    episode = metadata.get("episode_number")
    title_base = series_name or (video.get("caption") or video.get("file_name") or "فيديو").split("\n")[0]
    title_base = title_base.strip()
    parts = []
    if season: parts.append(f"{EMOJI_SEASON}{season}")
    if episode: parts.append(f"{EMOJI_EPISODE}{episode}")
    title_suffix = f" - {" ".join(parts)}" if parts else ""
    title = f"{video["id"]}. {title_base}{title_suffix}"
    info_parts = []
    if metadata.get("status"): info_parts.append(metadata["status"])
    if metadata.get("quality_resolution"): info_parts.append(metadata["quality_resolution"])
    if metadata.get("duration"): info_parts.append(format_duration(metadata["duration"]))
    info_line = f" ({" | ".join(info_parts)})" if info_parts else ""
    
    rating_value = video.get("avg_rating") 
    rating_text = f" {EMOJI_STAR} {rating_value:.1f}/5" if rating_value is not None and rating_value != 0 else ""
    views_text = f" {EMOJI_EYE} {video.get("view_count", 0)}"
    return f"{title}{info_line}{rating_text}{views_text}"

def main_menu_keyboard(bot_username=None):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton(f"{EMOJI_FILM} عرض كل الفيديوهات"), KeyboardButton(f"{EMOJI_FIRE} الفيديوهات الشائعة"))
    markup.add(KeyboardButton(f"{EMOJI_FAVORITE} المفضلة"), KeyboardButton(f"{EMOJI_HISTORY} سجل المشاهدة"))
    markup.add(KeyboardButton(f"{EMOJI_RANDOM_VIDEO} اقترح لي فيلم"), KeyboardButton(f"{EMOJI_SEARCH} بحث"))
    
    if bot_username:
        markup.add(KeyboardButton(f"{EMOJI_SEARCH} بحث سريع في أي محادثة"))
    
    return markup

def create_paginated_keyboard(videos, total_count, current_page, action_prefix, context_id):
    keyboard = InlineKeyboardMarkup(row_width=1)

    mutable_videos = [dict(v) for v in videos] 
    
    video_ids = [v["id"] for v in mutable_videos]
    ratings_dict = video_service.get_bulk_video_ratings(video_ids)
    
    for video in mutable_videos:
        rating_info = ratings_dict.get(video["id"], {"avg": 0, "count": 0})
        video["avg_rating"] = rating_info["avg"]

        display_title = format_video_display_info(video)
        keyboard.add(InlineKeyboardButton(display_title, callback_data=f"video{CALLBACK_DELIMITER}{video["id"]}{CALLBACK_DELIMITER}{video["message_id"]}{CALLBACK_DELIMITER}{video["chat_id"]}"))

    nav_buttons = []
    base_callback = f"{action_prefix}{CALLBACK_DELIMITER}{context_id}"

    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton(f"{EMOJI_BACK} السابق", callback_data=f"{base_callback}{CALLBACK_DELIMITER}{current_page - 1}"))

    total_pages = math.ceil(total_count / VIDEOS_PER_PAGE)
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(f"التالي {EMOJI_BACK}", callback_data=f"{base_callback}{CALLBACK_DELIMITER}{current_page + 1}"))

    if nav_buttons:
        keyboard.add(*nav_buttons, row_width=2)

    keyboard.add(InlineKeyboardButton(f"{EMOJI_BACK} رجوع للقائمة الرئيسية", callback_data="back_to_main"))
    return keyboard

def create_video_action_keyboard(video_id, user_id):
    keyboard = InlineKeyboardMarkup(row_width=5)
    user_rating = rating_service.get_user_rating_for_video(video_id, user_id)
    is_fav = favorite_service.check_if_video_is_favorite(user_id, video_id)

    fav_text = f"{EMOJI_STAR} إزالة من المفضلة" if is_fav else f"{EMOJI_STAR} إضافة للمفضلة"
    fav_data = f"fav{CALLBACK_DELIMITER}remove{CALLBACK_DELIMITER}{video_id}" if is_fav else f"fav{CALLBACK_DELIMITER}add{CALLBACK_DELIMITER}{video_id}"
    keyboard.add(InlineKeyboardButton(fav_text, callback_data=fav_data), row_width=1)
    
    buttons = [InlineKeyboardButton("🌟" if user_rating == i else "☆", callback_data=f"rate{CALLBACK_DELIMITER}{video_id}{CALLBACK_DELIMITER}{i}") for i in range(1, 6)]
    keyboard.add(*buttons)
    
    stats = rating_service.get_video_overall_rating_stats(video_id)
    if stats and stats.get("avg") is not None:
        keyboard.add(InlineKeyboardButton(f"متوسط التقييم: {stats["avg"]:.1f} ({stats["count"]} تقييم)", callback_data="noop"), row_width=1)
    
    keyboard.add(InlineKeyboardButton(f"{EMOJI_COMMENT} إضافة تعليق", callback_data=f"add_comment{CALLBACK_DELIMITER}{video_id}"), row_width=1)
    
    return keyboard


def create_categories_keyboard(parent_id=None):
    keyboard = InlineKeyboardMarkup(row_width=2)
    categories = category_service.get_child_categories(parent_id)
    buttons = [InlineKeyboardButton(cat["name"], callback_data=f"cat{CALLBACK_DELIMITER}{cat["id"]}{CALLBACK_DELIMITER}0") for cat in categories]
    keyboard.add(*buttons)
    if parent_id:
        parent_category = category_service.get_category_details(parent_id)
        if parent_category and parent_category.get("parent_id") is not None:
            keyboard.add(InlineKeyboardButton(f"{EMOJI_BACK} رجوع", callback_data=f"cat{CALLBACK_DELIMITER}{parent_category["parent_id"]}{CALLBACK_DELIMITER}0"))
        else:
            keyboard.add(InlineKeyboardButton(f"{EMOJI_BACK} رجوع للتصنيفات الرئيسية", callback_data="back_to_cats"))
    return keyboard

def create_hierarchical_category_keyboard(callback_prefix, add_back_button=True):
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    all_categories = category_service.get_all_categories_tree()
    
    if not all_categories:
        keyboard.add(InlineKeyboardButton(f"{EMOJI_ERROR} لا توجد تصنيفات", callback_data="noop"))
        return keyboard
    
    tree = category_service.build_category_tree_display(all_categories)
    
    for cat in tree:
        keyboard.add(
            InlineKeyboardButton(
                cat["name"], 
                callback_data=f"{callback_prefix}{CALLBACK_DELIMITER}{cat["id"]}"
            )
        )
    
    if add_back_button:
        keyboard.add(InlineKeyboardButton(f"{EMOJI_BACK} إلغاء", callback_data="back_to_main"))
    
    return keyboard

def create_combined_keyboard(child_categories, videos, total_video_count, current_page, parent_category_id):
    keyboard = InlineKeyboardMarkup()
    if child_categories:
        keyboard.add(InlineKeyboardButton(f"{EMOJI_FOLDER}--- الأقسام الفرعية ---{EMOJI_FOLDER}", callback_data="noop"), row_width=1)
        cat_buttons = [InlineKeyboardButton(f"{EMOJI_FOLDER} {cat["name"]}", callback_data=f"cat{CALLBACK_DELIMITER}{cat["id"]}{CALLBACK_DELIMITER}0") for cat in child_categories]
        for i in range(0, len(cat_buttons), 2):
            keyboard.add(*cat_buttons[i:i+2])
    if videos:
        if child_categories:
            keyboard.add(InlineKeyboardButton(f"{EMOJI_FILM}--- الفيديوهات ---{EMOJI_FILM}", callback_data="noop"), row_width=1)
        
        mutable_videos = [dict(v) for v in videos] 

        video_ids = [v["id"] for v in mutable_videos]
        ratings_dict = video_service.get_bulk_video_ratings(video_ids)
        
        for video in mutable_videos:
            rating_info = ratings_dict.get(video["id"], {"avg": 0, "count": 0})
            video["avg_rating"] = rating_info["avg"]

            display_title = format_video_display_info(video)
            keyboard.add(InlineKeyboardButton(display_title, callback_data=f"video{CALLBACK_DELIMITER}{video["id"]}{CALLBACK_DELIMITER}{video["message_id"]}{CALLBACK_DELIMITER}{video["chat_id"]}"))

    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton(f"{EMOJI_BACK} السابق", callback_data=f"cat{CALLBACK_DELIMITER}{parent_category_id}{CALLBACK_DELIMITER}{current_page - 1}"))
    total_pages = math.ceil(total_video_count / VIDEOS_PER_PAGE)
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(f"التالي {EMOJI_BACK}", callback_data=f"cat{CALLBACK_DELIMITER}{parent_category_id}{CALLBACK_DELIMITER}{current_page + 1}"))
    if nav_buttons:
        keyboard.add(*nav_buttons, row_width=2)
    parent_category = category_service.get_category_details(parent_category_id)
    if parent_category and parent_category.get("parent_id") is not None:
        keyboard.add(InlineKeyboardButton(f"{EMOJI_BACK} رجوع", callback_data=f"cat{CALLBACK_DELIMITER}{parent_category["parent_id"]}{CALLBACK_DELIMITER}0"), row_width=1)
    else:
        keyboard.add(InlineKeyboardButton(f"{EMOJI_BACK} رجوع للتصنيفات الرئيسية", callback_data="back_to_cats"), row_width=1)
    return keyboard

def list_videos_keyboard(parent_id=None):
    keyboard = create_categories_keyboard(parent_id)
    return keyboard

def send_message_with_main_menu(bot, chat_id, text, bot_username=None):
    markup = main_menu_keyboard(bot_username)
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode=PARSE_MODE_HTML)

def edit_message_with_main_menu(bot, chat_id, message_id, text, bot_username=None):
    markup = main_menu_keyboard(bot_username)
    bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode=PARSE_MODE_HTML)

def get_channel_link(channel_id, channel_name=None):
    channel_id_str = str(channel_id)
    if channel_id_str.startswith("-100"):
        return f"https://t.me/c/{channel_id_str.replace("-100", "")}"
    elif channel_id_str.startswith("@"):
        return f"https://t.me/{channel_id_str[1:]}"
    else:
        return f"https://t.me/{channel_id_str}"
