#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/handlers/group_handlers.py
# الوصف: معالجات الجروبات (ترحيب + مساعدة بحث)
# ==============================================================================

import logging
import threading
import time

logger = logging.getLogger(__name__)


def register(bot, admin_ids):

    @bot.message_handler(content_types=['new_chat_members'])
    def welcome_new_member(message):
        try:
            bot_info = bot.get_me()
            if any(m.id == bot_info.id for m in message.new_chat_members):
                text = (
                    "👋 *مرحباً! تم إضافتي للجروب*\n\n"
                    "🔍 *للبحث السريع:*\n"
                    f"اكتب: `@{bot_info.username} كلمة البحث`\n\n"
                    "💡 يمكنك البحث في أي محادثة بدون إضافتي كعضو!\n\n"
                    "⚡ ستُحذف هذه الرسالة بعد 30 دقيقة"
                )
                sent = bot.send_message(message.chat.id, text, parse_mode="Markdown")

                def delete_later():
                    try:
                        time.sleep(1800)
                        bot.delete_message(message.chat.id, sent.message_id)
                    except:
                        pass

                threading.Thread(target=delete_later, daemon=True).start()
                logger.info(f"Bot added to group {message.chat.id}")
        except Exception as e:
            logger.error(f"Welcome error: {e}", exc_info=True)

    @bot.message_handler(commands=['search_help'])
    def handle_search_help(message):
        try:
            bot_info = bot.get_me()
            is_group = message.chat.type in ['group', 'supergroup']
            text = (
                "🔍 *كيفية البحث عن الفيديوهات*\n\n"
                f"1️⃣ اكتب: `@{bot_info.username}`\n"
                "2️⃣ اكتب كلمة البحث\n"
                "3️⃣ اختر من النتائج\n\n"
                f"💡 `@{bot_info.username} أكشن`\n"
                f"💡 `@{bot_info.username}` (الأكثر مشاهدة)"
            )
            sent = bot.send_message(message.chat.id, text, parse_mode="Markdown")
            if is_group:
                def delete_later():
                    try:
                        time.sleep(1800)
                        bot.delete_message(message.chat.id, sent.message_id)
                        bot.delete_message(message.chat.id, message.message_id)
                    except:
                        pass
                threading.Thread(target=delete_later, daemon=True).start()
        except Exception as e:
            logger.error(f"Search help error: {e}", exc_info=True)
