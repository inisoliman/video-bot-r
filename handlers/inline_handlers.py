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
        """
        try:
            query_text = inline_query.query.strip()
            user_id = inline_query.from_user.id
            
            logger.info(f"Inline query from user {user_id}: '{query_text}'")
            
            # البحث في قاعدة البيانات
            videos = db.search_videos_for_inline(query_text, limit=25)
            
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
                bot.answer_inline_query(inline_query.id, results, cache_time=1)
            else:
                # [وضع التشخيص] العودة لوضع "الملفات فقط" (Legacy Mode)
                # لتحديد سبب اختفاء النتائج
                logger.info("Diagnosis: Forcing Document Mode (Legacy)")
                
                results_doc = []
                for video in videos:
                    # use_document=True يجبر الدالة على استخدام CachedDocument
                    res = create_inline_result(video, use_document=True)
                    if res: results_doc.append(res)
                
                # تقليل العدد لتجنب مشاكل الحجم
                results_doc = results_doc[:25]
                
                if results_doc:
                    bot.answer_inline_query(
                        inline_query.id,
                        results_doc,
                        cache_time=1, # إلغاء الكاش
                        is_personal=True
                    )
                    logger.info(f"✅ Legacy Mode: Sent {len(results_doc)} results as Documents")
                else:
                    logger.warning("⚠️ Legacy Mode: No valid results generated")
                    results = [
                        InlineQueryResultArticle(
                            id='no_valid_results_legacy',
                            title=f'⚠️ لا يمكن عرض النتائج',
                            description='تأكد من صلاحية الملفات',
                            input_message_content=InputTextMessageContent(
                                message_text='⚠️ لا يمكن عرض النتائج حالياً'
                            )
                        )
                    ]
                    bot.answer_inline_query(inline_query.id, results, cache_time=1)
            
        except Exception as e:
            logger.error(f"Error in inline query handler: {e}", exc_info=True)
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

def create_inline_result(video, use_document=False):
    """
    تحويل بيانات الفيديو إلى InlineQueryResult.
    """
    try:
        # التحقق من وجود file_id
        file_id = video.get('file_id')
        if not file_id:
            return None
        
        # التأكد أن file_id هو string وصالح
        file_id = str(file_id).strip()
        if not file_id or len(file_id) < 10:
            return None
        
        # العنوان: caption أو file_name
        title = video.get('caption') or video.get('file_name') or 'فيديو بدون عنوان'
        
        # تنظيف العنوان
        title = title.replace('\n', ' ').replace('\r', ' ').strip()
        if len(title) > 60:
            title = title[:57] + '...'
        
        # الوصف
        rating = round(video.get('avg_rating', 0), 1)
        views = video.get('view_count', 0)
        category = video.get('category_name', 'غير مصنف')
        
        description_parts = []
        if rating > 0: description_parts.append(f"⭐ {rating}")
        if views > 0: description_parts.append(f"👁️ {views:,}")
        if category: description_parts.append(f"📂 {category}")
        
        description = " | ".join(description_parts) if description_parts else "فيديو"
        if len(description) > 60:
            description = description[:57] + "..."
        
        # الكابشن الكامل
        full_caption = video.get('caption') or title
        final_caption = full_caption
        if description and description not in full_caption:
             final_caption = f"{full_caption}\n\n{description}"
        
        if len(final_caption) > 1024:
            final_caption = final_caption[:1021] + '...'

        if use_document:
            # وضع الأمان (وثيقة)
            return InlineQueryResultCachedDocument(
                id=f"doc_{video['id']}", # ID مميز لتجنب الكاش
                title=title,
                document_file_id=file_id,
                description=description,
                caption=final_caption,
                parse_mode='HTML'
            )
        else:
            # الوضع الطبيعي (فيديو)
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
