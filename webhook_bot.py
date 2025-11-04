# ==============================================================================
# ملف: webhook_bot.py (fix import syntax; stage 2 features intact)
# ==============================================================================

import os
import logging
from flask import Flask, request, jsonify, abort
import telebot
from telebot.types import Update

from db_manager import create_indexes
from handlers import register_all_handlers
from state_manager import state_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
# Fallback to BASE_URL if APP_URL missing
APP_URL = os.getenv("APP_URL") or os.getenv("BASE_URL")
PORT = int(os.getenv("PORT", "10000"))

logger.info("🔍 Environment Check:")
logger.info(f"BOT_TOKEN: {'✅ Set' if BOT_TOKEN else '❌ Missing'}")
logger.info(f"DATABASE_URL: {'✅ Set' if DATABASE_URL else '❌ Missing'}")
logger.info(f"CHANNEL_ID: {'✅ Set' if CHANNEL_ID else '❌ Missing'}")
logger.info(f"ADMIN_IDS: {'✅ Set' if ADMIN_IDS_STR else '❌ Missing'}")
logger.info(f"APP_URL: {'✅ Set' if os.getenv('APP_URL') else '❌ Missing'} | BASE_URL: {'✅ Set' if os.getenv('BASE_URL') else '❌ Missing'}")
logger.info(f"PORT: {PORT}")

missing = []
for k, v in {"BOT_TOKEN": BOT_TOKEN, "DATABASE_URL": DATABASE_URL, "CHANNEL_ID": CHANNEL_ID, "ADMIN_IDS": ADMIN_IDS_STR, "APP_URL/BASE_URL": APP_URL}.items():
    if not v: missing.append(k)
if missing:
    logger.critical(f"❌ Missing environment variables: {', '.join(missing)}")
    raise SystemExit(1)

try:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(',') if x.strip()]
except ValueError as e:
    logger.critical(f"ADMIN_IDS parse error: {e}")
    raise SystemExit(1)

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
    # إنشاء الفهارس لتحسين الأداء
    create_indexes()
    # تسجيل المعالجات
    register_all_handlers(bot, CHANNEL_ID, ADMIN_IDS)
    # تثبيت الويب هوك
    bot.remove_webhook()
    bot.set_webhook(url=f"{APP_URL}/bot{BOT_TOKEN}", max_connections=40, drop_pending_updates=True)
    logger.info("✅ Bot initialized")

if __name__ == '__main__':
    init_bot()
    app.run(host='0.0.0.0', port=PORT, debug=False)
