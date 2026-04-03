#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/services/subscription_service.py
# الوصف: خدمة التحقق من اشتراكات القنوات
# ==============================================================================

import logging
from typing import List, Tuple

from bot.database.repositories.settings_repo import SettingsRepository

logger = logging.getLogger(__name__)


class SubscriptionService:
    """خدمة التحقق من اشتراكات القنوات"""

    @staticmethod
    def check(bot, user_id: int) -> Tuple[bool, List[dict]]:
        """
        التحقق من اشتراك المستخدم في جميع القنوات المطلوبة.
        
        Returns:
            (is_subscribed, unsub_channels) - حالة الاشتراك وقائمة القنوات غير المشترك بها
        """
        channels = SettingsRepository.get_channels()
        if not channels:
            return True, []

        unsub = []
        for channel in channels:
            try:
                member = bot.get_chat_member(channel['channel_id'], user_id)
                if member.status in ['left', 'kicked']:
                    unsub.append(channel)
            except Exception as e:
                logger.warning(f"Could not check subscription for channel {channel['channel_id']}: {e}")
                # في حالة الخطأ نسمح بالمرور (لا نحبس المستخدم)

        return len(unsub) == 0, unsub
