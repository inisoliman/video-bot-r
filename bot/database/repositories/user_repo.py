#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/database/repositories/user_repo.py
# الوصف: عمليات CRUD للمستخدمين وإدارة الحالة
# ==============================================================================

import json
import logging
from typing import Optional, Dict, Any, List

from bot.database.connection import execute_query

logger = logging.getLogger(__name__)


class UserRepository:
    """Repository لعمليات المستخدمين"""

    @staticmethod
    def add(user_id: int, username: str, first_name: str):
        """إضافة مستخدم جديد (أو تجاهل إذا موجود)"""
        return execute_query(
            "INSERT INTO bot_users (user_id, username, first_name) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
            (user_id, username, first_name), commit=True
        )

    @staticmethod
    def get_all_ids() -> List[int]:
        """جلب معرفات جميع المستخدمين"""
        res = execute_query("SELECT user_id FROM bot_users", fetch="all")
        return [r['user_id'] for r in res] if res else []

    @staticmethod
    def get_count() -> int:
        """عدد المشتركين الكلي"""
        res = execute_query("SELECT COUNT(*) as count FROM bot_users", fetch="one")
        return res['count'] if res else 0

    @staticmethod
    def delete(user_id: int):
        """حذف مستخدم وكل بياناته المرتبطة"""
        execute_query("DELETE FROM user_states WHERE user_id = %s", (user_id,), commit=True)
        execute_query("DELETE FROM user_favorites WHERE user_id = %s", (user_id,), commit=True)
        execute_query("DELETE FROM user_history WHERE user_id = %s", (user_id,), commit=True)
        execute_query("DELETE FROM video_ratings WHERE user_id = %s", (user_id,), commit=True)
        return execute_query("DELETE FROM bot_users WHERE user_id = %s", (user_id,), commit=True)

    # --- State Management ---
    @staticmethod
    def set_state(user_id: int, state: str, context: dict = None):
        """تعيين حالة المستخدم"""
        context_json = json.dumps(context) if context else None
        return execute_query(
            "INSERT INTO user_states (user_id, state, context) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET state = EXCLUDED.state, context = EXCLUDED.context",
            (user_id, state, context_json), commit=True
        )

    @staticmethod
    def get_state(user_id: int) -> Optional[Dict[str, Any]]:
        """جلب حالة المستخدم"""
        return execute_query(
            "SELECT state, context FROM user_states WHERE user_id = %s",
            (user_id,), fetch="one"
        )

    @staticmethod
    def clear_state(user_id: int):
        """مسح حالة المستخدم"""
        return execute_query(
            "DELETE FROM user_states WHERE user_id = %s",
            (user_id,), commit=True
        )
