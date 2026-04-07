# handlers/helpers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import math
import re
import logging
from db_manager import (
    get_child_categories, get_category_by_id, get_user_video_rating,
    get_video_rating_stats, VIDEOS_PER_PAGE, CALLBACK_DELIMITER,
    get_required_channels, is_video_favorite, get_categories_tree,
    get_videos_ratings_bulk  # إضافة الدالة الجديدة
)

logger = logging.getLogger(__name__)

# القواميس المشتركة لتخزين حالة المستخدمين
admin_steps = {}
user_last_search = {}


# ============================================
# 🌟 دالة جديدة: بناء شجرة التصنيفات الهرمية
# ============================================
def build_category_tree(categories):
    """
    تنظم التصنيفات بشكل شجري هرمي مع إضافة رموز وإيموجي
    
    Args:
        categories: قائمة جميع التصنيفات من قاعدة البيانات
    
    Returns:
        list: قائمة منظمة بشكل شجري مع الرموز والإيموجي
    """
    tree = []
    cats_by_parent = {}
    
    # تجميع التصنيفات حسب parent_id
    for cat in categories:
        parent_id = cat.get('parent_id')
        if parent_id not in cats_by_parent:
            cats_by_parent[parent_id] = []
        cats_by_parent[parent_id].append(cat)
    
    def insert_cats(parent_id, prefix="", level=0):
        """دالة مساعدة لإدراج التصنيفات بشكل متداخل"""
        children = cats_by_parent.get(parent_id, [])
        
        for child in sorted(children, key=lambda x: x['name']):
            # اختيار الإيموجي والرمز حسب المستوى
            if level == 0:
                # تصنيف رئيسي
                emoji = "📂"
                display_name = f"{emoji} {child['name']}"
            elif level == 1:
                # تصنيف فرعي مستوى أول
                emoji = "🌿"
                display_name = f"{prefix}└─ {emoji} {child['name']}"
            else:
                # تصنيفات فرعية أعمق
                emoji = "🔸"
                display_name = f"{prefix}└─ {emoji} {child['name']}"
            
            tree.append({
                'id': child['id'],
                'name': display_name,
                'original_name': child['name'],
                'level': level,
                'parent_id': parent_id
            })
            
            # إضافة التصنيفات الفرعية بشكل متداخل
            next_prefix = prefix + ("    " if level == 0 else "  ")
            insert_cats(child['id'], next_prefix, level + 1)
    
    # البدء من التصنيفات الرئيسية (parent_id = None)
    insert_cats(None, "", 0)
    
    return tree


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


def main_menu(bot_username=None):
    """
    القائمة الرئيسية مع زر البحث السريع.
    محدثة للعمل على Telegram Desktop والموبايل.
    
    Args:
        bot_username: اسم البوت (اختياري) لإضافة زر switch inline
    """
    markup = ReplyKeyboardMarkup(
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="اختر أمرًا من القائمة أدناه 👇"
    )
    # [تعديل] إضافة أزرار المفضلة والسجل - ترتيب أفضل للديسكتوب
    markup.row(
        KeyboardButton("🎬 عرض كل الفيديوهات"),
        KeyboardButton("🔥 الفيديوهات الشائعة")
    )
    markup.row(
        KeyboardButton("⭐ المفضلة"),
        KeyboardButton("📺 سجل المشاهدة")
    )
    markup.row(
        KeyboardButton("🍿 اقترح لي فيلم"),
        KeyboardButton("🔍 بحث")
    )
    
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


# ============================================
# 🌟 دالة جديدة: إنشاء كيبورد هرمي للتصنيفات
# ============================================
def create_hierarchical_category_keyboard(callback_prefix, add_back_button=True):
    """
    تنشئ لوحة مفاتيح منظمة بشكل شجري لجميع التصنيفات
    
    Args:
        callback_prefix: البادئة المستخدمة في callback_data (مثل: "admin::move_confirm")
        add_back_button: إضافة زر رجوع أم لا
    
    Returns:
        InlineKeyboardMarkup: لوحة المفاتيح المنظمة
    """
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    # جلب جميع التصنيفات
    all_categories = get_categories_tree()
    
    if not all_categories:
        keyboard.add(InlineKeyboardButton("❌ لا توجد تصنيفات", callback_data="noop"))
        return keyboard
    
    # بناء الشجرة
    tree = build_category_tree(all_categories)
    
    # إضافة أزرار التصنيفات
    for cat in tree:
        keyboard.add(
            InlineKeyboardButton(
                cat['name'], 
                callback_data=f"{callback_prefix}::{cat['id']}"
            )
        )
    
    # إضافة زر الرجوع إذا كان مطلوباً
    if add_back_button:
        keyboard.add(InlineKeyboardButton("🔙 إلغاء", callback_data="back_to_main"))
    
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

    # تحويل كائن DictRow إلى قاموس عادي
    mutable_videos = [dict(v) for v in videos] 
    
    # جلب جميع التقييمات دفعة واحدة (حل N+1)
    video_ids = [v['id'] for v in mutable_videos]
    ratings_dict = get_videos_ratings_bulk(video_ids)
    
    for video in mutable_videos:
        # إضافة avg_rating من القاموس
        rating_info = ratings_dict.get(video['id'], {'avg': 0, 'count': 0})
        video['avg_rating'] = rating_info['avg']

        # عرض معلومات الفيديو
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
        
        # تحويل كائنات DictRow
        mutable_videos = [dict(v) for v in videos] 

        # جلب جميع التقييمات دفعة واحدة (حل N+1)
        video_ids = [v['id'] for v in mutable_videos]
        ratings_dict = get_videos_ratings_bulk(video_ids)

        for video in mutable_videos:
            # إضافة avg_rating من القاموس
            rating_info = ratings_dict.get(video['id'], {'avg': 0, 'count': 0})
            video['avg_rating'] = rating_info['avg']

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
    
    # [جديد] إضافة زر التعليق
    keyboard.add(InlineKeyboardButton("💬 إضافة تعليق", callback_data=f"add_comment::{video_id}"), row_width=1)
    
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
