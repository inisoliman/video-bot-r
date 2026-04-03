#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/handlers/admin_handlers.py
# الوصف: معالجات أوامر الأدمن
# ==============================================================================

import re
import time
import logging

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiTelegramException

from bot.core.config import settings
from bot.database.repositories.video_repo import VideoRepository
from bot.database.repositories.user_repo import UserRepository
from bot.database.repositories.category_repo import CategoryRepository
from bot.database.repositories.settings_repo import SettingsRepository
from bot.services.broadcast_service import BroadcastService
from bot.ui.messages import Messages
from bot.ui.keyboards import Keyboards
from bot.ui.emoji import Emoji

logger = logging.getLogger(__name__)

DELIMITER = settings.CALLBACK_DELIMITER

# ذاكرة مؤقتة للخطوات الإدارية
admin_steps = {}


def check_cancel(message, bot) -> bool:
    """التحقق من إلغاء العملية"""
    if message.text == "/cancel":
        admin_steps.pop(message.chat.id, None)
        bot.send_message(message.chat.id, Messages.operation_cancelled())
        return True
    return False


# --- Step Handlers ---

def handle_rich_broadcast(message, bot):
    if check_cancel(message, bot):
        return
    sent, failed, removed = BroadcastService.send_broadcast(bot, message)
    bot.send_message(
        message.chat.id,
        Messages.broadcast_complete(sent, failed, removed),
        parse_mode="Markdown"
    )


def handle_add_new_category(message, bot):
    if check_cancel(message, bot):
        return
    name = message.text.strip()
    step_data = admin_steps.pop(message.chat.id, {})
    parent_id = step_data.get("parent_id")

    success, result = CategoryRepository.add(name, parent_id)
    if success:
        parent_info = None
        if parent_id:
            parent = CategoryRepository.get_by_id(parent_id)
            if parent:
                parent_info = parent['name']
        bot.reply_to(message, Messages.category_created(name, parent_info))
    else:
        bot.reply_to(message, f"{Emoji.ERROR} خطأ في إنشاء التصنيف: {result}")


def handle_add_channel_step1(message, bot):
    if check_cancel(message, bot):
        return
    channel_id = message.text.strip()
    admin_steps[message.chat.id] = {"channel_id": channel_id}
    msg = bot.send_message(message.chat.id, "الآن أرسل اسم القناة (مثال: قناة الأفلام). (أو /cancel)")
    bot.register_next_step_handler(msg, handle_add_channel_step2, bot)


def handle_add_channel_step2(message, bot):
    if check_cancel(message, bot):
        return
    channel_name = message.text.strip()
    channel_id = admin_steps.pop(message.chat.id, {}).get("channel_id")
    if not channel_id:
        return
    if SettingsRepository.add_channel(channel_id, channel_name):
        bot.send_message(message.chat.id, f"{Emoji.SUCCESS} تم إضافة القناة \"{channel_name}\" (ID: `{channel_id}`).", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, f"{Emoji.ERROR} حدث خطأ أثناء إضافة القناة.")


def handle_remove_channel_step(message, bot):
    if check_cancel(message, bot):
        return
    channel_id = message.text.strip()
    if SettingsRepository.remove_channel(channel_id):
        bot.send_message(message.chat.id, f"{Emoji.SUCCESS} تم إزالة القناة (ID: `{channel_id}`).", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, f"{Emoji.ERROR} حدث خطأ أو القناة غير موجودة.")


def handle_list_channels(message, bot):
    channels = SettingsRepository.get_channels()
    if channels:
        response = f"{Emoji.CHANNELS} *القنوات المطلوبة:*\n"
        response += "\n".join([f"  {Emoji.DOT} {ch['channel_name']} (ID: `{ch['channel_id']}`)" for ch in channels])
        bot.send_message(message.chat.id, response, parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "لا توجد قنوات مطلوبة حالياً.")


def handle_delete_by_ids_input(message, bot):
    if check_cancel(message, bot):
        return
    try:
        ids_str = re.split(r'[,\s\n]+', message.text.strip())
        video_ids = [int(n) for n in ids_str if n.isdigit()]
        if not video_ids:
            msg = bot.reply_to(message, "لم يتم إدخال أرقام صحيحة. حاول مرة أخرى أو أرسل /cancel.")
            bot.register_next_step_handler(msg, handle_delete_by_ids_input, bot)
            return
        deleted = VideoRepository.delete_by_ids(video_ids)
        bot.reply_to(message, Messages.videos_deleted(deleted))
    except Exception as e:
        logger.error(f"Delete by ids error: {e}", exc_info=True)
        bot.reply_to(message, "حدث خطأ. تأكد من إدخال أرقام صحيحة.")


def handle_move_by_id_input(message, bot):
    if check_cancel(message, bot):
        return
    try:
        ids_str = re.split(r'[,\s\n]+', message.text.strip())
        video_ids = [int(n) for n in ids_str if n.isdigit()]

        if not video_ids:
            msg = bot.reply_to(message, f"{Emoji.ERROR} لم يتم إدخال أرقام صحيحة. حاول مرة أخرى أو أرسل /cancel.")
            bot.register_next_step_handler(msg, handle_move_by_id_input, bot)
            return

        valid = [vid for vid in video_ids if VideoRepository.get_by_id(vid)]
        invalid = [vid for vid in video_ids if vid not in valid]

        if not valid:
            msg = bot.reply_to(message, f"{Emoji.ERROR} لا توجد فيديوهات بالأرقام المدخلة.")
            bot.register_next_step_handler(msg, handle_move_by_id_input, bot)
            return

        admin_steps[message.chat.id] = {"video_ids": valid}
        
        move_keyboard = Keyboards.categories_tree(f"admin{DELIMITER}move_confirm", add_back=False)
        move_keyboard.add(InlineKeyboardButton(f"{Emoji.BACK} إلغاء", callback_data="back_to_main"))

        if len(valid) == 1:
            text = f"{Emoji.SUCCESS} تم اختيار الفيديو رقم {valid[0]}\n\n🎯 اختر التصنيف الجديد:"
        else:
            text = f"{Emoji.SUCCESS} تم اختيار {len(valid)} فيديو للنقل\n\n📝 الأرقام: {', '.join(map(str, valid))}\n\n"
            if invalid:
                text += f"{Emoji.WARNING} أرقام غير موجودة: {', '.join(map(str, invalid))}\n\n"
            text += "🎯 اختر التصنيف الجديد:"

        bot.reply_to(message, text, reply_markup=move_keyboard)
    except Exception as e:
        logger.error(f"Move by id error: {e}", exc_info=True)
        bot.reply_to(message, f"{Emoji.ERROR} حدث خطأ غير متوقع.")


# --- Registration ---

def register(bot, admin_ids):

    def check_admin(func):
        def wrapper(message):
            if message.from_user.id in admin_ids:
                return func(message)
            else:
                bot.reply_to(message, Messages.unauthorized())
        return wrapper

    @bot.message_handler(commands=["admin"])
    @check_admin
    def admin_panel(message):
        bot.send_message(
            message.chat.id,
            Messages.admin_panel(),
            parse_mode="Markdown",
            reply_markup=Keyboards.admin_panel()
        )

    @bot.message_handler(commands=["cancel"])
    @check_admin
    def cancel_step(message):
        if message.chat.id in admin_steps:
            del admin_steps[message.chat.id]
            bot.send_message(message.chat.id, Messages.operation_cancelled())
        else:
            bot.send_message(message.chat.id, Messages.no_operation())
