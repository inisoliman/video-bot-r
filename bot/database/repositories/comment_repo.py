#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/database/repositories/comment_repo.py
# الوصف: عمليات CRUD لنظام التعليقات
# ==============================================================================

import logging
from typing import Optional, Tuple, List

from bot.database.connection import execute_query
from bot.core.config import settings

logger = logging.getLogger(__name__)

COMMENTS_PER_PAGE = settings.pagination.comments_per_page


class CommentRepository:
    """Repository لعمليات التعليقات"""

    @staticmethod
    def add(video_id: int, user_id: int, username: str, comment_text: str) -> Optional[int]:
        """إضافة تعليق جديد"""
        result = execute_query("""
            INSERT INTO video_comments (video_id, user_id, username, comment_text)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (video_id, user_id, username, comment_text), fetch="one", commit=True)
        return result['id'] if result else None

    @staticmethod
    def get_by_id(comment_id: int):
        """جلب تعليق بالمعرف"""
        return execute_query("""
            SELECT c.*, v.caption as video_caption, v.file_name as video_name
            FROM video_comments c JOIN video_archive v ON c.video_id = v.id
            WHERE c.id = %s
        """, (comment_id,), fetch="one")

    @staticmethod
    def get_all(page: int = 0, unread_only: bool = False):
        """جلب جميع التعليقات"""
        offset = page * COMMENTS_PER_PAGE
        where = "WHERE is_read = FALSE" if unread_only else ""

        comments = execute_query(f"""
            SELECT c.*, v.caption as video_caption, v.file_name as video_name
            FROM video_comments c JOIN video_archive v ON c.video_id = v.id
            {where} ORDER BY c.created_at DESC LIMIT %s OFFSET %s
        """, (COMMENTS_PER_PAGE, offset), fetch="all")

        total = execute_query(f"SELECT COUNT(*) as count FROM video_comments {where}", fetch="one")
        return comments, total['count'] if total else 0

    @staticmethod
    def get_by_user(user_id: int, page: int = 0):
        """جلب تعليقات مستخدم"""
        offset = page * COMMENTS_PER_PAGE
        comments = execute_query("""
            SELECT c.*, v.caption as video_caption, v.file_name as video_name
            FROM video_comments c JOIN video_archive v ON c.video_id = v.id
            WHERE c.user_id = %s ORDER BY c.created_at DESC LIMIT %s OFFSET %s
        """, (user_id, COMMENTS_PER_PAGE, offset), fetch="all")
        total = execute_query("SELECT COUNT(*) as count FROM video_comments WHERE user_id = %s", (user_id,), fetch="one")
        return comments, total['count'] if total else 0

    @staticmethod
    def reply(comment_id: int, admin_reply: str):
        """الرد على تعليق"""
        return execute_query("""
            UPDATE video_comments
            SET admin_reply = %s, replied_at = CURRENT_TIMESTAMP, is_read = TRUE
            WHERE id = %s
        """, (admin_reply, comment_id), commit=True)

    @staticmethod
    def mark_read(comment_id: int):
        return execute_query("UPDATE video_comments SET is_read = TRUE WHERE id = %s", (comment_id,), commit=True)

    @staticmethod
    def delete(comment_id: int):
        return execute_query("DELETE FROM video_comments WHERE id = %s", (comment_id,), commit=True)

    @staticmethod
    def delete_all() -> int:
        result = execute_query("DELETE FROM video_comments RETURNING id", fetch="all", commit=True)
        return len(result) if result else 0

    @staticmethod
    def delete_by_user(user_id: int) -> int:
        result = execute_query("DELETE FROM video_comments WHERE user_id = %s RETURNING id", (user_id,), fetch="all", commit=True)
        return len(result) if result else 0

    @staticmethod
    def delete_old(days: int = 30) -> int:
        result = execute_query("""
            DELETE FROM video_comments WHERE created_at < NOW() - INTERVAL '%s days' RETURNING id
        """, (days,), fetch="all", commit=True)
        return len(result) if result else 0

    @staticmethod
    def get_unread_count() -> int:
        result = execute_query("SELECT COUNT(*) as count FROM video_comments WHERE is_read = FALSE", fetch="one")
        return result['count'] if result else 0

    @staticmethod
    def get_video_count(video_id: int) -> int:
        result = execute_query("SELECT COUNT(*) as count FROM video_comments WHERE video_id = %s", (video_id,), fetch="one")
        return result['count'] if result else 0

    @staticmethod
    def get_stats():
        return execute_query("""
            SELECT
                COUNT(*) as total_comments,
                COUNT(CASE WHEN is_read = FALSE THEN 1 END) as unread_comments,
                COUNT(CASE WHEN admin_reply IS NOT NULL THEN 1 END) as replied_comments,
                COUNT(DISTINCT user_id) as unique_users
            FROM video_comments
        """, fetch="one")
