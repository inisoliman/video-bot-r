#!/usr/bin/env python3
# ==============================================================================
# ملف: webhook_bot_v3.py
# الوصف: نقطة الدخول الرئيسية لبوت الفيديو v3.0
# ==============================================================================

import logging
import os
import sys
import traceback

import telebot
from flask import Flask, request, jsonify

# --- تهيئة النظام ---
from bot.core.config import settings
from bot.core.logging_config import setup_logging

setup_logging(debug=os.environ.get('DEBUG', '').lower() == 'true')
logger = logging.getLogger(__name__)

# استخدام print أيضاً لضمان ظهور الرسائل في Render logs
def log_print(msg):
    """طباعة + تسجيل لضمان رؤية الرسائل"""
    print(msg, flush=True)
    logger.info(msg)

# التحقق من الإعدادات
log_print("=" * 50)
log_print("🚀 VIDEO BOT v3.0 STARTING...")
log_print("=" * 50)
settings.validate_or_exit()
settings.log_status()

# --- إنشاء البوت وFlask ---
# ⚠️ threaded=False ضروري مع gunicorn + preload_app=True
# لأن threaded=True يُنشئ Threads في Master لا تنتقل للـ Workers بعد fork()
bot = telebot.TeleBot(settings.bot.token, threaded=False)
app = Flask(__name__)

# --- إعداد معالج أخطاء Telebot ---
class BotExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        print(f"❌ TELEBOT ERROR: {exception}", flush=True)
        logger.error(f"❌ Telebot internal error: {exception}", exc_info=True)
        traceback.print_exc()
        return True  # True = الخطأ تمت معالجته، لا تُعد رميه

bot.exception_handler = BotExceptionHandler()

# --- تسجيل المعالجات ---
try:
    from bot.handlers import register_all_handlers
    register_all_handlers(bot, settings.bot.channel_id, settings.bot.admin_ids)
    log_print(f"✅ Handlers registered. Total message handlers: {len(bot.message_handlers)}")
    log_print(f"   Callback handlers: {len(bot.callback_query_handlers)}")
    log_print(f"   Inline handlers: {len(bot.inline_handlers)}")
except Exception as e:
    print(f"❌ CRITICAL: Handler registration FAILED: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)

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
                print(f"⚠️ WEBHOOK SECRET MISMATCH! Got: '{secret[:10]}...'", flush=True)
                return jsonify({"status": "unauthorized"}), 403

        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)

        # تشخيص: طباعة نوع التحديث
        if update.message:
            msg = update.message
            print(f"📨 MSG from {msg.from_user.id}: text='{msg.text}' cmd={msg.content_type}", flush=True)
        elif update.callback_query:
            print(f"🔘 CALLBACK from {update.callback_query.from_user.id}: data='{update.callback_query.data}'", flush=True)
        elif update.inline_query:
            print(f"🔍 INLINE from {update.inline_query.from_user.id}: query='{update.inline_query.query}'", flush=True)
        else:
            print(f"📦 OTHER update type", flush=True)

        bot.process_new_updates([update])
        return jsonify({"status": "ok"})

    except Exception as e:
        print(f"❌ PROCESS_UPDATE ERROR: {e}", flush=True)
        traceback.print_exc()
        return jsonify({"status": "error"}), 500


# ==============================================================================
# Health & Info Routes
# ==============================================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "running",
        "bot": "Video Search Bot",
        "version": settings.VERSION,
        "handlers": {
            "message": len(bot.message_handlers),
            "callback": len(bot.callback_query_handlers),
            "inline": len(bot.inline_handlers)
        }
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "version": settings.VERSION})


# ==============================================================================
# صفحة التشخيص (Debug)
# ==============================================================================

@app.route("/debug", methods=["GET"])
def debug_info():
    """صفحة تشخيص - تُظهر حالة المعالجات"""
    handlers_info = []
    for h in bot.message_handlers:
        filters = h.get('filters', {})
        commands = filters.get('commands', None)
        func = h.get('function', None)
        name = func.__name__ if func else 'unknown'
        handlers_info.append({
            "name": name,
            "commands": commands,
            "content_types": filters.get('content_types', None),
        })

    return jsonify({
        "status": "running",
        "version": settings.VERSION,
        "message_handlers_count": len(bot.message_handlers),
        "callback_handlers_count": len(bot.callback_query_handlers),
        "inline_handlers_count": len(bot.inline_handlers),
        "message_handlers": handlers_info,
        "admin_ids": settings.bot.admin_ids,
        "channel_id": settings.bot.channel_id,
        "app_url": settings.bot.app_url,
    })


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
    log_print("🤖 Initializing bot...")

    try:
        verify_and_repair_schema()
        log_print("✅ Database schema OK")
    except Exception as e:
        print(f"❌ Database error: {e}", flush=True)
        traceback.print_exc()
        return False

    try:
        # إصلاح مشكلة السلاش المزدوج في URL
        app_url = settings.bot.app_url.rstrip('/')
        log_print(f"📍 APP_URL (cleaned): {app_url}")
        webhook_url = f"{app_url}/bot{settings.bot.token}"

        log_print(f"🔗 Setting webhook to: {webhook_url[:50]}...")
        bot.remove_webhook()

        webhook_params = {
            'url': webhook_url,
            'max_connections': 40,
            'drop_pending_updates': True,
            'allowed_updates': ["message", "callback_query", "inline_query"]
        }

        if settings.bot.webhook_secret and settings.bot.webhook_secret != "default_secret":
            webhook_params['secret_token'] = settings.bot.webhook_secret
            log_print("🔐 Webhook secret configured")
        else:
            log_print("⚠️ No webhook secret (less secure)")

        result = bot.set_webhook(**webhook_params)
        if result:
            log_print(f"✅ Webhook set successfully!")
        else:
            print("❌ Webhook setup FAILED!", flush=True)
    except Exception as e:
        print(f"❌ Webhook error: {e}", flush=True)
        traceback.print_exc()

    log_print("🚀 Bot initialization completed!")
    start_history_cleanup()
    return True


# ==============================================================================
# تهيئة عند الاستيراد (لـ gunicorn)
# ==============================================================================

_initialized = init_bot()
if not _initialized:
    print("💥 Bot initialization failed!", flush=True)


# ==============================================================================
# تشغيل التطبيق (للتطوير المحلي فقط)
# ==============================================================================

if __name__ == "__main__":
    log_print("🔥 Starting Video Bot v3.0...")
    if not _initialized:
        exit(1)
    log_print(f"🌐 Starting Flask on port {settings.bot.port}")
    app.run(host="0.0.0.0", port=settings.bot.port, debug=False)
