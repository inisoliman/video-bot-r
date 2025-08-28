# handlers/callback_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import threading

from db_manager import *
from . import helpers
from . import admin_handlers
from update_metadata import run_update_and_report_progress

logger = logging.getLogger(__name__)

def register(bot, admin_ids):

    @bot.callback_query_handler(func=lambda call: True)
    def callback_query(call):
        try:
            user_id = call.from_user.id
            data = call.data.split(helpers.CALLBACK_DELIMITER)
            action = data[0]

            is_subscribed, _ = helpers.check_subscription(bot, user_id)
            if action != "check_subscription" and not is_subscribed:
                bot.answer_callback_query(call.id, "🛑 يجب الاشتراك في القنوات المطلوبة أولاً.", show_alert=True)
                return

            if action == "search_type":
                search_type = data[1]
                query_data = helpers.user_last_search.get(call.message.chat.id)
                if not query_data or 'query' not in query_data:
                    bot.edit_message_text("انتهت صلاحية البحث، يرجى البحث مرة أخرى.", call.message.chat.id, call.message.message_id)
                    logger.warning(f"Search expired for chat_id {call.message.chat.id}")
                    return

                if search_type == "normal":
                    categories = get_categories_tree()
                    keyboard = InlineKeyboardMarkup(row_width=1)
                    keyboard.add(InlineKeyboardButton("بحث في كل التصنيفات", callback_data=f"search_scope::all::0"))
                    for cat in categories:
                        keyboard.add(InlineKeyboardButton(f"بحث في: {cat['name']}", callback_data=f"search_scope::{cat['id']}::0"))
                    bot.edit_message_text(f"أين تريد البحث عن \"{query_data['query']}\"؟", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif search_type == "advanced":
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    keyboard.add(
                        InlineKeyboardButton("الجودة", callback_data="adv_filter::quality"),
                        InlineKeyboardButton("الحالة", callback_data="adv_filter::status")
                    )
                    keyboard.add(InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))
                    bot.edit_message_text("اختر فلتر للبحث المتقدم:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                bot.answer_callback_query(call.id)

            elif action == "adv_filter":
                filter_type = data[1]
                if filter_type == "quality":
                    keyboard = InlineKeyboardMarkup(row_width=3)
                    qualities = ["1080p", "720p", "480p", "360p"]
                    buttons = [InlineKeyboardButton(q, callback_data=f"adv_search::quality::{q}::0") for q in qualities]
                    keyboard.add(*buttons)
                    keyboard.add(InlineKeyboardButton("🔙 رجوع للفلاتر", callback_data="search_type::advanced"))
                    bot.edit_message_text("اختر الجودة:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                elif filter_type == "status":
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    statuses = ["مترجم", "مدبلج"]
                    buttons = [InlineKeyboardButton(s, callback_data=f"adv_search::status::{s}::0") for s in statuses]
                    keyboard.add(*buttons)
                    keyboard.add(InlineKeyboardButton("🔙 رجوع للفلاتر", callback_data="search_type::advanced"))
                    bot.edit_message_text("اختر الحالة:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                bot.answer_callback_query(call.id)

            elif action == "adv_search":
                _, filter_type, filter_value, page_str = data
                page = int(page_str)
                query_data = helpers.user_last_search.get(call.message.chat.id)
                if not query_data or 'query' not in query_data:
                    bot.edit_message_text("انتهت صلاحية البحث، يرجى البحث مرة أخرى.", call.message.chat.id, call.message.message_id)
                    logger.warning(f"Advanced search expired for chat_id {call.message.chat.id}")
                    return

                query = query_data['query']
                kwargs = {'query': query, 'page': page}
                if filter_type == 'quality': kwargs['quality'] = filter_value
                elif filter_type == 'status': kwargs['status'] = filter_value

                videos, total_count = search_videos(**kwargs)
                logger.info(f"Advanced search: query={query}, filter={filter_type}:{filter_value}, page={page}, videos_count={len(videos)}, total={total_count}")

                if not videos:
                    bot.edit_message_text(f"لا توجد نتائج للبحث المتقدم.", call.message.chat.id, call.message.message_id)
                else:
                    keyboard = helpers.create_paginated_keyboard(videos, total_count, page, "adv_search", f"{filter_type}::{filter_value}")
                    text = f"نتائج البحث عن \"{query}\" ({total_count} نتيجة) - صفحة {page + 1}"
                    try:
                        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                    except telebot.apihelper.ApiTelegramException as e:
                        logger.error(f"Error editing message in adv_search: {e}")
                        bot.answer_callback_query(call.id, "حدث خطأ في تحديث الصفحة، حاول مرة أخرى.", show_alert=True)
                bot.answer_callback_query(call.id)

            elif action == "search_scope":
                _, scope, page_str = data
                page = int(page_str)
                query_data = helpers.user_last_search.get(call.message.chat.id)
                if not query_data or 'query' not in query_data:
                    bot.edit_message_text("انتهت صلاحية البحث، يرجى البحث مرة أخرى.", call.message.chat.id, call.message.message_id)
                    logger.warning(f"Search scope expired for chat_id {call.message.chat.id}, scope={scope}, page={page}")
                    return
                query = query_data['query']
                category_id = None if scope == "all" else int(scope)
                videos, total_count = search_videos(query=query, page=page, category_id=category_id)
                logger.info(f"Search scope: query={query}, scope={scope}, page={page}, videos_count={len(videos)}, total={total_count}")

                if not videos:
                    bot.edit_message_text(f"لم يتم العثور على نتائج للبحث عن \"{query}\".", call.message.chat.id, call.message.message_id)
                else:
                    keyboard = helpers.create_paginated_keyboard(videos, total_count, page, "search_all" if scope == "all" else "search_cat", scope)
                    text = f"نتائج البحث عن \"{query}\" ({total_count} نتيجة) - صفحة {page + 1}"
                    try:
                        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                    except telebot.apihelper.ApiTelegramException as e:
                        logger.error(f"Error editing message in search_scope: {e}")
                        bot.answer_callback_query(call.id, "حدث خطأ في تحديث الصفحة، حاول مرة أخرى.", show_alert=True)
                bot.answer_callback_query(call.id)

            elif action == "check_subscription":
                is_subscribed, _ = helpers.check_subscription(bot, user_id)
                if is_subscribed:
                    bot.answer_callback_query(call.id, "✅ شكراً لاشتراكك!")
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, "أهلاً بك في بوت البحث عن الفيديوهات!", reply_markup=helpers.main_menu())
                else:
                    bot.answer_callback_query(call.id, "❌ لم تشترك في جميع القنوات بعد.", show_alert=True)

            elif action == "popular":
                sub_action = data[1]
                popular_data = get_popular_videos()
                videos = popular_data.get(sub_action, [])
                title = "📈 الفيديوهات الأكثر مشاهدة:" if sub_action == "most_viewed" else "⭐ الفيديوهات الأعلى تقييماً:"
                if videos:
                    keyboard = helpers.create_paginated_keyboard(videos, len(videos), 0, "popular_page", sub_action)
                    bot.edit_message_text(title, call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                else:
                    bot.edit_message_text("لا توجد فيديوهات كافية لعرضها حالياً.", call.message.chat.id, call.message.message_id)
                bot.answer_callback_query(call.id)

            elif action == "back_to_cats":
                helpers.list_videos(bot, call.message, edit_message=call.message)
                bot.answer_callback_query(call.id)

            elif action == "back_to_main":
                helpers.list_videos(bot, call.message, edit_message=call.message)
                bot.answer_callback_query(call.id)

            elif action == "video":
                _, video_id, message_id, chat_id = data
                increment_video_view_count(int(video_id))
                try:
                    bot.copy_message(call.message.chat.id, chat_id, int(message_id))
                    rating_keyboard = helpers.create_video_action_keyboard(int(video_id), user_id)
                    bot.send_message(call.message.chat.id, "قيم هذا الفيديو:", reply_markup=rating_keyboard)
                    bot.answer_callback_query(call.id, "جاري إرسال الفيديو...")
                except Exception as e:
                    logger.error(f"Error handling video callback: {e}", exc_info=True)
                    bot.answer_callback_query(call.id, "خطأ: الفيديو غير موجود بالقناة.", show_alert=True)

            elif action == "rate":
                _, video_id, rating = data
                if add_video_rating(int(video_id), user_id, int(rating)):
                    new_keyboard = helpers.create_video_action_keyboard(int(video_id), user_id)
                    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=new_keyboard)
                    bot.answer_callback_query(call.id, f"تم تقييم الفيديو بـ {rating} نجوم!")
                else:
                    bot.answer_callback_query(call.id, "حدث خطأ في التقييم.")

            elif action == "cat":
                _, category_id_str, page_str = data
                category_id, page = int(category_id_str), int(page_str)
                child_categories = get_child_categories(category_id)
                videos, total_count = get_videos(category_id, page)
                category = get_category_by_id(category_id)
                if not child_categories and not videos:
                    bot.edit_message_text(f"التصنيف \"{category['name']}\" فارغ حالياً.", call.message.chat.id, call.message.message_id,
                                         reply_markup=helpers.create_combined_keyboard([], [], 0, 0, category_id))
                else:
                    keyboard = helpers.create_combined_keyboard(child_categories, videos, total_count, page, category_id)
                    bot.edit_message_text(f"محتويات تصنيف \"{category['name']}\":", call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                bot.answer_callback_query(call.id)

            elif action == "noop":
                bot.answer_callback_query(call.id)

        except Exception as e:
            logger.error(f"Callback query error: {e}", exc_info=True)
            try:
                bot.answer_callback_query(call.id, "حدث خطأ. حاول مرة أخرى.", show_alert=True)
            except Exception as e_inner:
                logger.error(f"Could not even answer callback query: {e_inner}")
