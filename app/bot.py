from telebot import TeleBot
from .config import settings
from .handlers import register_all_handlers
from .logger import logger

bot = TeleBot(settings.BOT_TOKEN, parse_mode='HTML')


def init_bot():
    logger.info('Initializing bot handlers')
    register_all_handlers(bot, settings.CHANNEL_ID, settings.admin_ids)


init_bot()
