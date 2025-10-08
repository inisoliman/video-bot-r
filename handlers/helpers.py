# handlers/helpers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import math
import re
import logging

from db_manager import (
    get_child_categories, get_category_by_id, get_user_video_rating,
    get_video_rating_stats, VIDEOS_PER_PAGE, CALLBACK_DELIMITER,
    get_required_channels, is_video_favorite
)

logger = logging.getLogger(__name__)

# القواميس المشتركة لتخزين حالة المستخدمين
admin_steps = {}
user_last_search = {}

def check_subscription(bot, user_id):
    """
    التحقق من اشتراك المستخدم في جميع القنوات المطلوبة.
    تم تحسينه ليعمل على جميع الأجهزة (موبايل + كمبيوتر).
    """
    required_channels = get_required_channels()
    if not required_channels:
        return True, []

    unsubscribed = []

    for channel in required_channels:
        try:
            # استخدام get_chat_member مع معالجة أفضل للأخطاء
            member = bot.get_chat_member(channel['channel_id'], user_id)

            # التحقق من الحالات: creator, administrator, member = مشترك
            # left, kicked = غير مشترك
            if member.status in ['left', 'kicked']:
                unsubscribed.append(channel)

        except telebot.apihelper.ApiTelegramException as e:
            # معالجة الأخطاء الشائعة
            error_desc = str(e.description).lower() if hasattr(e, 'description') else str(e).lower()

            # إذا كان المستخدم ليس عضواً
            if 'user not found' in error_desc or 'user is not a member' in error_desc:
                unsubscribed.append(channel)
            # إذا كانت القناة خاصة ولا يمكن التحقق
            elif 'chat not found' in error_desc or 'chat_id is invalid' in error_desc:
                logger.warning(f"Cannot verify channel {channel['channel_id']} - might be invalid. Skipping.")
                continue
            # إذا كان البوت ليس عضواً في القناة
            elif 'bot is not a member' in error_desc or 'forbidden' in error_desc:
                logger.error(f"Bot is not a member of channel {channel['channel_id']}.")
                continue
            else:
                logger.warning(f"Error checking subscription for user {user_id}: {e}")
                unsubscribed.append(channel)

        except Exception as e:
            logger.error(f"Unexpected error checking subscription: {e}", exc_info=True)
            unsubscribed.append(channel)

    return len(unsubscribed) == 0, unsubscribed

