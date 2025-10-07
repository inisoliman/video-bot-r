import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import os
import time
import re
import json
from urllib.parse import urlparse
import math
from datetime import datetime
import logging
import threading

# إعداد المسجل (logger) لهذا الملف
logger = logging.getLogger(__name__)

# --- استيراد الدوال من الملفات الأخرى ---
from db_manager import (
    add_category, get_categories_tree, get_child_categories,
    get_category_by_id, add_video, get_videos, increment_video_view_count,
    get_video_by_message_id, get_active_category_id, set_active_category_id,
    add_video_rating, get_video_rating_stats, get_user_video_rating,
    get_popular_videos, add_bot_user, get_all_user_ids, get_subscriber_count,
    get_bot_stats, search_videos, add_required_channel, remove_required_channel,
    get_required_channels, admin_steps, user_last_search, VIDEOS_PER_PAGE, CALLBACK_DELIMITER,
    move_video_to_category, get_video_by_id, delete_videos_by_ids,
    delete_category_and_contents, move_videos_from_category, delete_category_by_id as db_delete_category,
    get_random_video # <-- استيراد الدالة الجديدة
)
from utils import extract_video_metadata
from update_metadata import run_update_and_report_progress

# الدالة الرئيسية لتسجيل المعالجات
def register_handlers(bot, channel_id, admin_ids):
    """
    الدالة الرئيسية لتسجيل كل المعالجات.
    """

    # --- دوال مساعدة داخلية ---

    def generate_grouping_key(metadata, caption, file_name):
        series_name = metadata.get('series_name')
        if not series_name:
            raw_title = (caption or file_name or "").split('\n')[0]
            cleaned_title = re.sub(r'^[\d\s\W_-]+', '', raw_title).strip()
            series_name = cleaned_title
        if not series_name:
            return None
        sanitized_name = re.sub(r'[^a-zA-Z0-9\s-]', '', series_name).strip()
        sanitized_name = re.sub(r'\s+', '-', sanitized_name).lower()
        season = metadata.get('season_number')
        episode = metadata.get('episode_number')
        if season or episode:
            key_parts = ["series", sanitized_name]
            if season: key_parts.append(f"s{season:02d}")
            if episode: key_parts.append(f"e{episode:02d}")
            return "-".join(key_parts)
        else:
            return f"movie-{sanitized_name}"

    # ==============================================================================
    # تعديل: إضافة زر "اقتراح عشوائي" للقائمة الرئيسية
    # ==============================================================================
    def main_menu():
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(KeyboardButton("🎬 عرض كل الفيديوهات"), KeyboardButton("🔥 الفيديوهات الشائعة"))
        markup.add(KeyboardButton("🍿 اقترح لي فيلم"), KeyboardButton("🔍 بحث"))
        return markup

    def create_categories_keyboard(parent_id=None):
        keyboard = InlineKeyboardMarkup(row_width=2)
        categories = get_child_categories(parent_id)
        buttons = [InlineKeyboardButton(cat['name'], callback_data=f"cat::{cat['id']}::0") for cat in categories]
        keyboard.add(*buttons)
        if parent_id:
            parent_category = get_category_by_id(parent_id)
            if parent_category and parent_category.get('parent_id') is not None:
                keyboard.add(InlineKeyboardButton("🔙 رجوع", callback_data=f"cat::{parent_category['parent_id']}::0"))
            else:
                keyboard.add(InlineKeyboardButton("🔙 رجوع للتصنيفات الرئيسية", callback_data="back_to_cats"))
        return keyboard

    def format_duration(seconds):
        if not seconds or not isinstance(seconds, (int, float)): return ""
        secs = int(seconds)
        mins, secs = divmod(secs, 60)
        hours, mins = divmod(mins, 60)
        return f"{hours:02}:{mins:02}:{secs:02}" if hours > 0 else f"{mins:02}:{secs:02}"

    def format_video_display_info(video):
        metadata = video.get('metadata') or {}
        series_name = metadata.get('series_name')
        season = metadata.get('season_number')
        episode = metadata.get('episode_number')
        title_base = series_name or (video.get('caption') or video.get('file_name') or "فيديو").split('\n')[0]
        title_base = title_base.strip()
        parts = []
        if season: parts.append(f"م{season}")
        if episode: parts.append(f"ح{episode}")
        title_suffix = f" - {' '.join(parts)}" if parts else ""
        title = f"{video['id']}. {title_base}{title_suffix}"
        info_parts = []
        if metadata.get('status'): info_parts.append(metadata['status'])
        if metadata.get('quality_resolution'): info_parts.append(metadata['quality_resolution'])
        if metadata.get('duration'): info_parts.append(format_duration(metadata['duration']))
        info_line = f" ({' | '.join(info_parts)})" if info_parts else ""
        rating_text = f" ⭐ {video.get('avg_rating', 0):.1f}/5" if 'avg_rating' in video and video.get('avg_rating') is not None else ""
        views_text = f" 👁️ {video.get('view_count', 0)}"
        return f"{title}{info_line}{rating_text}{views_text}"

    def create_paginated_keyboard(videos, total_count, current_page, action_prefix, context_id):
        keyboard = InlineKeyboardMarkup(row_width=1)
        for video in videos:
            display_title = format_video_display_info(video)
            keyboard.add(InlineKeyboardButton(display_title, callback_data=f"video::{video['id']}::{video['message_id']}::{video['chat_id']}"))
        nav_buttons = []
        if current_page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"{action_prefix}::{context_id}::{current_page - 1}"))
        total_pages = math.ceil(total_count / VIDEOS_PER_PAGE)
        if current_page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"{action_prefix}::{context_id}::{current_page + 1}"))
        if nav_buttons:
            keyboard.add(*nav_buttons, row_width=2)
        keyboard.add(InlineKeyboardButton("🔙 رجوع للتصنيفات", callback_data="back_to_cats"))
        return keyboard

    def create_combined_keyboard(child_categories, videos, total_video_count, current_page, parent_category_id):
        keyboard = InlineKeyboardMarkup()
        if child_categories:
            keyboard.add(InlineKeyboardButton("📂--- الأقسام الفرعية ---📂", callback_data="noop"), row_width=1)
            cat_buttons = [InlineKeyboardButton(f"📁 {cat['name']}", callback_data=f"cat::{cat['id']}::0") for cat in child_categories]
            for i in range(0, len(cat_buttons), 2):
                keyboard.add(*cat_buttons[i:i+2])
        if videos:
            if child_categories:
                keyboard.add(InlineKeyboardButton("🎬--- الفيديوهات ---🎬", callback_data="noop"), row_width=1)
            for video in videos:
                display_title = format_video_display_info(video)
                keyboard.add(InlineKeyboardButton(display_title, callback_data=f"video::{video['id']}::{video['message_id']}::{video['chat_id']}"), row_width=1)
        nav_buttons = []
        if current_page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"cat::{parent_category_id}::{current_page - 1}"))
        total_pages = math.ceil(total_video_count / VIDEOS_PER_PAGE)
        if current_page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"cat::{parent_category_id}::{current_page + 1}"))
        if nav_buttons:
            keyboard.add(*nav_buttons, row_width=2)
        parent_category = get_category_by_id(parent_category_id)
        if parent_category and parent_category.get('parent_id') is not None:
            keyboard.add(InlineKeyboardButton("🔙 رجوع", callback_data=f"cat::{parent_category['parent_id']}::0"), row_width=1)
        else:
            keyboard.add(InlineKeyboardButton("🔙 رجوع للتصنيفات الرئيسية", callback_data="back_to_cats"), row_width=1)
        return keyboard

    def create_video_action_keyboard(video_id, user_id):
        keyboard = InlineKeyboardMarkup(row_width=5)
        user_rating = get_user_video_rating(video_id, user_id)
        buttons = [InlineKeyboardButton("⭐" if user_rating == i else "☆", callback_data=f"rate::{video_id}::{i}") for i in range(1, 6)]
        keyboard.add(*buttons)
        stats = get_video_rating_stats(video_id)
        if stats and stats.get('avg') is not None:
            keyboard.add(InlineKeyboardButton(f"متوسط التقييم: {stats['avg']:.1f} ({stats['count']} تقييم)", callback_data="noop"))
        return keyboard

    def check_admin(func):
        def wrapper(message):
            if message.from_user.id in admin_ids:
                return func(message)
            else:
                bot.reply_to(message, "ليس لديك صلاحية الوصول إلى هذا الأمر.")
        return wrapper

    def check_subscription(user_id):
        required_channels = get_required_channels()
        if not required_channels:
            return True, []
        unsubscribed = []
        for channel in required_channels:
            try:
                member = bot.get_chat_member(channel['channel_id'], user_id)
                if member.status not in ['member', 'administrator', 'creator']:
                    unsubscribed.append(channel)
            except telebot.apihelper.ApiTelegramException as e:
                error_msg = str(e.description).lower() if hasattr(e, 'description') else str(e).lower()
                if 'user not found' in error_msg or 'chat not found' in error_msg or 'bad request' in error_msg:
                    logger.warning(f"Could not check user {user_id} in channel {channel['channel_id']}. Assuming subscribed. Error: {e}")
                elif 'forbidden' in error_msg or 'kicked' in error_msg or 'left' in error_msg:
                    # المستخدم غير مشترك أو محظور أو غادر القناة
                    unsubscribed.append(channel)
                else:
                    # في حالة أخطاء أخرى، نفترض عدم الاشتراك للأمان
                    logger.error(f"Error checking subscription for user {user_id} in channel {channel['channel_id']}: {e}")
                    unsubscribed.append(channel)
            except Exception as e:
                logger.error(f"Unexpected error checking subscription for user {user_id} in channel {channel['channel_id']}: {e}")
                unsubscribed.append(channel)
        return not unsubscribed, unsubscribed

    # --- معالجات خطوات الآدمن ---
    def check_cancel(message):
        if message.text == "/cancel":
            if message.chat.id in admin_steps:
                del admin_steps[message.chat.id]
            bot.send_message(message.chat.id, "تم إلغاء العملية.")
            return True
        return False

    def handle_rich_broadcast(message):
        if check_cancel(message): return
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

    def handle_add_new_category(message):
        if check_cancel(message): return
        category_name = message.text.strip()
        step_data = admin_steps.pop(message.chat.id, {})
        parent_id = step_data.get("parent_id")
        success, result = add_category(category_name, parent_id=parent_id)
        if success:
            bot.reply_to(message, f"✅ تم إنشاء التصنيف الجديد بنجاح: \"{category_name}\".")
        else:
            bot.reply_to(message, f"❌ خطأ في إنشاء التصنيف: {result}")

    def handle_add_channel_step1(message):
        if check_cancel(message): return
        channel_id = message.text.strip()
        admin_steps[message.chat.id] = {"channel_id": channel_id}
        msg = bot.send_message(message.chat.id, "الآن أرسل اسم القناة (مثال: قناة الأفلام). (أو /cancel)")
        bot.register_next_step_handler(msg, handle_add_channel_step2)

    def handle_add_channel_step2(message):
        if check_cancel(message): return
        channel_name = message.text.strip()
        channel_id = admin_steps.pop(message.chat.id, {}).get("channel_id")
        if not channel_id: return
        if add_required_channel(channel_id, channel_name):
            bot.send_message(message.chat.id, f"✅ تم إضافة القناة \"{channel_name}\" (ID: {channel_id}) كقناة مطلوبة.")
        else:
            bot.send_message(message.chat.id, "❌ حدث خطأ أثناء إضافة القناة.")

    def handle_remove_channel_step(message):
        if check_cancel(message): return
        channel_id = message.text.strip()
        if remove_required_channel(channel_id):
            bot.send_message(message.chat.id, f"✅ تم إزالة القناة (ID: {channel_id}) من القنوات المطلوبة.")
        else:
            bot.send_message(message.chat.id, "❌ حدث خطأ أو القناة غير موجودة.")

    def handle_list_channels(message):
        channels = get_required_channels()
        if channels:
            response = "📋 *القنوات المطلوبة:*\n" + "\n".join([f"- {ch['channel_name']} (ID: `{ch['channel_id']}`)" for ch in channels])
            bot.send_message(message.chat.id, response, parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, "لا توجد قنوات مطلوبة حالياً.")

    def handle_delete_by_ids_input(message):
        if check_cancel(message): return
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

    def handle_move_by_id_input(message):
        if check_cancel(message): return
        try:
            video_id = int(message.text.strip())
            video = get_video_by_id(video_id)
            if not video:
                msg = bot.reply_to(message, "عذراً، لا يوجد فيديو بهذا الرقم. حاول مرة أخرى أو أرسل /cancel.")
                bot.register_next_step_handler(msg, handle_move_by_id_input)
                return
            keyboard = create_categories_keyboard()
            if not keyboard.keyboard:
                bot.reply_to(message, "لا توجد تصنيفات لنقل الفيديو إليها.")
                return
            # تحديث callback_data للأزرار لتتضمن معلومات نقل الفيديو
            for row in keyboard.keyboard:
                for button in row:
                    # التحقق من أن callback_data موجود وله التنسيق المتوقع
                    if button.callback_data and CALLBACK_DELIMITER in button.callback_data:
                        parts = button.callback_data.split(CALLBACK_DELIMITER)
                        if len(parts) >= 2:
                            category_id = parts[1]
                            button.callback_data = f"admin::move_confirm::{video['id']}::{category_id}"
            bot.reply_to(message, f"اختر التصنيف الجديد لنقل الفيديو رقم {video_id}:", reply_markup=keyboard)
        except ValueError:
            msg = bot.reply_to(message, "الرجاء إدخال رقم صحيح. حاول مرة أخرى أو أرسل /cancel.")
            bot.register_next_step_handler(msg, handle_move_by_id_input)
        except Exception as e:
            logger.error(f"Error in handle_move_by_id_input: {e}", exc_info=True)
            bot.reply_to(message, "حدث خطأ غير متوقع.")

    def generate_admin_panel():
        keyboard = InlineKeyboardMarkup(row_width=2)
        btn_add_cat = InlineKeyboardButton("➕ إضافة تصنيف", callback_data="admin::add_new_cat")
        btn_delete_cat = InlineKeyboardButton("🗑️ حذف تصنيف", callback_data="admin::delete_category_select")
        btn_move_video = InlineKeyboardButton("➡️ نقل فيديو بالرقم", callback_data="admin::move_video_by_id")
        btn_delete_video = InlineKeyboardButton("❌ حذف فيديوهات بالأرقام", callback_data="admin::delete_videos_by_ids")
        btn_set_active = InlineKeyboardButton("🔘 تعيين التصنيف النشط", callback_data="admin::set_active")
        btn_update_meta = InlineKeyboardButton("🔄 تحديث بيانات الفيديوهات القديمة", callback_data="admin::update_metadata")
        btn_add_channel = InlineKeyboardButton("➕ إضافة قناة اشتراك", callback_data="admin::add_channel")
        btn_remove_channel = InlineKeyboardButton("➖ إزالة قناة اشتراك", callback_data="admin::remove_channel")
        btn_list_channels = InlineKeyboardButton("📋 عرض القنوات", callback_data="admin::list_channels")
        btn_broadcast = InlineKeyboardButton("📢 بث رسالة", callback_data="admin::broadcast")
        btn_stats = InlineKeyboardButton("📊 الإحصائيات", callback_data="admin::stats")
        btn_subs = InlineKeyboardButton("👤 عدد المشتركين", callback_data="admin::sub_count")
        btn_help = InlineKeyboardButton("ℹ️ مساعدة", callback_data="admin::help")

        keyboard.add(btn_add_cat, btn_delete_cat)
        keyboard.add(btn_move_video, btn_delete_video)
        keyboard.add(btn_set_active, btn_update_meta)
        keyboard.add(btn_add_channel, btn_remove_channel)
        keyboard.add(btn_list_channels)
        keyboard.add(btn_broadcast, btn_stats, btn_subs, btn_help)
        return keyboard

    @bot.message_handler(commands=["start"])
    def start(message):
        # إضافة المستخدم إلى قاعدة البيانات
        add_bot_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        
        # التحقق من الاشتراك في القنوات المطلوبة
        is_subscribed, unsub_channels = check_subscription(message.from_user.id)
        
        if not is_subscribed:
            markup = InlineKeyboardMarkup(row_width=1)
            for channel in unsub_channels:
                try:
                    # إنشاء رابط القناة
                    channel_id_str = str(channel['channel_id'])
                    if channel_id_str.startswith('-100'):
                        # قناة بمعرف رقمي
                        link = f"https://t.me/c/{channel_id_str.replace('-100', '')}"
                    elif channel_id_str.startswith('@'):
                        # قناة باسم مستخدم
                        link = f"https://t.me/{channel_id_str[1:]}"
                    else:
                        # قناة باسم مستخدم بدون @
                        link = f"https://t.me/{channel_id_str}"
                    
                    markup.add(InlineKeyboardButton(f"📢 اشترك في {channel['channel_name']}", url=link))
                except Exception as e:
                    logger.error(f"Could not create link for channel {channel['channel_id']}: {e}")
            
            markup.add(InlineKeyboardButton("✅ لقد اشتركت، تحقق الآن", callback_data="check_subscription"))
            
            welcome_text = (
                "🤖 مرحباً بك في بوت البحث عن الفيديوهات!\n\n"
                "📋 للاستفادة من البوت، يجب عليك الاشتراك في القنوات التالية أولاً:\n"
                "👇 اضغط على الأزرار أدناه للاشتراك"
            )
            
            bot.reply_to(message, welcome_text, reply_markup=markup)
            return
        
        # إذا كان المستخدم مشتركاً في جميع القنوات
        welcome_text = (
            "🎬 أهلاً بك في بوت البحث عن الفيديوهات!\n\n"
            "يمكنك الآن:\n"
            "• 🎬 عرض كل الفيديوهات\n"
            "• 🔥 مشاهدة الفيديوهات الشائعة\n"
            "• 🍿 الحصول على اقتراح عشوائي\n"
            "• 🔍 البحث عن فيديوهات معينة\n\n"
            "استمتع بوقتك! 😊"
        )
        bot.reply_to(message, welcome_text, reply_markup=main_menu())

    @bot.message_handler(commands=["myid"])
    def get_my_id(message):
        bot.reply_to(message, f"معرف حسابك هو: `{message.from_user.id}`", parse_mode="Markdown")

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

    @bot.message_handler(func=lambda message: message.text == "🎬 عرض كل الفيديوهات")
    def handle_list_videos_button(message):
        list_videos(message)

    @bot.message_handler(func=lambda message: message.text == "🔥 الفيديوهات الشائعة")
    def handle_popular_videos_button(message):
        show_popular_videos(message)

    @bot.message_handler(func=lambda message: message.text == "🔍 بحث")
    def handle_search_button(message):
        msg = bot.reply_to(message, "أرسل الكلمة المفتاحية للبحث عن الفيديوهات:")
        bot.register_next_step_handler(msg, handle_private_text_search)

    # ==============================================================================
    # معالج جديد: التعامل مع زر "اقتراح عشوائي"
    # ==============================================================================
    @bot.message_handler(func=lambda message: message.text == "🍿 اقترح لي فيلم")
    def handle_random_suggestion(message):
        bot.send_chat_action(message.chat.id, 'typing')
        video = get_random_video()
        if video:
            try:
                increment_video_view_count(video['id'])
                bot.copy_message(message.chat.id, video['chat_id'], video['message_id'])
                rating_keyboard = create_video_action_keyboard(video['id'], message.from_user.id)
                bot.send_message(message.chat.id, "ما رأيك بهذا الفيديو؟ يمكنك تقييمه:", reply_markup=rating_keyboard)
            except Exception as e:
                logger.error(f"Error sending random video {video['id']}: {e}")
                bot.reply_to(message, "عذراً، حدث خطأ أثناء محاولة إرسال هذا الفيديو.")
        else:
            bot.reply_to(message, "لا توجد فيديوهات في قاعدة البيانات حالياً.")

    def handle_text_search(message):
        query = message.text.strip()
        user_last_search[message.chat.id] = query
        categories = get_categories_tree()
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton("بحث في كل التصنيفات", callback_data=f"search_scope::all"))
        for cat in categories:
            keyboard.add(InlineKeyboardButton(f"بحث في: {cat['name']}", callback_data=f"search_scope::{cat['id']}"))
        bot.reply_to(message, f"أين تريد البحث عن \"{query}\"؟", reply_markup=keyboard)

    @bot.message_handler(func=lambda message: message.text and not message.text.startswith("/") and message.chat.type == "private")
    def handle_private_text_search(message):
        handle_text_search(message)

    @bot.message_handler(commands=["search"])
    def handle_search_command(message):
        if message.chat.type == "private":
            msg = bot.reply_to(message, "أرسل الكلمة المفتاحية للبحث:")
            bot.register_next_step_handler(msg, handle_private_text_search)
        else:
            if len(message.text.split()) > 1:
                query = " ".join(message.text.split()[1:])
                perform_group_search(message, query)
            else:
                bot.reply_to(message, "يرجى إدخال كلمة البحث بعد الأمر /search")

    def show_popular_videos(message):
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton("📈 الأكثر مشاهدة", callback_data="popular::most_viewed"))
        keyboard.add(InlineKeyboardButton("⭐ الأعلى تقييماً", callback_data="popular::highest_rated"))
        bot.reply_to(message, "اختر نوع الفيديوهات الشائعة:", reply_markup=keyboard)

    def list_videos(message, edit_message=None, parent_id=None):
        keyboard = create_categories_keyboard(parent_id)
        text = "اختر تصنيفًا لعرض محتوياته:" if keyboard.keyboard else "لا توجد تصنيفات متاحة حالياً."
        if edit_message:
            bot.edit_message_text(text, edit_message.chat.id, edit_message.message_id, reply_markup=keyboard)
        else:
            bot.reply_to(message, text, reply_markup=keyboard)

    def perform_group_search(message, query):
        user_last_search[message.chat.id] = query
        videos, total_count = search_videos(query, page=0)
        if not videos:
            bot.reply_to(message, f"لم يتم العثور على نتائج للبحث عن \"{query}\".")
            return
        keyboard = create_paginated_keyboard(videos, total_count, 0, "search_all", "all")
        bot.reply_to(message, f"نتائج البحث عن \"{query}\":", reply_markup=keyboard)

    @bot.message_handler(content_types=["video"])
    def handle_new_video(message):
        if str(message.chat.id) == channel_id:
            active_category_id = get_active_category_id()
            if not active_category_id:
                logger.warning(f"No active category set. Video {message.message_id} will not be saved.")
                return
            metadata = extract_video_metadata(message.caption)
            if message.video:
                metadata['duration'] = message.video.duration
                if 'quality_resolution' not in metadata and message.video.height:
                    metadata['quality_resolution'] = f"{message.video.height}p"
            file_name = message.video.file_name if message.video else "video.mp4"
            file_id = message.video.file_id if message.video else None
            if not file_id:
                logger.error(f"Could not get file_id for message {message.message_id}. Skipping.")
                return
            grouping_key = generate_grouping_key(metadata, message.caption, file_name)
            video_db_id = add_video(
                message_id=message.message_id, caption=message.caption, chat_id=message.chat.id,
                file_name=file_name, file_id=file_id, metadata=metadata,
                grouping_key=grouping_key, category_id=active_category_id
            )
            if video_db_id:
                logger.info(f"Video {message.message_id} (DB ID: {video_db_id}) added to category {active_category_id}.")
            else:
                logger.error(f"Failed to add video {message.message_id} to the database.")

    @bot.callback_query_handler(func=lambda call: True)
    def callback_query(call):
        try:
            user_id = call.from_user.id
            data = call.data.split(CALLBACK_DELIMITER)
            action = data[0]

            # التحقق من الاشتراك لجميع العمليات باستثناء التحقق من الاشتراك نفسه
            if action != "check_subscription":
                is_subscribed, unsub_channels = check_subscription(user_id)
                if not is_subscribed:
                    bot.answer_callback_query(call.id, "🛑 يجب الاشتراك في القنوات المطلوبة أولاً.", show_alert=True)
                    return

            if action == "admin":
                sub_action = data[1]
                bot.answer_callback_query(call.id)

                if sub_action == "add_new_cat":
                    keyboard = InlineKeyboardMarkup()
                    keyboard.add(InlineKeyboardButton("تصنيف رئيسي جديد", callback_data="admin::add_cat_main"))
                    keyboard.add(InlineKeyboardButton("تصنيف فرعي", callback_data="admin::add_cat_sub_select_parent"))
                    bot.edit_message_text("اختر نوع التصنيف الذي تريد إضافته:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "add_cat_main":
                    admin_steps[call.message.chat.id] = {"parent_id": None}
                    msg = bot.send_message(call.message.chat.id, "أرسل اسم التصنيف الرئيسي الجديد. (أو /cancel)")
                    bot.register_next_step_handler(msg, handle_add_new_category)

                elif sub_action == "add_cat_sub_select_parent":
                    keyboard = create_categories_keyboard()
                    if not keyboard.keyboard:
                        bot.answer_callback_query(call.id, "أنشئ تصنيفاً رئيسياً أولاً.", show_alert=True)
                        return
                    for row in keyboard.keyboard:
                        for button in row:
                            parts = button.callback_data.split(CALLBACK_DELIMITER)
                            button.callback_data = f"admin::add_cat_sub_set_parent::{parts[1]}"
                    bot.edit_message_text("اختر التصنيف الأب:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "add_cat_sub_set_parent":
                    parent_id = int(data[2])
                    admin_steps[call.message.chat.id] = {"parent_id": parent_id}
                    msg = bot.send_message(call.message.chat.id, "الآن أرسل اسم التصنيف الفرعي الجديد. (أو /cancel)")
                    bot.register_next_step_handler(msg, handle_add_new_category)

                elif sub_action == "delete_category_select":
                    keyboard = create_categories_keyboard()
                    if not keyboard.keyboard:
                        bot.answer_callback_query(call.id, "لا توجد تصنيفات لحذفها.", show_alert=True)
                        return
                    for row in keyboard.keyboard:
                        for button in row:
                            parts = button.callback_data.split(CALLBACK_DELIMITER)
                            button.callback_data = f"admin::delete_category_confirm::{parts[1]}"
                    bot.edit_message_text("اختر التصنيف الذي تريد حذفه:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "delete_category_confirm":
                    category_id = int(data[2])
                    category = get_category_by_id(category_id)
                    keyboard = InlineKeyboardMarkup(row_width=1)
                    keyboard.add(InlineKeyboardButton("🗑️ حذف التصنيف مع كل فيديوهاته", callback_data=f"admin::delete_cat_and_videos::{category_id}"))
                    keyboard.add(InlineKeyboardButton("➡️ نقل فيديوهاته لتصنيف آخر", callback_data=f"admin::delete_cat_move_videos_select_dest::{category_id}"))
                    keyboard.add(InlineKeyboardButton("🔙 إلغاء", callback_data="admin::cancel_delete_cat"))
                    bot.edit_message_text(f"أنت على وشك حذف \"{category['name']}\". ماذا أفعل بالفيديوهات؟", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "delete_cat_and_videos":
                    category_id = int(data[2])
                    category = get_category_by_id(category_id)
                    delete_category_and_contents(category_id)
                    bot.edit_message_text(f"✅ تم حذف التصنيف \"{category['name']}\" وكل محتوياته.", call.message.chat.id, call.message.message_id)

                elif sub_action == "delete_cat_move_videos_select_dest":
                    old_category_id = int(data[2])
                    categories = [cat for cat in get_categories_tree() if cat['id'] != old_category_id]
                    if not categories:
                        bot.edit_message_text("لا يوجد تصنيف آخر لنقل الفيديوهات إليه.", call.message.chat.id, call.message.message_id)
                        return
                    keyboard = InlineKeyboardMarkup(row_width=1)
                    for cat in categories:
                        keyboard.add(InlineKeyboardButton(cat['name'], callback_data=f"admin::delete_cat_move_videos_confirm::{old_category_id}::{cat['id']}"))
                    keyboard.add(InlineKeyboardButton("🔙 إلغاء", callback_data="admin::cancel_delete_cat"))
                    bot.edit_message_text("اختر التصنيف الذي ستُنقل إليه الفيديوهات:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "delete_cat_move_videos_confirm":
                    old_category_id = int(data[2])
                    new_category_id = int(data[3])
                    category_to_delete = get_category_by_id(old_category_id)
                    move_videos_from_category(old_category_id, new_category_id)
                    db_delete_category(old_category_id)
                    new_cat = get_category_by_id(new_category_id)
                    bot.edit_message_text(f"✅ تم نقل الفيديوهات إلى \"{new_cat['name']}\" وحذف التصنيف \"{category_to_delete['name']}\".", call.message.chat.id, call.message.message_id)

                elif sub_action == "cancel_delete_cat":
                    bot.edit_message_text("👍 تم إلغاء عملية حذف التصنيف.", call.message.chat.id, call.message.message_id)

                elif sub_action == "move_video_by_id":
                    msg = bot.send_message(call.message.chat.id, "أرسل رقم الفيديو (ID) الذي تريد نقله. (أو /cancel)")
                    bot.register_next_step_handler(msg, handle_move_by_id_input)

                elif sub_action == "delete_videos_by_ids":
                    msg = bot.send_message(call.message.chat.id, "أرسل أرقام الفيديوهات (IDs) التي تريد حذفها، مفصولة بمسافة أو فاصلة. (أو /cancel)")
                    bot.register_next_step_handler(msg, handle_delete_by_ids_input)

                elif sub_action == "move_confirm":
                    video_id = int(data[2])
                    new_category_id = int(data[3])
                    result = move_video_to_category(video_id, new_category_id)
                    if result:
                        category = get_category_by_id(new_category_id)
                        bot.edit_message_text(f"✅ تم نقل الفيديو رقم {video_id} بنجاح إلى تصنيف \"{category['name']}\".", call.message.chat.id, call.message.message_id)
                    else:
                        bot.edit_message_text(f"❌ حدث خطأ أثناء نقل الفيديو رقم {video_id}.", call.message.chat.id, call.message.message_id)

                elif sub_action == "update_metadata":
                    msg = bot.edit_message_text("تم إرسال طلب تحديث البيانات...", call.message.chat.id, call.message.message_id)
                    update_thread = threading.Thread(target=run_update_and_report_progress, args=(bot, msg.chat.id, msg.message_id))
                    update_thread.start()

                elif sub_action == "set_active":
                    categories = get_categories_tree()
                    if not categories:
                        bot.answer_callback_query(call.id, "لا توجد تصنيفات حالياً.", show_alert=True)
                        return
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    buttons = [InlineKeyboardButton(text=cat['name'], callback_data=f"admin::setcat::{cat['id']}") for cat in categories]
                    keyboard.add(*buttons)
                    bot.edit_message_text("اختر التصنيف الذي تريد تفعيله:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "setcat":
                    category_id = int(data[2])
                    if set_active_category_id(category_id):
                        category = get_category_by_id(category_id)
                        bot.edit_message_text(f"✅ تم تفعيل التصنيف \"{category['name']}\" بنجاح.", call.message.chat.id, call.message.message_id)

                elif sub_action == "add_channel":
                    msg = bot.send_message(call.message.chat.id, "أرسل معرف القناة (مثال: -1001234567890 أو @username). (أو /cancel)")
                    bot.register_next_step_handler(msg, handle_add_channel_step1)

                elif sub_action == "remove_channel":
                    msg = bot.send_message(call.message.chat.id, "أرسل معرف القناة التي تريد إزالتها. (أو /cancel)")
                    bot.register_next_step_handler(msg, handle_remove_channel_step)

                elif sub_action == "list_channels":
                    handle_list_channels(call.message)

                elif sub_action == "broadcast":
                    msg = bot.send_message(call.message.chat.id, "أرسل الرسالة التي تريد بثها. (أو /cancel)")
                    bot.register_next_step_handler(msg, handle_rich_broadcast)

                elif sub_action == "sub_count":
                    count = get_subscriber_count()
                    bot.send_message(call.message.chat.id, f"👤 إجمالي عدد المشتركين: *{count}*", parse_mode="Markdown")

                elif sub_action == "stats":
                    stats = get_bot_stats()
                    popular = get_popular_videos()
                    stats_text = (f"📊 *إحصائيات المحتوى*\n\n"
                                  f"- إجمالي الفيديوهات: *{stats['video_count']}*\n"
                                  f"- إجمالي التصنيفات: *{stats['category_count']}*\n"
                                  f"- إجمالي المشاهدات: *{stats['total_views']}*\n"
                                  f"- إجمالي التقييمات: *{stats['total_ratings']}*")
                    if popular["most_viewed"]:
                        most_viewed = popular["most_viewed"][0]
                        title = (most_viewed['caption'] or "").split('\n')[0] or "فيديو"
                        stats_text += f"\n\n🔥 الأكثر مشاهدة: {title} ({most_viewed['view_count']} مشاهدة)"
                    if popular["highest_rated"]:
                        highest_rated = popular["highest_rated"][0]
                        title = (highest_rated['caption'] or "").split('\n')[0] or "فيديو"
                        stats_text += f"\n⭐ الأعلى تقييماً: {title} ({highest_rated['avg_rating']:.1f}/5)"
                    bot.send_message(call.message.chat.id, stats_text, parse_mode="Markdown")

                elif sub_action == "help":
                    help_text = "قائمة أوامر الإدارة:\n- يمكنك الآن إدارة التصنيفات والفيديوهات مباشرة من الأزرار.\n- استخدم الأوامر النصية عند الحاجة فقط."
                    bot.send_message(call.message.chat.id, help_text)

            elif action == "check_subscription":
                is_subscribed, unsub_channels = check_subscription(user_id)
                if is_subscribed:
                    bot.answer_callback_query(call.id, "✅ شكراً لاشتراكك!")
                    try:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                    except Exception as e:
                        logger.warning(f"Could not delete subscription check message: {e}")
                    bot.send_message(call.message.chat.id, "أهلاً بك في بوت البحث عن الفيديوهات!", reply_markup=main_menu())
                else:
                    # إعادة إنشاء رسالة الاشتراك مع القنوات التي لم يشترك فيها المستخدم
                    markup = InlineKeyboardMarkup(row_width=1)
                    for channel in unsub_channels:
                        try:
                            if not channel['channel_id'].startswith('-100'):
                                link = f"https://t.me/{channel['channel_id'].replace('@', '')}"
                            else:
                                link = f"https://t.me/c/{str(channel['channel_id']).replace('-100', '')}"
                            markup.add(InlineKeyboardButton(f"اشترك في {channel['channel_name']}", url=link))
                        except Exception as e:
                            logger.error(f"Could not create link for channel {channel['channel_id']}: {e}")
                    markup.add(InlineKeyboardButton("✅ لقد اشتركت، تحقق الآن", callback_data="check_subscription"))
                    
                    try:
                        bot.edit_message_text(
                            "❌ لم تشترك في جميع القنوات بعد. يرجى الاشتراك في القنوات التالية لاستخدام البوت:",
                            call.message.chat.id,
                            call.message.message_id,
                            reply_markup=markup
                        )
                        bot.answer_callback_query(call.id, "❌ لم تشترك في جميع القنوات بعد.")
                    except Exception as e:
                        logger.error(f"Error updating subscription message: {e}")
                        bot.answer_callback_query(call.id, "❌ لم تشترك في جميع القنوات بعد.", show_alert=True)

            elif action == "popular":
                sub_action = data[1]
                popular_data = get_popular_videos()
                videos = popular_data.get(sub_action, [])
                title = "📈 الفيديوهات الأكثر مشاهدة:" if sub_action == "most_viewed" else "⭐ الفيديوهات الأعلى تقييماً:"
                if videos:
                    keyboard = create_paginated_keyboard(videos, len(videos), 0, "popular_page", sub_action)
                    bot.edit_message_text(title, call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                else:
                    bot.edit_message_text("لا توجد فيديوهات كافية لعرضها حالياً.", call.message.chat.id, call.message.message_id)
                bot.answer_callback_query(call.id)

            elif action == "back_to_cats":
                list_videos(call.message, edit_message=call.message)
                bot.answer_callback_query(call.id)

            elif action == "video":
                try:
                    _, video_id, message_id, chat_id = data
                    
                    # التحقق من صحة البيانات
                    if not video_id.isdigit() or not message_id.isdigit():
                        bot.answer_callback_query(call.id, "خطأ في بيانات الفيديو.", show_alert=True)
                        return
                    
                    video_id_int = int(video_id)
                    message_id_int = int(message_id)
                    chat_id_int = int(chat_id)
                    
                    # زيادة عداد المشاهدات
                    increment_video_view_count(video_id_int)
                    
                    # محاولة إرسال الفيديو
                    bot.copy_message(call.message.chat.id, chat_id_int, message_id_int)
                    
                    # إضافة لوحة التقييم
                    rating_keyboard = create_video_action_keyboard(video_id_int, user_id)
                    bot.send_message(call.message.chat.id, "قيم هذا الفيديو:", reply_markup=rating_keyboard)
                    bot.answer_callback_query(call.id, "تم إرسال الفيديو!")
                    
                except telebot.apihelper.ApiTelegramException as e:
                    logger.error(f"Telegram API error handling video {video_id}: {e}", exc_info=True)
                    if "message not found" in str(e).lower():
                        bot.answer_callback_query(call.id, "❌ الفيديو غير متاح حالياً. ربما تم حذفه من القناة.", show_alert=True)
                    elif "chat not found" in str(e).lower():
                        bot.answer_callback_query(call.id, "❌ القناة غير متاحة حالياً.", show_alert=True)
                    else:
                        bot.answer_callback_query(call.id, "❌ حدث خطأ أثناء إرسال الفيديو.", show_alert=True)
                except Exception as e:
                    logger.error(f"Unexpected error handling video callback: {e}", exc_info=True)
                    bot.answer_callback_query(call.id, "❌ حدث خطأ غير متوقع.", show_alert=True)

            elif action == "rate":
                try:
                    _, video_id, rating = data
                    
                    # التحقق من صحة البيانات
                    if not video_id.isdigit() or not rating.isdigit():
                        bot.answer_callback_query(call.id, "خطأ في بيانات التقييم.", show_alert=True)
                        return
                    
                    video_id_int = int(video_id)
                    rating_int = int(rating)
                    
                    # التحقق من نطاق التقييم
                    if rating_int < 1 or rating_int > 5:
                        bot.answer_callback_query(call.id, "التقييم يجب أن يكون بين 1 و 5.", show_alert=True)
                        return
                    
                    # إضافة التقييم
                    if add_video_rating(video_id_int, user_id, rating_int):
                        new_keyboard = create_video_action_keyboard(video_id_int, user_id)
                        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=new_keyboard)
                        bot.answer_callback_query(call.id, f"⭐ تم تقييم الفيديو بـ {rating_int} نجوم! شكراً لك.")
                    else:
                        bot.answer_callback_query(call.id, "❌ حدث خطأ في حفظ التقييم. حاول مرة أخرى.")
                        
                except Exception as e:
                    logger.error(f"Error handling rating callback: {e}", exc_info=True)
                    bot.answer_callback_query(call.id, "❌ حدث خطأ أثناء حفظ التقييم.")

            elif action == "cat":
                try:
                    _, category_id_str, page_str = data
                    
                    # التحقق من صحة البيانات
                    if not category_id_str.isdigit() or not page_str.isdigit():
                        bot.answer_callback_query(call.id, "خطأ في بيانات التصنيف.", show_alert=True)
                        return
                    
                    category_id, page = int(category_id_str), int(page_str)
                    
                    # الحصول على معلومات التصنيف
                    category = get_category_by_id(category_id)
                    if not category:
                        bot.edit_message_text("❌ التصنيف غير موجود.", call.message.chat.id, call.message.message_id)
                        bot.answer_callback_query(call.id, "التصنيف غير موجود.")
                        return
                    
                    # الحصول على التصنيفات الفرعية والفيديوهات
                    child_categories = get_child_categories(category_id)
                    videos, total_count = get_videos(category_id, page)
                    
                    if not child_categories and not videos:
                        empty_keyboard = create_combined_keyboard([], [], 0, 0, category_id)
                        bot.edit_message_text(
                            f"📂 التصنيف \"{category['name']}\"\n\n"
                            "هذا التصنيف فارغ حالياً. لا توجد أقسام فرعية أو فيديوهات.",
                            call.message.chat.id, 
                            call.message.message_id,
                            reply_markup=empty_keyboard
                        )
                    else:
                        keyboard = create_combined_keyboard(child_categories, videos, total_count, page, category_id)
                        content_info = []
                        if child_categories:
                            content_info.append(f"{len(child_categories)} قسم فرعي")
                        if videos:
                            content_info.append(f"{total_count} فيديو")
                        
                        content_text = " • ".join(content_info) if content_info else "فارغ"
                        
                        bot.edit_message_text(
                            f"📂 محتويات تصنيف \"{category['name']}\"\n"
                            f"📊 المحتوى: {content_text}",
                            call.message.chat.id, 
                            call.message.message_id, 
                            reply_markup=keyboard
                        )
                    
                    bot.answer_callback_query(call.id)
                    
                except Exception as e:
                    logger.error(f"Error handling category callback: {e}", exc_info=True)
                    bot.answer_callback_query(call.id, "❌ حدث خطأ أثناء تحميل التصنيف.")

            elif action == "popular_page":
                sub_action = data[1]
                page = int(data[2])
                popular_data = get_popular_videos()
                videos = popular_data.get(sub_action, [])
                title = "📈 الفيديوهات الأكثر مشاهدة:" if sub_action == "most_viewed" else "⭐ الفيديوهات الأعلى تقييماً:"
                
                if videos:
                    # حساب الفيديوهات للصفحة المحددة
                    start_idx = page * VIDEOS_PER_PAGE
                    end_idx = start_idx + VIDEOS_PER_PAGE
                    page_videos = videos[start_idx:end_idx]
                    
                    keyboard = create_paginated_keyboard(page_videos, len(videos), page, "popular_page", sub_action)
                    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                else:
                    bot.edit_message_text("لا توجد فيديوهات كافية لعرضها حالياً.", call.message.chat.id, call.message.message_id)
                bot.answer_callback_query(call.id)

            elif action.startswith("search_"):
                query = user_last_search.get(call.message.chat.id)
                if not query:
                    bot.edit_message_text("انتهت صلاحية البحث، يرجى البحث مرة أخرى.", call.message.chat.id, call.message.message_id)
                    return
                if action == "search_scope":
                    scope = data[1]
                    page = 0
                    category_id = int(scope) if scope != "all" else None
                    videos, total_count = search_videos(query, page=page, category_id=category_id)
                    if not videos:
                        bot.edit_message_text(f"لا توجد نتائج لـ \"{query}\".", call.message.chat.id, call.message.message_id)
                        return
                    prefix = "search_cat" if category_id else "search_all"
                    keyboard = create_paginated_keyboard(videos, total_count, page, prefix, scope)
                    bot.edit_message_text(f"نتائج البحث عن \"{query}\":", call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                else:
                    _, context, page_str = data
                    page = int(page_str)
                    category_id = int(context) if context != "all" else None
                    videos, total_count = search_videos(query, page=page, category_id=category_id)
                    keyboard = create_paginated_keyboard(videos, total_count, page, action, context)
                    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                bot.answer_callback_query(call.id)

            elif action == "noop":
                bot.answer_callback_query(call.id)

        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Telegram API error in callback query: {e}", exc_info=True)
            try:
                if "query is too old" in str(e).lower():
                    # لا نحاول الرد على query قديم
                    pass
                elif "message is not modified" in str(e).lower():
                    bot.answer_callback_query(call.id, "تم تحديث المحتوى.")
                else:
                    bot.answer_callback_query(call.id, "❌ حدث خطأ في الاتصال. حاول مرة أخرى.", show_alert=True)
            except Exception as e_inner:
                logger.error(f"Could not answer callback query after API error: {e_inner}")
        except Exception as e:
            logger.error(f"Unexpected callback query error: {e}", exc_info=True)
            try:
                bot.answer_callback_query(call.id, "❌ حدث خطأ غير متوقع. حاول مرة أخرى.", show_alert=True)
            except Exception as e_inner:
                logger.error(f"Could not answer callback query after unexpected error: {e_inner}")
