from telebot import TeleBot
from telebot.types import InlineQueryResultCachedVideo, InlineQueryResultArticle, InputTextMessageContent
from ..services import video_service
from ..logger import logger


def register(bot: TeleBot):
    @bot.inline_handler(lambda query: True)
    def on_inline_query(inline_query):
        try:
            query_text = inline_query.query.strip()
            offset = int(inline_query.offset or 0)
            videos, _ = video_service.search(query_text, page=offset//25, per_page=25)
            results = []
            if not videos:
                results.append(InlineQueryResultArticle(id='no', title='لا توجد نتائج', input_message_content=InputTextMessageContent('لا توجد نتائج')))
            else:
                for v in videos:
                    if v.get('content_type') == 'VIDEO':
                        results.append(InlineQueryResultCachedVideo(
                            id=str(v['id']),
                            video_file_id=v['file_id'],
                            title=v.get('caption','فيديو'),
                            description=v.get('file_name',''),
                            input_message_content=InputTextMessageContent(v.get('caption',''))
                        ))
                    else:
                        results.append(InlineQueryResultArticle(
                            id=f"doc_{v['id']}",
                            title=v.get('caption', 'مستند'),
                            input_message_content=InputTextMessageContent(v.get('caption',''))
                        ))
            bot.answer_inline_query(inline_query.id, results, is_personal=False, cache_time=30, next_offset=str(offset+25 if len(videos)==25 else ''))
        except Exception as e:
            logger.exception('inline query failed')
