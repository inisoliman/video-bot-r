# ==============================================================================
# ملف: bot.py (النسخة النهائية مع تفعيل نظام فحص قاعدة البيانات)
# الوصف: نقطة انطلاق البوت التي تضمن سلامة قاعدة البيانات قبل بدء التشغيل.
# ==============================================================================

import telebot
import os
import time
import logging
import gc
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

# تحسينات الأداء للبوت
bot = telebot.TeleBot(
    BOT_TOKEN, 
    parse_mode='HTML',
    threaded=True,
    num_threads=8,
    skip_pending=True
)

# --- دالة تشغيل البوت في خيط منفصل ---
def run_bot_polling():
    logger.info("✅ Bot polling has started.")
    retry_count = 0
    max_retries = 5
    
    while True:
        try:
            # تنظيف الذاكرة قبل بدء التشغيل
            gc.collect()
            
            bot.polling(
                non_stop=True,
                interval=0.5,  # تقليل فترة الاستعلام
                timeout=20,    # زيادة timeout
                long_polling_timeout=20,
                restart_on_change=True
            )
            
        except ApiTelegramException as e:
            logger.error(f"Telegram API Error caught: {e.description}")
            if e.error_code == 409:
                logger.warning("Conflict error (409): Another instance of the bot is likely running.")
                time.sleep(60)
                retry_count += 1
            elif e.error_code == 429:  # Rate limiting
                logger.warning("Rate limit exceeded. Waiting 60 seconds...")
                time.sleep(60)
            else:
                logger.info(f"API error occurred. Retrying in {min(30 * (retry_count + 1), 300)} seconds...")
                time.sleep(min(30 * (retry_count + 1), 300))
                retry_count += 1
                
        except Exception as e:
            logger.error(f"An unexpected error occurred in the main polling loop: {e}", exc_info=True)
            retry_count += 1
            
            if retry_count >= max_retries:
                logger.critical("Maximum retry attempts reached. Resetting retry counter.")
                retry_count = 0
                time.sleep(300)  # انتظار 5 دقائق قبل إعادة المحاولة
            else:
                wait_time = min(15 * retry_count, 120)
                logger.info(f"Restarting in {wait_time} seconds... (Attempt {retry_count}/{max_retries})")
                time.sleep(wait_time)
                
        # تنظيف الذاكرة بعد كل خطأ
        gc.collect()

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
