#!/usr/bin/env python3
# ==============================================================================
# ملف: app/__init__.py
# الوصف: تهيئة حزمة التطبيق
# ==============================================================================

from .config import settings, BOT_TOKEN, DATABASE_URL, CHANNEL_ID, ADMIN_IDS, APP_URL, WEBHOOK_SECRET
from .database import init_db, get_db_connection
from .handlers import register_all_handlers
from .state_manager import StateManager
from .logger import setup_logger

__all__ = [
    'settings',
    'BOT_TOKEN', 
    'DATABASE_URL', 
    'CHANNEL_ID', 
    'ADMIN_IDS', 
    'APP_URL', 
    'WEBHOOK_SECRET',
    'init_db',
    'get_db_connection',
    'register_all_handlers',
    'StateManager',
    'setup_logger'
]