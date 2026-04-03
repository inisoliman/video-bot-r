#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/handlers/user_handlers.py
# الوصف: معالجات أوامر المستخدم (start, search, favorites, history, etc.)
# ==============================================================================

import logging

from bot.core.config import settings
from bot.database.repositories.video_repo import VideoRepository
from bot.database.repositories.user_repo import UserRepository
from bot.database.repositories.category_repo import CategoryRepository
from bot.services.subscription_service import SubscriptionService
from bot.ui.messages import Messages
from bot.ui.keyboards import Keyboards
from bot.ui.emoji import Emoji
from bot.handlers import comment_handlers

logger = logging.getLogger(__name__)

# ذاكرة مؤقتة للبحث الأخير
user_last_search = {}


def register(bot, admin_ids):
    """تسجيل معالجات المستخدم"""

    @bot.message_handler(commands=['start'])
    def handle_start(message):
        try:
            user = message.from_user
            UserRepository.add(user.id, user.username or "", user.first_name or "")

            # التحقق من الاشتراك
            is_sub, unsub_channels = SubscriptionService.check(bot, user.id)

            if not is_sub:
                bot.send_message(
                    message.chat.id,
                    Messages.subscription_required(),
                    parse_mode="Markdown",
                    reply_markup=Keyboards.subscription(unsub_channels)
                )
                return

            name = user.first_name or "صديقي"
            bot.send_message(
                message.chat.id,
                Messages.welcome(name),
                parse_mode="Markdown",
                reply_markup=Keyboards.main_menu()
            )
        except Exception as e:
            logger.error(f"Error in /start: {e}", exc_info=True)
            bot.reply_to(message, Messages.generic_error())

    @bot.message_handler(commands=['help'])
    def handle_help(message):
        bot_info = bot.get_me()
        help_text = (
            f"{Emoji.SEARCH} *كيفية البحث عن الفيديوهات*\n\n"
            f"📱 *في أي محادثة:*\n"
            f"1️⃣ اكتب: `@{bot_info.username}`\n"
            f"2️⃣ اكتب كلمة البحث بعدها\n"
            f"3️⃣ اختر الفيديو من النتائج\n\n"
            f"💡 *أمثلة:*\n"
            f"{Emoji.DOT} `@{bot_info.username} أكشن`\n"
            f"{Emoji.DOT} `@{bot_info.username} كوميدي`\n"
            f"{Emoji.DOT} `@{bot_info.username}` (بدون كلمة = الأكثر مشاهدة)"
        )
        bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

    @bot.message_handler(commands=['my_comments'])
    def handle_my_comments(message):
        comment_handlers.show_user_comments(bot, message, page=0)

    @bot.message_handler(func=lambda m: m.text == f"{Emoji.VIDEO} جميع الفيديوهات")
    def handle_list_videos(message):
        try:
            # عرض التصنيفات الجذرية
            categories = CategoryRepository.get_children(parent_id=None)
            if not categories:
                bot.send_message(message.chat.id, "لا توجد تصنيفات حالياً.")
                return

            keyboard = Keyboards.categories_tree("cat", add_back=True)
            bot.send_message(
                message.chat.id,
                f"{Emoji.FOLDER} *التصنيفات المتاحة:*",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Error in list_videos: {e}", exc_info=True)
            bot.reply_to(message, Messages.generic_error())

    @bot.message_handler(func=lambda m: m.text == f"{Emoji.SEARCH} بحث")
    def handle_search(message):
        try:
            bot.send_message(
                message.chat.id,
                Messages.search_prompt(),
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(message, process_search_query)
        except Exception as e:
            logger.error(f"Error in search: {e}", exc_info=True)

    def process_search_query(message):
        try:
            if message.text and message.text.startswith('/'):
                return  # تجاهل الأوامر

            # التحقق من حالة التعليقات
            state = UserRepository.get_state(message.from_user.id)
            if state and state['state'] in ['waiting_comment', 'replying_comment']:
                if state['state'] == 'waiting_comment':
                    comment_handlers.process_comment_text(bot, message)
                elif state['state'] == 'replying_comment' and message.from_user.id in admin_ids:
                    comment_handlers.process_reply_text(bot, message, admin_ids)
                return

            query = message.text.strip() if message.text else ""
            if not query:
                bot.reply_to(message, "الرجاء إدخال كلمة البحث.")
                return

            user_last_search[message.chat.id] = {'query': query}

            bot.send_message(
                message.chat.id,
                Messages.search_type_select(query),
                parse_mode="Markdown",
                reply_markup=Keyboards.search_type(query)
            )
        except Exception as e:
            logger.error(f"Search query error: {e}", exc_info=True)
            bot.reply_to(message, Messages.generic_error())

    @bot.message_handler(func=lambda m: m.text == f"{Emoji.FIRE} الأكثر شعبية")
    def handle_popular(message):
        bot.send_message(
            message.chat.id,
            f"{Emoji.FIRE} *الأكثر شعبية*",
            parse_mode="Markdown",
            reply_markup=Keyboards.popular_menu()
        )

    @bot.message_handler(func=lambda m: m.text == f"{Emoji.POPCORN} اقتراح عشوائي")
    def handle_random(message):
        try:
            video = VideoRepository.get_random()
            if video:
                VideoRepository.increment_views(video['id'])
                VideoRepository.add_to_history(message.from_user.id, video['id'])
                bot.copy_message(message.chat.id, video['chat_id'], video['message_id'])
                keyboard = Keyboards.video_actions(video['id'], message.from_user.id)
                bot.send_message(message.chat.id, Messages.rate_video(), reply_markup=keyboard)
            else:
                bot.send_message(message.chat.id, "لا توجد فيديوهات حالياً.")
        except Exception as e:
            logger.error(f"Random video error: {e}", exc_info=True)
            bot.reply_to(message, Messages.generic_error())

    @bot.message_handler(func=lambda m: m.text == f"{Emoji.STAR} المفضلة")
    def handle_favorites(message):
        try:
            videos, total = VideoRepository.get_favorites(message.from_user.id, 0)
            if not videos:
                bot.send_message(message.chat.id, "لا توجد فيديوهات في مفضلتك بعد.")
                return
            keyboard = Keyboards.paginated(videos, total, 0, "fav_page", "user_data")
            bot.send_message(message.chat.id, Messages.favorites_header(), reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Favorites error: {e}", exc_info=True)

    @bot.message_handler(func=lambda m: m.text == f"{Emoji.CLOCK} سجل المشاهدة")
    def handle_history(message):
        try:
            videos, total = VideoRepository.get_history(message.from_user.id, 0)
            if not videos:
                bot.send_message(message.chat.id, "لا توجد مشاهدات في سجلك بعد.")
                return
            keyboard = Keyboards.paginated(videos, total, 0, "history_page", "user_data")
            bot.send_message(message.chat.id, Messages.history_header(), reply_markup=keyboard)
        except Exception as e:
            logger.error(f"History error: {e}", exc_info=True)

    # معالج الرسائل العامة (لالتقاط نصوص البحث والتعليقات)
    @bot.message_handler(func=lambda m: m.text and not m.text.startswith('/'), content_types=['text'])
    def handle_text(message):
        try:
            user_id = message.from_user.id
            state = UserRepository.get_state(user_id)

            if state:
                if state['state'] == 'waiting_comment':
                    comment_handlers.process_comment_text(bot, message)
                    return
                elif state['state'] == 'replying_comment' and user_id in admin_ids:
                    comment_handlers.process_reply_text(bot, message, admin_ids)
                    return
        except Exception as e:
            logger.error(f"Text handler error: {e}", exc_info=True)
