# handlers/admin_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import re
import time
from telebot.apihelper import ApiTelegramException

from db_manager import (
    add_category, get_all_user_ids, add_required_channel, remove_required_channel,
    get_required_channels, get_subscriber_count, get_bot_stats, get_popular_videos,
    delete_videos_by_ids, get_video_by_id, delete_bot_user,
    delete_category_and_contents, move_videos_from_category, delete_category_by_id,
    get_categories_tree, set_active_category_id, get_child_categories,
    move_videos_bulk, get_category_by_id, set_default_thumbnail, update_video_thumbnail  # إضافة الدوال الجديدة
)

from state_manager import state_handler, clear_user_waiting_state, set_user_waiting_for_input, States

from .helpers import admin_steps, create_categories_keyboard, CALLBACK_DELIMITER, create_hierarchical_category_keyboard

logger = logging.getLogger(__name__)

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
            if 'bot was blocked by the user' in e.description:
                delete_bot_user(user_id)
                removed_count += 1
                logger.warning(f"Failed to send broadcast to {user_id}: Bot was blocked. User deleted.")
            else:
                failed_count += 1
                logger.warning(f"Failed to send broadcast to {user_id}: {e}")
        except Exception as e:
            failed_count += 1
            logger.error(f"Unexpected error broadcasting to {user_id}: {e}")
        time.sleep(0.1)
    bot.send_message(message.chat.id,
                     f"✅ اكتمل البث!\n\n- رسائل ناجحة: {sent_count}\n- رسائل فاشلة (لم يتم إرسالها): {failed_count}\n- مشتركين محذوفين (لأنهم حظروا البوت): {removed_count}")

