# ==============================================================================
# ملف: webhook_bot.py (نظام webhook المحسن)
# الوصف: البوت الرئيسي باستخدام webhook بدلاً من polling
# ==============================================================================

import os
import json
import logging
import hmac
import hashlib
from threading import Thread
from flask import Flask, request, jsonify, abort
import telebot
from telebot.types import Update
from cryptography.fernet import Fernet

# استيراد الوحدات المخصصة
from db_manager import verify_and_repair_schema
from handlers import register_all_handlers
from state_manager import state_manager

# --- إعداد نظام التسجيل ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("webhook_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- المتغيرات البيئية ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "your_secure_secret_key")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8000"))
APP_URL = os.getenv("APP_URL")  # رابط التطبيق على Render

# التحقق من المتغيرات المطلوبة
if not all([BOT_TOKEN, DATABASE_URL, CHANNEL_ID, ADMIN_IDS_STR, APP_URL]):
    logger.critical("FATAL ERROR: Missing required environment variables")
    exit(1)

try:
    ADMIN_IDS = [int(admin_id) for admin_id in ADMIN_IDS_STR.split(",") if admin_id.strip()]
except ValueError:
    logger.critical("FATAL ERROR: ADMIN_IDS contains non-integer values")
    exit(1)

# --- إعداد Flask والBot ---
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# --- دوال الأمان ---
def verify_webhook_signature(data: bytes, signature: str) -> bool:
    """التحقق من صحة توقيع webhook"""
    if not signature:
        return False
    
    try:
        expected_signature = hmac.new(
            WEBHOOK_SECRET.encode('utf-8'),
            data,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected_signature)
    except Exception as e:
        logger.error(f"Error verifying webhook signature: {e}")
        return False

def encrypt_sensitive_data(data: str) -> str:
    """تشفير البيانات الحساسة"""
    key = Fernet.generate_key()
    f = Fernet(key)
    return f.encrypt(data.encode()).decode()

# --- Routes ---
@app.route("/", methods=["GET"])
def health_check():
    """فحص صحة التطبيق"""
    return jsonify({
        "status": "healthy",
        "bot": "video-bot-r",
        "version": "2.0.0-webhook"
    })

@app.route("/health", methods=["GET"])
def detailed_health():
    """فحص صحة مفصل"""
    try:
        # اختبار قاعدة البيانات
        from db_manager import get_db_connection
        conn = get_db_connection()
        if conn:
            conn.close()
            db_status = "healthy"
        else:
            db_status = "unhealthy"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return jsonify({
        "status": "healthy",
        "database": db_status,
        "bot_token": "configured" if BOT_TOKEN else "missing",
        "webhook_url": f"{APP_URL}/webhook/{BOT_TOKEN}"
    })

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    """معالج webhook الرئيسي"""
    try:
        # التحقق من Content-Type
        if request.content_type != 'application/json':
            logger.warning(f"Invalid content type: {request.content_type}")
            abort(400)
        
        # الحصول على البيانات
        json_data = request.get_data()
        
        # التحقق من التوقيع (اختياري للحماية الإضافية)
        signature = request.headers.get('X-Webhook-Signature')
        if WEBHOOK_SECRET != "your_secure_secret_key":  # إذا تم تعيين سر حقيقي
            if not verify_webhook_signature(json_data, signature):
                logger.warning("Invalid webhook signature")
                abort(403)
        
        # تحليل JSON
        update_dict = request.get_json()
        if not update_dict:
            logger.warning("Empty JSON received")
            abort(400)
        
        # إنشاء كائن Update
        update = Update.de_json(update_dict)
        if not update:
            logger.warning("Invalid update object")
            abort(400)
        
        # معالجة التحديث
        process_update(update)
        
        return jsonify({"status": "ok"})
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return jsonify({"error": "internal_server_error"}), 500

