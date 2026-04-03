#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/services/broadcast_service.py
# الوصف: خدمة بث الرسائل الجماعية
# ==============================================================================

import time
import logging
from typing import Tuple

from telebot.apihelper import ApiTelegramException

from bot.database.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


class BroadcastService:
    """خدمة بث الرسائل الجماعية"""

    @staticmethod
    def send_broadcast(bot, source_message) -> Tuple[int, int, int]:
        """
        بث رسالة لجميع المستخدمين.
        
        Returns:
            (sent_count, failed_count, removed_count)
        """
        user_ids = UserRepository.get_all_ids()
        sent, failed, removed = 0, 0, 0

        for user_id in user_ids:
            try:
                bot.copy_message(user_id, source_message.chat.id, source_message.message_id)
                sent += 1
            except ApiTelegramException as e:
                if 'bot was blocked by the user' in e.description:
                    UserRepository.delete(user_id)
                    removed += 1
                    logger.warning(f"Broadcast: User {user_id} blocked bot, removed.")
                else:
                    failed += 1
                    logger.warning(f"Broadcast failed for {user_id}: {e}")
            except Exception as e:
                failed += 1
                logger.error(f"Broadcast unexpected error for {user_id}: {e}")

            time.sleep(0.1)  # تجنب Flood Limits

        return sent, failed, removed
