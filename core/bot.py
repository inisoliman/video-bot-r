
# core/bot.py

import telebot
from config.config import Config
from config.constants import PARSE_MODE_HTML
import logging

logger = logging.getLogger(__name__)

# Initialize the bot instance globally
bot = telebot.TeleBot(Config.BOT_TOKEN, parse_mode=PARSE_MODE_HTML)

logger.info("TeleBot instance initialized.")
