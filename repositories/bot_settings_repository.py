
# repositories/bot_settings_repository.py

from core.db import execute_query
import logging

logger = logging.getLogger(__name__)

def get_setting(key):
    query = "SELECT setting_value FROM bot_settings WHERE setting_key = %s;"
    result = execute_query(query, (key,), fetch="one")
    return result["setting_value"] if result else None

def set_setting(key, value):
    query = """
        INSERT INTO bot_settings (setting_key, setting_value)
        VALUES (%s, %s)
        ON CONFLICT (setting_key) DO UPDATE SET
            setting_value = EXCLUDED.setting_value;
    """
    return execute_query(query, (key, value), commit=True)
