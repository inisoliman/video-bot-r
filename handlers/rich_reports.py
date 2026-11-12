# handlers/rich_reports.py
"""تقارير Rich مهيكلة (RTL) لتقارير الأدمن مع بديل نصي HTML واحد.

- تُبنى بكائنات pyTelegramBotAPI الحقيقية (InputRichMessage/InputRichBlock*)، لا HTML
  عادي داخل InputRichMessage(html=...). النصوص الديناميكية تبقى نصاً عادياً حرفياً
  (لا تفسير لوسوم HTML فيها).
- ``send_rich_or_plain``: محاولة Rich واحدة؛ استثناء أو نتيجة None (غير مؤكدة)
  => يُرسل النص النصي البديل مرة واحدة فقط (النجاح لا يكرر التقرير).
- فشل الإرسال لا يغيّر أي أرقام عند المستدعي؛ يُعاد bool فقط، ويُسجّل نوع الاستثناء
  وحده (لا وسائط ولا أسرار).
"""

import logging

from telebot.types import (
    InputRichBlockDetails,
    InputRichBlockList,
    InputRichBlockListItem,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichMessage,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# بناة التقارير
# ----------------------------------------------------------------------------

def _bullets(lines):
    """قائمة نقطية: كل سطر فقرة نصية عادية داخل عنصر قائمة."""
    return InputRichBlockList(items=[
        InputRichBlockListItem(blocks=[InputRichBlockParagraph(text=line)])
        for line in lines
    ])


def build_broadcast_report(sent, failed, removed):
    """تقرير إكمال البث: عنوان + قائمة الأعداد الحقيقية كما هي."""
    return InputRichMessage(
        blocks=[
            InputRichBlockSectionHeading(text="📢 اكتمل البث!", size=2),
            _bullets([
                f"✅ نجح: {sent}",
                f"❌ فشل: {failed}",
                f"🗑️ حُذفوا (حظر/حساب معطّل): {removed}",
            ]),
        ],
        is_rtl=True,
    )


def build_stats_report(stats, most_viewed_line=None, highest_rated_line=None):
    """تقرير إحصائيات المحتوى: عنوان + قائمة الأعداد + كتلة تفاصيل بالأكثر مشاهدة.

    ``most_viewed_line``/``highest_rated_line`` نصوص عادية مُهيّأة مسبقاً (بلا HTML).
    """
    blocks = [
        InputRichBlockSectionHeading(text="📊 إحصائيات المحتوى", size=2),
        _bullets([
            f"إجمالي الفيديوهات: {stats['video_count']}",
            f"إجمالي التصنيفات: {stats['category_count']}",
            f"إجمالي المشاهدات: {stats['total_views']}",
            f"إجمالي التقييمات: {stats['total_ratings']}",
        ]),
    ]
    highlights = [line for line in (most_viewed_line, highest_rated_line) if line]
    if highlights:
        blocks.append(InputRichBlockDetails(
            summary="الأكثر مشاهدة والأعلى تقييماً",
            blocks=[InputRichBlockParagraph(text=line) for line in highlights],
            is_open=False,
        ))
    return InputRichMessage(blocks=blocks, is_rtl=True)


# ----------------------------------------------------------------------------
# الإرسال مع البديل
# ----------------------------------------------------------------------------

def send_rich_or_plain(bot, chat_id, rich_message, plain_text):
    """True إذا أُرسل Rich بنجاح؛ وإلا يُرسل ``plain_text`` كـHTML مرة واحدة.

    لا يرفع استثناءً أبداً: فشل Rich (استثناء أو None) يمرّ للبديل، وفشل البديل
    يُسجّل ويُعاد False (المستدعي يحفظ أرقامه وتدفقه).
    """
    sender = getattr(bot, 'send_rich_message', None)
    if callable(sender):
        try:
            result = sender(chat_id, rich_message)
        except Exception as exc:
            logger.warning("Rich report failed for %s: %s", chat_id, type(exc).__name__)
        else:
            if result is not None:
                return True
            logger.warning("Rich report unconfirmed for %s (send_rich_message returned None)",
                           chat_id)
    try:
        bot.send_message(chat_id, plain_text, parse_mode='HTML')
        return True
    except Exception as exc:
        logger.warning("Plain report fallback failed for %s: %s", chat_id, type(exc).__name__)
        return False
