from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from ..services import video_service, user_service, category_service
from .helpers import (
    main_menu_keyboard, 
    inline_search_choice_keyboard, 
    paginated_keyboard,
    video_action_keyboard
)
from ..logger import logger


def register(bot: TeleBot, channel_id: int, admin_ids: list):
    @bot.message_handler(commands=['start'])
    def start(message):
        """مرحباً بالمستخدم الجديد وعرض القائمة الرئيسية"""
        user_service.register_user(
            message.from_user.id, 
            message.from_user.username, 
            message.from_user.first_name
        )
        kb = main_menu_keyboard()
        bot.reply_to(message, '🎬 مرحباً! اختر العملية من لوحة الأزرار.', reply_markup=kb)

    @bot.message_handler(func=lambda m: m.text == '🎬 عرض كل الفيديوهات')
    def list_all(message):
        """عرض جميع الفيديوهات"""
        videos, total = video_service.list_videos(page=0)
        if not videos:
            bot.reply_to(message, '🚫 لا توجد فيديوهات حالياً.', reply_markup=main_menu_keyboard())
            return
        
        btns = [
            {
                'id': v['id'], 
                'display_title': v.get('caption', '(بدون عنوان)')[:30],
                'message_id': v.get('message_id', 0), 
                'chat_id': v.get('chat_id', 0)
            } 
            for v in videos[:20]
        ]
        
        kb, nav_kb = paginated_keyboard(btns, total, 0, 'video')
        
        combined_kb = InlineKeyboardMarkup()
        for button in kb.inline_keyboard:
            combined_kb.inline_keyboard.append(button)
        for button in nav_kb.inline_keyboard:
            combined_kb.inline_keyboard.append(button)
        
        bot.reply_to(message, '🎬 تصفح الفيديوهات:', reply_markup=combined_kb)

    @bot.message_handler(func=lambda m: m.text == '🔥 الفيديوهات الشائعة')
    def popular(message):
        """عرض الفيديوهات الأكثر مشاهدة"""
        videos = video_service.top_videos(10)
        if not videos:
            bot.reply_to(message, '🚫 لا توجد فيديوهات شائعة.', reply_markup=main_menu_keyboard())
            return
        
        btns = [
            {
                'id': v['id'], 
                'display_title': f"{v.get('caption','')[:25]} ({v.get('view_count',0)})",
                'message_id': v.get('message_id', 0), 
                'chat_id': v.get('chat_id', 0)
            } 
            for v in videos
        ]
        
        kb, nav_kb = paginated_keyboard(btns, len(videos), 0, 'popular')
        
        combined_kb = InlineKeyboardMarkup()
        for button in kb.inline_keyboard:
            combined_kb.inline_keyboard.append(button)
        for button in nav_kb.inline_keyboard:
            combined_kb.inline_keyboard.append(button)
        
        bot.reply_to(message, '📈 الأكثر مشاهدة:', reply_markup=combined_kb)

    @bot.message_handler(func=lambda m: m.text == '⭐ المفضلة')
    def favorites(message):
        """عرض المفضلة"""
        videos, total = user_service.get_favorites(message.from_user.id, page=0)
        if not videos:
            bot.reply_to(message, '🚫 لا توجد عناصر مفضلة.', reply_markup=main_menu_keyboard())
            return
        
        btns = [
            {
                'id': v['id'], 
                'display_title': v.get('caption', '')[:30],
                'message_id': v.get('message_id', 0), 
                'chat_id': v.get('chat_id', 0)
            } 
            for v in videos[:20]
        ]
        
        kb, nav_kb = paginated_keyboard(btns, total, 0, 'fav')
        
        combined_kb = InlineKeyboardMarkup()
        for button in kb.inline_keyboard:
            combined_kb.inline_keyboard.append(button)
        for button in nav_kb.inline_keyboard:
            combined_kb.inline_keyboard.append(button)
        
        bot.reply_to(message, '⭐ مفضلاتك:', reply_markup=combined_kb)

    @bot.message_handler(func=lambda m: m.text == '📺 سجل المشاهدة')
    def history(message):
        """عرض سجل المشاهدة"""
        videos, total = user_service.get_history(message.from_user.id, page=0)
        if not videos:
            bot.reply_to(message, '🚫 سجل المشاهدة فارغ.', reply_markup=main_menu_keyboard())
            return
        
        btns = [
            {
                'id': v['id'], 
                'display_title': v.get('caption', '')[:30],
                'message_id': v.get('message_id', 0), 
                'chat_id': v.get('chat_id', 0)
            } 
            for v in videos[:20]
        ]
        
        kb, nav_kb = paginated_keyboard(btns, total, 0, 'history')
        
        combined_kb = InlineKeyboardMarkup()
        for button in kb.inline_keyboard:
            combined_kb.inline_keyboard.append(button)
        for button in nav_kb.inline_keyboard:
            combined_kb.inline_keyboard.append(button)
        
        bot.reply_to(message, '📺 سجل المشاهدة:', reply_markup=combined_kb)

    @bot.message_handler(func=lambda m: m.text == '🍿 اقترح لي فيلم')
    def suggest_movie(message):
        """اقتراح فيلم عشوائي"""
        video = video_service.random_video()
        if not video:
            bot.reply_to(message, '🚫 لا توجد فيديوهات للاقتراح.', reply_markup=main_menu_keyboard())
            return
        
        is_fav = user_service.is_favorite(message.from_user.id, video['id'])
        user_rating = user_service.get_user_rating(message.from_user.id, video['id'])
        reply_markup = video_action_keyboard(video['id'], message.from_user.id, is_fav, user_rating)
        
        bot.reply_to(
            message, 
            f"🎬 {video.get('caption', '')}\n\n🆔 ID: {video['id']}",
            reply_markup=reply_markup
        )

    @bot.message_handler(func=lambda m: m.text == '🔍 بحث')
    def search_button(message):
        """زر البحث"""
        bot.reply_to(message, '🔎 اختر نوع البحث:', reply_markup=inline_search_choice_keyboard())

    @bot.message_handler(func=lambda m: m.text and not m.text.startswith('/'))
    def text_search(message):
        """بحث نصي في الفيديوهات"""
        query = message.text.strip()
        videos, total = video_service.search(query, 0)
        if not videos:
            bot.reply_to(message, '❌ لا توجد نتائج.', reply_markup=main_menu_keyboard())
            return
        
        btns = [
            {
                'id': v['id'], 
                'display_title': v.get('caption', '')[:30],
                'message_id': v.get('message_id', 0), 
                'chat_id': v.get('chat_id', 0)
            } 
            for v in videos[:20]
        ]
        
        kb, nav_kb = paginated_keyboard(btns, total, 0, 'search')
        
        combined_kb = InlineKeyboardMarkup()
        for button in kb.inline_keyboard:
            combined_kb.inline_keyboard.append(button)
        for button in nav_kb.inline_keyboard:
            combined_kb.inline_keyboard.append(button)
        
        bot.reply_to(message, f'🔍 نتائج البحث لـ "{query}"', reply_markup=combined_kb)

    @bot.message_handler(content_types=['video'])
    def handle_channel_video(message):
        """معالجة الفيديوهات القادمة من القناة"""
        if message.chat.id != channel_id:
            return
        
        try:
            record = {
                'message_id': message.message_id,
                'chat_id': message.chat.id,
                'file_id': message.video.file_id if message.video else None,
                'caption': message.caption,
                'file_name': message.video.file_name if message.video and message.video.file_name else 'video',
                'category_id': None,
                'metadata_json': '{}',
                'content_type': 'VIDEO'
            }
            video_id = video_service.add_video(record)
            logger.info(f'Added channel video {message.message_id} as id {video_id}')
        except Exception as e:
            logger.error(f'Error adding video: {e}')