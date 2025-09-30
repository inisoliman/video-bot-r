# handlers/admin_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import re
import time
from telebot.apihelper import ApiTelegramException # استيراد خطأ API للتعامل مع حظر المستخدم

from db_manager import (
    add_category, get_all_user_ids, add_required_channel, remove_required_channel,
    get_required_channels, get_subscriber_count, get_bot_stats, get_popular_videos,
    delete_videos_by_ids, get_video_by_id, execute_query, delete_category_by_id,
    delete_category_and_contents, move_videos_from_category, get_categories_tree, get_category_by_id
)
from .helpers import admin_steps, create_categories_keyboard, CALLBACK_DELIMITER, get_child_categories

logger = logging.getLogger(__name__)

# --- دالة مساعدة لحذف المستخدم من قاعدة البيانات ---
def delete_bot_user(user_id):
    """حذف المستخدم من جدول المشتركين (bot_users) بعد حظره للبوت."""
    query = "DELETE FROM bot_users WHERE user_id = %s"
    # نستخدم execute_query مباشرة هنا
    return execute_query(query, (user_id,), commit=True)


# --- Top-level functions for callbacks and next_step_handlers ---

def handle_rich_broadcast(message, bot):
    if check_cancel(message, bot): return
    user_ids = get_all_user_ids()
    sent_count, failed_count, removed_count = 0, 0, 0
    bot.send_message(message.chat.id, f"بدء إرسال الرسالة إلى {len(user_ids)} مشترك...")
    
    for user_id in user_ids:
        try:
            bot.copy_message(user_id, message.chat.id, message.message_id)
            sent_count += 1
        except ApiTelegramException as e: 
            if e.error_code == 403 and 'bot was blocked by the user' in e.description:
                # المستخدم قام بحظر البوت - يجب حذفه
                delete_bot_user(user_id)
                removed_count += 1
                logger.warning(f"User {user_id} blocked the bot and was removed from the database.")
            else:
                failed_count += 1
                logger.warning(f"Failed to send broadcast to {user_id}: {e}")
            
        except Exception as e:
            failed_count += 1
            logger.warning(f"Failed to send broadcast to {user_id}: {e}")
            
        time.sleep(0.1) # الانتظار لمنع تجاوز حد تليجرام

    bot.send_message(message.chat.id, 
                     f"✅ اكتمل البث!\n\n"
                     f"- رسائل ناجحة: {sent_count}\n"
                     f"- رسائل فاشلة: {failed_count}\n"
                     f"- مشتركين تم حذفهم (حظروا البوت): {removed_count}")

def handle_add_new_category(message, bot):
    if check_cancel(message, bot): return
    category_name = message.text.strip()
    step_data = admin_steps.pop(message.chat.id, {})
    parent_id = step_data.get("parent_id")
    success, result = add_category(category_name, parent_id=parent_id)
    if success:
        bot.reply_to(message, f"✅ تم إنشاء التصنيف الجديد بنجاح: \"{category_name}\".")
    else:
        # result في هذه الحالة هو النص "Failed to add category"
        bot.reply_to(message, f"❌ خطأ في إنشاء التصنيف: {result}")

def handle_add_channel_step1(message, bot):
    if check_cancel(message, bot): return
    channel_id = message.text.strip()
    admin_steps[message.chat.id] = {"channel_id": channel_id}
    msg = bot.send_message(message.chat.id, "الآن أرسل اسم القناة (مثال: قناة الأفلام). (أو /cancel)")
    bot.register_next_step_handler(msg, handle_add_channel_step2, bot)

def handle_add_channel_step2(message, bot):
    if check_cancel(message, bot): return
    channel_name = message.text.strip()
    channel_id = admin_steps.pop(message.chat.id, {}).get("channel_id")
    if not channel_id: return
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
            msg = bot.reply_to(message, "عذراً، لا يوجد فيديو بهذا الرقم. حاول مرة أخرى أو أرسل /cancel.")
            bot.register_next_step_handler(msg, handle_move_by_id_input, bot)
            return
        
        # إنشاء لوحة مفاتيح بجميع التصنيفات
        categories_tree = get_categories_tree()
        keyboard = InlineKeyboardMarkup(row_width=2)
        
        buttons = []
        for cat in categories_tree:
            buttons.append(InlineKeyboardButton(text=cat['name'], callback_data=f"admin::move_confirm::{video['id']}::{cat['id']}"))
            
            # إضافة التصنيفات الفرعية
            child_cats = get_child_categories(cat['id'])
            for child in child_cats:
                 buttons.append(InlineKeyboardButton(text=f"- {child['name']}", callback_data=f"admin::move_confirm::{video['id']}::{child['id']}"))
                 
        keyboard.add(*buttons)
        
        if not keyboard.keyboard:
            bot.reply_to(message, "لا توجد تصنيفات لنقل الفيديو إليها.")
            return
        
        bot.reply_to(message, f"اختر التصنيف الجديد لنقل الفيديو رقم {video_id}:", reply_markup=keyboard)
        
    except ValueError:
        msg = bot.reply_to(message, "الرجاء إدخال رقم صحيح. حاول مرة أخرى أو أرسل /cancel.")
        bot.register_next_step_handler(msg, handle_move_by_id_input, bot)
    except Exception as e:
        logger.error(f"Error in handle_move_by_id_input: {e}", exc_info=True)
        bot.reply_to(message, "حدث خطأ غير متوقع.")

def check_cancel(message, bot):
    if message.text == "/cancel":
        if message.chat.id in admin_steps:
            del admin_steps[message.chat.id]
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
