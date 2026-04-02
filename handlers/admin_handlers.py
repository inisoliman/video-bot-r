
# handlers/admin_handlers.py

import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from config.config import Config
from config.constants import (
    EMOJI_ADMIN, EMOJI_CHECK, EMOJI_ERROR, EMOJI_BACK, EMOJI_BROADCAST,
    EMOJI_DELETE, EMOJI_MOVE, EMOJI_CLOCK, EMOJI_WARNING, EMOJI_FOLDER,
    MSG_ADMIN_ONLY, MSG_BROADCAST_PROMPT, MSG_BROADCAST_CONFIRM, MSG_BROADCAST_SENT,
    MSG_BROADCAST_CANCELLED, MSG_BROADCAST_NO_USERS, MSG_VIDEO_MOVE_PROMPT,
    MSG_VIDEO_MOVE_CATEGORY_PROMPT, MSG_VIDEO_MOVE_CANCELLED, MSG_VIDEO_MOVE_INVALID_ID,
    MSG_VIDEO_MOVE_NO_CATEGORY, MSG_VIDEO_MOVE_SAME_CATEGORY, MSG_UPDATE_METADATA_STARTED,
    MSG_THUMBNAIL_UPDATE_STARTED, MSG_DB_OPTIMIZER_STARTED, MSG_HISTORY_CLEANER_STARTED,
    MSG_RESET_FILE_IDS_STARTED, MSG_FIX_DATABASE_FILE_IDS_STARTED, MSG_CHECK_FILE_IDS_STARTED,
    MSG_EXTRACT_CHANNEL_THUMBNAILS_STARTED, MSG_FIX_VIDEOS_PROFESSIONAL_STARTED,
    MSG_MIGRATE_DATABASE_STARTED, CALLBACK_DELIMITER, PARSE_MODE_HTML
)
from services import (
    user_service, video_service, category_service, required_channels_repository,
    bot_settings_repository
)
from core.state_manager import States, state_manager, set_user_waiting_for_input, clear_user_waiting_state
from utils.telegram_utils import create_hierarchical_category_keyboard

logger = logging.getLogger(__name__)

# Admin steps dictionary (can be replaced by state_manager context)
admin_steps = {}

