#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/handlers/inline_handlers.py
# الوصف: معالجات Inline Query
# ==============================================================================

import logging
from telebot.types import (
    InlineQueryResultCachedVideo, InlineQueryResultCachedDocument,
    InlineQueryResultArticle, InputTextMessageContent
)

from bot.database.repositories.video_repo import VideoRepository

logger = logging.getLogger(__name__)


def register(bot):
    @bot.inline_handler(lambda query: True)
    def handle_inline_query(inline_query):
        try:
            query_text = inline_query.query.strip()
            offset = inline_query.offset
            offset_val = int(offset) if offset else 0

            logger.info(f"📥 Inline q='{query_text}' offset={offset_val}")

            try:
                videos = VideoRepository.search_for_inline(query_text, offset=offset_val, limit=25)
                logger.info(f"📊 DB returned {len(videos) if videos else 0} videos")
            except Exception as db_err:
                logger.error(f"❌ DB search error: {db_err}", exc_info=True)
                videos = None

            if not videos:
                if offset_val == 0:
                    results = [InlineQueryResultArticle(
                        id='no_results', title='❌ لا توجد نتائج',
                        description=f'لم يتم العثور على فيديوهات تطابق "{query_text}"',
                        input_message_content=InputTextMessageContent('❌ لا نتائج')
                    )]
                    bot.answer_inline_query(inline_query.id, results, cache_time=1)
                else:
                    bot.answer_inline_query(inline_query.id, [], cache_time=1, next_offset="")
            else:
                results = []
                for video in videos:
                    ct = video.get('content_type')
                    res = _create_inline_result(video, use_document=(ct != 'VIDEO'))
                    if res:
                        results.append(res)

                next_offset = "" if len(videos) < 25 else str(offset_val + 25)

                if results:
                    try:
                        bot.answer_inline_query(inline_query.id, results, cache_time=30,
                            is_personal=False, next_offset=next_offset)
                        logger.info(f"✅ Sent {len(results)} results (next: '{next_offset}')")
                    except Exception as e:
                        logger.warning(f"⚠️ Primary failed: {e}, retrying as docs...")
                        doc_results = [_create_inline_result(v, True) for v in videos[:25]]
                        doc_results = [r for r in doc_results if r]
                        if doc_results:
                            try:
                                bot.answer_inline_query(inline_query.id, doc_results,
                                    cache_time=60, is_personal=False, next_offset=next_offset)
                            except Exception as e2:
                                logger.error(f"❌ Fallback failed: {e2}")
                else:
                    results = [InlineQueryResultArticle(
                        id='no_valid', title='⚠️ لا نتائج',
                        description='جرب كلمة أخرى',
                        input_message_content=InputTextMessageContent('⚠️ لا نتائج')
                    )]
                    bot.answer_inline_query(inline_query.id, results, cache_time=1)

        except Exception as e:
            logger.error(f"Inline error: {e}", exc_info=True)
            try:
                bot.answer_inline_query(inline_query.id, [
                    InlineQueryResultArticle(id='error', title='❌ خطأ',
                        description='حاول مرة أخرى',
                        input_message_content=InputTextMessageContent('❌ خطأ'))
                ], cache_time=0)
            except:
                pass


def _create_inline_result(video, use_document=False):
    try:
        file_id = video.get('file_id')
        if not file_id or len(str(file_id).strip()) < 10:
            return None

        file_id = str(file_id).strip()
        title = video.get('caption') or video.get('file_name') or 'فيديو'
        title = title.replace('\n', ' ').strip()[:60]

        rating = round(video.get('avg_rating', 0), 1)
        views = video.get('view_count', 0)
        category = video.get('category_name', 'غير مصنف')

        desc_parts = []
        if rating > 0: desc_parts.append(f"⭐ {rating}")
        if views > 0: desc_parts.append(f"👁️ {views:,}")
        if category: desc_parts.append(f"📂 {category}")
        description = " | ".join(desc_parts)[:60] if desc_parts else "فيديو"

        full_caption = video.get('caption') or title
        final = f"{full_caption}\n\n{description}" if description not in full_caption else full_caption
        if len(final) > 1024:
            final = final[:1021] + '...'

        if use_document:
            return InlineQueryResultCachedDocument(
                id=f"doc_{video['id']}", title=title,
                document_file_id=file_id, description=description,
                caption=final, parse_mode='HTML')
        else:
            return InlineQueryResultCachedVideo(
                id=str(video['id']), title=title,
                video_file_id=file_id, description=description,
                caption=final, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Inline result error for {video.get('id')}: {e}")
        return None
