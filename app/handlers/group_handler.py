from telebot import TeleBot
from ..logger import logger


def register(bot: TeleBot, admin_ids: list):
    @bot.message_handler(content_types=['new_chat_members'])
    def welcome(message):
        try:
            bot_info = bot.get_me()
            if any(member.id == bot_info.id for member in message.new_chat_members):
                bot.send_message(message.chat.id, f'👋 تم إضافتي للمجموعة! اكتب @${bot_info.username} ثم كلمة للبحث.')
        except Exception as e:
            logger.exception('Error in welcome handler')

    @bot.message_handler(commands=['search_help'])
    def search_help(message):
        text = '🔍 اكتب @username ثم الكلمة في أي مجموعة للبحث.'
        bot.send_message(message.chat.id, text)
