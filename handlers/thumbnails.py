# handlers/thumbnails.py
"""
تدفق الصور المصغرة للأدمن بحالة مخزّنة (بدل next_step/admin_steps).

- الصورة الافتراضية: أدمن + خاص + مستخدم الحالة -> photo -> set_default_thumbnail.
- تحديث فيديو معيّن: رقم الفيديو بعد التحقق من وجوده، ثم صورة -> update_video_thumbnail.
- الفشل، أو غياب سياق الفيديو، يُبلَّغ عنه صراحةً (لا نجاح وهمي ولا حذف صامت للحالة للخطأ).
"""

import logging

from telebot.types import InlineKeyboardMarkup

import db_manager
from db_manager import get_video_by_id, set_default_thumbnail, update_video_thumbnail
from state_manager import clear_user_waiting_state, read_state_row, set_user_state
from .button_styles import STYLE_DANGER, inline_button

logger = logging.getLogger(__name__)

STATE_DEFAULT_THUMB = 'waiting_default_thumb'
STATE_THUMB_VIDEO_ID = 'waiting_video_id_for_thumb'
STATE_THUMB_PHOTO = 'waiting_new_thumb_for_video'

PROMPT_DEFAULT = ("🖼️ أرسل الصورة التي تريد تعيينها كصورة مصغّرة افتراضية للفيديوهات "
                  "(أو /cancel). وفي حال كانت الصورة الافتراضية معروضة بالفعل فَالزر يطلب تحديثها.")
PROMPT_VIDEO_ID = ("📸 أرسل رقم الفيديو (ID) الذي تريد تحديث صورته المصغّرة يدوياً. (أو /cancel)")
PROMPT_PHOTO = "🖼️ أرسل الآن الصورة الجديدة لهذا الفيديو. (أو /cancel)"
CALLBACK_CANCEL = 'admin::cancel_thumb'


def _is_admin(user_id, admin_ids):
    try:
        return int(user_id) in {int(a) for a in admin_ids}
    except (TypeError, ValueError):
        return False


def _is_private(message):
    return getattr(getattr(message, 'chat', None), 'type', None) == 'private'


def _owner(context, user_id):
    """ملكية صريحة: admin_id المخزّن يجب أن يطابق المُرسِل (None ليس ملكية)."""
    owner = (context or {}).get('admin_id')
    return owner is not None and str(owner) == str(user_id)


def _prompt_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(inline_button("↩️ إلغاء 🟡", STYLE_DANGER, callback_data=CALLBACK_CANCEL))
    return markup


def _ask(bot, user_id, state, text, context):
    try:
        sent = bot.send_message(user_id, text, reply_markup=_prompt_markup())
    except Exception as exc:
        logger.error("Could not send thumbnail prompt %s: %s", state, type(exc).__name__)
        return False
    try:
        stored = set_user_state(user_id, state,
                                dict(context, prompt_message_id=getattr(sent, 'message_id', None)))
    except Exception as exc:
        logger.error("Could not start thumbnail flow %s: %s", state, type(exc).__name__)
        stored = False
    if stored is not True:
        _best_effort_clear(user_id)
        return False
    return True


def _best_effort_clear(user_id):
    try:
        if clear_user_waiting_state(user_id) is not True:
            logger.warning("Best-effort thumbnail state cleanup not confirmed for %s", user_id)
    except Exception as exc:
        logger.warning("Best-effort thumbnail state cleanup failed: %s", type(exc).__name__)


def begin(bot, user_id, admin_ids, action):
    """action: set_default_thumb | manual_thumb -> (ok, prompt_shown)."""
    if not _is_admin(user_id, admin_ids):
        return False, "⛔ هذا الأمر للإدارة فقط"
    if action == 'manual_thumb':
        ok = _ask(bot, user_id, STATE_THUMB_VIDEO_ID, PROMPT_VIDEO_ID, {'admin_id': user_id})
        return ok, "أرسل رقم الفيديو"
    ok = _ask(bot, user_id, STATE_DEFAULT_THUMB, PROMPT_DEFAULT, {'admin_id': user_id})
    return ok, "أرسل الصورة الافتراضية"


def cancel(bot, user_id, admin_ids, call=None):
    if not _is_admin(user_id, admin_ids):
        return False
    try:
        cleared = clear_user_waiting_state(user_id)
    except Exception as exc:
        logger.warning("Could not cancel thumbnail flow: %s", type(exc).__name__)
        cleared = False
    ok = cleared is True
    if call is not None:
        try:
            bot.answer_callback_query(call.id, "✅ تم الإلغاء" if ok else "❌ تعذّر الإلغاء",
                                      show_alert=not ok)
        except Exception:
            pass
    return ok


