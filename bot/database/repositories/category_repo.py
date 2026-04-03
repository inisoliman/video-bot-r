#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/database/repositories/category_repo.py
# الوصف: عمليات CRUD للتصنيفات
# ==============================================================================

import logging
from typing import Optional, List, Tuple

from bot.database.connection import execute_query

logger = logging.getLogger(__name__)


class CategoryRepository:
    """Repository لعمليات التصنيفات"""

    @staticmethod
    def get_by_id(category_id: int):
        """جلب تصنيف بالمعرف"""
        return execute_query("SELECT * FROM categories WHERE id = %s", (category_id,), fetch="one")

    @staticmethod
    def add(name: str, parent_id: int = None) -> Tuple[bool, object]:
        """إضافة تصنيف جديد"""
        full_path = name
        if parent_id is not None:
            parent = CategoryRepository.get_by_id(parent_id)
            if parent and parent.get('full_path'):
                full_path = f"{parent['full_path']}/{name}"
            elif parent:
                full_path = f"{parent['name']}/{name}"

        res = execute_query(
            "INSERT INTO categories (name, parent_id, full_path) VALUES (%s, %s, %s) RETURNING id",
            (name, parent_id, full_path), fetch="one", commit=True
        )
        return (True, res) if res else (False, "Failed to add category")

    @staticmethod
    def get_all():
        """جلب جميع التصنيفات"""
        return execute_query(
            "SELECT * FROM categories WHERE parent_id IS NULL OR parent_id IS NOT NULL ORDER BY name",
            fetch="all"
        )

    @staticmethod
    def get_children(parent_id: Optional[int] = None):
        """جلب التصنيفات الفرعية"""
        if parent_id is None:
            return execute_query(
                "SELECT * FROM categories WHERE parent_id IS NULL ORDER BY name",
                fetch="all"
            )
        return execute_query(
            "SELECT * FROM categories WHERE parent_id = %s ORDER BY name",
            (parent_id,), fetch="all"
        )

    @staticmethod
    def delete_with_contents(category_id: int):
        """حذف التصنيف مع محتوياته"""
        execute_query("DELETE FROM video_archive WHERE category_id = %s", (category_id,), commit=True)
        execute_query("DELETE FROM categories WHERE id = %s", (category_id,), commit=True)
        return True

    @staticmethod
    def delete_by_id(category_id: int):
        """حذف التصنيف فقط"""
        return execute_query("DELETE FROM categories WHERE id = %s", (category_id,), commit=True)

    @staticmethod
    def get_count() -> int:
        """عدد التصنيفات"""
        res = execute_query("SELECT COUNT(*) as count FROM categories", fetch="one")
        return res['count'] if res else 0
