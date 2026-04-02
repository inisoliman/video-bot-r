
# handlers/user_handlers.py

import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from config.config import Config
from config.constants import (
    MSG_WELCOME, MSG_WELCOME_UNSUBSCRIBED, MSG_NOT_SUBSCRIBED, MSG_ADMIN_ONLY,
    MSG_NO_VIDEOS, MSG_NO_FAVORITES, MSG_NO_HISTORY, MSG_SEARCH_PROMPT,
    MSG_SEARCH_TYPE_PROMPT, MSG_SEARCH_SCOPE_PROMPT, MSG_SEARCH_NO_RESULTS,
    EMOJI_SEARCH, EMOJI_CHECK, EMOJI_UNSUBSCRIBE, EMOJI_STAR, EMOJI_FILM,
    EMOJI_FIRE, EMOJI_RANDOM_VIDEO, EMOJI_FAVORITE, EMOJI_HISTORY, EMOJI_COMMENT,
    PARSE_MODE_MARKDOWN_V2, PARSE_MODE_HTML
)
from services import user_service, video_service, favorite_service, history_service, category_service
from core.state_manager import States, state_manager, set_user_waiting_for_input, clear_user_waiting_state
from utils.telegram_utils import (
    main_menu_keyboard, create_paginated_keyboard, create_video_action_keyboard,
    create_hierarchical_category_keyboard, get_channel_link
)

logger = logging.getLogger(__name__)