def process_update(update: Update):
    """معالجة التحديث الوارد من تليجرام"""
    try:
        # معالجة حالة المستخدم أولاً
        if update.message and update.message.from_user:
            if state_manager.handle_message(update.message, bot):
                return  # تم التعامل مع الرسالة بواسطة state manager
        
        # معالجة التحديث بواسطة معالجات البوت
        if update.message:
            bot.process_new_messages([update.message])
        elif update.callback_query:
            bot.process_new_callback_query([update.callback_query])
        elif update.inline_query:
            bot.process_new_inline_query([update.inline_query])
        elif update.chosen_inline_result:
            bot.process_new_chosen_inline_result([update.chosen_inline_result])
        else:
            logger.info(f"Unhandled update type: {type(update)}")
            
    except Exception as e:
        logger.error(f"Error processing update: {e}", exc_info=True)

@app.route("/setup", methods=["POST"])
def setup_webhook():
    """إعداد webhook تلقائياً"""
    try:
        webhook_url = f"{APP_URL}/webhook/{BOT_TOKEN}"
        
        # إزالة webhook القديم
        remove_result = bot.remove_webhook()
        logger.info(f"Remove webhook result: {remove_result}")
        
        # تعيين webhook جديد
        set_result = bot.set_webhook(
            url=webhook_url,
            max_connections=10,
            allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result"]
        )
        
        logger.info(f"Set webhook result: {set_result}")
        
        if set_result:
            return jsonify({
                "status": "success",
                "webhook_url": webhook_url,
                "message": "Webhook setup successfully"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to set webhook"
            }), 500
            
    except Exception as e:
        logger.error(f"Error setting up webhook: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route("/webhook-info", methods=["GET"])
def webhook_info():
    """معلومات webhook الحالي"""
    try:
        webhook_info = bot.get_webhook_info()
        return jsonify({
            "url": webhook_info.url,
            "has_custom_certificate": webhook_info.has_custom_certificate,
            "pending_update_count": webhook_info.pending_update_count,
            "last_error_date": webhook_info.last_error_date,
            "last_error_message": webhook_info.last_error_message,
            "max_connections": webhook_info.max_connections,
            "allowed_updates": webhook_info.allowed_updates
        })
    except Exception as e:
        logger.error(f"Error getting webhook info: {e}")
        return jsonify({"error": str(e)}), 500

# --- تهيئة التطبيق ---
def initialize_bot():
    """تهيئة البوت وقاعدة البيانات"""
    logger.info("Initializing bot...")
    
    # 1. فحص وإصلاح قاعدة البيانات
    try:
        verify_and_repair_schema()
        logger.info("Database schema verified successfully")
    except Exception as e:
        logger.error(f"Database schema verification failed: {e}")
        return False
    
    # 2. تسجيل معالجات البوت
    try:
        register_all_handlers(bot, CHANNEL_ID, ADMIN_IDS)
        logger.info("Bot handlers registered successfully")
    except Exception as e:
        logger.error(f"Failed to register bot handlers: {e}")
        return False
    
    # 3. إعداد webhook تلقائياً
    try:
        webhook_url = f"{APP_URL}/webhook/{BOT_TOKEN}"
        
        # إزالة webhook القديم
        bot.remove_webhook()
        
        # تعيين webhook جديد
        result = bot.set_webhook(
            url=webhook_url,
            max_connections=10,
            allowed_updates=["message", "callback_query", "inline_query"]
        )
        
        if result:
            logger.info(f"Webhook set successfully: {webhook_url}")
        else:
            logger.error("Failed to set webhook")
            return False
            
    except Exception as e:
        logger.error(f"Error setting up webhook: {e}")
        return False
    
    logger.info("Bot initialization completed successfully")
    return True

# --- نقطة البداية ---
if __name__ == "__main__":
    if not initialize_bot():
        logger.critical("Bot initialization failed. Exiting.")
        exit(1)
    
    # تشغيل Flask app
    app.run(
        host=WEBHOOK_HOST,
        port=WEBHOOK_PORT,
        debug=False,
        use_reloader=False
    )
