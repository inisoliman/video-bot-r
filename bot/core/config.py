#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/core/config.py
# الوصف: الإعدادات المركزية - يقرأ كل شيء من متغيرات البيئة
# ==============================================================================

import os
import sys
import logging
from urllib.parse import urlparse
from dataclasses import dataclass, field
from typing import List, Optional, Dict

# تحميل .env للتطوير المحلي (اختياري)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


@dataclass
class DatabaseConfig:
    """إعدادات قاعدة البيانات (Neon PostgreSQL)"""
    url: str = ""
    user: str = ""
    password: str = ""
    host: str = ""
    port: int = 5432
    dbname: str = ""
    pool_min: int = 2
    pool_max: int = 20

    def __post_init__(self):
        self.url = os.environ.get('DATABASE_URL', '')
        if self.url:
            result = urlparse(self.url)
            self.user = result.username or ""
            self.password = result.password or ""
            self.host = result.hostname or ""
            self.port = result.port or 5432
            self.dbname = (result.path or "/")[1:]
        self.pool_min = int(os.environ.get('DB_POOL_MIN', '2'))
        self.pool_max = int(os.environ.get('DB_POOL_MAX', '20'))

    @property
    def connection_params(self) -> Dict[str, object]:
        """إرجاع معاملات الاتصال كقاموس"""
        return {
            'user': self.user,
            'password': self.password,
            'host': self.host,
            'port': self.port,
            'dbname': self.dbname,
        }

    @property
    def is_configured(self) -> bool:
        return bool(self.url)


@dataclass
class BotConfig:
    """إعدادات البوت الأساسية"""
    token: str = ""
    channel_id: str = ""
    admin_ids: List[int] = field(default_factory=list)
    webhook_secret: str = "default_secret"
    app_url: str = ""
    port: int = 10000

    def __post_init__(self):
        self.token = os.environ.get('BOT_TOKEN', '')
        self.channel_id = os.environ.get('CHANNEL_ID', '')
        self.webhook_secret = os.environ.get('WEBHOOK_SECRET', 'default_secret')
        self.app_url = os.environ.get('APP_URL', '') or os.environ.get('BASE_URL', '')
        self.port = int(os.environ.get('PORT', '10000'))

        admin_str = os.environ.get('ADMIN_IDS', '')
        self.admin_ids = [
            int(x.strip()) for x in admin_str.split(',')
            if x.strip().isdigit()
        ]


@dataclass
class PaginationConfig:
    """إعدادات التصفح"""
    videos_per_page: int = 10
    comments_per_page: int = 10

    def __post_init__(self):
        self.videos_per_page = int(os.environ.get('VIDEOS_PER_PAGE', '10'))
        self.comments_per_page = int(os.environ.get('COMMENTS_PER_PAGE', '10'))


@dataclass
class CacheConfig:
    """إعدادات الذاكرة المؤقتة"""
    inline_cache_time: int = 300
    search_cache_time: int = 60
    max_cache_entries: int = 100

    def __post_init__(self):
        self.inline_cache_time = int(os.environ.get('INLINE_CACHE_TIME', '300'))
        self.search_cache_time = int(os.environ.get('SEARCH_CACHE_TIME', '60'))


@dataclass
class FeatureFlags:
    """أعلام التفعيل"""
    comments: bool = True
    ratings: bool = True
    favorites: bool = True
    history: bool = True

    def __post_init__(self):
        self.comments = os.environ.get('ENABLE_COMMENTS', 'true').lower() == 'true'
        self.ratings = os.environ.get('ENABLE_RATINGS', 'true').lower() == 'true'
        self.favorites = os.environ.get('ENABLE_FAVORITES', 'true').lower() == 'true'
        self.history = os.environ.get('ENABLE_HISTORY', 'true').lower() == 'true'


class Settings:
    """الإعدادات المركزية الموحدة"""

    def __init__(self):
        self.db = DatabaseConfig()
        self.bot = BotConfig()
        self.pagination = PaginationConfig()
        self.cache = CacheConfig()
        self.features = FeatureFlags()

        # ثوابت ثابتة
        self.CALLBACK_DELIMITER = "::"
        self.VERSION = "3.0.0"

    def validate(self) -> List[str]:
        """التحقق من صحة الإعدادات الضرورية"""
        errors = []

        if not self.bot.token:
            errors.append("BOT_TOKEN is missing")
        if not self.db.is_configured:
            errors.append("DATABASE_URL is missing")
        if not self.bot.channel_id:
            errors.append("CHANNEL_ID is missing")
        if not self.bot.admin_ids:
            errors.append("ADMIN_IDS is missing or invalid")
        if not self.bot.app_url:
            errors.append("APP_URL is missing")

        return errors

    def validate_or_exit(self):
        """التحقق والخروج عند الفشل"""
        errors = self.validate()
        if errors:
            for err in errors:
                logger.critical(f"❌ Configuration Error: {err}")
            logger.critical("📋 Required environment variables:")
            logger.critical("   BOT_TOKEN, DATABASE_URL, CHANNEL_ID, ADMIN_IDS, APP_URL")
            sys.exit(1)

        if self.bot.app_url and not self.bot.app_url.startswith('https://'):
            logger.critical("❌ APP_URL must use HTTPS!")
            sys.exit(1)

    def log_status(self):
        """طباعة حالة الإعدادات (بدون كشف أسرار)"""
        logger.info("🔍 Configuration Status:")
        logger.info(f"  BOT_TOKEN: {'✅ Set' if self.bot.token else '❌ Missing'}")
        logger.info(f"  DATABASE_URL: {'✅ Set' if self.db.is_configured else '❌ Missing'}")
        logger.info(f"  CHANNEL_ID: {'✅ Set' if self.bot.channel_id else '❌ Missing'}")
        logger.info(f"  ADMIN_IDS: {'✅ ' + str(len(self.bot.admin_ids)) + ' admins' if self.bot.admin_ids else '❌ Missing'}")
        logger.info(f"  APP_URL: {'✅ Set' if self.bot.app_url else '❌ Missing'}")
        logger.info(f"  PORT: {self.bot.port}")
        logger.info(f"  VERSION: {self.VERSION}")


# إنشاء مثيل (Singleton) واحد
settings = Settings()
