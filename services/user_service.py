
# services/user_service.py

import logging
import telebot
from repositories import user_repository, required_channels_repository

logger = logging.getLogger(__name__)

def add_or_update_user(user_id, username, first_name):
    return user_repository.add_bot_user(user_id, username, first_name)

def check_subscription(bot, user_id):
    required_channels = required_channels_repository.get_required_channels()
    if not required_channels:
        return True, []
    
    unsubscribed = []
    for channel in required_channels:
        try:
            member = bot.get_chat_member(channel["channel_id"], user_id)
            if member.status not in ["member", "administrator", "creator"]:
                unsubscribed.append(channel)
        except telebot.apihelper.ApiTelegramException as e:
            error_msg = str(e.description).lower() if hasattr(e, "description") else str(e).lower()
            if "user not found" in error_msg or "chat not found" in error_msg or "bad request" in error_msg:
                logger.warning(f"Could not check user {user_id} in channel {channel["channel_id"]}. Assuming subscribed. Error: {e}")
            elif "forbidden" in error_msg or "kicked" in error_msg or "left" in error_msg:
                unsubscribed.append(channel)
            else:
                logger.error(f"Error checking subscription for user {user_id} in channel {channel["channel_id"]}: {e}")
                unsubscribed.append(channel)
        except Exception as e:
            logger.error(f"Unexpected error checking subscription for user {user_id} in channel {channel["channel_id"]}: {e}")
            unsubscribed.append(channel)
    
    return not unsubscribed, unsubscribed

def get_all_bot_users():
    return user_repository.get_all_users()
