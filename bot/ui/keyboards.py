#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/ui/keyboards.py
# الوصف: أزرار وكيبوردات البوت الاحترافية
# ==============================================================================

import logging
from typing import List, Optional

from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)

from bot.ui.emoji import Emoji
from bot.core.config import settings
from bot.database.repositories.video_repo import VideoRepository, VIDEOS_PER_PAGE
from bot.database.repositories.category_repo import CategoryRepository
from bot.database.repositories.settings_repo import SettingsRepository

logger = logging.getLogger(__name__)

DELIMITER = settings.CALLBACK_DELIMITER


class Keyboards:
    """مركز الأزرار الموحد"""

    # ==============================================================================
    # القائمة الرئيسية
    # ==============================================================================

    @staticmethod
    def main_menu() -> ReplyKeyboardMarkup:
        """القائمة الرئيسية مع Reply Keyboard"""
        keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        keyboard.add(
            KeyboardButton(f"{Emoji.VIDEO} جميع الفيديوهات"),
            KeyboardButton(f"{Emoji.SEARCH} بحث")
        )
        keyboard.add(
            KeyboardButton(f"{Emoji.FIRE} الأكثر شعبية"),
            KeyboardButton(f"{Emoji.POPCORN} اقتراح عشوائي")
        )
        keyboard.add(
            KeyboardButton(f"{Emoji.STAR} المفضلة"),
            KeyboardButton(f"{Emoji.CLOCK} سجل المشاهدة")
        )
        return keyboard

    # ==============================================================================
    # أزرار الاشتراك
    # ==============================================================================

    @staticmethod
    def subscription(channels: List[dict]) -> InlineKeyboardMarkup:
        """أزرار الاشتراك في القنوات"""
        markup = InlineKeyboardMarkup(row_width=1)
        for channel in channels:
            try:
                channel_id_str = str(channel['channel_id'])
                if channel_id_str.startswith('-100'):
                    link = f"https://t.me/c/{channel_id_str.replace('-100', '')}"
                elif channel_id_str.startswith('@'):
                    link = f"https://t.me/{channel_id_str[1:]}"
                else:
                    link = f"https://t.me/{channel_id_str}"
                markup.add(InlineKeyboardButton(
                    f"{Emoji.BROADCAST} اشترك في {channel['channel_name']}", url=link
                ))
            except Exception as e:
                logger.error(f"Error creating channel link: {e}")
        markup.add(InlineKeyboardButton(
            f"{Emoji.SUCCESS} لقد اشتركت، تحقق الآن", callback_data="check_subscription"
        ))
        return markup

    # ==============================================================================
    # الفيديو والتقييم
    # ==============================================================================

    @staticmethod
    def video_actions(video_id: int, user_id: int) -> InlineKeyboardMarkup:
        """أزرار إجراءات الفيديو (تقييم + مفضلة + تعليق)"""
        keyboard = InlineKeyboardMarkup(row_width=5)

        # أزرار التقييم
        current_rating = VideoRepository.get_user_rating(video_id, user_id)
        stars = []
        for i in range(1, 6):
            label = "★" if current_rating and i <= current_rating else "☆"
            stars.append(InlineKeyboardButton(label, callback_data=f"rate{DELIMITER}{video_id}{DELIMITER}{i}"))
        keyboard.row(*stars)

        # زر المفضلة
        is_fav = VideoRepository.is_favorite(user_id, video_id)
        if is_fav:
            keyboard.add(InlineKeyboardButton(
                f"{Emoji.ERROR} إزالة من المفضلة",
                callback_data=f"fav{DELIMITER}remove{DELIMITER}{video_id}"
            ))
        else:
            keyboard.add(InlineKeyboardButton(
                f"{Emoji.STAR} إضافة للمفضلة",
                callback_data=f"fav{DELIMITER}add{DELIMITER}{video_id}"
            ))

        # زر التعليق
        if settings.features.comments:
            keyboard.add(InlineKeyboardButton(
                f"{Emoji.COMMENT} إضافة تعليق",
                callback_data=f"add_comment{DELIMITER}{video_id}"
            ))

        return keyboard

    @staticmethod
    def popular_menu() -> InlineKeyboardMarkup:
        """قائمة الأكثر شعبية"""
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(
            InlineKeyboardButton(f"{Emoji.FIRE} الأكثر مشاهدة", callback_data=f"popular{DELIMITER}most_viewed"),
            InlineKeyboardButton(f"{Emoji.STAR} الأعلى تقييماً", callback_data=f"popular{DELIMITER}highest_rated")
        )
        keyboard.add(InlineKeyboardButton(f"{Emoji.BACK} رجوع", callback_data="back_to_main"))
        return keyboard

    @staticmethod
    def search_type(query: str) -> InlineKeyboardMarkup:
        """أزرار نوع البحث"""
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton(f"{Emoji.SEARCH} بحث عادي", callback_data=f"search_type{DELIMITER}normal"),
            InlineKeyboardButton(f"{Emoji.SETTINGS} بحث متقدم", callback_data=f"search_type{DELIMITER}advanced")
        )
        keyboard.add(InlineKeyboardButton(f"{Emoji.BACK} رجوع", callback_data="back_to_main"))
        return keyboard

    # ==============================================================================
    # التصفح (Pagination)
    # ==============================================================================

    @staticmethod
    def paginated(videos: list, total_count: int, page: int, 
                  action_prefix: str, context_id: str) -> InlineKeyboardMarkup:
        """كيبورد مع قائمة فيديوهات وأزرار التصفح"""
        keyboard = InlineKeyboardMarkup(row_width=1)

        for v in videos:
            title = (v.get('caption') or v.get('file_name') or 'فيديو')
            title = title.replace('\n', ' ')[:45]
            vid_id = v['id']
            msg_id = v.get('message_id', 0)
            chat_id = v.get('chat_id', 0)
            keyboard.add(InlineKeyboardButton(
                title,
                callback_data=f"video{DELIMITER}{vid_id}{DELIMITER}{msg_id}{DELIMITER}{chat_id}"
            ))

        # أزرار التنقل
        nav_buttons = []
        total_pages = max(1, (total_count + VIDEOS_PER_PAGE - 1) // VIDEOS_PER_PAGE)

        if page > 0:
            nav_buttons.append(InlineKeyboardButton(
                f"{Emoji.PREV} السابق",
                callback_data=f"{action_prefix}{DELIMITER}{context_id}{DELIMITER}{page - 1}"
            ))

        nav_buttons.append(InlineKeyboardButton(
            f"📄 {page + 1}/{total_pages}", callback_data="noop"
        ))

        if (page + 1) * VIDEOS_PER_PAGE < total_count:
            nav_buttons.append(InlineKeyboardButton(
                f"التالي {Emoji.NEXT}",
                callback_data=f"{action_prefix}{DELIMITER}{context_id}{DELIMITER}{page + 1}"
            ))

        if nav_buttons:
            keyboard.row(*nav_buttons)

        keyboard.add(InlineKeyboardButton(f"{Emoji.BACK} رجوع", callback_data="back_to_cats"))

        return keyboard

    @staticmethod
    def combined(child_categories: list, videos: list, total_count: int,
                 page: int, parent_category_id: int) -> InlineKeyboardMarkup:
        """كيبورد مدمج: تصنيفات فرعية + فيديوهات + تنقل"""
        keyboard = InlineKeyboardMarkup(row_width=1)

        # تصنيفات فرعية
        for cat in (child_categories or []):
            keyboard.add(InlineKeyboardButton(
                f"{Emoji.SUBFOLDER} {cat['name']}",
                callback_data=f"cat{DELIMITER}{cat['id']}{DELIMITER}0"
            ))

        # فيديوهات
        for v in (videos or []):
            title = (v.get('caption') or v.get('file_name') or 'فيديو')
            title = title.replace('\n', ' ')[:45]
            keyboard.add(InlineKeyboardButton(
                f"{Emoji.VIDEO} {title}",
                callback_data=f"video{DELIMITER}{v['id']}{DELIMITER}{v.get('message_id', 0)}{DELIMITER}{v.get('chat_id', 0)}"
            ))

        # تنقل
        nav = []
        total_pages = max(1, (total_count + VIDEOS_PER_PAGE - 1) // VIDEOS_PER_PAGE)

        if page > 0:
            nav.append(InlineKeyboardButton(
                f"{Emoji.PREV}",
                callback_data=f"cat{DELIMITER}{parent_category_id}{DELIMITER}{page - 1}"
            ))

        if total_count > VIDEOS_PER_PAGE:
            nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))

        if (page + 1) * VIDEOS_PER_PAGE < total_count:
            nav.append(InlineKeyboardButton(
                f"{Emoji.NEXT}",
                callback_data=f"cat{DELIMITER}{parent_category_id}{DELIMITER}{page + 1}"
            ))

        if nav:
            keyboard.row(*nav)

        keyboard.add(InlineKeyboardButton(f"{Emoji.BACK} رجوع", callback_data="back_to_cats"))

        return keyboard

    # ==============================================================================
    # التصنيفات (Hierarchical)
    # ==============================================================================

    @staticmethod
    def categories_tree(callback_prefix: str, add_back: bool = True) -> InlineKeyboardMarkup:
        """كيبورد هرمي للتصنيفات"""
        keyboard = InlineKeyboardMarkup(row_width=1)

        categories = CategoryRepository.get_all()
        if not categories:
            return keyboard

        tree = _build_tree(categories)
        for cat in tree:
            keyboard.add(InlineKeyboardButton(
                cat['display_name'],
                callback_data=f"{callback_prefix}{DELIMITER}{cat['id']}"
            ))

        if add_back:
            keyboard.add(InlineKeyboardButton(f"{Emoji.BACK} رجوع", callback_data="back_to_main"))

        return keyboard

    # ==============================================================================
    # لوحة الأدمن
    # ==============================================================================

    @staticmethod
    def admin_panel() -> InlineKeyboardMarkup:
        """لوحة تحكم الأدمن"""
        keyboard = InlineKeyboardMarkup(row_width=2)

        keyboard.add(
            InlineKeyboardButton(f"{Emoji.ADD} إضافة تصنيف", callback_data=f"admin{DELIMITER}add_new_cat"),
            InlineKeyboardButton(f"{Emoji.DELETE} حذف تصنيف", callback_data=f"admin{DELIMITER}delete_category_select")
        )
        keyboard.add(
            InlineKeyboardButton(f"{Emoji.MOVE} نقل فيديو بالرقم", callback_data=f"admin{DELIMITER}move_video_by_id"),
            InlineKeyboardButton(f"{Emoji.ERROR} حذف فيديوهات بالأرقام", callback_data=f"admin{DELIMITER}delete_videos_by_ids")
        )
        keyboard.add(
            InlineKeyboardButton(f"🔘 تعيين التصنيف النشط", callback_data=f"admin{DELIMITER}set_active"),
            InlineKeyboardButton(f"{Emoji.REFRESH} تحديث بيانات الفيديوهات", callback_data=f"admin{DELIMITER}update_metadata")
        )

        # التعليقات
        keyboard.add(
            InlineKeyboardButton(f"{Emoji.COMMENT} عرض التعليقات", callback_data=f"admin{DELIMITER}view_comments"),
            InlineKeyboardButton(f"{Emoji.STATS} إحصائيات التعليقات", callback_data=f"admin{DELIMITER}comments_stats")
        )
        keyboard.add(
            InlineKeyboardButton(f"{Emoji.DELETE} حذف جميع التعليقات", callback_data=f"admin{DELIMITER}delete_all_comments"),
            InlineKeyboardButton(f"🧹 حذف التعليقات القديمة", callback_data=f"admin{DELIMITER}delete_old_comments")
        )

        # القنوات
        keyboard.add(
            InlineKeyboardButton(f"{Emoji.ADD} إضافة قناة اشتراك", callback_data=f"admin{DELIMITER}add_channel"),
            InlineKeyboardButton(f"{Emoji.REMOVE} إزالة قناة اشتراك", callback_data=f"admin{DELIMITER}remove_channel")
        )
        keyboard.add(InlineKeyboardButton(f"{Emoji.CHANNELS} عرض القنوات", callback_data=f"admin{DELIMITER}list_channels"))

        # إحصائيات وبث
        keyboard.add(
            InlineKeyboardButton(f"{Emoji.BROADCAST} بث رسالة", callback_data=f"admin{DELIMITER}broadcast"),
            InlineKeyboardButton(f"{Emoji.STATS} الإحصائيات", callback_data=f"admin{DELIMITER}stats"),
            InlineKeyboardButton(f"{Emoji.USER} عدد المشتركين", callback_data=f"admin{DELIMITER}sub_count")
        )

        return keyboard


# ==============================================================================
# Utility: بناء شجرة التصنيفات
# ==============================================================================

def _build_tree(categories: list, parent_id=None, level=0) -> list:
    """بناء شجرة التصنيفات مع مسافات بادئة"""
    result = []
    for cat in categories:
        cat_parent = cat.get('parent_id')
        if cat_parent == parent_id:
            prefix = "  " * level + ("📂 " if level == 0 else "╰ 📁 ")
            result.append({
                'id': cat['id'],
                'name': cat['name'],
                'display_name': f"{prefix}{cat['name']}"
            })
            result.extend(_build_tree(categories, cat['id'], level + 1))
    return result
