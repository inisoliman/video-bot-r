# handlers/callback_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import threading

from db_manager import *
from db_manager import move_videos_bulk  # إضافة الدالة الجديدة بشكل صريح
from . import helpers
from . import admin_handlers
from .helpers import admin_steps, create_hierarchical_category_keyboard  # إضافة استيراد الدالة الجديدة
from update_metadata import run_update_and_report_progress
from state_manager import States

logger = logging.getLogger(__name__)

def register(bot, admin_ids):
    @bot.callback_query_handler(func=lambda call: True)
    def callback_query(call):
        try:
            user_id = call.from_user.id
            data = call.data.split(helpers.CALLBACK_DELIMITER)
            action = data[0]

# ملاحظة: سنجيب على callback_query في كل معالج حسب الحاجة بدلاً من الرد الفوري هنا

            is_subscribed, unsub_channels = helpers.check_subscription(bot, user_id)

            # 2. فرض التحقق قبل تنفيذ أي إجراء
            if action != "check_subscription" and not is_subscribed:
                markup = InlineKeyboardMarkup(row_width=1)
                for channel in unsub_channels:
                    try:
                        link = f"https://t.me/{channel['channel_name']}" if not str(channel['channel_id']).startswith('-100') else f"https://t.me/c/{str(channel['channel_id']).replace('-100', '')}"
                        markup.add(InlineKeyboardButton(f"اشترك في {channel['channel_name']}", url=link))
                    except Exception as e:
                        logger.error(f"Could not create link for channel {channel['channel_id']}: {e}")

                markup.add(InlineKeyboardButton("✅ لقد اشتركت، تحقق الآن", callback_data="check_subscription"))

                try:
                    bot.edit_message_text("🛑 يجب الاشتراك في القنوات التالية أولاً لمتابعة استخدام البوت:",
                                        call.message.chat.id, call.message.message_id, reply_markup=markup)
                except telebot.apihelper.ApiTelegramException as e:
                    bot.send_message(call.message.chat.id, "🛑 يجب الاشتراك في القنوات التالية أولاً لمتابعة استخدام البوت:", reply_markup=markup)

                return

            # --- إذا كان مشتركاً، أكمل تنفيذ الأمر ---
            if action == "check_subscription":
                # إعادة فحص الاشتراك
                is_subscribed, unsub_channels = helpers.check_subscription(bot, user_id)
                
                if is_subscribed:
                    try:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                    except Exception as e:
                        logger.warning(f"Could not delete subscription check message: {e}")
                    
                    welcome_text = (
                        "🎬 أهلاً بك في بوت البحث عن الفيديوهات!\n\n"
                        "يمكنك الآن:\n"
                        "• 🎬 عرض كل الفيديوهات\n"
                        "• 🔥 مشاهدة الفيديوهات الشائعة\n"
                        "• 🍿 الحصول على اقتراح عشوائي\n"
                        "• 🔍 البحث عن فيديوهات معينة\n\n"
                        "استمتع بوقتك! 😊"
                    )
                    bot.send_message(call.message.chat.id, welcome_text, reply_markup=helpers.main_menu())
                else:
                    # إعادة إنشاء رسالة الاشتراك مع القنوات التي لم يشترك فيها المستخدم
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
                    
                    try:
                        bot.edit_message_text(
                            "❌ لم تشترك في جميع القنوات بعد. يرجى الاشتراك في القنوات التالية لاستخدام البوت:",
                            call.message.chat.id,
                            call.message.message_id,
                            reply_markup=markup
                        )
                    except Exception as e:
                        logger.error(f"Error updating subscription message: {e}")
                        bot.answer_callback_query(call.id, "❌ لم تشترك في جميع القنوات بعد.", show_alert=True)
                        
                return

            # --- معالجة المفضلة والسجل ---
            if action == "fav":
                _, action_type, video_id = data
                video_id = int(video_id)

                if action_type == "remove":
                    remove_from_favorites(user_id, video_id)
                    text = "❌ تم إزالة الفيديو من المفضلة."
                else:
                    add_to_favorites(user_id, video_id)
                    text = "⭐ تم إضافة الفيديو إلى المفضلة بنجاح!"

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
                    title = "⭐ قائمة مفضلاتك:"
                else:
                    videos, total_count = get_user_history(user_id, page)
                    prefix = "history_page"
                    title = "📺 سجل مشاهداتك:"

                if not videos:
                    bot.edit_message_text("لا توجد المزيد من النتائج.", call.message.chat.id, call.message.message_id)
                    return

                keyboard = helpers.create_paginated_keyboard(videos, total_count, page, prefix, "user_data")
                bot.edit_message_text(title, call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                return

            # --- معالجة البحث ---
            elif action == "search_type":
                search_type = data[1]
                query_data = helpers.user_last_search.get(call.message.chat.id)

                if not query_data or 'query' not in query_data:
                    bot.edit_message_text("❌ انتهت صلاحية البحث أو لم ترسل كلمة البحث. يرجى إرسال الكلمة المفتاحية الآن.",
                                        call.message.chat.id, call.message.message_id)
                    return

                query = query_data['query']

                if search_type == "normal":
                    # 🌟 استخدام الكيبورد الهرمي الجديد للبحث
                    keyboard = InlineKeyboardMarkup(row_width=1)
                    keyboard.add(InlineKeyboardButton("🔍 بحث في كل التصنيفات", callback_data=f"search_scope::all::0"))
                    
                    # استخدام الدالة الهرمية لعرض التصنيفات
                    categories = get_categories_tree()
                    tree = helpers.build_category_tree(categories)
                    
                    for cat in tree:
                        keyboard.add(InlineKeyboardButton(
                            f"🔍 {cat['name']}", 
                            callback_data=f"search_scope::{cat['id']}::0"
                        ))

                    bot.edit_message_text(f"🎯 أين تريد البحث عن \"{query}\"؟", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif search_type == "advanced":
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    keyboard.add(
                        InlineKeyboardButton("📺 الجودة", callback_data="adv_filter::quality"),
                        InlineKeyboardButton("🗣️ الحالة", callback_data="adv_filter::status")
                    )
                    keyboard.add(InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))
                    bot.edit_message_text("⚙️ اختر فلتر للبحث المتقدم:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

            elif action == "adv_filter":
                filter_type = data[1]
                query_data = helpers.user_last_search.get(call.message.chat.id)

                if not query_data or 'query' not in query_data:
                    bot.edit_message_text("❌ انتهت صلاحية البحث. يرجى إرسال الكلمة المفتاحية الآن.",
                                        call.message.chat.id, call.message.message_id)
                    return

                if filter_type == "quality":
                    keyboard = InlineKeyboardMarkup(row_width=3)
                    qualities = ["1080p", "720p", "480p", "360p"]
                    buttons = [InlineKeyboardButton(q, callback_data=f"adv_search::quality::{q}::0") for q in qualities]
                    keyboard.add(*buttons)
                    keyboard.add(InlineKeyboardButton("🔙 رجوع للفلاتر", callback_data="search_type::advanced"))
                    bot.edit_message_text("📺 اختر الجودة:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif filter_type == "status":
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    statuses = ["مترجم", "مدبلج"]
                    buttons = [InlineKeyboardButton(s, callback_data=f"adv_search::status::{s}::0") for s in statuses]
                    keyboard.add(*buttons)
                    keyboard.add(InlineKeyboardButton("🔙 رجوع للفلاتر", callback_data="search_type::advanced"))
                    bot.edit_message_text("🗣️ اختر الحالة:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

            elif action == "adv_search":
                _, filter_type, filter_value, page_str = data
                page = int(page_str)
                query_data = helpers.user_last_search.get(call.message.chat.id)

                if not query_data or 'query' not in query_data:
                    bot.edit_message_text("❌ انتهت صلاحية البحث. يرجى إرسال الكلمة المفتاحية الآن.",
                                        call.message.chat.id, call.message.message_id)
                    return

                query = query_data['query']
                kwargs = {'query': query, 'page': page}

                if filter_type == 'quality':
                    kwargs['quality'] = filter_value
                elif filter_type == 'status':
                    kwargs['status'] = filter_value

                videos, total_count = search_videos(**kwargs)

                if not videos:
                    bot.edit_message_text(f"❌ لا توجد نتائج للبحث المتقدم عن \"{query}\".", call.message.chat.id, call.message.message_id)
                    return

                action_prefix = f"adv_search::{filter_type}"
                context_id = filter_value
                keyboard = helpers.create_paginated_keyboard(videos, total_count, page, action_prefix, context_id)
                bot.edit_message_text(f"🔍 نتائج البحث المتقدم عن \"{query}\":", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

            elif action == "search_scope":
                _, scope, page_str = data
                page = int(page_str)
                query_data = helpers.user_last_search.get(call.message.chat.id)

                if not query_data or 'query' not in query_data:
                    bot.edit_message_text("❌ انتهت صلاحية البحث. يرجى إرسال الكلمة المفتاحية الآن.",
                                        call.message.chat.id, call.message.message_id)
                    return

                query = query_data['query']
                category_id = int(scope) if scope != "all" else None
                videos, total_count = search_videos(query=query, page=page, category_id=category_id)

                if not videos:
                    bot.edit_message_text(f"❌ لا توجد نتائج لـ \"{query}\".", call.message.chat.id, call.message.message_id)
                    return

                prefix = "search_scope"
                keyboard = helpers.create_paginated_keyboard(videos, total_count, page, prefix, scope)
                bot.edit_message_text(f"🔍 نتائج البحث عن \"{query}\":", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

            # --- معالجات الأدمن ---
            elif action == "admin":
                if user_id not in admin_ids:
                    bot.answer_callback_query(call.id, "غير مصرح لك.", show_alert=True)
                    return

                sub_action = data[1]

                if sub_action == "add_new_cat":
                    keyboard = InlineKeyboardMarkup()
                    keyboard.add(InlineKeyboardButton("📂 تصنيف رئيسي جديد", callback_data="admin::add_cat_main"))
                    keyboard.add(InlineKeyboardButton("🌿 تصنيف فرعي", callback_data="admin::add_cat_sub_select_parent"))
                    bot.edit_message_text("➕ اختر نوع التصنيف الذي تريد إضافته:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "add_cat_main":
                    helpers.admin_steps[call.message.chat.id] = {"parent_id": None}
                    msg = bot.send_message(call.message.chat.id, "📝 أرسل اسم التصنيف الرئيسي الجديد. (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_add_new_category, bot)

                elif sub_action == "add_cat_sub_select_parent":
                    # 🌟 استخدام الكيبورد الهرمي الجديد
                    move_keyboard = create_hierarchical_category_keyboard("admin::add_cat_sub_set_parent", add_back_button=False)
                    
                    if not move_keyboard.keyboard or len(move_keyboard.keyboard) == 0:
                        bot.answer_callback_query(call.id, "أنشئ تصنيفاً رئيسياً أولاً.", show_alert=True)
                        return

                    move_keyboard.add(InlineKeyboardButton("🔙 إلغاء", callback_data="back_to_main"))
                    bot.edit_message_text("🎯 اختر التصنيف الأب:", call.message.chat.id, call.message.message_id, reply_markup=move_keyboard)

                elif sub_action == "add_cat_sub_set_parent":
                    parent_id = int(data[2])
                    helpers.admin_steps[call.message.chat.id] = {"parent_id": parent_id}
                    parent_cat = get_category_by_id(parent_id)
                    msg = bot.send_message(call.message.chat.id, f"📝 الآن أرسل اسم التصنيف الفرعي الجديد تحت 📂 \"{parent_cat['name']}\". (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_add_new_category, bot)

                elif sub_action == "delete_category_select":
                    # 🌟 استخدام الكيبورد الهرمي الجديد
                    delete_keyboard = create_hierarchical_category_keyboard("admin::delete_category_confirm", add_back_button=False)
                    
                    if not delete_keyboard.keyboard or len(delete_keyboard.keyboard) == 0:
                        bot.answer_callback_query(call.id, "لا توجد تصنيفات لحذفها.", show_alert=True)
                        return

                    delete_keyboard.add(InlineKeyboardButton("🔙 إلغاء", callback_data="back_to_main"))
                    bot.edit_message_text("🗑️ اختر التصنيف الذي تريد حذفه:", call.message.chat.id, call.message.message_id, reply_markup=delete_keyboard)

                elif sub_action == "delete_category_confirm":
                    category_id = int(data[2])
                    category = get_category_by_id(category_id)
                    keyboard = InlineKeyboardMarkup(row_width=1)
                    keyboard.add(InlineKeyboardButton("🗑️ حذف التصنيف مع كل فيديوهاته", callback_data=f"admin::delete_cat_and_videos::{category_id}"))
                    keyboard.add(InlineKeyboardButton("➡️ نقل فيديوهاته لتصنيف آخر", callback_data=f"admin::delete_cat_move_videos_select_dest::{category_id}"))
                    keyboard.add(InlineKeyboardButton("🔙 إلغاء", callback_data="admin::cancel_delete_cat"))
                    bot.edit_message_text(f"⚠️ أنت على وشك حذف \"{category['name']}\". ماذا أفعل بالفيديوهات؟", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "delete_cat_and_videos":
                    category_id = int(data[2])
                    category = get_category_by_id(category_id)
                    delete_category_and_contents(category_id)
                    bot.edit_message_text(f"✅ تم حذف التصنيف \"{category['name']}\" وكل محتوياته.", call.message.chat.id, call.message.message_id)

                elif sub_action == "delete_cat_move_videos_select_dest":
                    old_category_id = int(data[2])
                    all_categories = get_categories_tree()
                    categories = [cat for cat in all_categories if cat['id'] != old_category_id]

                    if not categories:
                        bot.edit_message_text("❌ لا يوجد تصنيف آخر لنقل الفيديوهات إليه.", call.message.chat.id, call.message.message_id)
                        return

                    # 🌟 استخدام الشجرة الهرمية مع استبعاد التصنيف المحذوف
                    tree = helpers.build_category_tree(categories)
                    keyboard = InlineKeyboardMarkup(row_width=1)
                    for cat in tree:
                        keyboard.add(InlineKeyboardButton(cat['name'], callback_data=f"admin::delete_cat_move_videos_confirm::{old_category_id}::{cat['id']}"))
                    keyboard.add(InlineKeyboardButton("🔙 إلغاء", callback_data="admin::cancel_delete_cat"))
                    bot.edit_message_text("🎯 اختر التصنيف الذي ستُنقل إليه الفيديوهات:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

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
                    msg = bot.send_message(call.message.chat.id, "📝 أرسل رقم الفيديو (ID) أو أرقام متعددة مفصولة بمسافة للنقل الجماعي. (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_move_by_id_input, bot)

                elif sub_action == "delete_videos_by_ids":
                    msg = bot.send_message(call.message.chat.id, "📝 أرسل أرقام الفيديوهات (IDs) التي تريد حذفها، مفصولة بمسافة أو فاصلة. (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_delete_by_ids_input, bot)

                elif sub_action == "move_confirm":
                    # [الإصلاح] تصحيح استخراج البيانات
                    # data[0] = "admin"
                    # data[1] = "move_confirm"
                    # data[2] = category_id (التصنيف الجديد)
                    
                    # التحقق من وجود البيانات المطلوبة
                    if len(data) < 3:
                        bot.edit_message_text(
                            "❌ خطأ: بيانات النقل غير كاملة.", 
                            call.message.chat.id, 
                            call.message.message_id
                        )
                        return
                    
                    new_category_id = int(data[2])  # [الإصلاح] تغيير من data[3] إلى data[2]
                    
                    # استرجاع أرقام الفيديوهات من admin_steps
                    step_data = admin_steps.get(call.message.chat.id, {})
                    video_ids = step_data.get("video_ids", [])
                    
                    if not video_ids:
                        bot.edit_message_text(
                            "❌ خطأ: لم يتم العثور على أرقام الفيديوهات.", 
                            call.message.chat.id, 
                            call.message.message_id
                        )
                        return

                    # نقل الفيديوهات (فردي أو جماعي)
                    moved_count = move_videos_bulk(video_ids, new_category_id)
                    category = get_category_by_id(new_category_id)

                    # رسالة مختلفة للنقل الفردي أو الجماعي
                    if len(video_ids) == 1:
                        message_text = f"✅ تم نقل الفيديو رقم {video_ids[0]} بنجاح إلى تصنيف \"{category['name']}\"."
                    else:
                        message_text = (
                            f"✅ تم نقل {moved_count} فيديو بنجاح إلى تصنيف \"{category['name']}\".\n\n"
                            f"📝 الأرقام المنقولة: {', '.join(map(str, video_ids))}"
                        )

                    bot.edit_message_text(message_text, call.message.chat.id, call.message.message_id)
                    
                    # تنظيف البيانات المؤقتة
                    if call.message.chat.id in admin_steps:
                        del admin_steps[call.message.chat.id]

                elif sub_action == "update_metadata":
                    msg = bot.edit_message_text("⏳ تم إرسال طلب تحديث البيانات...", call.message.chat.id, call.message.message_id)
                    update_thread = threading.Thread(target=run_update_and_report_progress, args=(bot, msg.chat.id, msg.message_id))
                    update_thread.start()

                elif sub_action == "set_active":
                    # 🌟 استخدام الكيبورد الهرمي الجديد
                    keyboard = create_hierarchical_category_keyboard("admin::setcat", add_back_button=False)
                    
                    if not keyboard.keyboard or len(keyboard.keyboard) == 0:
                        bot.answer_callback_query(call.id, "لا توجد تصنيفات حالياً.", show_alert=True)
                        return

                    keyboard.add(InlineKeyboardButton("🔙 إلغاء", callback_data="back_to_main"))
                    bot.edit_message_text("🔘 اختر التصنيف الذي تريد تفعيله:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

                elif sub_action == "setcat":
                    category_id = int(data[2])
                    if set_active_category_id(category_id):
                        category = get_category_by_id(category_id)
                        bot.edit_message_text(f"✅ تم تفعيل التصنيف \"{category['name']}\" بنجاح.", call.message.chat.id, call.message.message_id)

                elif sub_action == "add_channel":
                    msg = bot.send_message(call.message.chat.id, "📝 أرسل معرف القناة (مثال: -1001234567890 أو @username). (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_add_channel_step1, bot)

                elif sub_action == "remove_channel":
                    msg = bot.send_message(call.message.chat.id, "📝 أرسل معرف القناة التي تريد إزالتها. (أو /cancel)")
                    bot.register_next_step_handler(msg, admin_handlers.handle_remove_channel_step, bot)

                elif sub_action == "list_channels":
                    admin_handlers.handle_list_channels(call.message, bot)

                elif sub_action == "broadcast":
                    msg = bot.send_message(call.message.chat.id, "📢 أرسل الرسالة التي تريد بثها. (أو /cancel)")
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

                    if popular["most_viewed"]:
                        most_viewed = popular["most_viewed"][0]
                        title = (most_viewed['caption'] or "").split('\n')[0] or "فيديو"
                        stats_text += f"\n\n🔥 الأكثر مشاهدة: {title} ({most_viewed['view_count']} مشاهدة)"

                    if popular["highest_rated"] and popular["highest_rated"][0].get('avg_rating') is not None:
                        highest_rated = popular["highest_rated"][0]
                        title = (highest_rated['caption'] or "").split('\n')[0] or "فيديو"
                        stats_text += f"\n⭐ الأعلى تقييماً: {title} ({highest_rated['avg_rating']:.1f}/5)"

                    bot.send_message(call.message.chat.id, stats_text, parse_mode="Markdown")

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
                    
                    keyboard = helpers.create_paginated_keyboard(page_videos, len(videos), page, "popular_page", sub_action)
                    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                else:
                    bot.edit_message_text("لا توجد فيديوهات كافية لعرضها حالياً.", call.message.chat.id, call.message.message_id)
                

            elif action == "back_to_cats":
                helpers.list_videos(bot, call.message, edit_message=call.message)

            elif action == "back_to_main":
                bot.delete_message(call.message.chat.id, call.message.message_id)
                bot.send_message(call.message.chat.id, "القائمة الرئيسية:", reply_markup=helpers.main_menu())

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
                    
                    # زيادة عداد المشاهدات وإضافة للسجل
                    increment_video_view_count(video_id_int)
                    add_to_history(user_id, video_id_int)
                    
                    # محاولة إرسال الفيديو
                    bot.copy_message(call.message.chat.id, chat_id_int, message_id_int)
                    
                    # إضافة لوحة التقييم
                    rating_keyboard = helpers.create_video_action_keyboard(video_id_int, user_id)
                    bot.send_message(call.message.chat.id, "⭐ قيم هذا الفيديو:", reply_markup=rating_keyboard)
                    
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
                        new_keyboard = helpers.create_video_action_keyboard(video_id_int, user_id)
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
                        return
                    
                    # الحصول على التصنيفات الفرعية والفيديوهات
                    child_categories = get_child_categories(category_id)
                    videos, total_count = get_videos(category_id, page)
                    
                    if not child_categories and not videos:
                        empty_keyboard = helpers.create_combined_keyboard([], [], 0, 0, category_id)
                        bot.edit_message_text(
                            f"📂 التصنيف \"{category['name']}\"\n\n"
                            "هذا التصنيف فارغ حالياً. لا توجد أقسام فرعية أو فيديوهات.",
                            call.message.chat.id, 
                            call.message.message_id,
                            reply_markup=empty_keyboard
                        )
                    else:
                        keyboard = helpers.create_combined_keyboard(child_categories, videos, total_count, page, category_id)
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
                        
                except Exception as e:
                    logger.error(f"Error handling category callback: {e}", exc_info=True)
                    bot.answer_callback_query(call.id, "❌ حدث خطأ أثناء تحميل التصنيف.")
                
            elif action == "noop":
                pass  # لا تفعل شيئاً

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
