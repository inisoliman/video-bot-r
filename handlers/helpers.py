# handlers/helpers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import math
import re
import logging
from db_manager import (
    get_child_categories, get_category_by_id, get_user_video_rating,
    get_video_rating_stats, VIDEOS_PER_PAGE, CALLBACK_DELIMITER,
    get_required_channels, is_video_favorite # [تعديل] إضافة is_video_favorite للتحقق من المفضلة
)

logger = logging.getLogger(__name__)

# القواميس المشتركة لتخزين حالة المستخدمين
admin_steps = {}
user_last_search = {}

def check_subscription(bot, user_id):
    """
    Verifies if a user is subscribed to all required channels.
    This function is now centralized here.
    """
    required_channels = get_required_channels()
    if not required_channels:
        return True, []
    
    unsubscribed = []
    for channel in required_channels:
        try:
            member = bot.get_chat_member(channel['channel_id'], user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                unsubscribed.append(channel)
        except telebot.apihelper.ApiTelegramException as e:
            error_msg = str(e.description).lower() if hasattr(e, 'description') else str(e).lower()
            if 'user not found' in error_msg or 'chat not found' in error_msg or 'bad request' in error_msg:
                logger.warning(f"Could not check user {user_id} in channel {channel['channel_id']}. Assuming subscribed. Error: {e}")
            elif 'forbidden' in error_msg or 'kicked' in error_msg or 'left' in error_msg:
                # المستخدم غير مشترك أو محظور أو غادر القناة
                unsubscribed.append(channel)
            else:
                # في حالة أخطاء أخرى، نفترض عدم الاشتراك للأمان
                logger.error(f"Error checking subscription for user {user_id} in channel {channel['channel_id']}: {e}")
                unsubscribed.append(channel)
        except Exception as e:
            logger.error(f"Unexpected error checking subscription for user {user_id} in channel {channel['channel_id']}: {e}")
            unsubscribed.append(channel)
    
    return not unsubscribed, unsubscribed

def list_videos(bot, message, edit_message=None, parent_id=None):
    """
    Displays the category selection menu.
    This function is now centralized here.
    """
    keyboard = create_categories_keyboard(parent_id)
    text = "اختر تصنيفًا لعرض محتوياته:" if keyboard.keyboard and keyboard.keyboard[0] else "لا توجد تصنيفات متاحة حالياً."
    try:
        if edit_message:
            bot.edit_message_text(text, edit_message.chat.id, edit_message.message_id, reply_markup=keyboard)
        else:
            bot.reply_to(message, text, reply_markup=keyboard)
    except telebot.apihelper.ApiTelegramException as e:
        if 'message is not modified' not in e.description:
            logger.error(f"Error in list_videos: {e}")


def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    # [تعديل] إضافة أزرار المفضلة والسجل
    markup.add(KeyboardButton("🎬 عرض كل الفيديوهات"), KeyboardButton("🔥 الفيديوهات الشائعة"))
    markup.add(KeyboardButton("⭐ المفضلة"), KeyboardButton("📺 سجل المشاهدة"))
    markup.add(KeyboardButton("🍿 اقترح لي فيلم"), KeyboardButton("🔍 بحث"))
    return markup

def create_categories_keyboard(parent_id=None):
    keyboard = InlineKeyboardMarkup(row_width=2)
    categories = get_child_categories(parent_id)
    buttons = [InlineKeyboardButton(cat['name'], callback_data=f"cat::{cat['id']}::0") for cat in categories]
    keyboard.add(*buttons)
    if parent_id:
        parent_category = get_category_by_id(parent_id)
        if parent_category and parent_category.get('parent_id') is not None:
            keyboard.add(InlineKeyboardButton("🔙 رجوع", callback_data=f"cat::{parent_category['parent_id']}::0"))
        else:
            keyboard.add(InlineKeyboardButton("🔙 رجوع للتصنيفات الرئيسية", callback_data="back_to_cats"))
    return keyboard

def format_duration(seconds):
    if not seconds or not isinstance(seconds, (int, float)): return ""
    secs = int(seconds)
    mins, secs = divmod(secs, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02}:{mins:02}:{secs:02}" if hours > 0 else f"{mins:02}:{secs:02}"

def format_video_display_info(video):
    metadata = video.get('metadata') or {}
    series_name = metadata.get('series_name')
    season = metadata.get('season_number')
    episode = metadata.get('episode_number')
    title_base = series_name or (video.get('caption') or video.get('file_name') or "فيديو").split('\n')[0]
    title_base = title_base.strip()
    parts = []
    if season: parts.append(f"م{season}")
    if episode: parts.append(f"ح{episode}")
    title_suffix = f" - {' '.join(parts)}" if parts else ""
    title = f"{video['id']}. {title_base}{title_suffix}"
    info_parts = []
    if metadata.get('status'): info_parts.append(metadata['status'])
    if metadata.get('quality_resolution'): info_parts.append(metadata['quality_resolution'])
    if metadata.get('duration'): info_parts.append(format_duration(metadata['duration']))
    info_line = f" ({' | '.join(info_parts)})" if info_parts else ""
    # [إصلاح] استخدام .get لتجنب KeyError في حال لم يتم تعيين القيمة بعد
    rating_value = video.get('avg_rating') 
    rating_text = f" ⭐ {rating_value:.1f}/5" if rating_value is not None and rating_value != 0 else ""
    views_text = f" 👁️ {video.get('view_count', 0)}"
    return f"{title}{info_line}{rating_text}{views_text}"

def create_paginated_keyboard(videos, total_count, current_page, action_prefix, context_id):
    keyboard = InlineKeyboardMarkup(row_width=1)

    # [الإصلاح الجذري لـ KeyError: 'avg_rating']
    # يجب تحويل كائن DictRow إلى قاموس عادي قبل محاولة تعديله/إضافة مفاتيح جديدة.
    mutable_videos = [dict(v) for v in videos] 
    
    for video in mutable_videos:
        # 1. جلب إحصائيات التقييم
        stats = get_video_rating_stats(video['id'])
        
        # 2. إضافة avg_rating إلى القاموس القابل للتعديل
        # تحديد avg_rating لإضافته إلى العرض
        video['avg_rating'] = stats.get('avg') if stats and stats.get('avg') is not None else 0 

        # 3. عرض معلومات الفيديو
        display_title = format_video_display_info(video)
        keyboard.add(InlineKeyboardButton(display_title, callback_data=f"video::{video['id']}::{video['message_id']}::{video['chat_id']}"))

    nav_buttons = []
    base_callback = f"{action_prefix}::{context_id}"

    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"{base_callback}::{current_page - 1}"))

    total_pages = math.ceil(total_count / VIDEOS_PER_PAGE)
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"{base_callback}::{current_page + 1}"))

    if nav_buttons:
        keyboard.add(*nav_buttons, row_width=2)

    keyboard.add(InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="back_to_main"))
    return keyboard

