# handlers/admin_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import re
import time

from db_manager import (
    add_category, get_all_user_ids, add_required_channel, remove_required_channel,
    get_required_channels, get_subscriber_count, get_bot_stats, get_popular_videos,
    delete_videos_by_ids, get_video_by_id
)
from .helpers import admin_steps, create_categories_keyboard, CALLBACK_DELIMITER
from state_manager import state_manager

logger = logging.getLogger(__name__)

# --- Top-level functions for callbacks and next_step_handlers ---

def handle_rich_broadcast(message, bot):
    if check_cancel(message, bot): return
    user_ids = get_all_user_ids()
    sent_count, failed_count = 0, 0
    bot.send_message(message.chat.id, f"بدء إرسال الرسالة إلى {len(user_ids)} مشترك...")
    for user_id in user_ids:
        try:
            bot.copy_message(user_id, message.chat.id, message.message_id)
            sent_count += 1
        except Exception as e:
            failed_count += 1
            logger.warning(f"Failed to send broadcast to {user_id}: {e}")
        time.sleep(0.1)
    bot.send_message(message.chat.id, f"✅ اكتمل البث!\n\n- رسائل ناجحة: {sent_count}\n- رسائل فاشلة: {failed_count}")

def handle_add_new_category(message, bot):
    if check_cancel(message, bot): return
    category_name = message.text.strip()
    step_data = admin_steps.pop(message.chat.id, {})
    parent_id = step_data.get("parent_id")
    success, result = add_category(category_name, parent_id=parent_id)
    if success:
        bot.reply_to(message, f"✅ تم إنشاء التصنيف الجديد بنجاح: \"{category_name}\".")
    else:
        bot.reply_to(message, f"❌ خطأ في إنشاء التصنيف: {result}")

def handle_add_channel_step1(message, bot):
    if check_cancel(message, bot): return
    channel_id = message.text.strip()
    state_manager.set_user_state(message.from_user.id, 'waiting_channel_name', {'channel_id': channel_id})
    bot.send_message(message.chat.id, "الآن أرسل اسم القناة (مثال: قناة الأفلام). (أو /cancel)")

def handle_add_channel_step2(message, bot):
    if check_cancel(message, bot): return
    channel_name = message.text.strip()
    user_state = state_manager.get_user_state(message.from_user.id)
    if not user_state or not user_state.get('data', {}).get('channel_id'):
        bot.send_message(message.chat.id, "❌ حدث خطأ في استرجاع معرف القناة.")
        return
    channel_id = user_state['data']['channel_id']
    state_manager.clear_user_state(message.from_user.id)
    if add_required_channel(channel_id, channel_name):
        bot.send_message(message.chat.id, f"✅ تم إضافة القناة \"{channel_name}\" (ID: {channel_id}) كقناة مطلوبة.")
    else:
        bot.send_message(message.chat.id, "❌ حدث خطأ أثناء إضافة القناة.")

def handle_remove_channel_step(message, bot):
    if check_cancel(message, bot): return
    channel_id = message.text.strip()
    if remove_required_channel(channel_id):
        bot.send_message(message.chat.id, f"✅ تم إزالة القناة (ID: {channel_id}) من القنوات المطلوبة.")
    else:
        bot.send_message(message.chat.id, "❌ حدث خطأ أو القناة غير موجودة.")

def handle_list_channels(message, bot):
    channels = get_required_channels()
    if channels:
        response = "📋 *القنوات المطلوبة:*\n" + "\n".join([f"- {ch['channel_name']} (ID: `{ch['channel_id']}`)" for ch in channels])
        bot.send_message(message.chat.id, response, parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "لا توجد قنوات مطلوبة حالياً.")

def handle_delete_by_ids_input(message, bot):
    if check_cancel(message, bot): return
    try:
        video_ids_str = re.split(r'[,\s\n]+', message.text.strip())
        video_ids = [int(num) for num in video_ids_str if num.isdigit()]
        if not video_ids:
            bot.reply_to(message, "لم يتم إدخال أرقام صحيحة. حاول مرة أخرى أو أرسل /cancel.")
            return
        deleted_count = delete_videos_by_ids(video_ids)
        bot.reply_to(message, f"✅ تم حذف {deleted_count} فيديو بنجاح.")
    except Exception as e:
        logger.error(f"Error in handle_delete_by_ids_input: {e}", exc_info=True)
        bot.reply_to(message, "حدث خطأ. تأكد من إدخال أرقام فقط مفصولة بمسافات أو فواصل.")

