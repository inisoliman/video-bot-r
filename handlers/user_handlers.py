# handlers/user_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging

from db_manager import (
    add_bot_user, get_popular_videos, search_videos,
    get_random_video, increment_video_view_count, get_categories_tree, add_video,
    get_active_category_id, get_user_favorites, get_user_history, add_to_history,
    get_user_state, clear_user_state  # إضافة دوال الحالة
)
from .helpers import (
    main_menu, create_paginated_keyboard,
    create_video_action_keyboard, user_last_search, generate_grouping_key,
    check_subscription, list_videos
)
from . import comment_handlers  # إضافة معالجات التعليقات
from utils import extract_video_metadata
from state_manager import (
    set_user_waiting_for_input, States, get_user_waiting_context, 
    clear_user_waiting_state, state_handler 
)

logger = logging.getLogger(__name__)

def register(bot, channel_id, admin_ids):

    # --- معالج حالة البحث (State Handler) ---
    @state_handler(States.WAITING_SEARCH_QUERY)
    def handle_search_query_state(message, bot, context):
        """
        يتلقى كلمة البحث من المستخدم بعد ضغطه على زر '🔍 بحث'
        ويحولها إلى معالج البحث المباشر.
        """
        if message.text == "/cancel":
            clear_user_waiting_state(message.from_user.id)
            bot.reply_to(message, "تم إلغاء عملية البحث.")
            return
        
        # نرسل الرسالة إلى معالج البحث المباشر (الذي سيبدأ عملية البحث)
        # ونقوم بمسح الحالة مباشرة لكي لا يتعارض مع الأوامر الأخرى
        clear_user_waiting_state(message.from_user.id)
        handle_private_text_search_direct(message, bot)

    @bot.message_handler(commands=["start"])
    def start(message):
        # إضافة المستخدم إلى قاعدة البيانات
        add_bot_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        
        # التحقق من الاشتراك في القنوات المطلوبة
        is_subscribed, unsub_channels = check_subscription(bot, message.from_user.id)
        
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
        bot_info = bot.get_me()
        welcome_text = (
            "🎬 أهلاً بك في بوت البحث عن الفيديوهات!\n\n"
            "يمكنك الآن:\n"
            "• 🎬 عرض كل الفيديوهات\n"
            "• 🔥 مشاهدة الفيديوهات الشائعة\n"
            "• 🍿 الحصول على اقتراح عشوائي\n"
            "• 🔍 البحث عن فيديوهات معينة\n\n"
            f"🔍 *بحث سريع في أي محادثة:*\n"
            f"اكتب: `@{bot_info.username} كلمة البحث`\n\n"
            "استمتع بوقتك! 😊"
        )
        
        # إضافة زر switch inline للبحث السريع
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(
            "🔍 ابحث الآن في أي محادثة",
            switch_inline_query_current_chat=""
        ))
        
        bot.reply_to(message, welcome_text, reply_markup=markup, parse_mode="Markdown")

    @bot.message_handler(commands=["myid"])
    def get_my_id(message):
        bot.reply_to(message, f"معرف حسابك هو: `{message.from_user.id}`", parse_mode="Markdown")

    @bot.message_handler(func=lambda message: message.text == "🎬 عرض كل الفيديوهات")
    def handle_list_videos_button(message):
        list_videos(bot, message)
        
    @bot.message_handler(func=lambda message: message.text == "⭐ المفضلة") 
    def handle_favorites_button(message):
        bot.send_chat_action(message.chat.id, 'typing')
        videos, total_count = get_user_favorites(message.from_user.id, page=0)
        if not videos:
            bot.reply_to(message, "لا توجد فيديوهات في قائمتك المفضلة حالياً.")
            return
        keyboard = create_paginated_keyboard(videos, total_count, 0, "fav_page", "user_fav")
        bot.reply_to(message, f"قائمة مفضلاتك ({total_count} فيديو):", reply_markup=keyboard)
        
    @bot.message_handler(func=lambda message: message.text == "📺 سجل المشاهدة") 
    def handle_history_button(message):
        bot.send_chat_action(message.chat.id, 'typing')
        videos, total_count = get_user_history(message.from_user.id, page=0)
        if not videos:
            bot.reply_to(message, "سجل المشاهدة الخاص بك فارغ حالياً.")
            return
        keyboard = create_paginated_keyboard(videos, total_count, 0, "history_page", "user_history")
        bot.reply_to(message, f"سجل مشاهداتك ({total_count} فيديو):", reply_markup=keyboard)


    @bot.message_handler(func=lambda message: message.text == "🔥 الفيديوهات الشائعة")
    def handle_popular_videos_button(message):
        show_popular_videos(message)

    @bot.message_handler(func=lambda message: message.text == "🔍 بحث")
    def handle_search_button(message):
        # [تعديل] إبقاء الحالة لـ "البحث" فقط في حال عدم إرسال كلمة مباشرة
        set_user_waiting_for_input(message.from_user.id, States.WAITING_SEARCH_QUERY)
        bot.reply_to(message, "أرسل الكلمة المفتاحية للبحث عن الفيديوهات:")

    @bot.message_handler(func=lambda message: message.text == "🍿 اقترح لي فيلم")
    def handle_random_suggestion(message):
        bot.send_chat_action(message.chat.id, 'typing')
        video = get_random_video()
        if video:
            try:
                video_id = video['id']
                increment_video_view_count(video_id)
                add_to_history(message.from_user.id, video_id) # تتبع المشاهدة

                bot.copy_message(message.chat.id, video['chat_id'], video['message_id'])
                rating_keyboard = create_video_action_keyboard(video_id, message.from_user.id)
                bot.send_message(message.chat.id, "ما رأيك بهذا الفيديو؟ يمكنك تقييمه:", reply_markup=rating_keyboard)
            except Exception as e:
                logger.error(f"Error sending random video {video['id']}: {e}")
                bot.reply_to(message, "عذراً، حدث خطأ أثناء محاولة إرسال هذا الفيديو.")
        else:
            bot.reply_to(message, "لا توجد فيديوهات في قاعدة البيانات حالياً.")

    # --- [جديد] دالة مساعدة للبحث المباشر ---
    def handle_private_text_search_direct(message, bot):
        query = message.text.strip()
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
            set_user_waiting_for_input(message.from_user.id, States.WAITING_SEARCH_QUERY)
            msg = bot.reply_to(message, "أرسل الكلمة المفتاحية للبحث:")
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

    # --- معالجات التعليقات ---
    @bot.message_handler(commands=["my_comments"])
    def handle_my_comments_command(message):
        """عرض تعليقات المستخدم"""
        comment_handlers.show_user_comments(bot, message, page=0)
    
    @bot.message_handler(commands=["comments"])
    def handle_admin_comments_command(message):
        """عرض جميع التعليقات للأدمن"""
        if message.from_user.id not in admin_ids:
            bot.reply_to(message, "⛔ هذا الأمر للإدارة فقط")
            return
        comment_handlers.show_all_comments(bot, message.from_user.id, admin_ids, page=0, unread_only=False)
    
    @bot.message_handler(commands=["delete_all_comments"])
    def handle_delete_all_comments_command(message):
        """حذف جميع التعليقات (أدمن فقط)"""
        comment_handlers.handle_delete_all_comments(bot, message.from_user.id, admin_ids)
    
    @bot.message_handler(commands=["delete_user_comments"])
    def handle_delete_user_comments_command(message):
        """حذف تعليقات مستخدم معين (أدمن فقط)"""
        comment_handlers.handle_delete_user_comments(bot, message.from_user.id, admin_ids, message.text)
    
    @bot.message_handler(commands=["delete_old_comments"])
    def handle_delete_old_comments_command(message):
        """حذف التعليقات القديمة (أدمن فقط)"""
        comment_handlers.handle_delete_old_comments(bot, message.from_user.id, admin_ids, message.text)
    
    @bot.message_handler(commands=["comments_stats"])
    def handle_comments_stats_command(message):
        """عرض إحصائيات التعليقات (أدمن فقط)"""
        comment_handlers.handle_comments_stats(bot, message.from_user.id, admin_ids)
    
    @bot.message_handler(commands=["cancel"])
    def handle_cancel_command(message):
        """إلغاء العملية الحالية"""
        clear_user_state(message.from_user.id)
        clear_user_waiting_state(message.from_user.id)
        bot.reply_to(message, "✅ تم إلغاء العملية")
    
    # معالج النصوص للتعليقات والردود
    @bot.message_handler(func=lambda message: message.text and not message.text.startswith("/") and message.chat.type == "private", content_types=["text"])
    def handle_comment_text_states(message):
        """معالج النصوص للتعليقات والردود"""
        # التحقق من حالة المستخدم
        state = get_user_state(message.from_user.id)
        
        if state:
            state_name = state.get('state')
            
            # معالجة التعليق
            if state_name == 'waiting_comment':
                comment_handlers.process_comment_text(bot, message)
                return
            
            # معالجة الرد (للأدمن)
            elif state_name == 'replying_comment':
                if message.from_user.id in admin_ids:
                    comment_handlers.process_reply_text(bot, message, admin_ids)
                    return
        
        # إذا لم تكن هناك حالة، استخدم معالج البحث الافتراضي
        handle_private_text_search_direct(message, bot)

    @bot.message_handler(content_types=["video", "document"])
    def handle_new_video(message):
        """معالج الفيديوهات الجديدة المضافة للقناة (يدعم الفيديو والملفات)"""
        if str(message.chat.id) != channel_id:
            return

        active_category_id = get_active_category_id()
        if not active_category_id:
            logger.warning(f"No active category set. Message {message.message_id} will not be saved.")
            return

        # تحديد ما إذا كان المحتوى فيديو أو ملفاً
        content_obj = message.video or message.document
        if not content_obj:
            return

        # إذا كان ملفاً، سنتأكد أنه فيديو (اختياري، يمكننا قبول أي ملف كفيديو)
        content_type = 'VIDEO' if message.video else 'DOCUMENT'
        
        file_name = getattr(content_obj, 'file_name', f"video_{message.message_id}.mp4")
        file_id = content_obj.file_id
        
        # استخراج الميتا داتا من الكابشن
        metadata = extract_video_metadata(message.caption)
        if message.video:
            metadata['duration'] = message.video.duration
            if 'quality_resolution' not in metadata and message.video.height:
                metadata['quality_resolution'] = f"{message.video.height}p"

        thumbnail_file_id = None
        
        # محاولة استخراج الصورة المصغرة والبيانات الكاملة عبر تمرير الرسالة للأدمن
        # (لأن تليجرام أحياناً لا يرسل thumbnail_file_id في رسائل القنوات للبوتات)
        try:
            admin_id = admin_ids[0] if admin_ids else None
            if admin_id:
                forwarded = bot.forward_message(admin_id, message.chat.id, message.message_id)
                
                # استخراج البيانات من الرسالة الموجهة
                if forwarded.video:
                    content_type = 'VIDEO'
                    file_id = forwarded.video.file_id # استخدام file_id المحدث
                    if forwarded.video.thumb:
                        thumbnail_file_id = forwarded.video.thumb.file_id
                elif forwarded.document:
                    file_id = forwarded.document.file_id
                    if forwarded.document.thumb:
                        thumbnail_file_id = forwarded.document.thumb.file_id
                
                # حذف الرسالة الموجهة
                try:
                    bot.delete_message(admin_id, forwarded.message_id)
                except:
                    pass
        except Exception as e:
            logger.warning(f"Could not refine metadata via forward for message {message.message_id}: {e}")
            # إذا فشل التمرير، نحاول استخدام البيانات المتوفرة مباشرة
            if content_obj and hasattr(content_obj, 'thumb') and content_obj.thumb:
                thumbnail_file_id = content_obj.thumb.file_id

        # إنشاء مفتاح التجميع
        grouping_key = generate_grouping_key(metadata, message.caption, file_name)
        
        # إضافة الفيديو لقاعدة البيانات
        video_db_id = add_video(
            message_id=message.message_id, 
            caption=message.caption, 
            chat_id=message.chat.id,
            file_name=file_name, 
            file_id=file_id, 
            metadata=metadata,
            grouping_key=grouping_key, 
            category_id=active_category_id,
            thumbnail_file_id=thumbnail_file_id,
            content_type=content_type
        )
        
        if video_db_id:
            logger.info(f"✅ Indexed {content_type} {message.message_id} (DB ID: {video_db_id}) with thumb: {thumbnail_file_id is not None}")
        else:
            logger.error(f"❌ Failed to index message {message.message_id}")
