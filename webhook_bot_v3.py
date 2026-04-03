#!/usr/bin/env python3
# ==============================================================================
# ملف: webhook_bot_v3.py
# الوصف: نقطة الدخول الرئيسية لبوت الفيديو v3.0
# ==============================================================================

import logging
import os

import telebot
from flask import Flask, request, jsonify

# --- تهيئة النظام ---
from bot.core.config import settings
from bot.core.logging_config import setup_logging

setup_logging(debug=os.environ.get('DEBUG', '').lower() == 'true')
logger = logging.getLogger(__name__)

# التحقق من الإعدادات
settings.validate_or_exit()
settings.log_status()

# --- إنشاء البوت وFlask ---
bot = telebot.TeleBot(settings.bot.token, threaded=True)
app = Flask(__name__)

# --- تسجيل المعالجات ---
from bot.handlers import register_all_handlers
register_all_handlers(bot, settings.bot.channel_id, settings.bot.admin_ids)

# --- Schema ---
from bot.database.connection import verify_and_repair_schema

# --- History Cleaner ---
from history_cleaner import start_history_cleanup


# ==============================================================================
# Webhook Route
# ==============================================================================

@app.route(f"/bot{settings.bot.token}", methods=['POST'])
def process_update():
    """معالجة تحديثات Webhook"""
    try:
        # التحقق من secret token
        if settings.bot.webhook_secret and settings.bot.webhook_secret != "default_secret":
            secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
            if secret != settings.bot.webhook_secret:
                logger.warning("⚠️ Invalid webhook secret token!")
                return jsonify({"status": "unauthorized"}), 403

        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return jsonify({"status": "ok"})

    except Exception as e:
        logger.error(f"Error processing update: {e}", exc_info=True)
        return jsonify({"status": "error"}), 500


# ==============================================================================
# Health & Info Routes
# ==============================================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "running",
        "bot": "Video Search Bot",
        "version": settings.VERSION
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "version": settings.VERSION})


# ==============================================================================
# Admin Diagnostic Routes (محمية)
# ==============================================================================

def _check_admin(admin_id_param):
    """التحقق من صلاحيات الأدمن"""
    admin_id = request.args.get(admin_id_param)
    if not admin_id:
        return None, (jsonify({"error": "Missing admin_id"}), 400)
    try:
        admin_id = int(admin_id)
    except ValueError:
        return None, (jsonify({"error": "Invalid admin_id"}), 400)
    if admin_id not in settings.bot.admin_ids:
        return None, (jsonify({"error": "Unauthorized"}), 403)
    return admin_id, None


@app.route("/admin/status", methods=["GET"])
def admin_status():
    admin_id, err = _check_admin("admin_id")
    if err:
        return err

    from bot.database.repositories.video_repo import VideoRepository
    from bot.database.repositories.user_repo import UserRepository

    stats = VideoRepository.get_stats()
    user_count = UserRepository.get_count()

    return jsonify({
        "status": "running",
        "version": settings.VERSION,
        "stats": {
            "videos": stats.get('video_count', 0),
            "users": user_count,
            "views": stats.get('total_views', 0),
        }
    })


# ==============================================================================
# تهيئة البوت
# ==============================================================================

def init_bot():
    logger.info("🤖 Initializing bot...")

    try:
        verify_and_repair_schema()
        logger.info("✅ Database schema OK")
    except Exception as e:
        logger.error(f"❌ Database error: {e}")
        return False

    try:
        webhook_url = f"{settings.bot.app_url}/bot{settings.bot.token}"
        bot.remove_webhook()

        webhook_params = {
            'url': webhook_url,
            'max_connections': 40,
            'drop_pending_updates': True,
            'allowed_updates': ["message", "callback_query", "inline_query"]
        }

        if settings.bot.webhook_secret and settings.bot.webhook_secret != "default_secret":
            webhook_params['secret_token'] = settings.bot.webhook_secret
            logger.info("🔐 Webhook secret configured")
        else:
            logger.warning("⚠️ No webhook secret (less secure)")

        result = bot.set_webhook(**webhook_params)
        if result:
            logger.info(f"✅ Webhook: {webhook_url}")
        else:
            logger.warning("⚠️ Webhook setup failed")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")

    logger.info("🚀 Bot initialization completed!")
    start_history_cleanup()
    return True


# ==============================================================================
# تهيئة عند الاستيراد (لـ gunicorn)
# ==============================================================================

# يجب استدعاء init_bot عند تحميل الوحدة حتى يعمل مع gunicorn
_initialized = init_bot()
if not _initialized:
    logger.critical("💥 Bot initialization failed!")


# ==============================================================================
# تشغيل التطبيق (للتطوير المحلي فقط)
# ==============================================================================

if __name__ == "__main__":
    logger.info("🔥 Starting Video Bot v3.0...")
    if not _initialized:
        exit(1)
    logger.info(f"🌐 Starting Flask on port {settings.bot.port}")
    app.run(host="0.0.0.0", port=settings.bot.port, debug=False)
