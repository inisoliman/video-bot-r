# handlers/callback_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import threading

from db_manager import *
from . import helpers
from . import admin_handlers
from update_metadata import run_update_and_report_progress
# [تعديل] لاستيراد دوال إدارة الحالة
from state_manager import set_user_waiting_for_input, States 


logger = logging.getLogger(__name__)

def register(bot, admin_ids):

    @bot.callback_query_handler(func=lambda call: True)
    def callback_query(call):
        try:
            bot.answer_callback_query(call.id) # الرد الفوري لحل مشكلة query is too old
            
            user_id = call.from_user.id
            data = call.data.split(helpers.CALLBACK_DELIMITER)
            action = data[0]

            is_subscribed, _ = helpers.check_subscription(bot, user_id)
            if action != "check_subscription" and not is_subscribed:
                bot.answer_callback_query(call.id, "🛑 يجب الاشتراك في القنوات المطلوبة أولاً.", show_alert=True)
                return

            # --- معالجة المفضلة والسجل ---
            if action == "fav":
                _, video_id, is_fav = data
                video_id = int(video_id)
                if is_fav == "True":
                    remove_from_favorites(user_id, video_id)
                    text = "❌ تم إزالة الفيديو من المفضلة."
                else:
                    add_to_favorites(user_id, video_id)
                    text = "⭐ تم إضافة الفيديو إلى المفضلة بنجاح!"
                
                # تحديث لوحة المفاتيح بعد الإضافة/الإزالة
                new_keyboard = helpers.create_video_action_keyboard(video_id, user_id)
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=new_keyboard)
                bot.answer_callback_query(call.id, text)
                return

            elif action in ["fav_page", "history_page"]:
                _, _, page_str = data
                page = int(page_str)
                if action == "fav_page":
                    videos, total_count = get_user_favorites(user_id, page)
                    prefix = "fav_page"
                    title = "قائمة مفضلاتك:"
                else:
                    videos, total_count = get_user_history(user_id, page)
                    prefix = "history_page"
                    title = "سجل مشاهداتك:"
                    
                if not videos:
                    bot.edit_message_text("لا توجد المزيد من النتائج.", call.message.chat.id, call.message.message_id)
                    return
                
                keyboard = helpers.create_paginated_keyboard(videos, total_count, page, prefix, "user_data")
                bot.edit_message_text(title, call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                return

            # --- معالجة البحث ونطاقه ---
            elif action == "search_type":
                search_type = data[1]
                query_data = helpers.user_last_search.get(call.message.chat.id)
                
                # [إصلاح مشكلة البحث] التحقق من وجود الكلمة في الذاكرة
                if not query_data or 'query' not in query_data:
                    # في حال فقدان الكلمة، نطلب من المستخدم بدء البحث من جديد
                    set_user_waiting_for_input(user_id, States.WAITING_SEARCH_QUERY)
                    bot.edit_message_text("❌ انتهت صلاحية البحث أو لم ترسل كلمة البحث. يرجى إرسال الكلمة المفتاحية الآن.", 
                                          call.message.chat.id, call.message.message_id)
                    return
                
                query = query_data['query']

                if search_type == "normal":
                    categories = get_categories_tree()
                    keyboard = InlineKeyboardMarkup(row_width=1)
                    keyboard.add(InlineKeyboardButton("بحث في كل التصنيفات", callback_data=f"search_scope::all::0"))
                    
                    # عرض التصنيفات الرئيسية والفرعية
                    for cat in categories:
                        keyboard.add(InlineKeyboardButton(f"بحث في: {cat['name']}", callback_data=f"search_scope::{cat['id']}::0"))
                        child_cats = get_child_categories(cat['id'])
                        for child in child_cats:
                            keyboard.add(InlineKeyboardButton(f"- {child['name']}", callback_data=f"search_scope::{child['id']}::0"))
                            
                    bot.edit_message_text(f"أين تريد البحث عن \"{query}\"؟", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif search_type == "advanced":
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    keyboard.add(
                        InlineKeyboardButton("الجودة", callback_data="adv_filter::quality"),
                        InlineKeyboardButton("الحالة", callback_data="adv_filter::status")
                    )
                    keyboard.add(InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))
                    bot.edit_message_text("اختر فلتر للبحث المتقدم:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                
            elif action == "adv_filter":
                filter_type = data[1]
                query_data = helpers.user_last_search.get(call.message.chat.id)
                
                if not query_data or 'query' not in query_data: # [إصلاح مشكلة البحث]
                    set_user_waiting_for_input(user_id, States.WAITING_SEARCH_QUERY)
                    bot.edit_message_text("❌ انتهت صلاحية البحث. يرجى إرسال الكلمة المفتاحية الآن.", 
                                          call.message.chat.id, call.message.message_id)
                    return
                
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
                
            elif action == "adv_search":
                _, filter_type, filter_value, page_str = data
                page = int(page_str)
                query_data = helpers.user_last_search.get(call.message.chat.id)
                
                if not query_data or 'query' not in query_data: # [إصلاح مشكلة البحث]
                    set_user_waiting_for_input(user_id, States.WAITING_SEARCH_QUERY)
                    bot.edit_message_text("❌ انتهت صلاحية البحث. يرجى إرسال الكلمة المفتاحية الآن.", 
                                          call.message.chat.id, call.message.message_id)
                    return

                query = query_data['query']
                kwargs = {'query': query, 'page': page}
                if filter_type == 'quality': kwargs['quality'] = filter_value
                elif filter_type == 'status': kwargs['status'] = filter_value

                videos, total_count = search_videos(**kwargs)

                if not videos:
                    bot.edit_message_text(f"لا توجد نتائج للبحث المتقدم عن \"{query}\".", call.message.chat.id, call.message.message_id)
                    return

                action_prefix = f"adv_search::{filter_type}"
                context_id = filter_value
                keyboard = helpers.create_paginated_keyboard(videos, total_count, page, action_prefix, context_id)
                bot.edit_message_text(f"نتائج البحث المتقدم عن \"{query}\":", call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                
            elif action == "search_scope":
                _, scope, page_str = data
                page = int(page_str)
                query_data = helpers.user_last_search.get(call.message.chat.id)
                
                if not query_data or 'query' not in query_data: # [إصلاح مشكلة البحث]
                    set_user_waiting_for_input(user_id, States.WAITING_SEARCH_QUERY)
                    bot.edit_message_text("❌ انتهت صلاحية البحث. يرجى إرسال الكلمة المفتاحية الآن.", 
                                          call.message.chat.id, call.message.message_id)
                    return
                
                query = query_data['query']
                category_id = int(scope) if scope != "all" else None
                videos, total_count = search_videos(query=query, page=page, category_id=category_id)
                if not videos:
                    bot.edit_message_text(f"لا توجد نتائج لـ \"{query}\".", call.message.chat.id, call.message.message_id)
                    return
                prefix = "search_scope"
                keyboard = helpers.create_paginated_keyboard(videos, total_count, page, prefix, scope)
                bot.edit_message_text(f"نتائج البحث عن \"{query}\":", call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                

            elif action == "admin":
                if user_id not in admin_ids:
                    bot.answer_callback_query(call.id, "غير مصرح لك.", show_alert=True)
                    return

                sub_action = data[1]
                # ... (باقي معالجات الأدمن)

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
                

            elif action == "back_to_cats":
                helpers.list_videos(bot, call.message, edit_message=call.message)
                

            elif action == "back_to_main":
                bot.delete_message(call.message.chat.id, call.message.message_id)
                bot.send_message(call.message.chat.id, "القائمة الرئيسية:", reply_markup=helpers.main_menu())
                

            elif action == "video":
                _, video_id, message_id, chat_id = data
                video_id = int(video_id)
                increment_video_view_count(video_id)
                add_to_history(user_id, video_id) # تتبع المشاهدة
                try:
                    bot.copy_message(call.message.chat.id, chat_id, int(message_id))
                    rating_keyboard = helpers.create_video_action_keyboard(video_id, user_id)
                    # يجب أن تكون الرسالة التقييمية مختلفة عن الرسالة الأصلية
                    bot.send_message(call.message.chat.id, "قيم هذا الفيديو:", reply_markup=rating_keyboard)
                    
                except Exception as e:
                    logger.error(f"Error handling video callback: {e}", exc_info=True)
                    bot.answer_callback_query(call.id, "خطأ: الفيديو غير موجود بالقناة.", show_alert=True)

            elif action == "rate":
                _, video_id, rating = data
                video_id = int(video_id)
                if add_video_rating(video_id, user_id, int(rating)):
                    new_keyboard = helpers.create_video_action_keyboard(video_id, user_id)
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
                if not category:
                    bot.edit_message_text("❌ التصنيف غير موجود.", call.message.chat.id, call.message.message_id)
                    return
                if not child_categories and not videos:
                    bot.edit_message_text(f"التصنيف \"{category['name']}\" فارغ حالياً.", call.message.chat.id, call.message.message_id,
                                         reply_markup=helpers.create_combined_keyboard([], [], 0, 0, category_id))
                else:
                    keyboard = helpers.create_combined_keyboard(child_categories, videos, total_count, page, category_id)
                    bot.edit_message_text(f"محتويات تصنيف \"{category['name']}\":", call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                
            elif action == "noop":
                pass # لا تفعل شيئاً

        except Exception as e:
            logger.error(f"Callback query error: {e}", exc_info=True)
            try:
                # محاولة الرد على الكولباك لمنع تكرار الأخطاء
                bot.answer_callback_query(call.id, "حدث خطأ غير متوقع. حاول مرة أخرى.", show_alert=True)
            except Exception as e_inner:
                logger.error(f"Could not even answer callback query: {e_inner}")
