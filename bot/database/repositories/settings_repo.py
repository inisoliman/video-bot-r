#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/database/repositories/settings_repo.py
# الوصف: عمليات إعدادات البوت والقنوات المطلوبة
# ==============================================================================

import logging
from typing import Optional, List

from bot.database.connection import execute_query

logger = logging.getLogger(__name__)


class SettingsRepository:
    """Repository لإعدادات البوت والقنوات المطلوبة"""

    # --- Bot Settings ---
    @staticmethod
    def get_active_category_id() -> Optional[int]:
        """جلب التصنيف النشط"""
        res = execute_query(
            "SELECT setting_value FROM bot_settings WHERE setting_key = 'active_category_id'",
            fetch="one"
        )
        return int(res['setting_value']) if res and res['setting_value'].isdigit() else None

    @staticmethod
    def set_active_category_id(category_id: int):
        """تعيين التصنيف النشط"""
        return execute_query(
            "INSERT INTO bot_settings (setting_key, setting_value) VALUES ('active_category_id', %s) "
            "ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
            (str(category_id),), commit=True
        )

    @staticmethod
    def get(key: str) -> Optional[str]:
        """جلب إعداد"""
        res = execute_query(
            "SELECT setting_value FROM bot_settings WHERE setting_key = %s",
            (key,), fetch="one"
        )
        return res['setting_value'] if res else None

    @staticmethod
    def set(key: str, value: str):
        """تعيين إعداد"""
        return execute_query(
            "INSERT INTO bot_settings (setting_key, setting_value) VALUES (%s, %s) "
            "ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
            (key, value), commit=True
        )

    # --- Required Channels ---
    @staticmethod
    def add_channel(channel_id: int, channel_name: str):
        """إضافة قناة مطلوبة"""
        return execute_query(
            "INSERT INTO required_channels (channel_id, channel_name) VALUES (%s, %s) ON CONFLICT(channel_id) DO NOTHING",
            (int(channel_id), channel_name), commit=True
        )

    @staticmethod
    def remove_channel(channel_id: int):
        """إزالة قناة مطلوبة"""
        return execute_query(
            "DELETE FROM required_channels WHERE channel_id = %s",
            (int(channel_id),), commit=True
        )

    @staticmethod
    def get_channels():
        """جلب جميع القنوات المطلوبة"""
        return execute_query("SELECT * FROM required_channels", fetch="all")
