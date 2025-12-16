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
                        input_message_content=InputTextMessageContent(
                            message_text='❌ لم يتم العثور على نتائج'
                        )
                    )
                ]
            else:
                # [تعديل] استراتيجية "المحاولة والبديل" (Smart Fallback)
                # المحاولة 1: عرض النتائج كفيديوهات (جودة أفضل)
                results_video = []
                for video in videos:
                    res = create_inline_result(video, use_document=False)
                    if res: results_video.append(res)
                
                try:
                    bot.answer_inline_query(
                        inline_query.id,
                        results_video,
                        cache_time=300,
                        is_personal=True
                    )
                except Exception as e_first_attempt:
                    logger.warning(f"First attempt (Video) failed: {e_first_attempt}. Retrying with Document fallback...")
                    
                    try:
                        # المحاولة 2: عرض النتائج كملفات (الأمان) عند حدوث خطأ
                        results_doc = []
                        for video in videos:
                            res = create_inline_result(video, use_document=True)
                            if res: results_doc.append(res)
                        
                        # [تعديل] تقليل عدد النتائج في الوضع الآمن لتجنب مشاكل الحجم
                        results_doc = results_doc[:20]
                        
                        if results_doc:
                            bot.answer_inline_query(
                                inline_query.id,
                                results_doc,
                                cache_time=60, # تقليل الكاش عند الخطأ
                                is_personal=True
                            )
                            logger.info(f"✅ Fallback (Document) success: Sent {len(results_doc)} results")
                        else:
                            logger.error("❌ Fallback (Document) failed: No valid results to send")
                            raise e_first_attempt # إذا فشل الاثنان، نرفع الخطأ الأصلي
                            
                    except Exception as e_second_attempt:
                        logger.error(f"❌ Fallback (Document) ALSO failed: {e_second_attempt}")
                        # محاولة أخيرة: إرسال نتائج فارغة لتجنب حالة "التحميل المستمر"
                        try:
                            error_result = [
                                InlineQueryResultArticle(
                                    id='error_fallback',
                                    title='⚠️ عذراً',
                                    description='حدث خطأ في عرض النتائج.',
                                    input_message_content=InputTextMessageContent(
                                        message_text='⚠️ لا يمكن عرض النتائج حالياً.'
                                    )
                                )
                            ]
                            bot.answer_inline_query(inline_query.id, error_result, cache_time=10)
                        except:
                            pass
            
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
            return InlineQueryResultCachedDocument(
                id=str(video['id']),
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

