# ==============================================================================
# ملف: bot.py (النسخة المحسنة لحل مشاكل timeout)
# الوصف: نقطة انطلاق البوت مع معالجة محسنة لمشاكل الاتصال
# ==============================================================================

import telebot
import telebot.apihelper
import os
import time
import logging
from threading import Thread
from telebot.apihelper import ApiTelegramException
import requests
from requests.exceptions import ConnectionError, ReadTimeout, Timeout

# استيراد BotManager
from bot_manager import BotManager 
from db_manager import verify_and_repair_schema
from handlers import register_all_handlers 
from keep_alive import keep_alive

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

# إعدادات timeout
POLLING_TIMEOUT = int(os.getenv("POLLING_TIMEOUT", "10"))
LONG_POLLING_TIMEOUT = int(os.getenv("LONG_POLLING_TIMEOUT", "5"))

if not all([BOT_TOKEN, DATABASE_URL, CHANNEL_ID, ADMIN_IDS_STR]):
    logger.critical("FATAL ERROR: Missing one or more environment variables.")
    exit()

try:
    ADMIN_IDS = [int(admin_id) for admin_id in ADMIN_IDS_STR.split(",") if admin_id.strip()]
except ValueError:
    logger.critical("FATAL ERROR: ADMIN_IDS contains non-integer values.")
    exit()

# تحسين إعدادات TeleBot
telebot.apihelper.CONNECT_TIMEOUT = 15
telebot.apihelper.READ_TIMEOUT = 20

bot = telebot.TeleBot(
    BOT_TOKEN, 
    parse_mode='HTML',
    threaded=True,
    skip_pending=True
)

# --- دالة تشغيل البوت مع معالجة محسنة للأخطاء ---
def run_bot_polling():
    """الدالة التي تقوم بتشغيل البولينج الفعلي للبوت مع معالجة أخطاء timeout."""
    logger.info("✅ Bot polling has started.")
    
    while True:
        try:
            # استخدام infinity_polling مع إعدادات محسنة
            bot.infinity_polling(
                timeout=POLLING_TIMEOUT,
                long_polling_timeout=LONG_POLLING_TIMEOUT,
                allowed_updates=None,
                none_stop=False,
                interval=2
            )
            
        except (ReadTimeout, requests.exceptions.ReadTimeout, TimeoutError) as e:
            logger.warning(f"Read timeout occurred: {e}. Retrying in 5 seconds...")
            time.sleep(5)
            
        except (ConnectionError, requests.exceptions.ConnectionError) as e:
            logger.warning(f"Connection error occurred: {e}. Retrying in 10 seconds...")
            time.sleep(10)
            
        except ApiTelegramException as e:
            logger.error(f"Telegram API Error caught: {e.description}")
            if e.error_code == 409:
                logger.warning("Conflict error (409) caught. This should ideally be prevented by BotManager.")
                time.sleep(60)
            elif e.error_code in [429, 502, 503, 504]:  # Rate limit or server errors
                logger.warning(f"Server error {e.error_code}. Retrying in 30 seconds...")
                time.sleep(30)
            else:
                logger.info("A non-conflict API error occurred. Retrying in 15 seconds...")
                time.sleep(15)
                
        except Exception as e:
            logger.error(f"An unexpected error occurred in the main polling loop: {e}", exc_info=True)
            logger.info("Restarting in 20 seconds...")
            time.sleep(20)

def run_bot_with_retry():
    """تشغيل البوت مع إعادة المحاولة التلقائية"""
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            logger.info(f"Starting bot (attempt {retry_count + 1}/{max_retries})")
            run_bot_polling()
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            retry_count += 1
            logger.error(f"Bot crashed (attempt {retry_count}/{max_retries}): {e}")
            if retry_count < max_retries:
                wait_time = min(30 * retry_count, 120)
                logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
                retry_count = 0  # إعادة تعيين العداد بعد الانتظار
            else:
                logger.error("Max retries reached. Bot will stop.")
                break

# --- نقطة انطلاق البوت ---
if __name__ == "__main__":
    logger.info("Bot is starting up...")

    # 1. تهيئة مدير البوت
    manager = BotManager(bot_name="video_bot")
    
    # 2. فحص وإصلاح قاعدة البيانات
    try:
        verify_and_repair_schema()
    except Exception as e:
        logger.error(f"Database verification failed: {e}")
        logger.info("Continuing with bot startup...")
    
    # 3. تسجيل المعالجات
    register_all_handlers(bot, CHANNEL_ID, ADMIN_IDS)
    
    # 4. تشغيل خادم keep_alive في خيط منفصل
    keep_alive()
    
    # 5. تشغيل البوت بأمان مع آلية إعادة المحاولة
    manager.start_bot_safely(run_bot_with_retry)
    
    logger.info("Bot process finished.")