def register_user_handlers(bot):

    @bot.message_handler(commands=["start"])
    def start(message):
        user_id = message.from_user.id
        username = message.from_user.username
        first_name = message.from_user.first_name

        user_service.add_or_update_user(user_id, username, first_name)
        
        is_subscribed, unsub_channels = user_service.check_subscription(bot, user_id)
        
        if not is_subscribed:
            markup = InlineKeyboardMarkup(row_width=1)
            for channel in unsub_channels:
                try:
                    link = get_channel_link(channel["channel_id"], channel["channel_name"])
                    markup.add(InlineKeyboardButton(f"{EMOJI_UNSUBSCRIBE} اشترك في {channel["channel_name"]}", url=link))
                except Exception as e:
                    logger.error(f"Could not create link for channel {channel["channel_id"]}: {e}")
            
            markup.add(InlineKeyboardButton(f"{EMOJI_CHECK} لقد اشتركت، تحقق الآن", callback_data="check_subscription"))
            
            bot.reply_to(message, MSG_WELCOME_UNSUBSCRIBED, reply_markup=markup, parse_mode=PARSE_MODE_HTML)
            return
        
        bot_info = bot.get_me()
        welcome_text = MSG_WELCOME
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(
            f"{EMOJI_SEARCH} ابحث الآن في أي محادثة",
            switch_inline_query_current_chat=""
        ))
        
        bot.send_message(message.chat.id, welcome_text, reply_markup=main_menu_keyboard(bot_info.username), parse_mode=PARSE_MODE_MARKDOWN_V2)

    @bot.message_handler(commands=["myid"])
    def get_my_id(message):
        bot.reply_to(message, f"معرف حسابك هو: `{message.from_user.id}`", parse_mode=PARSE_MODE_MARKDOWN_V2)

    @bot.message_handler(func=lambda message: message.text == f"{EMOJI_FILM} عرض كل الفيديوهات")
    def handle_list_videos_button(message):
        list_videos(bot, message)
        
    @bot.message_handler(func=lambda message: message.text == f"{EMOJI_FAVORITE} المفضلة") 
    def handle_favorites_button(message):
        bot.send_chat_action(message.chat.id, 'typing')
        videos, total_count = favorite_service.get_user_favorite_videos(message.from_user.id, page=0, videos_per_page=Config.VIDEOS_PER_PAGE)
        if not videos:
            bot.reply_to(message, MSG_NO_FAVORITES)
            return
        keyboard = create_paginated_keyboard(videos, total_count, 0, "fav_page", "user_fav")
        bot.reply_to(message, f"قائمة مفضلاتك ({total_count} فيديو):", reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)
        
    @bot.message_handler(func=lambda message: message.text == f"{EMOJI_HISTORY} سجل المشاهدة") 
    def handle_history_button(message):
        bot.send_chat_action(message.chat.id, 'typing')
        videos, total_count = history_service.get_user_video_history(message.from_user.id, page=0, videos_per_page=Config.VIDEOS_PER_PAGE)
        if not videos:
            bot.reply_to(message, MSG_NO_HISTORY)
            return
        keyboard = create_paginated_keyboard(videos, total_count, 0, "history_page", "user_history")
        bot.reply_to(message, f"سجل مشاهداتك ({total_count} فيديو):", reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)

    @bot.message_handler(func=lambda message: message.text == f"{EMOJI_FIRE} الفيديوهات الشائعة")
    def handle_popular_videos_button(message):
        show_popular_videos(bot, message)

    @bot.message_handler(func=lambda message: message.text == f"{EMOJI_SEARCH} بحث")
    def handle_search_button(message):
        set_user_waiting_for_input(message.from_user.id, States.WAITING_SEARCH_QUERY)
        bot.reply_to(message, MSG_SEARCH_PROMPT)

    @bot.message_handler(func=lambda message: message.text == f"{EMOJI_RANDOM_VIDEO} اقترح لي فيلم")
    def handle_random_suggestion(message):
        bot.send_chat_action(message.chat.id, 'typing')
        video = video_service.get_random_video_suggestion()
        if video:
            try:
                video_id = video["id"]
                video_service.record_video_view(video_id, message.from_user.id)
                history_service.add_video_to_history(message.from_user.id, video_id)

                bot.copy_message(message.chat.id, video["chat_id"], video["message_id"])
                rating_keyboard = create_video_action_keyboard(video_id, message.from_user.id)
                bot.send_message(message.chat.id, f"{EMOJI_STAR} قيم هذا الفيديو:", reply_markup=rating_keyboard, parse_mode=PARSE_MODE_HTML)
            except Exception as e:
                logger.error(f"Error sending random video {video["id"]}: {e}")
                bot.reply_to(message, f"{EMOJI_ERROR} عذراً، حدث خطأ أثناء محاولة إرسال هذا الفيديو.")
        else:
            bot.reply_to(message, MSG_NO_VIDEOS)

    @bot.message_handler(commands=["search"])
    def handle_search_command(message):
        if message.chat.type == "private":
            set_user_waiting_for_input(message.from_user.id, States.WAITING_SEARCH_QUERY)
            bot.reply_to(message, MSG_SEARCH_PROMPT)
        else:
            if len(message.text.split()) > 1:
                query = " ".join(message.text.split()[1:])
                perform_group_search(bot, message, query)
            else:
                bot.reply_to(message, f"{EMOJI_ERROR} يرجى إدخال كلمة البحث بعد الأمر /search")

    @bot.message_handler(commands=["cancel"])
    def handle_cancel_command(message):
        clear_user_waiting_state(message.from_user.id)
        bot.reply_to(message, f"{EMOJI_CHECK} تم إلغاء العملية")

    # --- State Handlers ---
    @state_manager.state_handler(States.WAITING_SEARCH_QUERY)
    def handle_search_query_state(message, bot, context):
        if message.text == "/cancel":
            clear_user_waiting_state(message.from_user.id)
            bot.reply_to(message, f"{EMOJI_CHECK} تم إلغاء عملية البحث.")
            return
        
        clear_user_waiting_state(message.from_user.id)
        handle_private_text_search_direct(bot, message)

    # --- Helper Functions (moved from original helpers.py or user_handlers.py) ---
    def list_videos(bot, message, edit_message=None, parent_id=None):
        keyboard = create_categories_keyboard(parent_id)
        text = "اختر تصنيفًا لعرض محتوياته:" if keyboard.keyboard and keyboard.keyboard[0] else "لا توجد تصنيفات متاحة حالياً."
        try:
            if edit_message:
                bot.edit_message_text(text, edit_message.chat.id, edit_message.message_id, reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)
            else:
                bot.reply_to(message, text, reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)
        except telebot.apihelper.ApiTelegramException as e:
            if 'message is not modified' not in e.description:
                logger.error(f"Error in list_videos: {e}")

    def show_popular_videos(bot, message):
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton(f"{EMOJI_POPULAR} الأكثر مشاهدة", callback_data="popular::most_viewed"))
        keyboard.add(InlineKeyboardButton(f"{EMOJI_STAR} الأعلى تقييماً", callback_data="popular::highest_rated"))
        bot.reply_to(message, "اختر نوع الفيديوهات الشائعة:", reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)

    def handle_private_text_search_direct(bot, message):
        query = message.text.strip()
        state_manager.set_user_state(message.chat.id, States.MAIN_MENU, context={'last_search_query': query})
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton(f"{EMOJI_SEARCH} بحث عادي", callback_data="search_type::normal"),
            InlineKeyboardButton(f"{EMOJI_ADMIN} بحث متقدم", callback_data="search_type::advanced")
        )
        bot.reply_to(message, MSG_SEARCH_TYPE_PROMPT.format(query=query), reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)

    def perform_group_search(bot, message, query):
        state_manager.set_user_state(message.chat.id, States.MAIN_MENU, context={'last_search_query': query})
        videos, total_count = video_service.search_videos_with_filters(query=query, page=0, videos_per_page=Config.VIDEOS_PER_PAGE)
        if not videos:
            bot.reply_to(message, MSG_SEARCH_NO_RESULTS.format(query=query))
            return
        keyboard = create_paginated_keyboard(videos, total_count, 0, "search_all", "all")
        bot.reply_to(message, f"نتائج البحث عن \"{query}\":", reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)

