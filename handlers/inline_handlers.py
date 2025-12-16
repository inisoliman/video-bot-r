# handlers/inline_handlers.py

import telebot
from telebot.types import (
    InlineQueryResultCachedVideo,
    InlineQueryResultCachedDocument,
    InlineQueryResultArticle,
    InputTextMessageContent
)
import logging
import os

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
            # [تعديل] تقليل عدد النتائج لتجنب خطأ 431 (Header Too Large)
            videos = db.search_videos_for_inline(query_text, limit=25)
            
            if not videos:
                # لا توجد نتائج
                results = [
                    InlineQueryResultArticle(
                        id='no_results',
                        title='❌ لا توجد نتائج',
                        description=f'لم يتم العثور على فيديوهات تطابق "{query_text}"',
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

def create_inline_result(video, use_document=False):
    """
    تحويل بيانات الفيديو إلى InlineQueryResultCachedVideo (أو Document كمحاولة بديلة).
    
    Args:
        video: dict مع بيانات الفيديو
        use_document: إذا كان True، يتم استخدام CachedDocument بدلاً من CachedVideo
    
    Returns:
        InlineQueryResult object أو None
    """
    try:
        # التحقق من وجود file_id
        file_id = video.get('file_id')
        if not file_id:
            return None
        
        # التأكد أن file_id هو string وصالح
        file_id = str(file_id).strip()
        if not file_id or len(file_id) < 10:  # file_id يجب أن يكون طويل
            return None
        
        # العنوان: caption أو file_name
        title = video.get('caption') or video.get('file_name') or 'فيديو بدون عنوان'
        
        # تنظيف العنوان من أي أحرف خاصة قد تسبب مشاكل
        title = title.replace('\n', ' ').replace('\r', ' ').strip()
        if len(title) > 60:  # [تعديل] تقليل طول العنوان
            title = title[:57] + '...'
        
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
        # [تعديل] التأكد من أنها ليست طويلة جداً
        if len(description) > 60:
            description = description[:57] + "..."
        
        # [تعديل] استخدام الكابشن الكامل من قاعدة البيانات بدلاً من العنوان المقطوع
        full_caption = video.get('caption') or title
        
        # إضافة الوصف للكابشن إذا لم يكن موجوداً
        final_caption = full_caption
        if description and description not in full_caption:
             final_caption = f"{full_caption}\n\n{description}"
        
        # التأكد من حدود تليجرام (1024 حرف)
        if len(final_caption) > 1024:
            final_caption = final_caption[:1021] + '...'

        # التبديل بين Video و Document
        if use_document:
            # وضع الأمان: استخدام CachedDocument
            # [تعديل] إضافة بادئة للـ ID لتجنب تضارب الكاش
            return InlineQueryResultCachedDocument(
                id=f"doc_{video['id']}",
                title=title,
                document_file_id=file_id,
                description=description,
                caption=final_caption,
                parse_mode='HTML'
            )
        else:
            # الوضع الطبيعي: استخدام CachedVideo
            return InlineQueryResultCachedVideo(
                id=str(video['id']),
                title=title,
                video_file_id=file_id,
                description=description,
                caption=final_caption,
                parse_mode='HTML'
            )
        
    except Exception as e:
        logger.error(f"Error creating inline result for video {video.get('id')}: {e}", exc_info=True)
        return None