def handle_add_new_category(message, bot):
    if check_cancel(message, bot): return
    category_name = message.text.strip()
    step_data = admin_steps.pop(message.chat.id, {})
    parent_id = step_data.get("parent_id")
    success, result = add_category(category_name, parent_id=parent_id)
    if success:
        parent_info = ""
        if parent_id:
            parent_cat = get_category_by_id(parent_id)
            if parent_cat:
                parent_info = f" تحت التصنيف 📂 \"{parent_cat['name']}\""
        bot.reply_to(message, f"✅ تم إنشاء التصنيف الجديد بنجاح: \"{category_name}\"{parent_info}.")
    else:
        bot.reply_to(message, f"❌ خطأ في إنشاء التصنيف: {result[1] if isinstance(result, tuple) else result}")

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
        bot.send_message(message.chat.id, f"✅ تم إضافة القناة \"{channel_name}\" (ID: `{channel_id}`).", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ حدث خطأ أثناء إضافة القناة. تأكد من أن المعرف صحيح.")

def handle_remove_channel_step(message, bot):
    if check_cancel(message, bot): return
    channel_id = message.text.strip()
    if remove_required_channel(channel_id):
        bot.send_message(message.chat.id, f"✅ تم إزالة القناة (ID: `{channel_id}`) من القنوات المطلوبة.", parse_mode="Markdown")
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
            msg = bot.reply_to(message, "لم يتم إدخال أرقام صحيحة. حاول مرة أخرى أو أرسل /cancel.")
            bot.register_next_step_handler(msg, handle_delete_by_ids_input, bot)
            return
        deleted_count = delete_videos_by_ids(video_ids)
        bot.reply_to(message, f"✅ تم حذف {deleted_count} فيديو بنجاح.")
    except Exception as e:
        logger.error(f"Error in handle_delete_by_ids_input: {e}", exc_info=True)
        bot.reply_to(message, "حدث خطأ. تأكد من إدخال أرقام فقط مفصولة بمسافات أو فواصل.")

def handle_move_by_id_input(message, bot):
    """معالج النقل الفردي والجماعي للفيديوهات - محدث بالشجرة الهرمية"""
    if check_cancel(message, bot): 
        return

    try:
        # قراءة الأرقام المدخلة (يدعم رقم واحد أو أرقام متعددة)
        video_ids_str = re.split(r'[,\s\n]+', message.text.strip())
        video_ids = [int(num) for num in video_ids_str if num.isdigit()]

        if not video_ids:
            msg = bot.reply_to(message, "❌ لم يتم إدخال أرقام صحيحة. حاول مرة أخرى أو أرسل /cancel.")
            bot.register_next_step_handler(msg, handle_move_by_id_input, bot)
            return

        # التحقق من وجود الفيديوهات
        valid_videos = []
        invalid_ids = []

        for vid_id in video_ids:
            video = get_video_by_id(vid_id)
            if video:
                valid_videos.append(vid_id)
            else:
                invalid_ids.append(vid_id)

        if not valid_videos:
            msg = bot.reply_to(message, "❌ لا توجد فيديوهات صحيحة بالأرقام المدخلة. حاول مرة أخرى أو أرسل /cancel.")
            bot.register_next_step_handler(msg, handle_move_by_id_input, bot)
            return

        # حفظ أرقام الفيديوهات في admin_steps
        admin_steps[message.chat.id] = {"video_ids": valid_videos}

        # 🌟 استخدام الكيبورد الهرمي الجديد بدلاً من البناء اليدوي
        move_keyboard = create_hierarchical_category_keyboard("admin::move_confirm", add_back_button=False)
        move_keyboard.add(InlineKeyboardButton("🔙 إلغاء", callback_data="back_to_main"))

        # رسالة مختلفة للنقل الفردي أو الجماعي
        if len(valid_videos) == 1:
            message_text = f"✅ تم اختيار الفيديو رقم {valid_videos[0]}\n\n🎯 اختر التصنيف الجديد:"
        else:
            message_text = (
                f"✅ تم اختيار {len(valid_videos)} فيديو للنقل\n\n"
                f"📝 الأرقام: {', '.join(map(str, valid_videos))}\n\n"
            )
            if invalid_ids:
                message_text += f"⚠️ أرقام غير موجودة (تم تجاهلها): {', '.join(map(str, invalid_ids))}\n\n"
            message_text += "🎯 اختر التصنيف الجديد:"

        bot.reply_to(message, message_text, reply_markup=move_keyboard)

    except ValueError:
        msg = bot.reply_to(message, "❌ الرجاء إدخال أرقام صحيحة. حاول مرة أخرى أو أرسل /cancel.")
        bot.register_next_step_handler(msg, handle_move_by_id_input, bot)
    except Exception as e:
        logger.error(f"Error in handle_move_by_id_input: {e}", exc_info=True)
        bot.reply_to(message, "❌ حدث خطأ غير متوقع.")

def check_cancel(message, bot):
    if message.text == "/cancel":
        if message.chat.id in admin_steps:
            del admin_steps[message.chat.id]
        clear_user_waiting_state(message.chat.id)
        bot.send_message(message.chat.id, "تم إلغاء العملية.")
        return True
    return False

# --- معالجات الحالات الجديدة (Thumbnail Management) ---

@state_handler(States.WAITING_DEFAULT_THUMB)
def handle_set_default_thumb(message, bot, context):
    if check_cancel(message, bot): return
    
    if message.content_type != 'photo':
        bot.reply_to(message, "❌ يرجى إرسال صورة فقط. أو أرسل /cancel لإلغاء العملية.")
        return
    
    # الحصول على رابط الصورة (أكبر حجم)
    file_id = message.photo[-1].file_id
    if set_default_thumbnail(file_id):
        clear_user_waiting_state(message.from_user.id)
        bot.reply_to(message, "✅ تم تعيين الصورة المصغرة الافتراضية بنجاح!")
    else:
        bot.reply_to(message, "❌ حدث خطأ أثناء حفظ الصورة.")

@state_handler(States.WAITING_VIDEO_ID_FOR_THUMB)
def handle_manual_thumb_id(message, bot, context):
    if check_cancel(message, bot): return
    
    if not message.text or not message.text.isdigit():
        bot.reply_to(message, "❌ يرجى إرسال رقم الفيديو (ID) فقط. مثال: 123")
        return
    
    video_id = int(message.text)
    video = get_video_by_id(video_id)
    
    if not video:
        bot.reply_to(message, f"❌ لم يتم العثور على فيديو بالرقم {video_id}. جرب رقماً آخر.")
        return
    
    # الانتقال للحالة التالية مع حفظ ID الفيديو في السياق
    set_user_waiting_for_input(message.from_user.id, States.WAITING_NEW_THUMB_FOR_VIDEO, {"video_id": video_id})
    bot.reply_to(message, f"🎯 تم اختيار الفيديو: {video['caption'][:50]}...\n\nالآن أرسل الصورة المصغرة الجديدة لهذا الفيديو:")

@state_handler(States.WAITING_NEW_THUMB_FOR_VIDEO)
def handle_manual_thumb_image(message, bot, context):
    if check_cancel(message, bot): return
    
    video_id = context.get("video_id")
    if not video_id:
        clear_user_waiting_state(message.from_user.id)
        bot.reply_to(message, "❌ حدث خطأ في النظام (ID مفقود).")
        return

    if message.content_type != 'photo':
        bot.reply_to(message, "❌ يرجى إرسال صورة فقط لهذا الفيديو.")
        return

    file_id = message.photo[-1].file_id
    if update_video_thumbnail(video_id, file_id):
        clear_user_waiting_state(message.from_user.id)
        bot.reply_to(message, f"✅ تم تحديث الصورة المصغرة للفيديو رقم {video_id} بنجاح!")
    else:
        bot.reply_to(message, "❌ حدث خطأ أثناء تحديث الصورة.")

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

        # ─── قسم: إدارة التصنيفات ───
        keyboard.add(InlineKeyboardButton("╭─ 🗂️ إدارة التصنيفات ─╮", callback_data="noop"))
        keyboard.add(InlineKeyboardButton("➕ إضافة تصنيف 🟢", callback_data="admin::add_new_cat"),
                     InlineKeyboardButton("🗑️ حذف تصنيف 🔴", callback_data="admin::delete_category_select"))
        keyboard.add(InlineKeyboardButton("🔘 تعيين التصنيف النشط 🟡", callback_data="admin::set_active"))

        # ─── قسم: إدارة الفيديوهات ───
        keyboard.add(InlineKeyboardButton("╭─ 🎬 إدارة الفيديوهات ─╮", callback_data="noop"))
        keyboard.add(InlineKeyboardButton("➡️ نقل فيديو بالرقم 🔵", callback_data="admin::move_video_by_id"),
                     InlineKeyboardButton("❌ حذف فيديوهات 🔴", callback_data="admin::delete_videos_by_ids"))
        keyboard.add(InlineKeyboardButton("🔄 تحديث البيانات القديمة 🟣", callback_data="admin::update_metadata"))

        # ─── قسم: إدارة التعليقات ───
        keyboard.add(InlineKeyboardButton("╭─ 💬 إدارة التعليقات ─╮", callback_data="noop"))
        keyboard.add(InlineKeyboardButton("💬 عرض التعليقات 🔵", callback_data="admin::view_comments"),
                     InlineKeyboardButton("📊 الإحصائيات 🟣", callback_data="admin::comments_stats"))
        keyboard.add(InlineKeyboardButton("🧹 حذف التعليقات القديمة 🟡", callback_data="admin::delete_old_comments"),
                     InlineKeyboardButton("🗑️ حذف الكل 🔴", callback_data="admin::delete_all_comments"))

        # ─── قسم: قنوات الاشتراك ───
        keyboard.add(InlineKeyboardButton("╭─ 📢 قنوات الاشتراك ─╮", callback_data="noop"))
        keyboard.add(InlineKeyboardButton("➕ إضافة قناة 🟢", callback_data="admin::add_channel"),
                     InlineKeyboardButton("➖ إزالة قناة 🔴", callback_data="admin::remove_channel"))
        keyboard.add(InlineKeyboardButton("📋 عرض القنوات 🔵", callback_data="admin::list_channels"))

        # ─── قسم: الصور المصغرة ───
        keyboard.add(InlineKeyboardButton("╭─ 🖼️ الصور المصغرة ─╮", callback_data="noop"))
        keyboard.add(InlineKeyboardButton("🖼️ صورة افتراضية 🟡", callback_data="admin::set_default_thumb"),
                     InlineKeyboardButton("📸 تحديث يدوي 🔵", callback_data="admin::manual_thumb"))

        # ─── قسم: أدوات الصيانة ───
        keyboard.add(InlineKeyboardButton("╭─ 🛠️ أدوات الصيانة ─╮", callback_data="noop"))
        keyboard.add(InlineKeyboardButton("✨ إصلاح شامل للأرشيف 🟣", callback_data="admin::heal_archive"))
        keyboard.add(InlineKeyboardButton("📄 عرض المستندات 🔵", callback_data="admin::list_documents"),
                     InlineKeyboardButton("🔄 تحويل → فيديو 🟢", callback_data="admin::convert_all_docs"))

        # ─── قسم: البث والإحصائيات ───
        keyboard.add(InlineKeyboardButton("╭─ 📊 البث والإحصائيات ─╮", callback_data="noop"))
        keyboard.add(InlineKeyboardButton("📢 بث رسالة 🟠", callback_data="admin::broadcast"))
        keyboard.add(InlineKeyboardButton("📊 إحصائيات البوت 🟣", callback_data="admin::stats"),
                     InlineKeyboardButton("👤 عدد المشتركين 🔵", callback_data="admin::sub_count"))
        return keyboard

    @bot.message_handler(commands=["admin"])
    @check_admin
    def admin_panel(message):
        bot.send_message(message.chat.id, "🎛️ أهلاً بك في لوحة تحكم الآدمن. اختر أحد الخيارات:", reply_markup=generate_admin_panel())

    @bot.message_handler(commands=["cancel"])
    @check_admin
    def cancel_step(message):
        if message.chat.id in admin_steps:
            del admin_steps[message.chat.id]
            bot.send_message(message.chat.id, "✅ تم إلغاء العملية الحالية بنجاح.")
        else:
            bot.send_message(message.chat.id, "لا توجد عملية لإلغائها.")
