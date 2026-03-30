
import os
import logging
from flask import Flask, request, jsonify, abort
from telebot.types import Update
import threading

from config.config import Config
from core.db import verify_and_repair_schema
from core.bot import bot
from core.state_manager import state_manager
from handlers.user_handlers import register_user_handlers
from handlers.callback_handlers import register_callback_handlers
from handlers.admin_handlers import register_admin_handlers
# from handlers.inline_handlers import register_inline_handlers # TODO: Implement inline handlers
# from handlers.group_handlers import register_group_handlers # TODO: Implement group handlers
# from handlers.comment_handlers import register_comment_handlers # TODO: Implement comment handlers

logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Register Handlers ---
register_user_handlers(bot)
register_callback_handlers(bot, Config.ADMIN_IDS)
register_admin_handlers(bot, Config.ADMIN_IDS)
# register_inline_handlers(bot) # TODO
# register_group_handlers(bot) # TODO
# register_comment_handlers(bot) # TODO

# --- Rate Limiting (Optional) ---
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
    if limiter:
        limiter.exempt(health_check)
    return jsonify({
        "status": "healthy",
        "bot": "video-bot-webhook",
        "version": "3.0.0", # Updated version
        "webhook_configured": bool(Config.APP_URL)
    })

@app.route("/health", methods=["GET"])
def health():
    if limiter:
        limiter.exempt(health)
    try:
        from core.db import get_db_connection
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
        "bot_token": "configured" if Config.BOT_TOKEN else "missing",
        "webhook_configured": bool(Config.APP_URL)
    })

@app.route(f"/bot{Config.BOT_TOKEN}", methods=["POST"])
def webhook():
    if limiter:
        try:
            limiter.check()
        except Exception:
            logger.warning(f"Rate limit exceeded from {request.remote_addr}")
            abort(429)  # Too Many Requests
    
    try:
        if Config.WEBHOOK_SECRET and Config.WEBHOOK_SECRET != "default_secret":
            secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if secret_token and secret_token != Config.WEBHOOK_SECRET:
                logger.warning(f"Webhook secret mismatch from {request.remote_addr}")
        
        if request.content_type != "application/json":
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
        
        # Process update in a separate thread to avoid blocking the webhook
        threading.Thread(target=process_update, args=(update,)).start()
        
        return jsonify({"ok": True})
        
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({"error": "server_error"}), 500

def process_update(update):
    try:
        # Handle user state first
        if update.message and update.message.from_user:
            if state_manager.handle_message(update.message, bot):
                return
        
        # Process different update types
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
        webhook_url = f"{Config.APP_URL}/bot{Config.BOT_TOKEN}"
        
        bot.remove_webhook()
        logger.info("🗑️ Old webhook removed")
        
        webhook_params = {
            "url": webhook_url,
            "max_connections": 40,
            "drop_pending_updates": True,
            "allowed_updates": ["message", "callback_query", "inline_query"]
        }
        
        if Config.WEBHOOK_SECRET and Config.WEBHOOK_SECRET != "default_secret":
            webhook_params["secret_token"] = Config.WEBHOOK_SECRET
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

# --- Background Tasks (Placeholder for actual implementation) ---
# These would typically be handled by a separate worker process or a task queue (e.g., Celery)
# For now, they are just stubs that log a message.

@app.route("/admin/update_thumbnails", methods=["GET", "POST"])
def admin_update_thumbnails_route():
    user_id = request.args.get("admin_id") or request.form.get("admin_id")
    if not user_id or int(user_id) not in Config.ADMIN_IDS:
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    logger.info("Admin requested thumbnail update. (Background task not yet implemented)")
    return jsonify({"status": "info", "message": "Thumbnail update initiated (placeholder)."})

# ... other admin routes for maintenance tasks ...

# --- Initial Setup ---
if __name__ == "__main__":
    verify_and_repair_schema()
    logger.info(f"Starting Flask app on port {Config.PORT}")
    app.run(host="0.0.0.0", port=Config.PORT)
