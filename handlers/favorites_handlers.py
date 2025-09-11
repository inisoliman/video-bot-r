# handlers/favorites_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging

from db_manager import (
    add_to_favorites, remove_from_favorites, get_user_favorites,
    is_video_favorite, add_to_watch_history, get_user_watch_history,
    get_recommended_videos, increment_video_view_count
)
from .helpers import (
    create_paginated_keyboard, create_video_action_keyboard
)

logger = logging.getLogger(__name__)

def register(bot, admin_ids):
    """Register favorites and watch history handlers"""
    
    @bot.message_handler(func=lambda message: message.text == "⭐ المفضلة")
    def handle_favorites_button(message):
        show_user_favorites(message)
    
    @bot.message_handler(func=lambda message: message.text == "📺 سجل المشاهدة")
    def handle_watch_history_button(message):
        show_watch_history(message)
    
    @bot.message_handler(func=lambda message: message.text == "🎯 اقتراحات شخصية")
    def handle_recommendations_button(message):
        show_recommendations(message)
    
    def show_user_favorites(message):
        """Show user's favorite videos"""
        favorites, total_count = get_user_favorites(message.from_user.id, page=0)
        
        if not favorites:
            bot.reply_to(message, "لا توجد فيديوهات في قائمة المفضلة الخاصة بك.")
            return
        
        keyboard = create_paginated_keyboard(favorites, total_count, 0, "favorites", "user")
        bot.reply_to(message, f"⭐ المفضلة ({total_count} فيديو):", reply_markup=keyboard)
    
    def show_watch_history(message):
        """Show user's watch history"""
        history, total_count = get_user_watch_history(message.from_user.id, page=0)
        
        if not history:
            bot.reply_to(message, "لا يوجد سجل مشاهدة.")
            return
        
        keyboard = create_paginated_keyboard(history, total_count, 0, "history", "user")
        bot.reply_to(message, f"📺 سجل المشاهدة ({total_count} فيديو):", reply_markup=keyboard)
    
    def show_recommendations(message):
        """Show personalized recommendations"""
        recommendations = get_recommended_videos(message.from_user.id, limit=10)
        
        if not recommendations:
            bot.reply_to(message, "لا توجد اقتراحات متاحة حالياً. شاهد المزيد من الفيديوهات للحصول على اقتراحات شخصية.")
            return
        
        keyboard = create_paginated_keyboard(recommendations, len(recommendations), 0, "recommendations", "user")
        bot.reply_to(message, "🎯 اقتراحات شخصية لك:", reply_markup=keyboard)