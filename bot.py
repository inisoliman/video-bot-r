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

if not all([BOT_TOKEN, DATABASE_URL, CHANNEL_ID, ADMIN_IDS_STR]):
    logger.critical("FATAL ERROR: Missing one or more environment variables.")
    exit()

try:
    ADMIN_IDS = [int(admin_id) for admin_id in ADMIN_IDS_STR.split(",") if admin_id.strip()]
except ValueError:
    logger.critical("FATAL ERROR: ADMIN_IDS contains non-integer values.")
    exit()

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# --- دالة تشغيل البوت في خيط منفصل (يتم استدعاؤها من BotManager) ---
def run_bot_polling():
    """الدالة التي تقوم بتشغيل البولينج الفعلي للبوت."""
    logger.info("✅ Bot polling has started.")
    while True:
        try:
            bot.polling(non_stop=True, timeout=30) # زيادة الـ timeout يقلل من طلبات الـ API
        except ApiTelegramException as e:
            logger.error(f"Telegram API Error caught: {e.description}")
            if e.error_code == 409:
                logger.warning("Conflict error (409) caught. This should ideally be prevented by BotManager.")
                time.sleep(60)
            else:
                logger.info("A non-conflict API error occurred. Retrying in 30 seconds...")
                time.sleep(30)
        except Exception as e:
            logger.error(f"An unexpected error occurred in the main polling loop: {e}", exc_info=True)
            logger.info("Restarting in 15 seconds...")
            time.sleep(15)

# --- نقطة انطلاق البوت ---
if __name__ == "__main__":
    logger.info("Bot is starting up...")

    # 1. تهيئة مدير البوت
    manager = BotManager(bot_name="video_bot")
    
    # 2. فحص وإصلاح قاعدة البيانات (خطوة سريعة لضمان سلامة العمل)
    verify_and_repair_schema()
    
    # 3. تسجيل المعالجات
    register_all_handlers(bot, CHANNEL_ID, ADMIN_IDS)
    
    # 4. تشغيل خادم keep_alive في خيط منفصل
    keep_alive()

# في نهاية ملف bot.py، قبل bot.polling()
from history_cleaner import start_history_cleanup

start_history_cleanup()
logger.info("✅ نظام تنظيف سجل المشاهدة مفعّل")
    
    # 5. تشغيل البوت بأمان باستخدام BotManager لمنع خطأ 409
    # هذه الخطوة ستقوم بالتحقق من وجود نسخة أخرى وإيقافها إذا لزم الأمر
    # ثم تبدأ بتشغيل run_bot_polling
    manager.start_bot_safely(run_bot_polling)
    
    logger.info("Bot process finished.")
