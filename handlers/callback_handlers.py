
# handlers/callback_handlers.py

import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from config.config import Config
from config.constants import (
    CALLBACK_DELIMITER, MSG_NOT_SUBSCRIBED, EMOJI_CHECK, EMOJI_UNSUBSCRIBE,
    MSG_WELCOME, EMOJI_STAR, MSG_NO_MORE_RESULTS, MSG_SEARCH_TYPE_PROMPT,
    MSG_SEARCH_SCOPE_PROMPT, EMOJI_SEARCH, EMOJI_ADMIN, EMOJI_BACK, EMOJI_ERROR,
    MSG_VIDEO_NOT_AVAILABLE, MSG_CHANNEL_NOT_AVAILABLE, MSG_ERROR_SENDING_VIDEO,
    MSG_UNEXPECTED_ERROR, MSG_RATING_SAVED, MSG_RATING_ERROR, MSG_INVALID_RATING,
    MSG_CATEGORY_NOT_FOUND, PARSE_MODE_HTML, PARSE_MODE_MARKDOWN_V2
)
from services import (
    user_service, video_service, favorite_service, rating_service,
    category_service, history_service
)
from core.state_manager import States, state_manager
from utils.telegram_utils import (
    main_menu_keyboard, create_paginated_keyboard, create_video_action_keyboard,
    create_hierarchical_category_keyboard, get_channel_link, create_categories_keyboard,
    create_combined_keyboard
)

logger = logging.getLogger(__name__)