def main_menu():
    """القائمة الرئيسية للمستخدم"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton("🎬 التصنيفات"),
        KeyboardButton("🔍 بحث")
    )
    keyboard.add(
        KeyboardButton("⭐ المفضلة"),
        KeyboardButton("📜 سجل المشاهدة")
    )
    keyboard.add(
        KeyboardButton("📈 الأكثر مشاهدة"),
        KeyboardButton("🌟 الأعلى تقييماً")
    )
    return keyboard

def create_categories_keyboard():
    """إنشاء لوحة مفاتيح التصنيفات الرئيسية فقط"""
    from db_manager import get_categories_tree
    categories = get_categories_tree()
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)

    for cat in categories:
        keyboard.add(KeyboardButton(cat['name']))

    keyboard.add(KeyboardButton("🔙 رجوع"))
    return keyboard

def create_combined_keyboard(child_categories, videos, total_videos, page, parent_category_id):
    """إنشاء لوحة مفاتيح مدمجة للتصنيفات الفرعية والفيديوهات"""
    keyboard = InlineKeyboardMarkup(row_width=2)

    # إضافة التصنيفات الفرعية
    for child in child_categories:
        keyboard.add(InlineKeyboardButton(
            f"📁 {child['name']}", 
            callback_data=f"cat{CALLBACK_DELIMITER}{child['id']}{CALLBACK_DELIMITER}0"
        ))

    # إضافة الفيديوهات
    for video in videos:
        title = (video['caption'] or '').split('\n')[0] or f"فيديو {video['id']}"
        keyboard.add(InlineKeyboardButton(
            f"🎬 {title[:40]}...", 
            callback_data=f"video{CALLBACK_DELIMITER}{video['id']}{CALLBACK_DELIMITER}{video['message_id']}{CALLBACK_DELIMITER}{video['chat_id']}"
        ))

    # أزرار التنقل
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(
            "⬅️ السابق", 
            callback_data=f"cat{CALLBACK_DELIMITER}{parent_category_id}{CALLBACK_DELIMITER}{page-1}"
        ))

    total_pages = math.ceil(total_videos / VIDEOS_PER_PAGE)
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(
            "التالي ➡️", 
            callback_data=f"cat{CALLBACK_DELIMITER}{parent_category_id}{CALLBACK_DELIMITER}{page+1}"
        ))

    if nav_buttons:
        keyboard.row(*nav_buttons)

    keyboard.add(InlineKeyboardButton("🔙 رجوع للتصنيفات", callback_data="back_to_cats"))

    return keyboard

def create_paginated_keyboard(videos, total_count, page, action_prefix, context_id):
    """إنشاء لوحة مفاتيح مع صفحات للفيديوهات"""
    keyboard = InlineKeyboardMarkup(row_width=1)

    for video in videos:
        title = (video['caption'] or '').split('\n')[0] or f"فيديو {video['id']}"
        keyboard.add(InlineKeyboardButton(
            f"🎬 {title[:50]}",
            callback_data=f"video{CALLBACK_DELIMITER}{video['id']}{CALLBACK_DELIMITER}{video['message_id']}{CALLBACK_DELIMITER}{video['chat_id']}"
        ))

    # أزرار التنقل
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(
            "⬅️ السابق",
            callback_data=f"{action_prefix}{CALLBACK_DELIMITER}{context_id}{CALLBACK_DELIMITER}{page-1}"
        ))

    total_pages = math.ceil(total_count / VIDEOS_PER_PAGE)
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(
            "التالي ➡️",
            callback_data=f"{action_prefix}{CALLBACK_DELIMITER}{context_id}{CALLBACK_DELIMITER}{page+1}"
        ))

    if nav_buttons:
        keyboard.row(*nav_buttons)

    keyboard.add(InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))

    return keyboard

def create_video_action_keyboard(video_id, user_id):
    """إنشاء لوحة مفاتيح إجراءات الفيديو (تقييم ومفضلة)"""
    keyboard = InlineKeyboardMarkup(row_width=5)

    # أزرار التقييم
    rating_buttons = []
    user_rating = get_user_video_rating(video_id, user_id)

    for i in range(1, 6):
        if user_rating == i:
            rating_buttons.append(InlineKeyboardButton(
                f"⭐{i}",
                callback_data=f"rate{CALLBACK_DELIMITER}{video_id}{CALLBACK_DELIMITER}{i}"
            ))
        else:
            rating_buttons.append(InlineKeyboardButton(
                f"☆{i}",
                callback_data=f"rate{CALLBACK_DELIMITER}{video_id}{CALLBACK_DELIMITER}{i}"
            ))

    keyboard.row(*rating_buttons)

    # زر المفضلة
    is_fav = is_video_favorite(user_id, video_id)
    if is_fav:
        keyboard.add(InlineKeyboardButton(
            "💔 إزالة من المفضلة",
            callback_data=f"fav{CALLBACK_DELIMITER}remove{CALLBACK_DELIMITER}{video_id}"
        ))
    else:
        keyboard.add(InlineKeyboardButton(
            "⭐ إضافة للمفضلة",
            callback_data=f"fav{CALLBACK_DELIMITER}add{CALLBACK_DELIMITER}{video_id}"
        ))

    # عرض إحصائيات التقييم
    stats = get_video_rating_stats(video_id)
    if stats['count'] > 0:
        keyboard.add(InlineKeyboardButton(
            f"📊 متوسط التقييم: {stats['average']:.1f}/5 ({stats['count']} تقييم)",
            callback_data="noop"
        ))

    return keyboard

def generate_grouping_key(metadata):
    """توليد مفتاح تجميع للفيديوهات المتشابهة"""
    if not metadata:
        return None

    series_name = metadata.get('series_name')
    movie_name = metadata.get('movie_name')
    season = metadata.get('season')

    if series_name and season:
        return f"{series_name}_S{season}"
    elif movie_name:
        return movie_name

    return None

def list_videos(bot, message, edit_message=None):
    """عرض قائمة التصنيفات الرئيسية"""
    from db_manager import get_categories_tree
    categories = get_categories_tree()

    if not categories:
        text = "لا توجد تصنيفات حالياً."
        if edit_message:
            bot.edit_message_text(text, edit_message.chat.id, edit_message.message_id)
        else:
            bot.send_message(message.chat.id, text)
        return

    keyboard = InlineKeyboardMarkup(row_width=1)
    for cat in categories:
        keyboard.add(InlineKeyboardButton(
            f"📁 {cat['name']}", 
            callback_data=f"cat{CALLBACK_DELIMITER}{cat['id']}{CALLBACK_DELIMITER}0"
        ))

    text = "🎬 اختر التصنيف:"
    if edit_message:
        bot.edit_message_text(text, edit_message.chat.id, edit_message.message_id, reply_markup=keyboard)
    else:
        bot.send_message(message.chat.id, text, reply_markup=keyboard)
