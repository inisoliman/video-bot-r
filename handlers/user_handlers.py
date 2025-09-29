# handlers/user_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging

from db_manager import (
    add_bot_user, get_popular_videos, search_videos,
    get_random_video, increment_video_view_count, get_categories_tree, add_video,
    get_active_category_id
)
from .helpers import (
    main_menu, create_paginated_keyboard,
    create_video_action_keyboard, user_last_search, generate_grouping_key,
    check_subscription, list_videos
)
from state_manager import set_user_waiting_for_input, States, get_user_waiting_context, clear_user_waiting_state # [تعديل]
from utils import extract_video_metadata

logger = logging.getLogger(__name__)

def register(bot, channel_id, admin_ids):

    @bot.message_handler(commands=["start"])
    def start(message):
        add_bot_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        is_subscribed, unsub_channels = check_subscription(bot, message.from_user.id)
        if not is_subscribed:
            markup = InlineKeyboardMarkup(row_width=1)
            for channel in unsub_channels:
                try:
                    link = f"https://t.me/{channel['channel_name']}" if not str(channel['channel_id']).startswith('-100') else f"https://t.me/c/{str(channel['channel_id']).replace('-100', '')}"
                    markup.add(InlineKeyboardButton(f"اشترك في {channel['channel_name']}", url=link))
                except Exception as e:
                    logger.error(f"Could not create link for channel {channel['channel_id']}: {e}")
            markup.add(InlineKeyboardButton("✅ لقد اشتركت، تحقق الآن", callback_data="check_subscription"))
            bot.reply_to(message, "يرجى الاشتراك في القنوات التالية لاستخدام البوت:", reply_markup=markup)
            return
        bot.reply_to(message, "أهلاً بك في بوت البحث عن الفيديوهات!", reply_markup=main_menu())

    @bot.message_handler(commands=["myid"])
    def get_my_id(message):
        bot.reply_to(message, f"معرف حسابك هو: `{message.from_user.id}`", parse_mode="Markdown")

    @bot.message_handler(func=lambda message: message.text == "🎬 عرض كل الفيديوهات")
    def handle_list_videos_button(message):
        list_videos(bot, message)

    @bot.message_handler(func=lambda message: message.text == "🔥 الفيديوهات الشائعة")
    def handle_popular_videos_button(message):
        show_popular_videos(message)

    @bot.message_handler(func=lambda message: message.text == "🔍 بحث")
    def handle_search_button(message):
        # [تعديل] استخدام نظام الحالة بدلاً من next_step_handler
        set_user_waiting_for_input(message.chat.id, States.WAITING_SEARCH_QUERY)
        bot.reply_to(message, "أرسل الكلمة المفتاحية للبحث عن الفيديوهات:")

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

    # [تعديل] هذه الدالة الآن تعالج البحث من زر "🔍 بحث" ومن الرسائل النصية
    @bot.message_handler(func=lambda message: message.text and not message.text.startswith("/") and message.chat.type == "private")
    def handle_private_text_search(message):
        query = message.text.strip()
        user_id = message.chat.id
        
        # [تعديل] التحقق مما إذا كان المستخدم في حالة انتظار البحث (القادمة من زر "🔍 بحث")
        user_state_context = get_user_waiting_context(user_id)
        if user_state_context and 'state' in user_state_context and user_state_context['state'] == States.WAITING_SEARCH_QUERY:
            clear_user_waiting_state(user_id) # إنهاء حالة الانتظار

        # [تعديل] تخزين كلمة البحث في المتغير المؤقت user_last_search
        # (سنستخدم هذا المتغير مؤقتاً طالما أننا لم ننشئ دالة لتخزين الحالة في db_manager)
        user_last_search[message.chat.id] = {'query': query}
        
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("🔎 بحث عادي", callback_data="search_type::normal"),
            InlineKeyboardButton("⚙️ بحث متقدم", callback_data="search_type::advanced")
        )
        bot.reply_to(message, f"اختر نوع البحث عن \"{query}\":", reply_markup=keyboard)

    @bot.message_handler(commands=["search"])
    def handle_search_command(message):
        if message.chat.type == "private":
            # [تعديل] استخدام نظام الحالة بدلاً من next_step_handler
            set_user_waiting_for_input(message.chat.id, States.WAITING_SEARCH_QUERY)
            bot.reply_to(message, "أرسل الكلمة المفتاحية للبحث:")
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

    def perform_group_search(message, query):
        user_last_search[message.chat.id] = {'query': query}
        videos, total_count = search_videos(query=query, page=0)
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
