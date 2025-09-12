# handlers/advanced_search_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
from datetime import datetime, timedelta

from db_manager import (
    search_videos, get_videos_by_date_range, get_video_metadata_options
)
from .helpers import (
    create_paginated_keyboard, user_last_search
)
from state_manager import state_manager

logger = logging.getLogger(__name__)

def register(bot, admin_ids):
    """Register advanced search handlers"""
    
    def show_advanced_search_menu(message, query):
        """Show advanced search options"""
        keyboard = InlineKeyboardMarkup(row_width=2)
        
        # Search type options
        keyboard.add(
            InlineKeyboardButton("🔤 بحث في العنوان", callback_data=f"adv_search::title::{query}"),
            InlineKeyboardButton("📝 بحث في الوصف", callback_data=f"adv_search::description::{query}")
        )
        
        # Quality filters
        keyboard.add(
            InlineKeyboardButton("🎬 HD فقط", callback_data=f"adv_search::quality::HD::{query}"),
            InlineKeyboardButton("📱 SD فقط", callback_data=f"adv_search::quality::SD::{query}")
        )
        
        # Duration filters
        keyboard.add(
            InlineKeyboardButton("⏱️ أقل من 30 دقيقة", callback_data=f"adv_search::duration::short::{query}"),
            InlineKeyboardButton("🎭 أكثر من ساعة", callback_data=f"adv_search::duration::long::{query}")
        )
        
        # Date filters
        keyboard.add(
            InlineKeyboardButton("📅 آخر أسبوع", callback_data=f"adv_search::date::week::{query}"),
            InlineKeyboardButton("📆 آخر شهر", callback_data=f"adv_search::date::month::{query}")
        )
        
        # Sorting options
        keyboard.add(
            InlineKeyboardButton("⭐ الأعلى تقييماً", callback_data=f"adv_search::sort::rating::{query}"),
            InlineKeyboardButton("👁️ الأكثر مشاهدة", callback_data=f"adv_search::sort::views::{query}")
        )
        
        keyboard.add(
            InlineKeyboardButton("📊 الأحدث", callback_data=f"adv_search::sort::newest::{query}"),
            InlineKeyboardButton("🔍 بحث عادي", callback_data=f"search_type::normal")
        )
        
        return keyboard
    
    def perform_advanced_search(query, search_type=None, quality_filter=None, 
                              duration_filter=None, date_filter=None, sort_by=None):
        """Perform advanced search with filters"""
        
        # Prepare search parameters
        search_params = {
            'query': query,
            'page': 0,
            'sort_by': sort_by or 'newest'
        }
        
        # Apply quality filter
        if quality_filter == 'HD':
            search_params['min_resolution'] = 720
        elif quality_filter == 'SD':
            search_params['max_resolution'] = 720
        
        # Apply duration filter
        if duration_filter == 'short':
            search_params['max_duration'] = 1800  # 30 minutes
        elif duration_filter == 'long':
            search_params['min_duration'] = 3600  # 1 hour
        
        # Apply date filter
        if date_filter == 'week':
            date_from = datetime.now() - timedelta(days=7)
            search_params['date_from'] = date_from
        elif date_filter == 'month':
            date_from = datetime.now() - timedelta(days=30)
            search_params['date_from'] = date_from
        
        # Apply search type filter
        if search_type == 'title':
            search_params['search_in'] = 'title'
        elif search_type == 'description':
            search_params['search_in'] = 'description'
        
        return search_videos(**search_params)
    
    # Export functions for use in callback handlers
    return {
        'show_advanced_search_menu': show_advanced_search_menu,
        'perform_advanced_search': perform_advanced_search
    }