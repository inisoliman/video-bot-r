#!/usr/bin/env python3
# ==============================================================================
# ملف: main.py (الملف الرئيسي للبوت المحسّن)
# الوصف: تشغيل البوت باستخدام Flask + Gunicorn لـ Render
# ==============================================================================

import os
import logging
import psycopg2
from urllib.parse import urlparse
from flask import Flask, request, jsonify, abort
from telebot import TeleBot
from telebot.types import Update
import threading
import time

# استيراد الوحدات المخصصة
from app.config import settings
from app.database import init_db, get_db_connection
from app.handlers import register_all_handlers
from app.state_manager import StateManager
from app.logger import setup_logger

# --- إعداد نظام التسجيل ---
setup_logger()

logger = logging.getLogger(__name__)

# --- إعداد Flask والBot ---
app = Flask(__name__)
bot = TeleBot(settings.BOT_TOKEN, parse_mode='HTML')

# --- إدارة الحالة ---
state_manager = StateManager()

# --- إعداد Rate Limiting (اختياري) ---
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per day", "50 per hour"],
        storage_uri="memory://"
    )
    logger.info("✅ Rate limiting enabled")
except ImportError:
    logger.warning("⚠️ Flask-Limiter not installed. Rate limiting disabled.")
    limiter = None

# --- Routes ---
@app.route("/", methods=["GET"])
def health_check():
    """فحص صحة الخدمة"""
    return jsonify({
        "status": "healthy",
        "bot": "video-bot-webhook",
        "version": "2.0.0",
        "webhook_configured": False
    })

@app.route("/health", methods=["GET"])
def health():
    """فحص صحة قاعدة البيانات"""
    try:
        with get_db_connection() as conn:
            if conn:
                db_status = "connected"
            else:
                db_status = "disconnected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return jsonify({
        "status": "ok",
        "database": db_status,
        "bot_token": "configured" if settings.BOT_TOKEN else "missing",
        "webhook_configured": False
    })

@app.route(f"/bot{settings.BOT_TOKEN}", methods=["POST"])
def webhook():
    """معالجة الويبهاوك"""
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
    """معالجة التحديثات"""
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
    """تعيين الويبهاوك"""
    try:
        webhook_url = f"{settings.APP_URL}/bot{settings.BOT_TOKEN}"
        
        # حذف الويبهاوك القديم
        bot.remove_webhook()
        logger.info("🗑️ Old webhook removed")
        
        # تعيين ويبهاوك جديد
        webhook_params = {
            'url': webhook_url,
            'max_connections': 40,
            'drop_pending_updates': True,
            'allowed_updates': ["message", "callback_query", "inline_query"]
        }
        
        # إضافة secret_token فقط إذا تم تعيينه بشكل مخصص
        if settings.WEBHOOK_SECRET and settings.WEBHOOK_SECRET != "":
            webhook_params['secret_token'] = settings.WEBHOOK_SECRET
            logger.info("🔐 Webhook secret token configured")
        else:
            logger.warning("⚠️ Using webhook without secret token")
        
        result = bot.set_webhook(**webhook_params)
        
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

# --- تشغيل التطبيق ---
def init_bot():
    """تهيئة البوت"""
    logger.info("🤖 Initializing bot...")
    
    try:
        # فحص وتصحيح قاعدة البيانات
        init_db()
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"❌ Database error: {e}")
        return False
    
    try:
        # تسجيل معالجات البوت
        register_all_handlers(bot, settings.CHANNEL_ID, settings.admin_ids)
        logger.info("✅ Bot handlers registered")
    except Exception as e:
        logger.error(f"❌ Handlers error: {e}")
        return False
    
    try:
        # إعداد الويبهاوك
        webhook_url = f"{settings.APP_URL}/bot{settings.BOT_TOKEN}"
        bot.remove_webhook()
        
        webhook_params = {
            'url': webhook_url,
            'max_connections': 40,
            'drop_pending_updates': True,
            'allowed_updates': ["message", "callback_query", "inline_query"]
        }
        
        # إضافة secret_token فقط إذا تم تعيينه بشكل مخصص
        if settings.WEBHOOK_SECRET and settings.WEBHOOK_SECRET != "":
            webhook_params['secret_token'] = settings.WEBHOOK_SECRET
            logger.info("🔐 Webhook secret token configured")
        else:
            logger.warning("⚠️ Using webhook without secret token")
        
        result = bot.set_webhook(**webhook_params)
        
        if result:
            logger.info(f"✅ Webhook set: {webhook_url}")
        else:
            logger.warning("⚠️ Webhook setup failed")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        # لا نتوقف هنا، سيتم إعداده لاحقاً
    
    logger.info("🚀 Bot initialization completed!")
    return True

if __name__ == '__main__':
    logger.info("🔥 Starting Video Bot Webhook Server...")
    
    if not init_bot():
        logger.critical("💥 Bot initialization failed")
        exit(1)
    
    # تشغيل Flask على المنفذ الصحيح لـ Render
    logger.info(f"🌐 Starting Flask server on port {settings.PORT}")
    app.run(host="0.0.0.0", port=settings.PORT, debug=False)

# --- تشغيل مع Gunicorn لـ Render ---
if __name__ == '__wsgi__':
    # هذا لـ Render.com
    init_bot()
    app.run(host="0.0.0.0", port=settings.PORT)