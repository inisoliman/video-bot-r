# ==============================================================================
# webhook_bot.py (ensure bootstrap before handlers and indexes; better logs)
# ==============================================================================

import os
import logging
from flask import Flask, request, jsonify, abort
import telebot
from telebot.types import Update

from db_manager import bootstrap_schema, create_indexes
from handlers import register_all_handlers
from state_manager import state_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
APP_URL = os.getenv("APP_URL") or os.getenv("BASE_URL")
PORT = int(os.getenv("PORT", "10000"))

if not all([BOT_TOKEN, DATABASE_URL, CHANNEL_ID, ADMIN_IDS_STR, APP_URL]):
    raise SystemExit("Missing required environment variables")

try:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(',') if x.strip()]
except ValueError as e:
    raise SystemExit(f"ADMIN_IDS parse error: {e}")

app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "ok", "webhook_url": f"{APP_URL}/bot{BOT_TOKEN[:8]}..."})

@app.route('/live', methods=['GET'])
def live():
    return jsonify({"live": True})

@app.route('/ready', methods=['GET'])
def ready():
    return jsonify({"ready": True})

@app.route(f"/bot{BOT_TOKEN}", methods=['POST'])
def webhook():
    if request.content_type != 'application/json':
        abort(400)
    data = request.get_json(silent=True)
    if not data:
        abort(400)
    upd = Update.de_json(data)
    if upd.message and state_manager.handle_message(upd.message, bot):
        return jsonify({"ok": True})
    if upd.message:
        bot.process_new_messages([upd.message])
    elif upd.callback_query:
        bot.process_new_callback_query([upd.callback_query])
    return jsonify({"ok": True})

@app.route('/set_webhook', methods=['GET', 'POST'])
def set_webhook():
    bot.remove_webhook()
    url = f"{APP_URL}/bot{BOT_TOKEN}"
    ok = bot.set_webhook(url=url, max_connections=40, drop_pending_updates=True, allowed_updates=["message", "callback_query"])
    return jsonify({"ok": bool(ok), "url": url})

@app.route('/webhook_info', methods=['GET'])
def webhook_info():
    info = bot.get_webhook_info()
    return jsonify({"url": info.url, "pending": info.pending_update_count, "last_error": info.last_error_message})


def init_bot():
    # 1) إنشاء الجداول أولاً
    logger.info("Bootstrapping database schema...")
    bootstrap_schema()
    logger.info("✅ Bootstrap completed.")

    # 2) إنشاء الفهارس
    logger.info("Ensuring performance indexes...")
    create_indexes()
    logger.info("✅ Indexes ensured.")

    # 3) تسجيل المعالجات بعد وجود الجداول
    logger.info("Registering handlers...")
    register_all_handlers(bot, CHANNEL_ID, ADMIN_IDS)
    logger.info("✅ Handlers registered.")

    # 4) تثبيت الويب هوك
    bot.remove_webhook()
    bot.set_webhook(url=f"{APP_URL}/bot{BOT_TOKEN}", max_connections=40, drop_pending_updates=True)
    logger.info("✅ Webhook set.")

if __name__ == '__main__':
    init_bot()
    app.run(host='0.0.0.0', port=PORT, debug=False)
