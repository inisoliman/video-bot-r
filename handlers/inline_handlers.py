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
            offset = inline_query.offset
            
            # تحديد الصفحة (offset)
            if not offset:
                offset_val = 0
            else:
                try:
                    offset_val = int(offset)
                except ValueError:
                    offset_val = 0

            logger.info(f"📥 Inline query user={user_id} q='{query_text}' offset={offset_val}")
            
            # البحث في قاعدة البيانات مع الـ offset
            try:
                # نطلب 25 نتيجة
                videos = db.search_videos_for_inline(query_text, offset=offset_val, limit=25)
                logger.info(f"📊 DB returned {len(videos) if videos else 0} videos")
            except Exception as db_err:
                logger.error(f"❌ DB search error: {db_err}", exc_info=True)
                videos = None
            
            if not videos:
                # إذا لم تكن هناك نتائج في الصفحة الأولى، نرسل رسالة "لا يوجد"
                if offset_val == 0:
                    logger.info(f"⚠️ No videos found for '{query_text}'")
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
                    # إذا كنا في صفحة تالية ولا توجد نتائج إضافية، نرسل قائمة فارغة (نهاية التمرير)
                    bot.answer_inline_query(inline_query.id, [], cache_time=1, next_offset="")
            else:
                # استراتيجية العرض
                logger.info(f"🔄 Processing results offset={offset_val}...")
                
                # حساب الـ offset القادم
                if len(videos) < 25:
                    next_offset = ""
                else:
                    next_offset = str(offset_val + 25)

                # المحاولة الأولى: عرض كل النتائج حسب نوعها المخزن
                results = []
                for video in videos:
                    is_document = (video.get('content_type') == 'DOCUMENT')
                    res = create_inline_result(video, use_document=is_document)
                    if res:
                        results.append(res)

                if not results:
                    logger.warning("⚠️ No valid results generated")
                    results = [
                        InlineQueryResultArticle(
                            id='no_valid_results',
                            title='⚠️ لا توجد نتائج',
                            description='جرب كلمة بحث أخرى',
                            input_message_content=InputTextMessageContent(
                                message_text='⚠️ لا توجد نتائج'
                            )
                        )
                    ]
                    bot.answer_inline_query(inline_query.id, results, cache_time=1)
                else:
                    try:
                        bot.answer_inline_query(
                            inline_query.id,
                            results,
                            cache_time=10,
                            is_personal=False,
                            next_offset=next_offset
                        )
                        logger.info(f"✅ Sent {len(results)} results (Next offset: '{next_offset}')")
                    except telebot.apihelper.ApiTelegramException as api_err:
                        error_desc = str(api_err)
                        if "VIDEO_CONTENT_TYPE_INVALID" in error_desc:
                            # ✨ Fallback: تحويل كل النتائج إلى مستندات وإعادة الإرسال
                            logger.warning(f"⚠️ VIDEO_CONTENT_TYPE_INVALID - retrying all as documents...")
                            doc_results = []
                            for video in videos:
                                res = create_inline_result(video, use_document=True)
                                if res:
                                    doc_results.append(res)
                            if doc_results:
                                try:
                                    bot.answer_inline_query(
                                        inline_query.id,
                                        doc_results,
                                        cache_time=10,
                                        is_personal=False,
                                        next_offset=next_offset
                                    )
                                    logger.info(f"✅ Fallback OK: Sent {len(doc_results)} document results")
                                except Exception as e2:
                                    logger.error(f"❌ Fallback also failed: {e2}")
                        else:
                            logger.error(f"❌ API error: {api_err}")
                    except Exception as e:
                        logger.error(f"❌ Unexpected send error: {e}")
            
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