def _finish(bot, user_id, success_text):
    """إنهاء التدفق بعد نجاح الحفظ: نجاح المسح يُبلَّغ عنه ولا يُعلن نجاح وهمي."""
    try:
        cleared = clear_user_waiting_state(user_id)
    except Exception as exc:
        logger.warning("Could not clear thumbnail state: %s", type(exc).__name__)
        cleared = False
    bot.send_message(user_id, success_text)
    if cleared is not True:
        bot.send_message(user_id, "⚠️ حُفظت الصورة لكن لم تُمسح حالة الطلب؛ أرسل /cancel لإنهاء العملية.")


def _photo_file_id(message):
    if getattr(message, 'content_type', None) != 'photo':
        return None
    photos = getattr(message, 'photo', None) or []
    if not photos:
        return None
    return getattr(photos[-1], 'file_id', None)


def _gate(state_name, message, admin_ids):
    """(user_id, context) إذا كانت الرسالة من صاحب حالة الصور المصغرة، أو None."""
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    state = read_state_row(user_id)
    if not state or state.get('state') != state_name:
        return None
    if not _is_private(message) or not _is_admin(user_id, admin_ids):
        return None
    context = state.get('context') or {}
    if not _owner(context, user_id):
        return None
    return user_id, context


def receive_default_photo(bot, admin_ids, message):
    gated = _gate(STATE_DEFAULT_THUMB, message, admin_ids)
    if gated is None:
        return False
    user_id, _context = gated
    file_id = _photo_file_id(message)
    if not file_id:
        bot.send_message(user_id, "❌ أرسل صورة فقط لهذا الإعداد (أو /cancel).")
        return True
    try:
        saved = set_default_thumbnail(file_id) is True
    except Exception as exc:
        logger.error("Default thumbnail save failed: %s", type(exc).__name__)
        saved = False
    if not saved:
        bot.send_message(user_id, "❌ تعذّر حفظ الصورة الافتراضية. أرسل الصورة مرة أخرى أو /cancel.")
        return True
    _finish(bot, user_id, "✅ تم تعيين الصورة المصغّرة الافتراضية.")
    return True


def receive_video_id(bot, admin_ids, message):
    gated = _gate(STATE_THUMB_VIDEO_ID, message, admin_ids)
    if gated is None:
        return False
    user_id, context = gated
    raw = str(getattr(message, 'text', None) or '').translate(
        str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')).strip().lstrip('#')
    if not raw.isdigit() or int(raw) <= 0:
        bot.send_message(user_id, "❌ أرسل رقم الفيديو (ID) فقط. (أو /cancel)")
        return True
    video_id = int(raw)
    try:
        video = get_video_by_id(video_id)
    except Exception as exc:
        logger.error("Video lookup failed for thumbnail: %s", type(exc).__name__)
        bot.send_message(user_id, "❌ تعذّرت قراءة الفيديو (خطأ في قاعدة البيانات). حاول مجدداً.")
        return True
    if not video:
        bot.send_message(user_id, f"❌ لا يوجد فيديو بالرقم {video_id}. أرسل رقماً آخر أو /cancel.")
        return True
    try:
        stored = set_user_state(user_id, STATE_THUMB_PHOTO,
                                dict(context, video_id=video_id, prompt_message_id=None))
    except Exception as exc:
        logger.error("Could not store thumbnail video state: %s", type(exc).__name__)
        stored = False
    if stored is not True:
        bot.send_message(user_id, "❌ تعذّرت متابعة العملية (خطأ في قاعدة البيانات).")
        return True
    bot.send_message(user_id, PROMPT_PHOTO, reply_markup=_prompt_markup())
    return True


def receive_video_photo(bot, admin_ids, message):
    gated = _gate(STATE_THUMB_PHOTO, message, admin_ids)
    if gated is None:
        return False
    user_id, context = gated
    video_id = context.get('video_id')
    if not video_id:
        clear_user_waiting_state(user_id)
        bot.send_message(user_id, "❌ ضاع رقم الفيديو من الطلب. ابدأ من جديد.")
        return True
    file_id = _photo_file_id(message)
    if not file_id:
        bot.send_message(user_id, "❌ أرسل صورة فقط لهذا الفيديو (أو /cancel).")
        return True
    try:
        saved = update_video_thumbnail(video_id, file_id) is True
    except Exception as exc:
        logger.error("Thumbnail update failed: %s", type(exc).__name__)
        saved = False
    if not saved:
        bot.send_message(user_id, "❌ تعذّر تحديث الصورة. أرسلها مرة أخرى أو /cancel.")
        return True
    _finish(bot, user_id, f"✅ تم تحديث الصورة المصغّرة للفيديو #{video_id}.")
    return True


def handle_message(bot, admin_ids, message):
    """True إذا عالجت هذه الرسالة تدفق صور مصغّرة مفتوحاً لهذا الأدمن."""
    for receiver in (receive_default_photo, receive_video_id, receive_video_photo):
        if receiver(bot, admin_ids, message):
            return True
    return False
