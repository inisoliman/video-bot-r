#!/usr/bin/env python3
# ==============================================================================
# Ù…Ù„Ù: app/webhook.py (Flask Application)
# Ø§Ù„ÙˆØµÙ: ØªØ·Ø¨ÙŠÙ‚ Flask Ù„Ù„ØªØ¹Ø§Ù…Ù„ Ù…Ø¹ webhooks Ù…Ù† Telegram
# ==============================================================================

from flask import Flask, request, jsonify, abort
from telebot.types import Update
import logging

from .config import settings
from .database import init_db, get_db_connection
from .handlers import register_all_handlers
from .state_manager import StateManager
from .logger import setup_logger

logger = logging.getLogger(__name__)

# Ø¥Ø¹Ø¯Ø§Ø¯ Flask
app = Flask(__name__)

# Ø¥Ø¯Ø§Ø±Ø© Ø§Ù„Ø­Ø§Ù„Ø©
state_manager = StateManager()

# --- Routes ---
@app.route("/", methods=["GET"])
def health_check():
    """ÙØ­Øµ ØµØ­Ø© Ø§Ù„Ø®Ø¯Ù…Ø©"""
    return jsonify({
        "status": "healthy",
        "bot": "video-bot-webhook",
        "version": "2.0.0",
        "webhook_configured": False
    })

@app.route("/health", methods=["GET"])
def health():
    """ÙØ­Øµ ØµØ­Ø© Ù‚Ø§Ø¹Ø¯Ø© Ø§Ù„Ø¨ÙŠØ§Ù†Ø§Øª"""
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
    """Ù…Ø¹Ø§Ù„Ø¬Ø© Ø§Ù„ÙˆÙŠØ¨Ù‡Ø§ÙˆÙƒ"""
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
        
        # Ù…Ø¹Ø§Ù„Ø¬Ø© Ø§Ù„ØªØ­Ø¯ÙŠØ«
        process_update(update)
        
        return jsonify({"ok": True})
        
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({"error": "server_error"}), 500