# handlers/inline_handlers.py

import telebot
from telebot.types import (
    InlineQueryResultCachedVideo,
    InlineQueryResultArticle,
    InputTextMessageContent
)
import logging

import db_manager as db

logger = logging.getLogger(__name__)

def register(bot):
    """تسجيل معالج inline query"""
    
    @bot.inline_handler(lambda query: True)
    def handle_inline_query(inline_query):
        """
        معالج الـ inline query الرئيسي.
        
        يسمح للمستخدمين بالبحث عن الفيديوهات ومشاركتها في أي محادثة.
        """
        try:
            query_text = inline_query.query.strip()
            user_id = inline_query.from_user.id
            
            logger.info(f"Inline query from user {user_id}: '{query_text}'")
            
            # البحث في قاعدة البيانات
            videos = db.search_videos_for_inline(query_text, limit=50)
            
            if not videos:
                # لا توجد نتائج
                results = [
                    InlineQueryResultArticle(
                        id='no_results',
                        title='❌ لا توجد نتائج',
                        description=f'لم يتم العثور على فيديوهات تطابق "{query_text}"',
                        input_message_content=InputTextMessageContent(
                            message_text='❌ لم يتم العثور على نتائج'
                        )
                    )
                ]
            else:
                # تحويل النتائج إلى InlineQueryResult
                results = []
                for video in videos:
                    result = create_inline_result(video)
                    if result:
                        results.append(result)
            
            # إرسال النتائج
            bot.answer_inline_query(
                inline_query.id,
                results,
                cache_time=300,  # 5 دقائق
                is_personal=True
            )
            
        except Exception as e:
            logger.error(f"Error in inline query handler: {e}", exc_info=True)
            # إرسال رسالة خطأ للمستخدم
            try:
                error_result = [
                    InlineQueryResultArticle(
                        id='error',
                        title='❌ حدث خطأ',
                        description='حاول مرة أخرى',
                        input_message_content=InputTextMessageContent(
                            message_text='❌ حدث خطأ أثناء البحث'
                        )
                    )
                ]
                bot.answer_inline_query(inline_query.id, error_result, cache_time=0)
            except Exception as e_inner:
                logger.error(f"Failed to send error response: {e_inner}")

def create_inline_result(video):
    """
    تحويل بيانات الفيديو إلى InlineQueryResultCachedVideo.
    
    Args:
        video: dict مع بيانات الفيديو
    
    Returns:
        InlineQueryResultCachedVideo object أو None
    """
    try:
        # التحقق من وجود file_id
        file_id = video.get('file_id')
        if not file_id:
            logger.warning(f"Video {video.get('id')} has no file_id, skipping")
            return None
        
        # التأكد أن file_id هو string
        file_id = str(file_id)
        
        # العنوان: caption أو file_name
        title = video.get('caption') or video.get('file_name') or 'فيديو بدون عنوان'
        if len(title) > 100:
            title = title[:97] + '...'
        
        # الوصف: التقييم، المشاهدات، التصنيف
        rating = round(video.get('avg_rating', 0), 1)
        views = video.get('view_count', 0)
        category = video.get('category_name', 'غير مصنف')
        
        # تنسيق الوصف
        description_parts = []
        if rating > 0:
            description_parts.append(f"⭐ {rating}")
        if views > 0:
            description_parts.append(f"👁️ {views:,}")
        if category:
            description_parts.append(f"📂 {category}")
        
        description = " | ".join(description_parts) if description_parts else "فيديو"
        
        # إنشاء النتيجة
        result = InlineQueryResultCachedVideo(
            id=str(video['id']),
            video_file_id=file_id,  # استخدام file_id المحول لـ string
            title=title,
            description=description
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Error creating inline result for video {video.get('id')}: {e}", exc_info=True)
        return None
