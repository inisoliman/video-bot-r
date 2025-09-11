# handlers/admin_message_handler.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging

from state_manager import state_manager
from .admin_handlers import (
    handle_rich_broadcast, handle_add_new_category, handle_add_channel_step1,
    handle_add_channel_step2, handle_remove_channel_step, handle_delete_by_ids_input,
    handle_move_by_id_input
)

logger = logging.getLogger(__name__)

def register_admin_message_handler(bot, admin_ids):
    """Register message handler for admin state management"""
    
    @bot.message_handler(func=lambda message: message.from_user.id in admin_ids and message.chat.type == "private" and not message.text.startswith("/"))
    def handle_admin_messages(message):
        user_state = state_manager.get_user_state(message.from_user.id)
        
        if not user_state:
            return  # No state, ignore message
            
        state = user_state.get('state')
        
        if state == 'waiting_broadcast_message':
            handle_rich_broadcast(message, bot)
        elif state == 'waiting_category_name':
            handle_add_new_category(message, bot)
        elif state == 'waiting_channel_id':
            handle_add_channel_step1(message, bot)
        elif state == 'waiting_channel_name':
            handle_add_channel_step2(message, bot)
        elif state == 'waiting_remove_channel_id':
            handle_remove_channel_step(message, bot)
        elif state == 'waiting_video_ids_delete':
            handle_delete_by_ids_input(message, bot)
        elif state == 'waiting_video_id_move':
            handle_move_by_id_input(message, bot)
        else:
            # Unknown state, clear it
            state_manager.clear_user_state(message.from_user.id)