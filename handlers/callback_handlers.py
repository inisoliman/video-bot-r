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
                bot.answer_callback_query(call.id) # [إضافة] رد فوري
                search_type = data[1]
                query_data = helpers.user_last_search.get(call.message.chat.id)
                if not query_data or 'query' not in query_data:
                    bot.edit_message_text("انتهت صلاحية البحث، يرجى البحث مرة أخرى.", call.message.chat.id, call.message.message_id)
                    return

                if search_type == "normal":
                    # [تعديل] يجب جلب كل التصنيفات وليس فقط الرئيسية لإتاحة البحث في الفرعية
                    # سنعتمد على دالة get_categories_tree لاسترجاع الأباء ثم جلب الأبناء
                    categories_parents = get_categories_tree()
                    all_categories = []
                    
                    for parent in categories_parents:
                        all_categories.append(parent)
                        children = get_child_categories(parent['id'])
                        for child in children:
                            child['name'] = f"- {child['name']}" # إضافة علامة للفرعي
                            all_categories.append(child)

                    keyboard = InlineKeyboardMarkup(row_width=1)
                    keyboard.add(InlineKeyboardButton("بحث في كل التصنيفات", callback_data=f"search_scope::all::0"))
                    
                    for cat in all_categories:
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

            elif action == "adv_filter":
                bot.answer_callback_query(call.id) # [إضافة] رد فوري
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

            elif action == "adv_search":
                bot.answer_callback_query(call.id) # [إضافة] رد فوري
                _, filter_type, filter_value, page_str = data
                page = int(page_str)
                query_data = helpers.user_last_search.get(call.message.chat.id)
                if not query_data or 'query' not in query_data:
                    bot.edit_message_text("انتهت صلاحية البحث، يرجى البحث مرة أخرى.", call.message.chat.id, call.message.message_id)
                    return

                query = query_data['query']
                kwargs = {'query': query, 'page': page}
                if filter_type == 'quality': kwargs['quality'] = filter_value
                elif filter_type == 'status': kwargs['status'] = filter_value

                videos, total_count = search_videos(**kwargs)

                if not videos:
                    bot.edit_message_text(f"لا توجد نتائج للبحث المتقدم.", call.message.chat.id, call.message.message_id)
                    return

                action_prefix = f"adv_search::{filter_type}"
                context_id = filter_value
                keyboard = helpers.create_paginated_keyboard(videos, total_count, page, action_prefix, context_id)
                bot.edit_message_text(f"نتائج البحث المتقدم:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

            elif action == "search_scope":
                bot.answer_callback_query(call.id) # [إضافة] رد فوري
                _, scope, page_str = data
                page = int(page_str)
                query_data = helpers.user_last_search.get(call.message.chat.id)
                if not query_data or 'query' not in query_data:
                    bot.edit_message_text("انتهت صلاحية البحث، يرجى البحث مرة أخرى.", call.message.chat.id, call.message.message_id)
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
                bot.answer_callback_query(call.id) # [تعديل] تم رفعه ليكون في هذا المستوى

                if sub_action == "add_new_cat":
                    keyboard = InlineKeyboardMarkup()
                    keyboard.add(InlineKeyboardButton("تصنيف رئيسي جديد", callback_data="admin::add_cat_main"))
                    keyboard.add(InlineKeyboardButton("تصنيف فرعي", callback_data="admin::add_cat_sub_select_parent"))
                    bot.edit_message_text("اختر نوع التصنيف الذي تريد إضافته:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "add_cat_main":
                    helpers.admin_steps[call.message.chat.id] = {"parent_id": None}
                    msg = bot.send_message(call.message.chat.id, "أرسل اسم التصنيف الرئيسي الجديد. (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_add_new_category, bot)

                elif sub_action == "add_cat_sub_select_parent":
                    # [الإصلاح] يجب أن نستخدم دالة get_categories_tree للحصول على التصنيفات الرئيسية المتاحة كآباء
                    categories = get_categories_tree()
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    
                    if not categories:
                        bot.send_message(call.message.chat.id, "❌ يجب إنشاء تصنيف رئيسي أولاً ليكون أبًا.")
                        return

                    for cat in categories:
                         # يجب التأكد من أن التصنيف الذي تختاره هو فقط التصنيف الأب (بدون ترقيم الصفحات)
                        keyboard.add(InlineKeyboardButton(cat['name'], callback_data=f"admin::add_cat_sub_set_parent::{cat['id']}"))
                    
                    bot.edit_message_text("اختر التصنيف الأب:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "add_cat_sub_set_parent":
                    parent_id = int(data[2])
                    helpers.admin_steps[call.message.chat.id] = {"parent_id": parent_id}
                    msg = bot.send_message(call.message.chat.id, "الآن أرسل اسم التصنيف الفرعي الجديد. (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_add_new_category, bot)

                elif sub_action == "delete_category_select":
                    keyboard = helpers.create_categories_keyboard()
                    if not keyboard.keyboard:
                        bot.send_message(call.message.chat.id, "لا توجد تصنيفات لحذفها.")
                        return
                    for row in keyboard.keyboard:
                        for button in row:
                            parts = button.callback_data.split(helpers.CALLBACK_DELIMITER)
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
                    delete_category_by_id(old_category_id)
                    new_cat = get_category_by_id(new_category_id)
                    bot.edit_message_text(f"✅ تم نقل الفيديوهات إلى \"{new_cat['name']}\" وحذف التصنيف \"{category_to_delete['name']}\".", call.message.chat.id, call.message.message_id)

                elif sub_action == "cancel_delete_cat":
                    bot.edit_message_text("👍 تم إلغاء عملية حذف التصنيف.", call.message.chat.id, call.message.message_id)

                elif sub_action == "move_video_by_id":
                    msg = bot.send_message(call.message.chat.id, "أرسل رقم الفيديو (ID) الذي تريد نقله. (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_move_by_id_input, bot)

                elif sub_action == "delete_videos_by_ids":
                    msg = bot.send_message(call.message.chat.id, "أرسل أرقام الفيديوهات (IDs) التي تريد حذفها، مفصولة بمسافة أو فاصلة. (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_delete_by_ids_input, bot)

                elif sub_action == "move_confirm":
                    _, video_id, new_category_id = data
                    move_video_to_category(int(video_id), int(new_category_id))
                    category = get_category_by_id(int(new_category_id))
                    bot.edit_message_text(f"✅ تم نقل الفيديو بنجاح إلى تصنيف \"{category['name']}\".", call.message.chat.id, call.message.message_id)

                elif sub_action == "update_metadata":
                    msg = bot.edit_message_text("تم إرسال طلب تحديث البيانات...", call.message.chat.id, call.message.message_id)
                    update_thread = threading.Thread(target=run_update_and_report_progress, args=(bot, msg.chat.id, msg.message_id))
                    update_thread.start()

                elif sub_action == "set_active":
                    # [تعديل] يجب جلب كل التصنيفات وليس فقط الرئيسية
                    categories_parents = get_categories_tree()
                    all_categories = []
                    
                    for parent in categories_parents:
                        all_categories.append(parent)
                        children = get_child_categories(parent['id'])
                        for child in children:
                            child['name'] = f"- {child['name']}" # إضافة علامة للفرعي
                            all_categories.append(child)

                    if not all_categories:
                        bot.send_message(call.message.chat.id, "لا توجد تصنيفات حالياً.")
                        return
                    
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    buttons = [InlineKeyboardButton(text=cat['name'], callback_data=f"admin::setcat::{cat['id']}") for cat in all_categories]
                    keyboard.add(*buttons)
                    bot.edit_message_text("اختر التصنيف الذي تريد تفعيله:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "setcat":
                    category_id = int(data[2])
                    if set_active_category_id(category_id):
                        category = get_category_by_id(category_id)
                        bot.edit_message_text(f"✅ تم تفعيل التصنيف \"{category['name']}\" بنجاح.", call.message.chat.id, call.message.message_id)

                elif sub_action == "add_channel":
                    msg = bot.send_message(call.message.chat.id, "أرسل معرف القناة (مثال: -1001234567890 أو @username). (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_add_channel_step1, bot)

                elif sub_action == "remove_channel":
                    msg = bot.send_message(call.message.chat.id, "أرسل معرف القناة التي تريد إزالتها. (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_remove_channel_step, bot)

                elif sub_action == "list_channels":
                    admin_handlers.handle_list_channels(call.message, bot)

                elif sub_action == "broadcast":
                    msg = bot.send_message(call.message.chat.id, "أرسل الرسالة التي تريد بثها. (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_rich_broadcast, bot)

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
                    if popular and popular.get("most_viewed") and popular["most_viewed"][0]:
                        most_viewed = popular["most_viewed"][0]
                        title = (most_viewed['caption'] or "").split('\n')[0] or "فيديو"
                        stats_text += f"\n\n🔥 الأكثر مشاهدة: {title} ({most_viewed['view_count']} مشاهدة)"
                    if popular and popular.get("highest_rated") and popular["highest_rated"][0] and popular["highest_rated"][0].get('avg_rating') is not None:
                        highest_rated = popular["highest_rated"][0]
                        title = (highest_rated['caption'] or "").split('\n')[0] or "فيديو"
                        stats_text += f"\n⭐ الأعلى تقييماً: {title} ({highest_rated['avg_rating']:.1f}/5)"
                    bot.send_message(call.message.chat.id, stats_text, parse_mode="Markdown")

            elif action == "check_subscription":
                is_subscribed, _ = helpers.check_subscription(bot, user_id)
                if is_subscribed:
                    bot.answer_callback_query(call.id, "✅ شكراً لاشتراكك!")
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, "أهلاً بك في بوت البحث عن الفيديوهات!", reply_markup=helpers.main_menu())
                else:
                    bot.answer_callback_query(call.id, "❌ لم تشترك في جميع القنوات بعد.", show_alert=True)

            elif action == "popular":
                bot.answer_callback_query(call.id) # [إضافة] رد فوري
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
                bot.answer_callback_query(call.id) # [إضافة] رد فوري
                helpers.list_videos(bot, call.message, edit_message=call.message)

            elif action == "back_to_main":
                bot.answer_callback_query(call.id) # [إضافة] رد فوري
                bot.delete_message(call.message.chat.id, call.message.message_id)
                bot.send_message(call.message.chat.id, "القائمة الرئيسية:", reply_markup=helpers.main_menu())

            elif action == "video":
                _, video_id, message_id, chat_id = data
                bot.answer_callback_query(call.id, "جاري إرسال الفيديو...") # [تعديل] تحويله لرد فوري مع رسالة
                increment_video_view_count(int(video_id))
                try:
                    bot.copy_message(call.message.chat.id, chat_id, int(message_id))
                    rating_keyboard = helpers.create_video_action_keyboard(int(video_id), user_id)
                    bot.send_message(call.message.chat.id, "قيم هذا الفيديو:", reply_markup=rating_keyboard)
                except Exception as e:
                    logger.error(f"Error handling video callback: {e}", exc_info=True)
                    bot.send_message(call.message.chat.id, "خطأ: الفيديو غير موجود بالقناة.")

            elif action == "rate":
                _, video_id, rating = data
                if add_video_rating(int(video_id), user_id, int(rating)):
                    new_keyboard = helpers.create_video_action_keyboard(int(video_id), user_id)
                    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=new_keyboard)
                    bot.answer_callback_query(call.id, f"تم تقييم الفيديو بـ {rating} نجوم!")
                else:
                    bot.answer_callback_query(call.id, "حدث خطأ في التقييم.")

            elif action == "cat":
                bot.answer_callback_query(call.id) # [إضافة] رد فوري
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

            elif action == "noop":
                bot.answer_callback_query(call.id)

        except Exception as e:
            logger.error(f"Callback query error: {e}", exc_info=True)
            try:
                bot.answer_callback_query(call.id, "حدث خطأ. حاول مرة أخرى.", show_alert=True)
            except Exception as e_inner:
                logger.error(f"Could not even answer callback query: {e_inner}")
