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
                
                results = []
                skipped_count = 0
                
                for video in videos:
                    # التحقق من صلاحية file_id قبل إنشاء النتيجة
                    file_id = video.get('file_id')
                    if not file_id or len(str(file_id).strip()) < 10:
                        skipped_count += 1
                        continue
                    
                    # إنشاء النتيجة مع الصورة المصغرة
                    res = create_inline_result(video)
                    if res:
                        results.append(res)
                    else:
                        skipped_count += 1
                
                if skipped_count > 0:
                    logger.warning(f"⚠️ Skipped {skipped_count} videos with invalid file_id")
                
                # حساب الـ offset القادم
                # إذا كان عدد النتائج أقل من الحد (25)، فهذا يعني أننا وصلنا للنهاية
                if len(videos) < 25:
                    next_offset = ""
                else:
                    # يوجد احتمال لوجود المزيد، نزيد الـ offset
                    next_offset = str(offset_val + 25)

                if results:
                    try:
                        bot.answer_inline_query(
                            inline_query.id,
                            results,
                            cache_time=30,  # تقليل الكاش ليشعر المستخدم بالسرعة والتحديث
                            is_personal=False,
                            next_offset=next_offset
                        )
                        logger.info(f"✅ Sent {len(results)} results (Next offset: '{next_offset}')")
                    except Exception as e:
                        # Fallback في حال حدوث خطأ
                        logger.warning(f"⚠️ Send failed: {e}")
                        # نرسل نتائج أقل في حال الفشل
                        if len(results) > 10:
                            try:
                                bot.answer_inline_query(
                                    inline_query.id,
                                    results[:10],
                                    cache_time=60,
                                    is_personal=False,
                                    next_offset=next_offset
                                )
                                logger.info(f"✅ Sent reduced results (10) after error")
                            except Exception as e2:
                                logger.error(f"❌ Reduced send also failed: {e2}")
                else:
                    logger.warning("⚠️ No valid results generated")
                    results = [
                        InlineQueryResultArticle(
                            id='no_valid_results',
                            title='⚠️ لا توجد نتائج صالحة',
                            description='جرب كلمة بحث أخرى',
                            input_message_content=InputTextMessageContent(
                                message_text='⚠️ لا توجد نتائج صالحة - ربما الفيديوهات محذوفة'
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

def create_inline_result(video):
    """
    تحويل بيانات الفيديو إلى InlineQueryResult مع الصورة المصغرة.
    """
    try:
        # التحقق من وجود file_id
        file_id = video.get('file_id')
        if not file_id:
            logger.debug(f"Video {video.get('id')} has no file_id")
            return None
        
        # التأكد أن file_id هو string وصالح
        file_id = str(file_id).strip()
        if len(file_id) < 10:
            logger.debug(f"Video {video.get('id')} has invalid file_id length")
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
        if rating > 0:
            description_parts.append(f"⭐ {rating}")
        if views > 0:
            description_parts.append(f"👁️ {views:,}")
        if category:
            description_parts.append(f"📂 {category}")
        
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
        
        # الحصول على الصورة المصغرة
        thumb_file_id = video.get('thumbnail_file_id')
        has_thumbnail = thumb_file_id and len(str(thumb_file_id).strip()) > 10
        
        # إنشاء النتيجة حسب توفر الصورة المصغرة
        if has_thumbnail:
            # إذا كانت الصورة المصغرة متوفرة، نستخدم InlineQueryResultCachedVideo
            # لأنه يدعم عرض الفيديو مع الصورة المصغرة بشكل أفضل
            result = InlineQueryResultCachedVideo(
                id=f"vid_{video['id']}",
                video_file_id=file_id,
                title=title,
                description=description,
                caption=final_caption,
                parse_mode='HTML',
                thumb_url=str(thumb_file_id).strip()
            )
        else:
            # بدون صورة مصغرة، نستخدم Document
            result = InlineQueryResultCachedDocument(
                id=f"doc_{video['id']}",
                title=title,
                document_file_id=file_id,
                description=description,
                caption=final_caption,
                parse_mode='HTML'
            )
        
        return result
        
    except Exception as e:
        logger.error(f"Error creating inline result for video {video.get('id', 'unknown')}: {e}", exc_info=True)
        return None