def register_callback_handlers(bot, admin_ids):

    @bot.callback_query_handler(func=lambda call: True)
    def callback_query(call):
        try:
            user_id = call.from_user.id
            data = call.data.split(CALLBACK_DELIMITER)
            action = data[0]

            # Check subscription for all actions except 'check_subscription'
            is_subscribed, unsub_channels = user_service.check_subscription(bot, user_id)
            if action != "check_subscription" and not is_subscribed:
                markup = InlineKeyboardMarkup(row_width=1)
                for channel in unsub_channels:
                    try:
                        link = get_channel_link(channel["channel_id"], channel["channel_name"])
                        markup.add(InlineKeyboardButton(f"{EMOJI_UNSUBSCRIBE} اشترك في {channel["channel_name"]}", url=link))
                    except Exception as e:
                        logger.error(f"Could not create link for channel {channel["channel_id"]}: {e}")
                markup.add(InlineKeyboardButton(f"{EMOJI_CHECK} لقد اشتركت، تحقق الآن", callback_data="check_subscription"))
                
                try:
                    bot.edit_message_text(MSG_NOT_SUBSCRIBED,
                                        call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode=PARSE_MODE_HTML)
                except telebot.apihelper.ApiTelegramException as e:
                    bot.send_message(call.message.chat.id, MSG_NOT_SUBSCRIBED, reply_markup=markup, parse_mode=PARSE_MODE_HTML)
                bot.answer_callback_query(call.id)
                return

            if action == "check_subscription":
                is_subscribed, unsub_channels = user_service.check_subscription(bot, user_id)
                if is_subscribed:
                    try:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                    except Exception as e:
                        logger.warning(f"Could not delete subscription check message: {e}")
                    bot_info = bot.get_me()
                    bot.send_message(call.message.chat.id, MSG_WELCOME, reply_markup=main_menu_keyboard(bot_info.username), parse_mode=PARSE_MODE_MARKDOWN_V2)
                else:
                    markup = InlineKeyboardMarkup(row_width=1)
                    for channel in unsub_channels:
                        try:
                            link = get_channel_link(channel["channel_id"], channel["channel_name"])
                            markup.add(InlineKeyboardButton(f"{EMOJI_UNSUBSCRIBE} اشترك في {channel["channel_name"]}", url=link))
                        except Exception as e:
                            logger.error(f"Could not create link for channel {channel["channel_id"]}: {e}")
                    markup.add(InlineKeyboardButton(f"{EMOJI_CHECK} لقد اشتركت، تحقق الآن", callback_data="check_subscription"))
                    try:
                        bot.edit_message_text(
                            MSG_NOT_SUBSCRIBED,
                            call.message.chat.id,
                            call.message.message_id,
                            reply_markup=markup, parse_mode=PARSE_MODE_HTML
                        )
                    except Exception as e:
                        logger.error(f"Error updating subscription message: {e}")
                        bot.answer_callback_query(call.id, MSG_NOT_SUBSCRIBED, show_alert=True)
                bot.answer_callback_query(call.id)
                return

            elif action == "fav":
                _, action_type, video_id = data
                video_id = int(video_id)
                if action_type == "remove":
                    favorite_service.remove_video_from_favorites(user_id, video_id)
                    text = f"{EMOJI_ERROR} تم إزالة الفيديو من المفضلة."
                else:
                    favorite_service.add_video_to_favorites(user_id, video_id)
                    text = f"{EMOJI_STAR} تم إضافة الفيديو إلى المفضلة بنجاح!"
                new_keyboard = create_video_action_keyboard(video_id, user_id)
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=new_keyboard)
                bot.answer_callback_query(call.id, text)
                return

            elif action in ["fav_page", "history_page"]:
                _, _, page_str = data
                page = int(page_str)
                if action == "fav_page":
                    videos, total_count = favorite_service.get_user_favorite_videos(user_id, page, Config.VIDEOS_PER_PAGE)
                    prefix = "fav_page"
                    title = f"{EMOJI_STAR} قائمة مفضلاتك:"
                else:
                    videos, total_count = history_service.get_user_video_history(user_id, page, Config.VIDEOS_PER_PAGE)
                    prefix = "history_page"
                    title = f"{EMOJI_HISTORY} سجل مشاهداتك:"
                if not videos:
                    bot.edit_message_text(MSG_NO_MORE_RESULTS, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
                    bot.answer_callback_query(call.id)
                    return
                keyboard = create_paginated_keyboard(videos, total_count, page, prefix, "user_data")
                bot.edit_message_text(title, call.message.chat.id, call.message.message_id, reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)
                bot.answer_callback_query(call.id)
                return

            elif action == "search_type":
                search_type = data[1]
                query_data = state_manager.get_user_state(call.message.chat.id)
                query = query_data["context"]["last_search_query"] if query_data and "last_search_query" in query_data["context"] else None

                if not query:
                    bot.edit_message_text(f"{EMOJI_ERROR} انتهت صلاحية البحث أو لم ترسل كلمة البحث. يرجى إرسال الكلمة المفتاحية الآن.",
                                        call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
                    bot.answer_callback_query(call.id)
                    return

                if search_type == "normal":
                    keyboard = InlineKeyboardMarkup(row_width=1)
                    categories = category_service.get_all_categories_tree()
                    tree = category_service.build_category_tree_display(categories)
                    for cat in tree:
                        keyboard.add(InlineKeyboardButton(
                            f"{EMOJI_SEARCH} {cat["name"]}", 
                            callback_data=f"search_scope{CALLBACK_DELIMITER}{cat["id"]}{CALLBACK_DELIMITER}0"
                        ))
                    keyboard.add(InlineKeyboardButton(f"{EMOJI_SEARCH} بحث في كل التصنيفات", callback_data=f"search_scope{CALLBACK_DELIMITER}all{CALLBACK_DELIMITER}0"))
                    bot.edit_message_text(MSG_SEARCH_SCOPE_PROMPT.format(query=query), call.message.chat.id, call.message.message_id, reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)

                elif search_type == "advanced":
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    keyboard.add(
                        InlineKeyboardButton(f"{EMOJI_QUALITY} الجودة", callback_data="adv_filter::quality"),
                        InlineKeyboardButton(f"{EMOJI_STATUS} الحالة", callback_data="adv_filter::status")
                    )
                    keyboard.add(InlineKeyboardButton(f"{EMOJI_BACK} رجوع", callback_data="back_to_main"))
                    bot.edit_message_text(f"{EMOJI_ADMIN} اختر فلتر للبحث المتقدم:", call.message.chat.id, call.message.message_id, reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)
                bot.answer_callback_query(call.id)
                return

            elif action == "adv_filter":
                filter_type = data[1]
                query_data = state_manager.get_user_state(call.message.chat.id)
                query = query_data["context"]["last_search_query"] if query_data and "last_search_query" in query_data["context"] else None

                if not query:
                    bot.edit_message_text(f"{EMOJI_ERROR} انتهت صلاحية البحث. يرجى إرسال الكلمة المفتاحية الآن.",
                                        call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
                    bot.answer_callback_query(call.id)
                    return

                if filter_type == "quality":
                    keyboard = InlineKeyboardMarkup(row_width=3)
                    qualities = ["1080p", "720p", "480p", "360p"]
                    buttons = [InlineKeyboardButton(q, callback_data=f"adv_search{CALLBACK_DELIMITER}quality{CALLBACK_DELIMITER}{q}{CALLBACK_DELIMITER}0") for q in qualities]
                    keyboard.add(*buttons)
                    keyboard.add(InlineKeyboardButton(f"{EMOJI_BACK} رجوع للفلاتر", callback_data="search_type::advanced"))
                    bot.edit_message_text(f"{EMOJI_QUALITY} اختر الجودة:", call.message.chat.id, call.message.message_id, reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)

                elif filter_type == "status":
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    statuses = ["مترجم", "مدبلج"]
                    buttons = [InlineKeyboardButton(s, callback_data=f"adv_search{CALLBACK_DELIMITER}status{CALLBACK_DELIMITER}{s}{CALLBACK_DELIMITER}0") for s in statuses]
                    keyboard.add(*buttons)
                    keyboard.add(InlineKeyboardButton(f"{EMOJI_BACK} رجوع للفلاتر", callback_data="search_type::advanced"))
                    bot.edit_message_text(f"{EMOJI_STATUS} اختر الحالة:", call.message.chat.id, call.message.message_id, reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)
                bot.answer_callback_query(call.id)
                return

            elif action == "adv_search":
                _, filter_type, filter_value, page_str = data
                page = int(page_str)
                query_data = state_manager.get_user_state(call.message.chat.id)
                query = query_data["context"]["last_search_query"] if query_data and "last_search_query" in query_data["context"] else None

                if not query:
                    bot.edit_message_text(f"{EMOJI_ERROR} انتهت صلاحية البحث. يرجى إرسال الكلمة المفتاحية الآن.",
                                        call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
                    bot.answer_callback_query(call.id)
                    return

                quality = filter_value if filter_type == "quality" else None
                status = filter_value if filter_type == "status" else None

                videos, total_count = video_service.search_videos_with_filters(query, page, quality=quality, status=status, videos_per_page=Config.VIDEOS_PER_PAGE)
                if not videos:
                    bot.edit_message_text(MSG_SEARCH_NO_RESULTS.format(query=query), call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
                    bot.answer_callback_query(call.id)
                    return

                keyboard = create_paginated_keyboard(videos, total_count, page, f"adv_search{CALLBACK_DELIMITER}{filter_type}{CALLBACK_DELIMITER}{filter_value}", "adv_search_results")
                bot.edit_message_text(f"نتائج البحث المتقدم عن \"{query}\" ({filter_type}: {filter_value}):", call.message.chat.id, call.message.message_id, reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)
                bot.answer_callback_query(call.id)
                return

            elif action == "search_scope":
                _, category_id_str, page_str = data
                page = int(page_str)
                query_data = state_manager.get_user_state(call.message.chat.id)
                query = query_data["context"]["last_search_query"] if query_data and "last_search_query" in query_data["context"] else None

                if not query:
                    bot.edit_message_text(f"{EMOJI_ERROR} انتهت صلاحية البحث. يرجى إرسال الكلمة المفتاحية الآن.",
                                        call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
                    bot.answer_callback_query(call.id)
                    return

                category_id = int(category_id_str) if category_id_str != "all" else None
                videos, total_count = video_service.search_videos_with_filters(query, page, category_id=category_id, videos_per_page=Config.VIDEOS_PER_PAGE)

                if not videos:
                    bot.edit_message_text(MSG_SEARCH_NO_RESULTS.format(query=query), call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
                    bot.answer_callback_query(call.id)
                    return

                keyboard = create_paginated_keyboard(videos, total_count, page, f"search_scope{CALLBACK_DELIMITER}{category_id_str}", "search_results")
                bot.edit_message_text(f"نتائج البحث عن \"{query}\":", call.message.chat.id, call.message.message_id, reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)
                bot.answer_callback_query(call.id)
                return

            elif action == "popular":
                sub_action = data[1]
                popular_data = video_service.get_popular_and_highest_rated_videos()
                videos = popular_data.get(sub_action, [])
                title = f"{EMOJI_POPULAR} الفيديوهات الأكثر مشاهدة:" if sub_action == "most_viewed" else f"{EMOJI_STAR} الفيديوهات الأعلى تقييماً:"

                if videos:
                    keyboard = create_paginated_keyboard(videos, len(videos), 0, "popular_page", sub_action)
                    bot.edit_message_text(title, call.message.chat.id, call.message.message_id, reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)
                else:
                    bot.edit_message_text(MSG_NO_VIDEOS, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
                bot.answer_callback_query(call.id)
                return
            
            elif action == "popular_page":
                sub_action = data[1]
                page = int(data[2])
                popular_data = video_service.get_popular_and_highest_rated_videos()
                videos = popular_data.get(sub_action, [])
                title = f"{EMOJI_POPULAR} الفيديوهات الأكثر مشاهدة:" if sub_action == "most_viewed" else f"{EMOJI_STAR} الفيديوهات الأعلى تقييماً:"
                
                if videos:
                    start_idx = page * Config.VIDEOS_PER_PAGE
                    end_idx = start_idx + Config.VIDEOS_PER_PAGE
                    page_videos = videos[start_idx:end_idx]
                    
                    keyboard = create_paginated_keyboard(page_videos, len(videos), page, "popular_page", sub_action)
                    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                else:
                    bot.edit_message_text(MSG_NO_VIDEOS, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
                bot.answer_callback_query(call.id)
                return

            elif action == "back_to_cats":
                # This should display the main categories menu
                keyboard = create_categories_keyboard(None)
                text = "اختر تصنيفًا لعرض محتوياته:" if keyboard.keyboard and keyboard.keyboard[0] else "لا توجد تصنيفات متاحة حالياً."
                bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=keyboard, parse_mode=PARSE_MODE_HTML)
                bot.answer_callback_query(call.id)
                return

            elif action == "back_to_main":
                # This is the critical part for the desktop issue
                # Instead of deleting and sending new, try to edit if possible
                bot_info = bot.get_me()
                try:
                    bot.edit_message_text("القائمة الرئيسية:", call.message.chat.id, call.message.message_id, reply_markup=main_menu_keyboard(bot_info.username), parse_mode=PARSE_MODE_MARKDOWN_V2)
                except telebot.apihelper.ApiTelegramException as e:
                    if "message is not modified" in str(e).lower():
                        logger.info("Message not modified, no need to edit.")
                    else:
                        logger.warning(f"Could not edit message to main menu, sending new one: {e}")
                        bot.send_message(call.message.chat.id, "القائمة الرئيسية:", reply_markup=main_menu_keyboard(bot_info.username), parse_mode=PARSE_MODE_MARKDOWN_V2)
                bot.answer_callback_query(call.id)
                return

            elif action == "video":
                try:
                    _, video_id, message_id, chat_id = data
                    video_id_int = int(video_id)
                    message_id_int = int(message_id)
                    chat_id_int = int(chat_id)
                    
                    video_service.record_video_view(video_id_int, user_id)
                    history_service.add_video_to_history(user_id, video_id_int)
                    
                    bot.copy_message(call.message.chat.id, chat_id_int, message_id_int)
                    
                    rating_keyboard = create_video_action_keyboard(video_id_int, user_id)
                    bot.send_message(call.message.chat.id, f"{EMOJI_STAR} قيم هذا الفيديو:", reply_markup=rating_keyboard, parse_mode=PARSE_MODE_HTML)
                    bot.answer_callback_query(call.id)
                    
                except telebot.apihelper.ApiTelegramException as e:
                    logger.error(f"Telegram API error handling video {video_id}: {e}", exc_info=True)
                    if "message not found" in str(e).lower():
                        bot.answer_callback_query(call.id, MSG_VIDEO_NOT_AVAILABLE, show_alert=True)
                    elif "chat not found" in str(e).lower():
                        bot.answer_callback_query(call.id, MSG_CHANNEL_NOT_AVAILABLE, show_alert=True)
                    else:
                        bot.answer_callback_query(call.id, MSG_ERROR_SENDING_VIDEO, show_alert=True)
                except Exception as e:
                    logger.error(f"Unexpected error handling video callback: {e}", exc_info=True)
                    bot.answer_callback_query(call.id, MSG_UNEXPECTED_ERROR, show_alert=True)
                return

            elif action == "rate":
                try:
                    _, video_id, rating = data
                    video_id_int = int(video_id)
                    rating_int = int(rating)
                    
                    if rating_int < 1 or rating_int > 5:
                        bot.answer_callback_query(call.id, MSG_INVALID_RATING, show_alert=True)
                        return
                    
                    if rating_service.add_or_update_video_rating(video_id_int, user_id, rating_int):
                        new_keyboard = create_video_action_keyboard(video_id_int, user_id)
                        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=new_keyboard)
                        bot.answer_callback_query(call.id, MSG_RATING_SAVED.format(rating=rating_int))
                    else:
                        bot.answer_callback_query(call.id, MSG_RATING_ERROR)
                        
                except Exception as e:
                    logger.error(f"Error handling rating callback: {e}", exc_info=True)
                    bot.answer_callback_query(call.id, MSG_UNEXPECTED_ERROR)
                return

            elif action == "cat":
                try:
                    _, category_id_str, page_str = data
                    category_id, page = int(category_id_str), int(page_str)
                    
                    category = category_service.get_category_details(category_id)
                    if not category:
                        bot.edit_message_text(MSG_CATEGORY_NOT_FOUND, call.message.chat.id, call.message.message_id, parse_mode=PARSE_MODE_HTML)
                        bot.answer_callback_query(call.id)
                        return
                    
                    child_categories = category_service.get_child_categories(category_id)
                    videos, total_count = video_service.get_paginated_videos(category_id, page)
                    
                    if not child_categories and not videos:
                        empty_keyboard = create_combined_keyboard([], [], 0, 0, category_id)
                        bot.edit_message_text(
                            f"{EMOJI_FOLDER} التصنيف \"{category["name"]}\"\n\n"
                            "هذا التصنيف فارغ حالياً. لا توجد أقسام فرعية أو فيديوهات.",
                            call.message.chat.id, 
                            call.message.message_id,
                            reply_markup=empty_keyboard, parse_mode=PARSE_MODE_HTML
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
                            f"{EMOJI_FOLDER} محتويات تصنيف \"{category["name"]}\"\n"
                            f"📊 المحتوى: {content_text}",
                            call.message.chat.id, 
                            call.message.message_id, 
                            reply_markup=keyboard, parse_mode=PARSE_MODE_HTML
                        )
                    bot.answer_callback_query(call.id)
                    
                except Exception as e:
                    logger.error(f"Error handling category callback: {e}", exc_info=True)
                    bot.answer_callback_query(call.id, MSG_UNEXPECTED_ERROR)
                return

            elif action == "noop":
                bot.answer_callback_query(call.id)
                return

            # Add other callback handlers here (e.g., comment handlers, admin handlers)
            # For now, we'll just answer the callback if it's not handled to prevent loading state
            bot.answer_callback_query(call.id)

        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Telegram API error in callback query: {e}", exc_info=True)
            try:
                if "query is too old" in str(e).lower():
                    pass
                elif "message is not modified" in str(e).lower():
                    bot.answer_callback_query(call.id, "تم تحديث المحتوى.")
                else:
                    bot.answer_callback_query(call.id, f"{EMOJI_ERROR} حدث خطأ في الاتصال. حاول مرة أخرى.", show_alert=True)
            except Exception as e_inner:
                logger.error(f"Could not answer callback query after API error: {e_inner}")
        except Exception as e:
            logger.error(f"Unexpected callback query error: {e}", exc_info=True)
            try:
                bot.answer_callback_query(call.id, f"{EMOJI_ERROR} حدث خطأ غير متوقع. حاول مرة أخرى.", show_alert=True)
            except Exception as e_inner:
                logger.error(f"Could not answer callback query after unexpected error: {e_inner}")