def handle_move_by_id_input(message, bot):
    if check_cancel(message, bot): return
    try:
        video_id = int(message.text.strip())
        video = get_video_by_id(video_id)
        if not video:
            state_manager.set_user_state(message.from_user.id, 'waiting_video_id_move')
            bot.reply_to(message, "عذراً، لا يوجد فيديو بهذا الرقم. حاول مرة أخرى أو أرسل /cancel.")
            return
        keyboard = create_categories_keyboard()
        if not keyboard.keyboard:
            bot.reply_to(message, "لا توجد تصنيفات لنقل الفيديو إليها.")
            return
        for row in keyboard.keyboard:
            for button in row:
                parts = button.callback_data.split(CALLBACK_DELIMITER)
                button.callback_data = f"admin::move_confirm::{video['id']}::{parts[1]}"
        bot.reply_to(message, f"اختر التصنيف الجديد لنقل الفيديو رقم {video_id}:", reply_markup=keyboard)
    except ValueError:
        state_manager.set_user_state(message.from_user.id, 'waiting_video_id_move')
        bot.reply_to(message, "الرجاء إدخال رقم صحيح. حاول مرة أخرى أو أرسل /cancel.")
    except Exception as e:
        logger.error(f"Error in handle_move_by_id_input: {e}", exc_info=True)
        bot.reply_to(message, "حدث خطأ غير متوقع.")

def check_cancel(message, bot):
    if message.text == "/cancel":
        if message.chat.id in admin_steps:
            del admin_steps[message.chat.id]
        state_manager.clear_user_state(message.from_user.id)
        bot.send_message(message.chat.id, "تم إلغاء العملية.")
        return True
    return False

# --- Handler Registration ---
def register(bot, admin_ids):

    def check_admin(func):
        def wrapper(message):
            if message.from_user.id in admin_ids:
                return func(message)
            else:
                bot.reply_to(message, "ليس لديك صلاحية الوصول إلى هذا الأمر.")
        return wrapper

    def generate_admin_panel():
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(InlineKeyboardButton("➕ إضافة تصنيف", callback_data="admin::add_new_cat"),
                     InlineKeyboardButton("🗑️ حذف تصنيف", callback_data="admin::delete_category_select"))
        keyboard.add(InlineKeyboardButton("➡️ نقل فيديو بالرقم", callback_data="admin::move_video_by_id"),
                     InlineKeyboardButton("❌ حذف فيديوهات بالأرقام", callback_data="admin::delete_videos_by_ids"))
        keyboard.add(InlineKeyboardButton("🔘 تعيين التصنيف النشط", callback_data="admin::set_active"),
                     InlineKeyboardButton("🔄 تحديث بيانات الفيديوهات القديمة", callback_data="admin::update_metadata"))
        keyboard.add(InlineKeyboardButton("➕ إضافة قناة اشتراك", callback_data="admin::add_channel"),
                     InlineKeyboardButton("➖ إزالة قناة اشتراك", callback_data="admin::remove_channel"))
        keyboard.add(InlineKeyboardButton("📋 عرض القنوات", callback_data="admin::list_channels"))
        keyboard.add(InlineKeyboardButton("📢 بث رسالة", callback_data="admin::broadcast"),
                     InlineKeyboardButton("📊 الإحصائيات", callback_data="admin::stats"),
                     InlineKeyboardButton("👤 عدد المشتركين", callback_data="admin::sub_count"))
        return keyboard

    @bot.message_handler(commands=["admin"])
    @check_admin
    def admin_panel(message):
        bot.send_message(message.chat.id, "أهلاً بك في لوحة تحكم الآدمن. اختر أحد الخيارات:", reply_markup=generate_admin_panel())

    @bot.message_handler(commands=["cancel"])
    @check_admin
    def cancel_step(message):
        if message.chat.id in admin_steps:
            del admin_steps[message.chat.id]
            bot.send_message(message.chat.id, "✅ تم إلغاء العملية الحالية بنجاح.")
        else:
            bot.send_message(message.chat.id, "لا توجد عملية لإلغائها.")
