# ==============================================================================
# ملف: webhook_bot.py (محدث للعمل مع Render)
# الوصف: البوت الرئيسي باستخدام webhook - محسن لـ Render
# ==============================================================================

import os
import json
import logging
from flask import Flask, request, jsonify, abort
import telebot
from telebot.types import Update

# استيراد الوحدات المخصصة
from db_manager import verify_and_repair_schema
from handlers import register_all_handlers
from state_manager import state_manager

# --- إعداد نظام التسجيل ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- المتغيرات البيئية مع قيم افتراضية للاختبار ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL") 
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "default_secret")
APP_URL = os.getenv("APP_URL")

# Render يستخدم PORT بدلاً من WEBHOOK_PORT
PORT = int(os.getenv("PORT", "10000"))

# طباعة المتغيرات للتشخيص (بدون كشف القيم الحساسة)
logger.info(f"🔍 Environment Check:")
logger.info(f"BOT_TOKEN: {'✅ Set' if BOT_TOKEN else '❌ Missing'}")
logger.info(f"DATABASE_URL: {'✅ Set' if DATABASE_URL else '❌ Missing'}")
logger.info(f"CHANNEL_ID: {'✅ Set' if CHANNEL_ID else '❌ Missing'}")
logger.info(f"ADMIN_IDS: {'✅ Set' if ADMIN_IDS_STR else '❌ Missing'}")
logger.info(f"APP_URL: {'✅ Set' if APP_URL else '❌ Missing'}")
logger.info(f"PORT: {PORT}")

# التحقق من المتغيرات المطلوبة
missing_vars = []
if not BOT_TOKEN: missing_vars.append("BOT_TOKEN")
if not DATABASE_URL: missing_vars.append("DATABASE_URL")
if not CHANNEL_ID: missing_vars.append("CHANNEL_ID")
if not ADMIN_IDS_STR: missing_vars.append("ADMIN_IDS")
if not APP_URL: missing_vars.append("APP_URL")

if missing_vars:
    logger.critical(f"❌ MISSING ENVIRONMENT VARIABLES: {', '.join(missing_vars)}")
    logger.critical("📋 Required variables:")
    logger.critical("   BOT_TOKEN=your_bot_token")
    logger.critical("   DATABASE_URL=your_postgres_url")
    logger.critical("   CHANNEL_ID=-1001234567890")
    logger.critical("   ADMIN_IDS=123456789,987654321")
    logger.critical("   APP_URL=https://your-app.onrender.com")
    exit(1)

try:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]
    logger.info(f"✅ ADMIN_IDS parsed: {len(ADMIN_IDS)} admins")
except ValueError as e:
    logger.critical(f"❌ ADMIN_IDS format error: {e}")
    exit(1)

# --- إعداد Flask والBot ---
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# --- Routes ---
@app.route("/", methods=["GET"])
def health_check():
    return jsonify({
        "status": "healthy",
        "bot": "video-bot-webhook",
        "version": "2.0.0",
        "webhook_url": f"{APP_URL}/bot{BOT_TOKEN[:10]}..."
    })

@app.route("/health", methods=["GET"])
def health():
    try:
        from db_manager import get_db_connection
        conn = get_db_connection()
        if conn:
            conn.close()
            db_status = "connected"
        else:
            db_status = "disconnected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return jsonify({
        "status": "ok",
        "database": db_status,
        "bot_token": "configured" if BOT_TOKEN else "missing",
        "webhook_url": f"{APP_URL}/bot{BOT_TOKEN}"
    })

@app.route(f"/bot{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        if request.content_type != 'application/json':
            logger.warning(f"Invalid content-type: {request.content_type}")
            abort(400)
        
        json_data = request.get_json()
        if not json_data:
            logger.warning("Empty JSON received")
            abort(400)
        
        update = Update.de_json(json_data)
        if not update:
            logger.warning("Invalid update object")
            abort(400)
        
        # معالجة التحديث
        process_update(update)
        
        return jsonify({"ok": True})
        
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({"error": "server_error"}), 500

def process_update(update):
    try:
        # معالجة حالة المستخدم أولاً
        if update.message and update.message.from_user:
            if state_manager.handle_message(update.message, bot):
                return
        
        # معالجة أنواع التحديثات المختلفة
        if update.message:
            bot.process_new_messages([update.message])
        elif update.callback_query:
            bot.process_new_callback_query([update.callback_query])
        elif update.inline_query:
            bot.process_new_inline_query([update.inline_query])
            
    except Exception as e:
        logger.error(f"Process update error: {e}", exc_info=True)

@app.route("/set_webhook", methods=["POST", "GET"])
def set_webhook():
    try:
        webhook_url = f"{APP_URL}/bot{BOT_TOKEN}"
        
        # حذف webhook القديم
        bot.remove_webhook()
        logger.info("🗑️ Old webhook removed")
        
        # تعيين webhook جديد
        result = bot.set_webhook(
            url=webhook_url,
            max_connections=40,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"]
        )
        
        if result:
            logger.info(f"✅ Webhook set: {webhook_url}")
            return jsonify({
                "status": "success", 
                "webhook": webhook_url
            })
        else:
            logger.error("❌ Failed to set webhook")
            return jsonify({
                "status": "failed",
                "error": "Could not set webhook"
            }), 500
            
    except Exception as e:
        logger.error(f"Set webhook error: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route("/webhook_info", methods=["GET"])
def webhook_info():
    try:
        info = bot.get_webhook_info()
        return jsonify({
            "url": info.url,
            "pending_updates": info.pending_update_count,
            "last_error": info.last_error_message,
            "max_connections": info.max_connections
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- تهيئة البوت ---
def init_bot():
    logger.info("🤖 Initializing bot...")
    
    try:
        # فحص قاعدة البيانات
        verify_and_repair_schema()
        logger.info("✅ Database schema OK")
    except Exception as e:
        logger.error(f"❌ Database error: {e}")
        return False
    
    try:
        # تسجيل معالجات البوت
        register_all_handlers(bot, CHANNEL_ID, ADMIN_IDS)
        logger.info("✅ Bot handlers registered")
    except Exception as e:
        logger.error(f"❌ Handlers error: {e}")
        return False
    
    try:
        # إعداد webhook
        webhook_url = f"{APP_URL}/bot{BOT_TOKEN}"
        bot.remove_webhook()
        result = bot.set_webhook(
            url=webhook_url,
            max_connections=40,
            drop_pending_updates=True
        )
        
        if result:
            logger.info(f"✅ Webhook set: {webhook_url}")
        else:
            logger.warning("⚠️ Webhook setup failed")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        # لا نتوقف هنا، سيتم إعداده لاحقاً
    
    logger.info("🚀 Bot initialization completed!")
    return True

# --- تشغيل التطبيق ---
if __name__ == "__main__":
    logger.info("🔥 Starting Video Bot Webhook Server...")
    
    if not init_bot():
        logger.critical("💥 Bot initialization failed")
        exit(1)
    
    # تشغيل Flask على المنفذ الصحيح لـ Render
    logger.info(f"🌐 Starting Flask server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)