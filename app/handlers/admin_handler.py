from telebot import TeleBot
from ..services import video_service
from ..logger import logger
from .helpers import main_menu_keyboard


def register(bot: TeleBot, admin_ids: list):
    @bot.message_handler(commands=['admin'])
    def admin_panel(message):
        if message.from_user.id not in admin_ids:
            bot.reply_to(message, '⛔ غير مصرح.')
            return
        bot.send_message(message.chat.id, 'لوحة تحكم الأدمن: /sync_db /top_videos', reply_markup=main_menu_keyboard())

    @bot.message_handler(commands=['top_videos'])
    def top_videos(message):
        videos = video_service.top_videos(10)
        text = '📈 أعلى الفيديوهات:\n' + '\n'.join([f"{v['id']}. {v.get('caption','')}({v.get('view_count',0)})" for v in videos])
        bot.reply_to(message, text)
