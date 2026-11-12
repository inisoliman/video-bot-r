# handlers/ephemeral_messages.py
"""رسائل Ephemeral مستهدفة لإشعارات الأدمن في المجموعات فقط.

تيليجرام 7.x+ يسمح برد يقرأه أدمن واحد في مجموعة عبر معامل
``ephemeral_message_parameters``؛ نستخدمه هنا لإشعارات الإدارة/الرفض بدل
الرد العام الذي يفضح وجود أدوات الإدارة لكل أعضاء المجموعة.

قواعد صارمة في هذا الملف:
- الإرسال في ``group``/``supergroup`` ولأدمن معروف فقط (لا محادثات خاصة).
- لا إدخال تشغيلي: المساعد للإشعارات النصية فقط، وأي حالة مخزّنة تخصّ أدمن
  آخر تُرفض بلا إرسال (fail closed).
- عند فشل الإرسال لا يوجد أي بديل عام (reply_to/public) لأن ذلك قد يعرض محتوى
  الإدارة؛ يُسجَّل نوع الاستثناء فقط بلا نص ولا سياق ولا أسرار.
"""

import logging

from telebot.types import EphemeralMessageParameters

from state_manager import read_state_row

logger = logging.getLogger(__name__)

GROUP_CHAT_TYPES = frozenset({'group', 'supergroup'})


def _admin_matches(candidate, admin_ids):
    """مقارنة معرّفات آمنة النوع: لا ترفع عند معرّف نصي/غير صحيح."""
    try:
        value = int(candidate)
    except (TypeError, ValueError):
        return False
    for admin_id in admin_ids or ():
        try:
            if int(admin_id) == value:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _state_conflicts_with_owner(user_id, admin_id):
    """True إذا كانت الحالة المخزّنة مملوكة لأدمن آخر (أو تعذّرت قراءتها)."""
    try:
        row = read_state_row(user_id)
    except Exception as exc:
        logger.warning("Ephemeral notice skipped: state read failed (%s)", type(exc).__name__)
        return True
    context = row.get('context') if isinstance(row, dict) else None
    if not isinstance(context, dict) or 'admin_id' not in context:
        return False
    return not _admin_matches(context.get('admin_id'), [admin_id])


def send_admin_notice(bot, message, text, admin_ids):
    """إشعار Ephemeral لصاحب الرسالة إن كان أدمن داخل مجموعة؛ وإلا False بلا إرسال."""
    chat = getattr(message, 'chat', None)
    chat_id = getattr(chat, 'id', None)
    admin = getattr(message, 'from_user', None)
    admin_id = getattr(admin, 'id', None)
    if getattr(chat, 'type', None) not in GROUP_CHAT_TYPES:
        return False
    if admin_id is None or not _admin_matches(admin_id, admin_ids):
        return False
    if _state_conflicts_with_owner(admin_id, admin_id):
        return False
    try:
        bot.send_message(
            chat_id,
            text,
            parse_mode='HTML',
            ephemeral_message_parameters=EphemeralMessageParameters(
                receiver_user_id=admin_id),
        )
    except Exception as exc:
        # نوع الاستثناء فقط: لا نص الرسالة ولا سياق الحالة ولا أي سر
        logger.warning("Ephemeral admin notice failed: %s", type(exc).__name__)
        return False
    return True