def create_combined_keyboard(child_categories, videos, total_video_count, current_page, parent_category_id):
    keyboard = InlineKeyboardMarkup()
    if child_categories:
        keyboard.add(InlineKeyboardButton("📂--- الأقسام الفرعية ---📂", callback_data="noop"), row_width=1)
        cat_buttons = [InlineKeyboardButton(f"📁 {cat['name']}", callback_data=f"cat::{cat['id']}::0") for cat in child_categories]
        for i in range(0, len(cat_buttons), 2):
            keyboard.add(*cat_buttons[i:i+2])
    if videos:
        if child_categories:
            keyboard.add(InlineKeyboardButton("🎬--- الفيديوهات ---🎬", callback_data="noop"), row_width=1)
        
        # [تعديل] يجب أيضاً تحويل كائنات DictRow هنا
        mutable_videos = [dict(v) for v in videos] 

        for video in mutable_videos:
            # جلب إحصائيات التقييم وإضافتها للقاموس
            stats = get_video_rating_stats(video['id'])
            video['avg_rating'] = stats.get('avg') if stats and stats.get('avg') is not None else 0 

            display_title = format_video_display_info(video)
            keyboard.add(InlineKeyboardButton(display_title, callback_data=f"video::{video['id']}::{video['message_id']}::{video['chat_id']}"), row_width=1)
            
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"cat::{parent_category_id}::{current_page - 1}"))
    total_pages = math.ceil(total_video_count / VIDEOS_PER_PAGE)
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"cat::{parent_category_id}::{current_page + 1}"))
    if nav_buttons:
        keyboard.add(*nav_buttons, row_width=2)
    parent_category = get_category_by_id(parent_category_id)
    if parent_category and parent_category.get('parent_id') is not None:
        keyboard.add(InlineKeyboardButton("🔙 رجوع", callback_data=f"cat::{parent_category['parent_id']}::0"), row_width=1)
    else:
        keyboard.add(InlineKeyboardButton("🔙 رجوع للتصنيفات الرئيسية", callback_data="back_to_cats"), row_width=1)
    return keyboard

def create_video_action_keyboard(video_id, user_id):
    keyboard = InlineKeyboardMarkup(row_width=5)
    user_rating = get_user_video_rating(video_id, user_id)
    is_fav = is_video_favorite(user_id, video_id) # [تعديل] التحقق من حالة المفضلة

    # [تعديل] إضافة زر المفضلة
    fav_text = "⭐ إزالة من المفضلة" if is_fav else "☆ إضافة للمفضلة"
    fav_data = f"fav::remove::{video_id}" if is_fav else f"fav::add::{video_id}"
    keyboard.add(InlineKeyboardButton(fav_text, callback_data=fav_data), row_width=1)
    
    # أزرار التقييم
    buttons = [InlineKeyboardButton("🌟" if user_rating == i else "☆", callback_data=f"rate::{video_id}::{i}") for i in range(1, 6)]
    keyboard.add(*buttons)
    
    stats = get_video_rating_stats(video_id)
    if stats and stats.get('avg') is not None:
        keyboard.add(InlineKeyboardButton(f"متوسط التقييم: {stats['avg']:.1f} ({stats['count']} تقييم)", callback_data="noop"), row_width=1)
    return keyboard

def generate_grouping_key(metadata, caption, file_name):
    series_name = metadata.get('series_name')
    if not series_name:
        raw_title = (caption or file_name or "").split('\n')[0]
        cleaned_title = re.sub(r'^[\d\s\W_-]+', '', raw_title).strip()
        series_name = cleaned_title
    if not series_name: return None
    sanitized_name = re.sub(r'[^a-zA-Z0-9\s-]', '', series_name).strip()
    sanitized_name = re.sub(r'\s+', '-', sanitized_name).lower()
    season = metadata.get('season_number')
    episode = metadata.get('episode_number')
    if season or episode:
        key_parts = ["series", sanitized_name]
        if season: key_parts.append(f"s{season:02d}")
        if episode: key_parts.append(f"e{episode:02d}")
        return "-".join(key_parts)
    else:
        return f"movie-{sanitized_name}"
