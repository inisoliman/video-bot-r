from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup
from ..services import user_service, video_service
from .helpers import (
    video_action_keyboard, 
    paginated_keyboard, 
    main_menu_keyboard,
    rating_keyboard,
    category_keyboard,
    subcategory_keyboard
)
from ..logger import logger


def register(bot: TeleBot, admin_ids: list):
    @bot.callback_query_handler(func=lambda call: True)
    def on_callback(call):
        try:
            # إجابة سريعة على الـ callback
            bot.answer_callback_query(call.id)
            
            data = call.data or ''
            parts = data.split(':')
            action = parts[0]

            # --- المفضلة ---
            if action == 'fav':
                mode = parts[1]
                video_id = int(parts[2])
                user_id = call.from_user.id
                
                if mode == 'a':  # add
                    user_service.add_favorite(user_id, video_id)
                    bot.answer_callback_query(call.id, '⭐ تمت الإضافة إلى المفضلة')
                elif mode == 'r':  # remove
                    user_service.remove_favorite(user_id, video_id)
                    bot.answer_callback_query(call.id, '🗑️ تمت الإزالة من المفضلة')
                
                # تحديث الأزرار
                is_fav = user_service.is_favorite(user_id, video_id)
                user_rating = user_service.get_user_rating(user_id, video_id)
                reply_markup = video_action_keyboard(video_id, user_id, is_fav, user_rating)
                bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id, 
                    message_id=call.message.message_id,
                    reply_markup=reply_markup
                )

            # --- التقييم ---
            elif action == 'rate':
                video_id = int(parts[1])
                user_id = call.from_user.id
                
                # عرض أزرار التقييم
                reply_markup = rating_keyboard(video_id)
                bot.edit_message_text(
                    '⭐ قيّم الفيديو من 1 إلى 5:',
                    call.message.chat.id, 
                    call.message.message_id,
                    reply_markup=reply_markup
                )

            elif action == 'rt':  # rating submit
                video_id = int(parts[1])
                rating = int(parts[2])
                user_id = call.from_user.id
                
                user_service.set_user_rating(user_id, video_id, rating)
                bot.answer_callback_query(call.id, f'⭐ تم التقييم: {rating}/5')
                
                # العودة لأزرار الفيديو
                is_fav = user_service.is_favorite(user_id, video_id)
                reply_markup = video_action_keyboard(video_id, user_id, is_fav, rating)
                bot.edit_message_text(
                    '🎬 تفاصيل الفيديو:',
                    call.message.chat.id, 
                    call.message.message_id,
                    reply_markup=reply_markup
                )

            # --- التعليقات ---
            elif action == 'com':
                video_id = int(parts[1])
                bot.answer_callback_query(call.id, '💬 إرسال تعليقك:')
                # يمكن إضافة حالة للمستخدم هنا
                bot.edit_message_text(
                    '💬 أرسل تعليقك على الفيديو:',
                    call.message.chat.id, 
                    call.message.message_id,
                    reply_markup=video_action_keyboard(video_id, call.from_user.id)
                )

            # --- البحث ---
            elif action == 'st':  # search type
                mode = parts[1]
                if mode == 'n':  # normal
                    bot.edit_message_text(
                        '🔎 أرسل كلمة البحث الآن:',
                        call.message.chat.id, 
                        call.message.message_id
                    )
                elif mode == 'a':  # advanced
                    bot.edit_message_text(
                        '⚙️ البحث المتقدم:\nأرسل: العنوان | الجودة | الحالة',
                        call.message.chat.id, 
                        call.message.message_id
                    )

            # --- التصنيفات ---
            elif action == 'cat':
                category_id = int(parts[1])
                categories = video_service.get_category_videos(category_id)
                items = [{'id': v['id'], 'display_title': v.get('caption', '')[:30]} for v in categories[:20]]
                kb, nav_kb = paginated_keyboard(items, len(categories), 0, 'cat')
                combined_kb = InlineKeyboardMarkup()
                for button in kb.inline_keyboard:
                    combined_kb.inline_keyboard.append(button)
                for button in nav_kb.inline_keyboard:
                    combined_kb.inline_keyboard.append(button)
                
                bot.edit_message_text(
                    f'📁 فيديوهات التصنيف:',
                    call.message.chat.id, 
                    call.message.message_id,
                    reply_markup=combined_kb
                )

            elif action == 'sub':
                subcategory_id = int(parts[1])
                # معالجة التصنيفات الفرعية
                bot.answer_callback_query(call.id, 'جاري تحميل الفيديوهات...')

            # --- التنقل بين الصفحات ---
            elif action in ['video', 'fav', 'history', 'popular', 'cat']:
                if len(parts) > 1 and parts[1] == 'p':  # page
                    page = int(parts[2])
                    
                    if action == 'fav':
                        videos, total = user_service.get_favorites(call.from_user.id, page)
                    elif action == 'history':
                        videos, total = user_service.get_history(call.from_user.id, page)
                    elif action == 'popular':
                        videos = video_service.top_videos(10)
                        total = len(videos)
                    elif action == 'cat':
                        category_id = int(parts[3]) if len(parts) > 3 else 0
                        videos = video_service.get_category_videos(category_id)
                        total = len(videos)
                    else:  # video
                        videos, total = video_service.list_videos(page)
                    
                    items = [{'id': v['id'], 'display_title': v.get('caption', '')[:30]} for v in videos[:20]]
                    kb, nav_kb = paginated_keyboard(items, total, page, action)
                    
                    # دمج الكيبوردات
                    combined_kb = InlineKeyboardMarkup()
                    for button in kb.inline_keyboard:
                        combined_kb.inline_keyboard.append(button)
                    for button in nav_kb.inline_keyboard:
                        combined_kb.inline_keyboard.append(button)
                    
                    bot.edit_message_text(
                        '🎬 تصفح الفيديوهات:',
                        call.message.chat.id, 
                        call.message.message_id,
                        reply_markup=combined_kb
                    )
                else:
                    # اختيار فيديو
                    video_id = int(parts[1])
                    video = video_service.get_video(video_id)
                    if video:
                        is_fav = user_service.is_favorite(call.from_user.id, video_id)
                        user_rating = user_service.get_user_rating(call.from_user.id, video_id)
                        reply_markup = video_action_keyboard(video_id, call.from_user.id, is_fav, user_rating)
                        
                        bot.edit_message_text(
                            f"🎬 {video.get('caption', '')}\n\n🆔 ID: {video_id}",
                            call.message.chat.id, 
                            call.message.message_id,
                            reply_markup=reply_markup
                        )

            # --- الرجوع ---
            elif action == 'back':
                bot.edit_message_text(
                    '🎬 القائمة الرئيسية:',
                    call.message.chat.id, 
                    call.message.message_id,
                    reply_markup=main_menu_keyboard()
                )

            else:
                bot.answer_callback_query(call.id, '❌ غير مدعوم حالياً')

        except Exception as e:
            logger.exception('Error in callback handler')
            try:
                bot.answer_callback_query(call.id, '❌ حدث خطأ. حاول مرة أخرى.', show_alert=True)
            except:
                pass