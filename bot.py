# ==============================================================================
# ملف: bot.py (النسخة النهائية مع تفعيل نظام فحص قاعدة البيانات)
# الوصف: نقطة انطلاق البوت التي تضمن سلامة قاعدة البيانات قبل بدء التشغيل.
# ==============================================================================

import telebot
import os
import time
import logging
from threading import Thread
from telebot.apihelper import ApiTelegramException

from db_manager import verify_and_repair_schema
from handlers import register_all_handlers # <-- تعديل هنا
from keep_alive import keep_alive
from bot_manager import BotManager

# --- إعداد نظام التسجيل (Logging) ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- الإعدادات الأساسية ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")

if not all([BOT_TOKEN, DATABASE_URL, CHANNEL_ID, ADMIN_IDS_STR]):
    logger.critical("FATAL ERROR: Missing one or more environment variables.")
    exit()

try:
    ADMIN_IDS = [int(admin_id) for admin_id in ADMIN_IDS_STR.split(",") if admin_id.strip()]
except ValueError:
    logger.critical("FATAL ERROR: ADMIN_IDS contains non-integer values.")
    exit()

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# --- دالة تشغيل البوت في خيط منفصل ---
def run_bot_polling():
    logger.info("✅ Bot polling has started.")
    while True:
        try:
            bot.polling(non_stop=True)
        except ApiTelegramException as e:
            logger.error(f"Telegram API Error caught: {e.description}")
            if e.error_code == 409:
                logger.warning("Conflict error (409): Another instance of the bot is likely running.")
                time.sleep(60)
            else:
                logger.info("A non-conflict API error occurred. Retrying in 30 seconds...")
                time.sleep(30)
        except Exception as e:
            logger.error(f"An unexpected error occurred in the main polling loop: {e}", exc_info=True)
            logger.info("Restarting in 15 seconds...")
            time.sleep(15)

# --- دالة تشغيل البوت الرئيسية ---
def main_bot_function():
    logger.info("Bot is starting up...")
    
    verify_and_repair_schema()
    register_all_handlers(bot, CHANNEL_ID, ADMIN_IDS)
    
    keep_alive()
    
    bot_thread = Thread(target=run_bot_polling)
    bot_thread.start()
    bot_thread.join()

# --- نقطة انطلاق البوت ---
if __name__ == "__main__":
    bot_manager = BotManager("video_bot")
    
    # التحقق من وجود نسخة أخرى من البوت
    if bot_manager.is_bot_running():
        logger.warning("Another bot instance is already running!")
        response = input("Do you want to stop the existing instance and start a new one? (y/N): ")
        if response.lower() == 'y':
            if bot_manager.stop_existing_bot():
                logger.info("Starting new bot instance...")
                bot_manager.start_bot_safely(main_bot_function)
            else:
                logger.error("Failed to stop existing bot instance.")
        else:
            logger.info("Exiting...")
    else:
        bot_manager.start_bot_safely(main_bot_function)