def register_admin_handlers(bot, admin_ids):

    @bot.message_handler(commands=["admin"])
    def admin_panel(message):
        if message.from_user.id not in admin_ids:
            bot.reply_to(message, MSG_ADMIN_ONLY)
            return
        
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton(f"{EMOJI_FOLDER} إدارة التصنيفات", callback_data="admin::manage_categories"),
            InlineKeyboardButton(f"{EMOJI_MOVE} نقل فيديو", callback_data="admin::move_video"),
            InlineKeyboardButton(f"{EMOJI_BROADCAST} إرسال بث", callback_data="admin::broadcast"),
            InlineKeyboardButton(f"{EMOJI_ADMIN} إدارة القنوات", callback_data="admin::manage_channels"),
            InlineKeyboardButton(f"{EMOJI_CLOCK} مهام الصيانة", callback_data="admin::maintenance"),
            InlineKeyboardButton(f"{EMOJI_ADMIN} الإعدادات", callback_data="admin::settings")
        )
        bot.reply_to(message, f"{EMOJI_ADMIN} لوحة تحكم الأدمن:", reply_markup=markup, parse_mode=PARSE_MODE_HTML)

    # --- Admin Callbacks ---
    @bot.callback_query_handler(func=lambda call: call.data.startswith("admin::"))
    def admin_callbacks(call):
        user_id = call.from_user.id
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, MSG_ADMIN_ONLY, show_alert=True)
            return

        data = call.data.split(CALLBACK_DELIMITER)
        action = data[1]

        if action == "manage_categories":
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(
                InlineKeyboardButton(f"{EMOJI_FOLDER} إضافة تصنيف", callback_data="admin::add_category"),
                InlineKeyboardButton(f"{EMOJI_FOLDER} إعادة تسمية تصنيف", callback_data="admin::rename_category"),
                InlineKeyboardButton(f"{EMOJI_DELETE} حذف تصنيف", callback_data="admin::delete_category"),
                InlineKeyboardButton(f"{EMOJI_MOVE} نقل فيديوهات لتصنيف", callback_data="admin::move_videos_to_category"),
                InlineKeyboardButton(f"{EMOJI_BACK} رجوع", callback_data="admin::back_to_admin_panel")
            )
            bot.edit_message_text(f"{EMOJI_FOLDER} إدارة التصنيفات:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode=PARSE_MODE_HTML)

        elif action == "add_category":
            set_user_waiting_for_input(user_id, States.ADMIN_ADD_CATEGORY)
            bot.edit_message_text(f"{EMOJI_FOLDER} أرسل اسم التصنيف الجديد (يمكنك إضافة تصنيف فرعي بكتابة: `اسم التصنيف الرئيسي/اسم التصنيف الفرعي`):", call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)

        elif action == "rename_category":
            set_user_waiting_for_input(user_id, States.ADMIN_RENAME_CATEGORY)
            bot.edit_message_text(f"{EMOJI_FOLDER} أرسل معرف التصنيف الذي تريد إعادة تسميته واسمه الجديد (مثال: `123/اسم جديد`):", call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)

        elif action == "delete_category":
            set_user_waiting_for_input(user_id, States.ADMIN_DELETE_CATEGORY)
            bot.edit_message_text(f"{EMOJI_DELETE} أرسل معرف التصنيف الذي تريد حذفه (سيتم حذف جميع الفيديوهات والتصنيفات الفرعية المرتبطة به):", call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)

        elif action == "move_video":
            set_user_waiting_for_input(user_id, States.WAITING_VIDEO_ID_FOR_MOVE)
            bot.edit_message_text(MSG_VIDEO_MOVE_PROMPT, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)

        elif action == "broadcast":
            set_user_waiting_for_input(user_id, States.WAITING_BROADCAST_MESSAGE)
            bot.edit_message_text(MSG_BROADCAST_PROMPT, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)

        elif action == "manage_channels":
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(
                InlineKeyboardButton(f"{EMOJI_CHECK} إضافة قناة", callback_data="admin::add_channel"),
                InlineKeyboardButton(f"{EMOJI_DELETE} حذف قناة", callback_data="admin::remove_channel"),
                InlineKeyboardButton(f"{EMOJI_BACK} رجوع", callback_data="admin::back_to_admin_panel")
            )
            bot.edit_message_text(f"{EMOJI_ADMIN} إدارة القنوات:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode=PARSE_MODE_HTML)

        elif action == "add_channel":
            set_user_waiting_for_input(user_id, States.ADMIN_ADD_CHANNEL)
            bot.edit_message_text(f"{EMOJI_CHECK} أرسل معرف القناة واسمها (مثال: `-1001234567890/اسم القناة` أو `@username/اسم القناة`):", call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)

        elif action == "remove_channel":
            set_user_waiting_for_input(user_id, States.ADMIN_REMOVE_CHANNEL)
            bot.edit_message_text(f"{EMOJI_DELETE} أرسل معرف القناة التي تريد حذفها (مثال: `-1001234567890` أو `@username`):", call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)

        elif action == "maintenance":
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton(f"{EMOJI_CLOCK} تحديث البيانات الوصفية للفيديوهات", callback_data="admin::update_metadata"),
                InlineKeyboardButton(f"{EMOJI_CLOCK} تحديث الصور المصغرة للفيديوهات", callback_data="admin::update_thumbnails"),
                InlineKeyboardButton(f"{EMOJI_CLOCK} تحسين قاعدة البيانات", callback_data="admin::optimize_db"),
                InlineKeyboardButton(f"{EMOJI_CLOCK} تنظيف سجل المشاهدة القديم", callback_data="admin::clean_history"),
                InlineKeyboardButton(f"{EMOJI_CLOCK} إعادة تعيين معرفات الملفات", callback_data="admin::reset_file_ids"),
                InlineKeyboardButton(f"{EMOJI_CLOCK} إصلاح معرفات الملفات في قاعدة البيانات", callback_data="admin::fix_database_file_ids"),
                InlineKeyboardButton(f"{EMOJI_CLOCK} التحقق من معرفات الملفات", callback_data="admin::check_file_ids"),
                InlineKeyboardButton(f"{EMOJI_CLOCK} استخراج الصور المصغرة للقنوات", callback_data="admin::extract_channel_thumbnails"),
                InlineKeyboardButton(f"{EMOJI_CLOCK} إصلاح الفيديوهات الاحترافية", callback_data="admin::fix_videos_professional"),
                InlineKeyboardButton(f"{EMOJI_CLOCK} ترحيل قاعدة البيانات", callback_data="admin::migrate_database"),
                InlineKeyboardButton(f"{EMOJI_BACK} رجوع", callback_data="admin::back_to_admin_panel")
            )
            bot.edit_message_text(f"{EMOJI_CLOCK} مهام الصيانة:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode=PARSE_MODE_HTML)

        elif action == "update_metadata":
            bot.edit_message_text(MSG_UPDATE_METADATA_STARTED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
            # TODO: Implement actual background task for update_metadata
            bot.answer_callback_query(call.id, "بدأت عملية تحديث البيانات الوصفية في الخلفية.")

        elif action == "update_thumbnails":
            bot.edit_message_text(MSG_THUMBNAIL_UPDATE_STARTED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
            # TODO: Implement actual background task for update_thumbnails
            bot.answer_callback_query(call.id, "بدأت عملية تحديث الصور المصغرة في الخلفية.")

        elif action == "optimize_db":
            bot.edit_message_text(MSG_DB_OPTIMIZER_STARTED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
            # TODO: Implement actual background task for optimize_db
            bot.answer_callback_query(call.id, "بدأت عملية تحسين قاعدة البيانات في الخلفية.")

        elif action == "clean_history":
            bot.edit_message_text(MSG_HISTORY_CLEANER_STARTED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
            # TODO: Implement actual background task for clean_history
            bot.answer_callback_query(call.id, "بدأت عملية تنظيف سجل المشاهدة في الخلفية.")

        elif action == "reset_file_ids":
            bot.edit_message_text(MSG_RESET_FILE_IDS_STARTED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
            # TODO: Implement actual background task for reset_file_ids
            bot.answer_callback_query(call.id, "بدأت عملية إعادة تعيين معرفات الملفات في الخلفية.")

        elif action == "fix_database_file_ids":
            bot.edit_message_text(MSG_FIX_DATABASE_FILE_IDS_STARTED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
            # TODO: Implement actual background task for fix_database_file_ids
            bot.answer_callback_query(call.id, "بدأت عملية إصلاح معرفات الملفات في قاعدة البيانات في الخلفية.")

        elif action == "check_file_ids":
            bot.edit_message_text(MSG_CHECK_FILE_IDS_STARTED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
            # TODO: Implement actual background task for check_file_ids
            bot.answer_callback_query(call.id, "بدأت عملية التحقق من معرفات الملفات في الخلفية.")

        elif action == "extract_channel_thumbnails":
            bot.edit_message_text(MSG_EXTRACT_CHANNEL_THUMBNAILS_STARTED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
            # TODO: Implement actual background task for extract_channel_thumbnails
            bot.answer_callback_query(call.id, "بدأت عملية استخراج الصور المصغرة للقنوات في الخلفية.")

        elif action == "fix_videos_professional":
            bot.edit_message_text(MSG_FIX_VIDEOS_PROFESSIONAL_STARTED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
            # TODO: Implement actual background task for fix_videos_professional
            bot.answer_callback_query(call.id, "بدأت عملية إصلاح الفيديوهات الاحترافية في الخلفية.")

        elif action == "migrate_database":
            bot.edit_message_text(MSG_MIGRATE_DATABASE_STARTED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
            # TODO: Implement actual background task for migrate_database
            bot.answer_callback_query(call.id, "بدأت عملية ترحيل قاعدة البيانات في الخلفية.")

        elif action == "settings":
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton(f"{EMOJI_BACK} رجوع", callback_data="admin::back_to_admin_panel")
            )
            bot.edit_message_text(f"{EMOJI_ADMIN} الإعدادات (قريباً):", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode=PARSE_MODE_HTML)

        elif action == "back_to_admin_panel":
            admin_panel(call.message)

        bot.answer_callback_query(call.id)

    # --- Admin State Handlers ---
    @state_manager.state_handler(States.ADMIN_ADD_CATEGORY)
    def handle_admin_add_category_state(message, bot, context):
        user_id = message.from_user.id
        clear_user_waiting_state(user_id)
        
        parts = message.text.split("/")
        category_name = parts[-1].strip()
        parent_category_name = parts[0].strip() if len(parts) > 1 else None

        parent_id = None
        if parent_category_name:
            parent_category = category_service.get_category_by_name_and_parent(parent_category_name)
            if not parent_category:
                bot.reply_to(message, f"{EMOJI_ERROR} التصنيف الرئيسي ‘{parent_category_name}’ غير موجود.", parse_mode=PARSE_MODE_HTML)
                return
            parent_id = parent_category["id"]

        if category_service.get_category_by_name_and_parent(category_name, parent_id):
            bot.reply_to(message, f"{EMOJI_WARNING} التصنيف ‘{category_name}’ موجود بالفعل ضمن هذا التصنيف الرئيسي.", parse_mode=PARSE_MODE_HTML)
            return

        new_category_id = category_service.add_new_category(category_name, parent_id)
        if new_category_id:
            bot.reply_to(message, f"{EMOJI_CHECK} تم إضافة التصنيف ‘{category_name}’ بنجاح بمعرف: `{new_category_id}`", parse_mode=PARSE_MODE_HTML)
        else:
            bot.reply_to(message, f"{EMOJI_ERROR} حدث خطأ أثناء إضافة التصنيف.", parse_mode=PARSE_MODE_HTML)

    @state_manager.state_handler(States.ADMIN_RENAME_CATEGORY)
    def handle_admin_rename_category_state(message, bot, context):
        user_id = message.from_user.id
        clear_user_waiting_state(user_id)

        parts = message.text.split("/")
        if len(parts) != 2 or not parts[0].strip().isdigit():
            bot.reply_to(message, f"{EMOJI_ERROR} صيغة غير صحيحة. يرجى استخدام `معرف_التصنيف/الاسم_الجديد`.", parse_mode=PARSE_MODE_HTML)
            return
        
        category_id = int(parts[0].strip())
        new_name = parts[1].strip()

        if not category_service.get_category_details(category_id):
            bot.reply_to(message, f"{EMOJI_ERROR} التصنيف بمعرف `{category_id}` غير موجود.", parse_mode=PARSE_MODE_HTML)
            return

        if category_service.update_category_name(category_id, new_name):
            bot.reply_to(message, f"{EMOJI_CHECK} تم تحديث اسم التصنيف `{category_id}` إلى ‘{new_name}’ بنجاح.", parse_mode=PARSE_MODE_HTML)
        else:
            bot.reply_to(message, f"{EMOJI_ERROR} حدث خطأ أثناء إعادة تسمية التصنيف.", parse_mode=PARSE_MODE_HTML)

    @state_manager.state_handler(States.ADMIN_DELETE_CATEGORY)
    def handle_admin_delete_category_state(message, bot, context):
        user_id = message.from_user.id
        clear_user_waiting_state(user_id)

        if not message.text.strip().isdigit():
            bot.reply_to(message, f"{EMOJI_ERROR} معرف التصنيف يجب أن يكون رقماً.", parse_mode=PARSE_MODE_HTML)
            return
        
        category_id = int(message.text.strip())

        if not category_service.get_category_details(category_id):
            bot.reply_to(message, f"{EMOJI_ERROR} التصنيف بمعرف `{category_id}` غير موجود.", parse_mode=PARSE_MODE_HTML)
            return

        if category_service.delete_existing_category(category_id):
            bot.reply_to(message, f"{EMOJI_CHECK} تم حذف التصنيف `{category_id}` وجميع الفيديوهات والتصنيفات الفرعية المرتبطة به بنجاح.", parse_mode=PARSE_MODE_HTML)
        else:
            bot.reply_to(message, f"{EMOJI_ERROR} حدث خطأ أثناء حذف التصنيف.", parse_mode=PARSE_MODE_HTML)

    @state_manager.state_handler(States.WAITING_VIDEO_ID_FOR_MOVE)
    def handle_waiting_video_id_for_move_state(message, bot, context):
        user_id = message.from_user.id
        video_id_str = message.text.strip()
        if not video_id_str.isdigit():
            bot.reply_to(message, MSG_VIDEO_MOVE_INVALID_ID, parse_mode=PARSE_MODE_HTML)
            clear_user_waiting_state(user_id)
            return
        video_id = int(video_id_str)
        video = video_service.get_video_details(video_id)
        if not video:
            bot.reply_to(message, MSG_VIDEO_MOVE_INVALID_ID, parse_mode=PARSE_MODE_HTML)
            clear_user_waiting_state(user_id)
            return
        
        set_user_waiting_for_input(user_id, States.WAITING_CATEGORY_FOR_MOVE, context={"video_id": video_id})
        keyboard = create_hierarchical_category_keyboard(f"admin{CALLBACK_DELIMITER}move_confirm")
        bot.reply_to(message, MSG_VIDEO_MOVE_CATEGORY_PROMPT.format(video_id=video_id), reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)

    @state_manager.state_handler(States.WAITING_CATEGORY_FOR_MOVE)
    def handle_waiting_category_for_move_state(message, bot, context):
        user_id = message.from_user.id
        clear_user_waiting_state(user_id)
        bot.reply_to(message, MSG_VIDEO_MOVE_CANCELLED, parse_mode=PARSE_MODE_HTML)

    @state_manager.state_handler(States.WAITING_BROADCAST_MESSAGE)
    def handle_waiting_broadcast_message_state(message, bot, context):
        user_id = message.from_user.id
        broadcast_message = message.text
        user_count = len(user_service.get_all_bot_users())

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton(f"{EMOJI_CHECK} تأكيد الإرسال", callback_data=f"admin{CALLBACK_DELIMITER}broadcast_confirm"),
            InlineKeyboardButton(f"{EMOJI_ERROR} إلغاء", callback_data=f"admin{CALLBACK_DELIMITER}broadcast_cancel")
        )
        set_user_waiting_for_input(user_id, States.ADMIN_BROADCAST_CONFIRM, context={"message": broadcast_message})
        bot.reply_to(message, MSG_BROADCAST_CONFIRM.format(user_count=user_count), reply_markup=markup, parse_mode=PARSE_MODE_HTML)

    @state_manager.state_handler(States.ADMIN_BROADCAST_CONFIRM)
    def handle_admin_broadcast_confirm_state(message, bot, context):
        user_id = message.from_user.id
        clear_user_waiting_state(user_id)
        bot.reply_to(message, MSG_BROADCAST_CANCELLED, parse_mode=PARSE_MODE_HTML)

    @state_manager.state_handler(States.ADMIN_ADD_CHANNEL)
    def handle_admin_add_channel_state(message, bot, context):
        user_id = message.from_user.id
        clear_user_waiting_state(user_id)

        parts = message.text.split("/")
        if len(parts) != 2:
            bot.reply_to(message, f"{EMOJI_ERROR} صيغة غير صحيحة. يرجى استخدام `معرف_القناة/اسم_القناة`.", parse_mode=PARSE_MODE_HTML)
            return
        
        channel_id_str = parts[0].strip()
        channel_name = parts[1].strip()

        try:
            channel_id = int(channel_id_str) if channel_id_str.startswith("-100") else channel_id_str
        except ValueError:
            bot.reply_to(message, f"{EMOJI_ERROR} معرف القناة غير صحيح.", parse_mode=PARSE_MODE_HTML)
            return

        if required_channels_repository.add_required_channel(channel_id, channel_name):
            bot.reply_to(message, f"{EMOJI_CHECK} تم إضافة القناة ‘{channel_name}’ بنجاح.", parse_mode=PARSE_MODE_HTML)
        else:
            bot.reply_to(message, f"{EMOJI_ERROR} حدث خطأ أثناء إضافة القناة.", parse_mode=PARSE_MODE_HTML)

    @state_manager.state_handler(States.ADMIN_REMOVE_CHANNEL)
    def handle_admin_remove_channel_state(message, bot, context):
        user_id = message.from_user.id
        clear_user_waiting_state(user_id)

        channel_id_str = message.text.strip()
        try:
            channel_id = int(channel_id_str) if channel_id_str.startswith("-100") else channel_id_str
        except ValueError:
            bot.reply_to(message, f"{EMOJI_ERROR} معرف القناة غير صحيح.", parse_mode=PARSE_MODE_HTML)
            return

        if required_channels_repository.remove_required_channel(channel_id):
            bot.reply_to(message, f"{EMOJI_CHECK} تم حذف القناة بنجاح.", parse_mode=PARSE_MODE_HTML)
        else:
            bot.reply_to(message, f"{EMOJI_ERROR} حدث خطأ أثناء حذف القناة أو أنها غير موجودة.", parse_mode=PARSE_MODE_HTML)

    # --- Admin Callback for moving videos to category ---
    @bot.callback_query_handler(func=lambda call: call.data.startswith(f"admin{CALLBACK_DELIMITER}move_confirm"))
    def admin_move_confirm_callback(call):
        user_id = call.from_user.id
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, MSG_ADMIN_ONLY, show_alert=True)
            return

        data = call.data.split(CALLBACK_DELIMITER)
        action = data[1]
        category_id = int(data[2])

        state_data = state_manager.get_user_state(user_id)
        if not state_data or state_data["state"] != States.WAITING_CATEGORY_FOR_MOVE or "video_id" not in state_data["context"]:
            bot.answer_callback_query(call.id, f"{EMOJI_ERROR} انتهت صلاحية العملية أو حدث خطأ.", show_alert=True)
            clear_user_waiting_state(user_id)
            return
        
        video_id = state_data["context"]["video_id"]
        clear_user_waiting_state(user_id)

        if video_service.move_video_to_new_category(video_id, category_id):
            bot.edit_message_text(MSG_VIDEO_MOVED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
        else:
            bot.edit_message_text(f"{EMOJI_ERROR} حدث خطأ أثناء نقل الفيديو.", call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
        bot.answer_callback_query(call.id)

    # --- Admin Callback for broadcast confirmation ---
    @bot.callback_query_handler(func=lambda call: call.data == f"admin{CALLBACK_DELIMITER}broadcast_confirm")
    def admin_broadcast_confirm_callback(call):
        user_id = call.from_user.id
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, MSG_ADMIN_ONLY, show_alert=True)
            return

        state_data = state_manager.get_user_state(user_id)
        if not state_data or state_data["state"] != States.ADMIN_BROADCAST_CONFIRM or "message" not in state_data["context"]:
            bot.answer_callback_query(call.id, f"{EMOJI_ERROR} انتهت صلاحية عملية البث أو حدث خطأ.", show_alert=True)
            clear_user_waiting_state(user_id)
            return
        
        broadcast_message = state_data["context"]["message"]
        clear_user_waiting_state(user_id)

        all_users = user_service.get_all_bot_users()
        total_users = len(all_users)
        success_count = 0

        for user in all_users:
            try:
                bot.send_message(user["user_id"], broadcast_message, parse_mode=PARSE_MODE_HTML)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast to user {user["user_id"]}: {e}")
        
        bot.edit_message_text(MSG_BROADCAST_SENT.format(success_count=success_count, total_count=total_users), call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
        bot.answer_callback_query(call.id, "تم إرسال البث.")

    @bot.callback_query_handler(func=lambda call: call.data == f"admin{CALLBACK_DELIMITER}broadcast_cancel")
    def admin_broadcast_cancel_callback(call):
        user_id = call.from_user.id
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, MSG_ADMIN_ONLY, show_alert=True)
            return
        clear_user_waiting_state(user_id)
        bot.edit_message_text(MSG_BROADCAST_CANCELLED, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
        bot.answer_callback_query(call.id, "تم إلغاء البث.")


