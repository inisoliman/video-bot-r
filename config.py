#!/usr/bin/env python3
# ==============================================================================
# ملف: config.py
# الوصف: ملف الإعدادات المركزي للبوت
# ==============================================================================

import os
from urllib.parse import urlparse

# ==============================================================================
# Database Configuration
# ==============================================================================
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    result = urlparse(DATABASE_URL)
    DB_CONFIG = {
        'user': result.username,
        'password': result.password,
        'host': result.hostname,
        'port': result.port,
        'dbname': result.path[1:]
    }
else:
    DB_CONFIG = None

# Connection Pool Settings
DB_POOL_MIN = int(os.environ.get('DB_POOL_MIN', '1'))
DB_POOL_MAX = int(os.environ.get('DB_POOL_MAX', '20'))

# ==============================================================================
# Bot Configuration
# ==============================================================================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')
ADMIN_IDS_STR = os.environ.get('ADMIN_IDS', '')
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(',') if x.strip().isdigit()]

# ==============================================================================
# Webhook Configuration  
# ==============================================================================
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', 'default_secret')
APP_URL = os.environ.get('APP_URL') or os.environ.get('BASE_URL')
PORT = int(os.environ.get('PORT', '10000'))

# ==============================================================================
# Pagination Settings
# ==============================================================================
VIDEOS_PER_PAGE = int(os.environ.get('VIDEOS_PER_PAGE', '10'))
COMMENTS_PER_PAGE = int(os.environ.get('COMMENTS_PER_PAGE', '10'))

# ==============================================================================
# Cache Settings
# ==============================================================================
INLINE_CACHE_TIME = int(os.environ.get('INLINE_CACHE_TIME', '300'))  # 5 minutes
SEARCH_CACHE_TIME = int(os.environ.get('SEARCH_CACHE_TIME', '60'))   # 1 minute

# ==============================================================================
# UI Constants
# ==============================================================================
CALLBACK_DELIMITER = '::'

# ==============================================================================
# Feature Flags
# ==============================================================================
ENABLE_COMMENTS = os.environ.get('ENABLE_COMMENTS', 'true').lower() == 'true'
ENABLE_RATINGS = os.environ.get('ENABLE_RATINGS', 'true').lower() == 'true'
ENABLE_FAVORITES = os.environ.get('ENABLE_FAVORITES', 'true').lower() == 'true'
ENABLE_HISTORY = os.environ.get('ENABLE_HISTORY', 'true').lower() == 'true'

# ==============================================================================
# Validation
# ==============================================================================
def validate_config():
    """التحقق من صحة الإعدادات الضرورية"""
    errors = []
    
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN is missing")
    
    if not DATABASE_URL:
        errors.append("DATABASE_URL is missing")
    
    if not CHANNEL_ID:
        errors.append("CHANNEL_ID is missing")
    
    if not ADMIN_IDS:
        errors.append("ADMIN_IDS is missing or invalid")
    
    return errors
