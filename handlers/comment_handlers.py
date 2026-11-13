#!/usr/bin/env python3
# ==============================================================================
# ملف: comment_handlers.py
# الوصف: معالجات نظام التعليقات الخاصة بين المستخدمين والأدمن
#        (محادثات خاصة مستمرة - كل الرسائل تُحفظ في سجل مرتب، والتاريخ القديم محفوظ)
# ==============================================================================

import json
import logging
import re
import uuid
from telebot import types
import db_manager as db
from .button_styles import (
    STYLE_DANGER,
    STYLE_PRIMARY,
    STYLE_SUCCESS,
    disabled_button,
    inline_button,
)
from utils import MAX_DISPLAY_TITLE_LENGTH, video_title
from .thread_format import (
    STATUS_ANSWERED,
    STATUS_READ,
    STATUS_WAITING,
    build_thread_text,
    compute_thread_state,
    paginate_messages,
    safe_html,
    status_label,
    thread_fingerprint,
)

logger = logging.getLogger(__name__)

STATE_WAITING_COMMENT = 'waiting_comment'
STATE_REPLYING_COMMENT = 'replying_comment'

THREAD_PAGE_SIZE = getattr(db, 'THREAD_PAGE_SIZE', 10)
# قائمة الأدمن - تُحدَّث عند تسجيل المعالجات (user_handlers.register)
ADMIN_IDS = []
# البوت المستخدم في إشعارات الأدمن الفورية (يُضبط أيضاً عند التسجيل)
_admin_notify_bot = None
# مفاتيح الإشعارات المُرسلة: (comment_id, followup) - إشعار واحد لكل استفسار/متابعة
_NOTIFIED_KEYS = []
# حد مقتطف الإشعار (مهروب قبل التقليم)
ADMIN_ALERT_EXCERPT_LENGTH = 200
PAGE_SIZE = getattr(db, 'VIDEOS_PER_PAGE', 10)


# ==============================================================================
# أدوات مشتركة (تحقق الصلاحيات والملكية والمحادثة الخاصة)
# ==============================================================================

def _parse_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_admin(user_id, admin_ids):
    try:
        return int(user_id) in set(int(a) for a in admin_ids)
    except (TypeError, ValueError):
        return False


def _call_is_private(call):
    message = getattr(call, 'message', None)
    chat = getattr(message, 'chat', None) if message else None
    return getattr(chat, 'type', None) == 'private'


def _message_is_private(message):
    chat = getattr(message, 'chat', None)
    return getattr(chat, 'type', None) == 'private'


def _answer(bot, call, text=None, alert=False):
    try:
        if text:
            bot.answer_callback_query(call.id, text, show_alert=alert)
        else:
            bot.answer_callback_query(call.id)
    except Exception as exc:
        logger.warning("Could not answer callback query: %s", exc)


def _refuse_non_private(bot, call):
    _answer(bot, call, "⛔ هذه الخاصية متاحة في المحادثة الخاصة فقط", alert=True)


def _safe_send(bot, chat_id, text, reply_markup=None, reply_to_message_id=None):
    """
    إرسال رسالة بلا استثناء يوقف المعالج.

    عند تمرير ``reply_to_message_id`` تُرسل كردّ حقيقي على تلك الرسالة (يظهر زر
    «رد» في كل العملاء)، وإن تعذّر الإرسال كردٍّ يُعاد مرة واحدة بلا ربط بدل
    فقدان الرسالة.
    """
    if reply_to_message_id is not None:
        try:
            return bot.send_message(chat_id, text, parse_mode="HTML",
                                    reply_markup=reply_markup,
                                    reply_to_message_id=reply_to_message_id)
        except Exception as exc:
            logger.warning("Reply target rejected for %s, sending unbound: %s", chat_id, exc)
    try:
        return bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as exc:
        logger.warning("Could not send message to %s: %s", chat_id, exc)
        return None


def _safe_edit(bot, chat_id, message_id, text=None, reply_markup=None):
    try:
        if text is not None:
            return bot.edit_message_text(text, chat_id, message_id, parse_mode="HTML")
        return bot.edit_message_reply_markup(chat_id, message_id, reply_markup=reply_markup)
    except Exception as exc:
        logger.warning("Could not edit message %s: %s", message_id, exc)
        return None


def _state_context(state):
    context = (state or {}).get('context') or {}
    return context if isinstance(context, dict) else {}


def _parse_context(raw):
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return raw if isinstance(raw, dict) else {}


def fetch_user_state(user_id):
    """
    قراءة حالة المستخدم عبر dunder module (db_manager.get_user_state) مع معالجة
    خامات JSONB النصية، حتى تعمل الاختبارات واستبدال الدوال كليهما.
    """
    getter = getattr(db, 'get_user_state', None)
    if getter is None:
        return None
    state = getter(user_id)
    if not state:
        return None
    return {
        'state': state.get('state') if hasattr(state, 'get') else getattr(state, 'state', None),
        'context': _parse_context(state.get('context') if hasattr(state, 'get') else getattr(state, 'context', None)),
    }


def _bound_reply_message_id(context):
    """رقم رسالة تلميح البوت المرتبطة بالطلب (إن وُجد)."""
    return _parse_int(context.get('prompt_message_id'))


def is_bound_to_prompt(message, context):
    """
    هل هذه الرسالة مرتبطة بتلميح الطلب؟ (message.reply_to_message يشير لرسالة البوت)

    يُستخدم لمنع إعادة توجيه النص إلى محادثة أخرى (A/B) بسبب أزرار قديمة.
    الطلبات القديمة التي لا تحمل رقم رسالة تلميح تبقى مسموحة (الربط بالسياق فقط).
    """
    expected = _bound_reply_message_id(context)
    if expected is None:
        return True
    reply_to = getattr(message, 'reply_to_message', None)
    if reply_to is not None:
        return _parse_int(getattr(reply_to, 'message_id', None)) == expected
    bound = context.get('prompt_message')
    if isinstance(bound, dict) and bound.get('message_id') is not None:
        return _parse_int(getattr(message, 'message_id', None)) == _parse_int(bound.get('message_id'))
    # لا ردّ على تلميح: نرفض حتى لا يُوجَّه النص لمحادثة/طلب آخر
    return False


def _prompt_line(prompt_id, video_id=None):
    """سطر التمييز المرئي للطلب (يُطابق prompt_id المحفوظ في حالة المستخدم)."""
    return f"🔗 ربط الطلب: <code>{safe_html(prompt_id, 40)}</code>"


def _reply_target_for_call(call, user_id):
    """
    رقم الرسالة التي ضُغط زرها (ليكون التلميح ردّاً حقيقياً عليها).

    يُستخدم فقط إذا كانت رسالة الزر في نفس محادثة المستخدم الخاصة، فلا يُردّ
    على رسالة في محادثة أخرى أبداً.
    """
    message = getattr(call, 'message', None)
    if message is None:
        return None
    chat = getattr(message, 'chat', None)
    if chat is None or getattr(chat, 'type', None) != 'private':
        return None
    if _parse_int(getattr(chat, 'id', None)) != _parse_int(user_id):
        return None
    return _parse_int(getattr(message, 'message_id', None))


def _reply_markup(comment_id, prompt_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        inline_button(f"✍️ رد على #{comment_id} 🟢", STYLE_SUCCESS,
                      callback_data=f"reply_comment::{comment_id}"),
        inline_button(f"👁️ المحادثة #{comment_id} 🔵", STYLE_PRIMARY,
                      callback_data=f"thread_view::{comment_id}::0"),
    )
    markup.add(inline_button("↩️ إلغاء 🟡", STYLE_DANGER, callback_data=f"cancel_reply::{comment_id}"))
    return markup


def _split_callback(call):
    return str(getattr(call, 'data', '') or '').split("::")


def _thread_actions_markup(comment_id, include_read=False):
    """أزرار المحادثة للأدمن (الرد متاح دائماً حتى بعد التعليم كمقروء)."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        inline_button(f"✍️ رد على #{comment_id} 🟢", STYLE_SUCCESS,
                      callback_data=f"reply_comment::{comment_id}"),
        inline_button("👁️ المحادثة كاملة 🔵", STYLE_PRIMARY,
                      callback_data=f"thread_view::{comment_id}::0"),
    )
    if include_read:
        markup.add(inline_button("✓ مقروء 🔵", STYLE_PRIMARY, callback_data=f"mark_read::{comment_id}"))
    markup.add(inline_button(f"🗑️ حذف #{comment_id} 🔴", STYLE_DANGER,
                             callback_data=f"delete_comment::{comment_id}"))
    return markup


def _issue_prompt_id():
    return uuid.uuid4().hex[:12]


# ==============================================================================
# معالجات المستخدم
# ==============================================================================

def handle_add_comment(bot, call):
    """معالج لبدء إضافة تعليق (استفسار جديد) على فيديو"""
    try:
        if not _call_is_private(call):
            _refuse_non_private(bot, call)
            return
        user_id = call.from_user.id
        parts = _split_callback(call)
        video_id = _parse_int(parts[1]) if len(parts) > 1 else None
        if not video_id:
            _answer(bot, call, "❌ بيانات الفيديو غير صحيحة", alert=True)
            return

        prompt_id = _issue_prompt_id()
        _answer(bot, call)
        prompt_message = _safe_send(
            bot,
            user_id,
            "📝 <b>إضافة تعليق</b>\n\n"
            "الرجاء كتابة تعليقك أو استفسارك عن هذا الفيديو.\n"
            "سيتم إرساله للإدارة وسيتم الرد عليك في أقرب وقت.\n\n"
            "💡 <i>ردّ على هذه الرسالة لإرسال التعليق، أو أرسل النص مباشرة.</i>\n"
            "💡 <i>للإلغاء، اضغط /cancel</i>\n"
            + _prompt_line(prompt_id),
            reply_to_message_id=_reply_target_for_call(call, user_id),
        )
        # الربط: رقم رسالة التلميح في السياق يحدد الرسالة التي يخصها الإدخال
        db.set_user_state(user_id, STATE_WAITING_COMMENT, {
            "video_id": video_id,
            "prompt_id": prompt_id,
            "prompt_message_id": getattr(prompt_message, 'message_id', None),
        })
    except Exception as e:
        logger.error("Error in handle_add_comment: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ، حاول مرة أخرى", alert=True)


def start_followup(bot, call, admin_ids=None):
    """عضو يكتب رسالة متابعة في محادثة قائمة (تحقق ملكية قبل أي إدخال)."""
    try:
        if not _call_is_private(call):
            _refuse_non_private(bot, call)
            return
        user_id = call.from_user.id
        parts = _split_callback(call)
        comment_id = _parse_int(parts[1]) if len(parts) > 1 else None
        if not comment_id:
            _answer(bot, call, "❌ بيانات المحادثة غير صحيحة", alert=True)
            return
        owner_id = db.get_thread_owner_id(comment_id)
        if owner_id is None or int(owner_id) != int(user_id):
            _answer(bot, call, "⛔ لا تملك صلاحية الكتابة في هذه المحادثة", alert=True)
            return
        prompt_id = _issue_prompt_id()
        _answer(bot, call)
        prompt_message = _safe_send(
            bot,
            user_id,
            f"➕ <b>متابعة المحادثة #{comment_id}</b>\n\n"
            "اكتب رسالتك التالية وستُضاف إلى سجل المحادثة (رسائلك السابقة محفوظة).\n\n"
            "💡 <i>ردّ على هذه الرسالة لإضافة رسالة إلى هذه المحادثة، أو أرسل النص مباشرة.</i>\n"
            "💡 <i>للإلغاء، اضغط /cancel</i>\n"
            + _prompt_line(prompt_id),
            reply_to_message_id=_reply_target_for_call(call, user_id),
        )
        db.set_user_state(user_id, STATE_WAITING_COMMENT, {
            "comment_id": comment_id,
            "prompt_id": prompt_id,
            "prompt_message_id": getattr(prompt_message, 'message_id', None),
        })
    except Exception as e:
        logger.error("Error in start_followup: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ، حاول مرة أخرى", alert=True)


def process_comment_text(bot, message):
    """استقبال نص من عضو في حالة انتظار تعليق/متابعة (مربوط بتلميح الطلب)."""
    user_id = message.from_user.id
    try:
        if not _message_is_private(message):
            return False
        state = fetch_user_state(user_id)
        if not state or state.get('state') != STATE_WAITING_COMMENT:
            return False

        text = (message.text or "").strip()
        if not text:
            _safe_send(bot, user_id, "❌ النص فارغ. أرسل تعليقك أو /cancel.")
            return True

        context = _state_context(state)
        if not is_bound_to_prompt(message, context):
            _safe_send(
                bot,
                user_id,
                "⚠️ الرسالة غير مرتبطة بطلب التعليق الحالي.\n"
                "ردّ على رسالة البوت الأخيرة أو أعد طلب التعليق من زر «💬 التعليقات والردود».",
            )
            return True

        followup_comment_id = _parse_int(context.get('comment_id'))

        if followup_comment_id:
            _append_followup(bot, user_id, followup_comment_id, text)
            return True

        video_id = _parse_int(context.get('video_id'))
        if not video_id:
            _safe_send(bot, user_id, "❌ حدث خطأ، الرجاء المحاولة مرة أخرى")
            db.clear_user_state(user_id)
            return True

        username = message.from_user.username or message.from_user.first_name or "مستخدم"
        comment_id = db.add_comment(video_id, user_id, username, text)
        if not comment_id:
            _safe_send(bot, user_id, "❌ فشل إرسال التعليق، حاول مرة أخرى")
            return True

        db.clear_user_state(user_id)
        # إشعار فوري للأدمن (فشله لا يؤثر على تأكيد العضو أبداً)
        _notify_admins_new_comment(
            comment_id, followup=False, bot=bot, body=text,
            comment={"id": comment_id, "video_id": video_id, "user_id": user_id,
                     "username": username, "comment_text": text})
        _safe_send(
            bot,
            user_id,
            "✅ <b>تم إرسال تعليقك بنجاح!</b>\n\n"
            f"<blockquote>{safe_html(text)}</blockquote>\n\n"
            "سيتم مراجعته من قبل الإدارة والرد عليك في أقرب وقت.\n"
            "تابع محادثتك من /my_comments ويمكنك الكتابة فيها متى شئت.",
        )
        return True

    except Exception as e:
        logger.error("Error in process_comment_text: %s", e, exc_info=True)
        try:
            bot.send_message(user_id, "❌ حدث خطأ، حاول مرة أخرى")
        except Exception:
            pass
        return True


def _append_followup(bot, user_id, comment_id, text):
    """إضافة رسالة متابعة مع تحقق ملكية (لا يوجّه النص لمحادثة عضو آخر أبداً)."""
    owner_id = db.get_thread_owner_id(comment_id)
    db.clear_user_state(user_id)
    if owner_id is None:
        _safe_send(bot, user_id, "❌ المحادثة غير موجودة.")
        return False
    if int(owner_id) != int(user_id):
        _safe_send(bot, user_id, "⛔ لا تملك صلاحية الكتابة في هذه المحادثة.")
        return False
    message_id = db.append_thread_message(comment_id, db.ROLE_MEMBER, user_id, text)
    if not message_id:
        _safe_send(bot, user_id, "❌ فشل إرسال رسالتك، حاول مرة أخرى")
        return False
    _notify_admins_new_comment(comment_id, followup=True, bot=bot, body=text)
    _safe_send(
        bot,
        user_id,
        "✅ <b>تم إرسال رسالتك إلى الإدارة</b>\n\n"
        f"<blockquote>{safe_html(text)}</blockquote>\n\n"
        f"💬 محادثة #{comment_id} - تابع الردود من /my_comments",
    )
    return True


def _notify_admins_new_comment(comment_id, followup=False, comment=None, bot=None, body=None):
    """
    إشعار فوري لكل الأدمن (من handlers.user_handlers عند التسجيل) باستفسار/متابعة جديدة.

    - لا يرمي استثناء أبداً: فشل إشعار أي أدمن يُسجَّل كتحذير ولا يفشل عمل العضو.
    - لا يعيد إرسال الإشعار لنفس العنوان (comment_id, followup) مرتين.
    - ``comment``/``body`` يُمرَّران من السياق (الرد في المحادثة) لتفادي استعلامات إضافية.
    """
    try:
        key = (int(comment_id), bool(followup))
    except (TypeError, ValueError):
        return False
    if key in _NOTIFIED_KEYS:
        return False
    _NOTIFIED_KEYS.append(key)
    try:
        notifier = bot if bot is not None else _admin_notify_bot
        if notifier is None:
            logger.warning("Admin notification for comment %s skipped: no bot bound", comment_id)
            return False
        comment = comment or db.get_comment_by_id(comment_id)
        if not comment:
            logger.warning("Could not load comment %s for admin notification", comment_id)
            return False
        text = _admin_alert_content(comment, followup, body)
        markup = _admin_alert_markup(comment_id)
        for admin_id in sorted(set(int(a) for a in ADMIN_IDS)):
            if int(admin_id) == _author_id(comment):
                continue
            try:
                notifier.send_message(admin_id, text, parse_mode="HTML", reply_markup=markup)
            except Exception as exc:
                logger.warning("Could not notify admin %s about comment %s: %s",
                               admin_id, comment_id, exc)
        return True
    except Exception as exc:
        logger.warning("Admin notification for comment %s failed: %s", comment_id, exc)
        return False


def _author_id(comment):
    try:
        return int(comment.get('user_id'))
    except (AttributeError, TypeError, ValueError):
        return None


_TITLE_FIELDS = ('display_title', 'video_caption', 'caption', 'video_name', 'file_name')


def _has_title(comment):
    return any(comment.get(field) for field in _TITLE_FIELDS)


def _video_for(comment_id):
    """أفضل جهد لجلب صف الفيديو لعنوان الإشعار من رقم التعليق (رمز تعليق فقط)."""
    getter = getattr(db, 'get_comment_by_id', None)
    if getter is None:
        return None
    try:
        fetched = getter(comment_id)
    except Exception as exc:
        logger.warning("Could not load placeholder comment %s: %s", comment_id, exc)
        return None
    video_id = _parse_int((fetched or {}).get('video_id'))
    if not video_id:
        return None
    video_getter = getattr(db, 'get_video_title_source', None)
    if video_getter is None:
        return None
    try:
        return video_getter(video_id)
    except Exception as exc:
        logger.warning("Could not load video %s for comment %s: %s", video_id, comment_id, exc)
        return None


def _admin_alert_markup(comment_id):
    """أزرار الإشعار الفوري: رد الآن + عرض المحادثة كاملة."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        inline_button("✍️ رد الآن 🟢", STYLE_SUCCESS, callback_data=f"reply_comment::{comment_id}"),
        inline_button("👁️ المحادثة كاملة 🔵", STYLE_PRIMARY, callback_data=f"thread_view::{comment_id}::0"),
    )
    return markup


def _admin_alert_content(comment, followup=False, body=None):
    """نص الإشعار: العنوان + العضو + مقتطف قصير مهروب + رقم المحادثة."""
    comment_id = _parse_int(comment.get('id')) or comment.get('id')
    excerpt_source = body or comment.get('comment_text')
    if followup and not excerpt_source:
        try:
            latest = db.get_latest_thread_message(comment_id)
        except Exception:
            latest = None
        excerpt_source = (latest or {}).get('body')
    header = "➕ <b>رسالة متابعة جديدة</b>" if followup else "📥 <b>استفسار جديد</b>"
    # عند عدم توفر عنوان محفوظ في الصف نعيد تعبئة العنوان من جدول الفيديو (أفضل جهد)
    title_source = comment if _has_title(comment) else (_video_for(comment_id) or comment)
    title = safe_html(video_title(title_source), 80) or "غير متوفر"
    text = (
        f"{header}\n"
        f"💬 محادثة #{safe_html(comment_id, 20)}\n"
        f"📹 الفيديو: {title}\n"
        f"👤 العضو: @{safe_html(comment.get('username') or 'مستخدم', 40)} "
        f"(ID: {safe_html(comment.get('user_id'), 20)})\n\n"
        f"<b>المقتطف:</b>\n<blockquote>{safe_html(excerpt_source, ADMIN_ALERT_EXCERPT_LENGTH)}</blockquote>"
    )
    return text


def show_user_comments(bot, user_id, page=0, caller=None, message=None):
    """عرض محادثات المستخدم (بلا استثناءات عند غياب هوية المستخدم)."""
    try:
        page = max(_parse_int(page, 0) or 0, 0)
        if caller is not None:
            if not _call_is_private(caller):
                _refuse_non_private(bot, caller)
                return
            user_id = caller.from_user.id
            message = getattr(caller, 'message', None)
        elif message is not None:
            user_id = message.from_user.id
            if not _message_is_private(message):
                _safe_send(bot, user_id, "⛔ هذا الأمر متاح في المحادثة الخاصة فقط")
                return
        if not user_id:
            return

        comments, total = db.get_user_comments(user_id, page)

        if not comments:
            _safe_send(
                bot,
                user_id,
                "📭 <b>لا توجد محادثات</b>\n\n"
                "لم تقم بإضافة أي تعليقات بعد.\n"
                "يمكنك إضافة تعليق على أي فيديو من خلال زر «💬 التعليقات والردود»",
            )
            if caller is not None:
                _answer(bot, caller)
            return

        for comment in comments:
            _send_member_thread_summary(bot, user_id, comment)

        total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
        markup = types.InlineKeyboardMarkup(row_width=3)
        nav = []
        if page > 0:
            nav.append(inline_button("◀️ السابق", STYLE_PRIMARY, callback_data=f"my_comments::{page - 1}"))
        nav.append(disabled_button(f"📄 {page + 1}/{total_pages}", STYLE_PRIMARY))
        if (page + 1) * PAGE_SIZE < total:
            nav.append(inline_button("التالي ▶️", STYLE_PRIMARY, callback_data=f"my_comments::{page + 1}"))
        markup.row(*nav)

        target = getattr(caller, 'message', None) if caller is not None else None
        if target is not None:
            _safe_edit(bot, target.chat.id, target.message_id, reply_markup=markup)
        else:
            _safe_send(bot, user_id, "🔽 التنقل بين المحادثات:", markup)
        if caller is not None:
            _answer(bot, caller)

    except Exception as e:
        logger.error("Error in show_user_comments: %s", e, exc_info=True)
        try:
            bot.send_message(user_id, "❌ حدث خطأ، حاول مرة أخرى")
        except Exception:
            pass


def _member_state(comment):
    try:
        latest = db.get_latest_thread_message(comment['id'])
    except Exception as exc:
        logger.warning("Could not read latest message for comment %s: %s", comment.get('id'), exc)
        latest = None
    return latest, compute_thread_state(latest, comment.get('member_read_at'), 'member')


def _thread_state_for_admin(comment):
    try:
        latest = db.get_latest_thread_message(comment['id'])
    except Exception as exc:
        logger.warning("Could not read latest message for comment %s: %s", comment.get('id'), exc)
        latest = None
    return latest, compute_thread_state(latest, comment.get('admin_read_at'), 'admin')


def _send_member_thread_summary(bot, user_id, comment):
    """ملخص محادثة واحد للعضو: الحالة (قرأت/في انتظار الرد) وآخر نص كاملاً."""
    try:
        title = safe_html(comment.get('display_title') or comment.get('video_caption')
                          or comment.get('video_name') or "فيديو", 80)
        latest, state = _member_state(comment)
        preview = safe_html((latest or {}).get('body') or comment.get('comment_text'))
        text = (
            f"💬 <b>محادثة #{comment['id']}</b>\n"
            f"📹 الفيديو: {title}\n"
            f"📌 الحالة: {status_label(state, 'member')}\n\n"
            f"{preview}"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            inline_button("👁️ عرض المحادثة", STYLE_PRIMARY, callback_data=f"thread::{comment['id']}::0"),
            inline_button("➕ اكتب رسالة", STYLE_SUCCESS, callback_data=f"thread_write::{comment['id']}"),
        )
        _safe_send(bot, user_id, text, markup)
    except Exception as e:
        logger.error("Could not render member thread %s: %s", comment.get('id'), e, exc_info=True)


def _paginate_thread(comment_id, page):
    """جلب كل رسائل المحادثة ثم تقسيمها لصفحات كاملة (لا قطع لأي رسالة)."""
    messages, total = db.get_thread_messages(comment_id, page=0, page_size=10000)
    if not messages:
        messages, total = [], total or 0
    pages = paginate_messages(messages)
    total_pages = max(len(pages), (total + THREAD_PAGE_SIZE - 1) // THREAD_PAGE_SIZE if total else 1)
    return messages, pages, max(len(pages), 1), total_pages


def _thread_nav_markup(comment_id, page, total_pages, prefix, extra_rows=None):
    markup = types.InlineKeyboardMarkup(row_width=3)
    nav = []
    if page > 0:
        nav.append(inline_button("◀️ السابق", STYLE_PRIMARY, callback_data=f"{prefix}::{comment_id}::{page - 1}"))
    nav.append(disabled_button(f"📄 {page + 1}/{total_pages}", STYLE_PRIMARY))
    if page < total_pages - 1:
        nav.append(inline_button("التالي ▶️", STYLE_PRIMARY, callback_data=f"{prefix}::{comment_id}::{page + 1}"))
    markup.row(*nav)
    for row in extra_rows or []:
        markup.row(*row)
    return markup


def open_member_thread(bot, call, page=None):
    """عرض محادثة كاملة للعضو (تحقق ملكية + علامة قراءة)."""
    try:
        if not _call_is_private(call):
            _refuse_non_private(bot, call)
            return
        user_id = call.from_user.id
        parts = _split_callback(call)
        comment_id = _parse_int(parts[1]) if len(parts) > 1 else None
        page = max(_parse_int(parts[2], 0) or 0, 0) if len(parts) > 2 else max(_parse_int(page, 0) or 0, 0)
        if not comment_id:
            _answer(bot, call, "❌ بيانات المحادثة غير صحيحة", alert=True)
            return
        comment = db.get_comment_by_id(comment_id)
        if not comment:
            _answer(bot, call, "❌ المحادثة غير موجودة", alert=True)
            return
        if int(comment['user_id']) != int(user_id):
            _answer(bot, call, "⛔ لا تملك صلاحية عرض هذه المحادثة", alert=True)
            return
        messages, pages, total_pages, _legacy_pages = _paginate_thread(comment_id, page)
        page = min(page, total_pages - 1)
        text = build_thread_text(comment, pages[page], page, total_pages, 'member')
        db.mark_thread_preferred(comment_id, db.ROLE_MEMBER)
        markup = _thread_nav_markup(
            comment_id, page, total_pages, "thread",
            extra_rows=[[inline_button("➕ اكتب رسالة متابعة 🟢", STYLE_SUCCESS,
                                       callback_data=f"thread_write::{comment_id}")]],
        )
        _safe_send(bot, user_id, text, markup)
        _answer(bot, call)
    except Exception as e:
        logger.error("Error in open_member_thread: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ، حاول مرة أخرى", alert=True)


# ==============================================================================
# معالجات الأدمن
# ==============================================================================

def show_all_comments(bot, user_id, admin_ids, page=0, unread_only=False):
    """عرض جميع المحادثات للأدمن (HTML آمن، الرد متاح دائماً)."""
    try:
        if not _is_admin(user_id, admin_ids):
            _safe_send(bot, user_id, "⛔ هذا الأمر للإدارة فقط")
            return
        page = max(_parse_int(page, 0) or 0, 0)
        comments, total = db.get_all_comments(page, unread_only)

        filter_text = "غير المقروءة" if unread_only else "جميع"

        if not comments:
            _safe_send(bot, user_id, f"📭 <b>لا توجد تعليقات {filter_text}</b>")
            return

        unread_count = db.get_unread_comments_count()
        _safe_send(
            bot,
            user_id,
            f"📬 <b>التعليقات {filter_text}</b>\n"
            f"🔔 غير المقروءة: {safe_html(unread_count, 10)}\n"
            f"📊 المجموع: {safe_html(total, 10)}",
        )

        for comment in comments:
            _send_admin_thread_summary(bot, user_id, comment)

        total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
        markup = types.InlineKeyboardMarkup(row_width=3)
        nav = []
        prefix = "admin_comments_unread" if unread_only else "admin_comments"
        if page > 0:
            nav.append(inline_button("◀️ السابق", STYLE_PRIMARY, callback_data=f"{prefix}::{page - 1}"))
        nav.append(disabled_button(f"📄 {page + 1}/{total_pages}", STYLE_PRIMARY))
        if (page + 1) * PAGE_SIZE < total:
            nav.append(inline_button("التالي ▶️", STYLE_PRIMARY, callback_data=f"{prefix}::{page + 1}"))
        if nav:
            markup.row(*nav)
        markup.row(inline_button(
            "📋 عرض الكل 🔵" if unread_only else "🔔 غير المقروءة فقط 🟡",
            STYLE_PRIMARY,
            callback_data="admin_comments::0" if unread_only else "admin_comments_unread::0",
        ))
        _safe_send(bot, user_id, "🔽 الخيارات:", markup)

    except Exception as e:
        logger.error("Error in show_all_comments: %s", e, exc_info=True)
        try:
            bot.send_message(user_id, "❌ حدث خطأ، حاول مرة أخرى")
        except Exception:
            pass


def _send_admin_thread_summary(bot, admin_id, comment):
    """ملخص محادثة واحد للأدمن مع أزرار الرد (بلا استثناء يوقف العرض)."""
    try:
        comment_id = comment['id']
        latest, state = _thread_state_for_admin(comment)
        title = safe_html(comment.get('display_title') or comment.get('video_caption')
                          or comment.get('video_name') or "فيديو", 80)
        username = safe_html(comment.get('username') or "", 40)
        body = safe_html((latest or {}).get('body') or comment.get('comment_text'))
        text = (
            f"💬 <b>محادثة #{comment_id}</b> — {status_label(state, 'admin')}\n"
            f"👤 العضو: @{username} (ID: {safe_html(comment.get('user_id'))})\n"
            f"📹 الفيديو: {title}\n\n"
            f"<b>آخر رسالة:</b>\n{body}"
        )
        _safe_send(bot, admin_id, text, _thread_actions_markup(comment_id, include_read=not comment.get('is_read')))
    except Exception as e:
        logger.error("Could not render admin thread %s: %s", comment.get('id'), e, exc_info=True)


def view_admin_thread(bot, call, admin_ids, page=None):
    """عرض محادثة كاملة للأدمن (خاص فقط) مع أزرار الرد على أي صفحة."""
    try:
        user_id = call.from_user.id
        if not _is_admin(user_id, admin_ids):
            _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
            return
        if not _call_is_private(call):
            _refuse_non_private(bot, call)
            return
        parts = _split_callback(call)
        comment_id = _parse_int(parts[1]) if len(parts) > 1 else None
        page = max(_parse_int(parts[2], 0) or 0, 0) if len(parts) > 2 else max(_parse_int(page, 0) or 0, 0)
        if not comment_id:
            _answer(bot, call, "❌ بيانات المحادثة غير صحيحة", alert=True)
            return
        comment = db.get_comment_by_id(comment_id)
        if not comment:
            _answer(bot, call, "❌ المحادثة غير موجودة", alert=True)
            return
        messages, pages, total_pages, _legacy_pages = _paginate_thread(comment_id, page)
        page = min(page, total_pages - 1)
        text = build_thread_text(comment, pages[page], page, total_pages, 'admin')
        db.mark_thread_preferred(comment_id, db.ROLE_ADMIN)
        markup = _thread_nav_markup(
            comment_id, page, total_pages, "thread_view",
            extra_rows=[[inline_button(f"✍️ رد على #{comment_id} 🟢", STYLE_SUCCESS,
                                       callback_data=f"reply_comment::{comment_id}")]],
        )
        _safe_send(bot, user_id, text, markup)
        _answer(bot, call)
    except Exception as e:
        logger.error("Error in view_admin_thread: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ، حاول مرة أخرى", alert=True)


def handle_reply_comment(bot, call, admin_ids):
    """بدء طلب رد: يربط الطلب بمحادثة محددة وببصمة حالتها لحظة الفتح."""
    try:
        user_id = call.from_user.id
        if not _is_admin(user_id, admin_ids):
            _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
            return
        if not _call_is_private(call):
            _refuse_non_private(bot, call)
            return
        parts = _split_callback(call)
        comment_id = _parse_int(parts[1]) if len(parts) > 1 else None
        if not comment_id:
            _answer(bot, call, "❌ رقم المحادثة غير صالح", alert=True)
            return
        comment = db.get_comment_by_id(comment_id)
        if not comment:
            _answer(bot, call, "❌ المحادثة غير موجودة", alert=True)
            return

        latest = db.get_latest_thread_message(comment_id)
        fingerprint = thread_fingerprint(latest)

        # زر قديم يحمل بصمة صريحة (reply_comment::id::v<id>): لا نوجّه صامتاً
        if len(parts) > 2 and parts[2]:
            expected = parts[2][1:] if parts[2].startswith("v") else parts[2]
            if expected != fingerprint:
                _answer(bot, call,
                        "⚠️ وصلت رسالة جديدة بعد فتح زر الرد. راجع المحادثة ثم اضغط «رد» مجدداً.",
                        alert=True)
                return

        prompt_id = _issue_prompt_id()
        _answer(bot, call)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(inline_button("↩️ إلغاء الرد 🟡", STYLE_DANGER,
                                 callback_data=f"cancel_reply::{comment_id}"))
        prompt_message = _safe_send(
            bot,
            user_id,
            f"✍️ <b>الرد على المحادثة #{comment_id}</b>\n\n"
            "ردّ على هذه الرسالة بنص الرد حتى يُربط بالمحادثة الصحيحة.\n"
            "سيُحفظ في سجل المحادثة ويُرسل للعضو.\n\n"
            "💡 <i>للإلغاء، اضغط /cancel</i>\n"
            + _prompt_line(prompt_id),
            markup,
        )
        db.set_user_state(user_id, STATE_REPLYING_COMMENT, {
            "comment_id": comment_id,
            "admin_id": user_id,
            "prompt_id": prompt_id,
            "updated_before": fingerprint,
            "prompt_message_id": getattr(prompt_message, 'message_id', None),
        })
    except Exception as e:
        logger.error("Error in handle_reply_comment: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ، حاول مرة أخرى", alert=True)


def handle_cancel_reply(bot, call, admin_ids):
    """إلغاء طلب الرد الجاري (لا يحذف أي رسالة محفوظة)."""
    try:
        user_id = call.from_user.id
        if not _is_admin(user_id, admin_ids):
            _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
            return
        parts = _split_callback(call)
        comment_id = _parse_int(parts[1]) if len(parts) > 1 else None
        state = fetch_user_state(user_id)
        context = _state_context(state)
        if state and state.get('state') == STATE_REPLYING_COMMENT and (
            comment_id is None or _parse_int(context.get('comment_id')) == comment_id
        ):
            db.clear_user_state(user_id)
            _answer(bot, call, "✅ تم إلغاء طلب الرد")
        else:
            _answer(bot, call, "لا يوجد طلب رد جارٍ")
        message = getattr(call, 'message', None)
        if message is not None:
            _safe_edit(bot, message.chat.id, message.message_id, reply_markup=None)
    except Exception as e:
        logger.error("Error in handle_cancel_reply: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ")


def evaluate_reply_binding(state, current_admin_id, admin_ids, latest):
    """
    هل نص الرسالة الحالية ردٌّ سليمٌ على المحادثة المرتبطة بالحالة؟

    Returns:
        tuple: (ok, reason, comment_id)
    """
    if not state or state.get('state') != STATE_REPLYING_COMMENT:
        return False, 'no_state', None
    context = _state_context(state)
    comment_id = _parse_int(context.get('comment_id'))
    if not _is_admin(current_admin_id, admin_ids):
        return False, 'not_admin', comment_id
    bound_admin = _parse_int(context.get('admin_id'))
    if bound_admin is not None and bound_admin != int(current_admin_id):
        return False, 'other_admin', comment_id
    if not comment_id:
        return False, 'no_comment', None
    expected = context.get('updated_before')
    if expected is not None and expected != thread_fingerprint(latest):
        return False, 'stale', comment_id
    return True, 'ok', comment_id


def deliver_reply(bot, comment, reply_text):
    """إشعار العضو بالرد (منفصل عن حفظه). يعيد 'delivered' | 'blocked' | 'failed'."""
    body = safe_html(reply_text, 2000)
    title = safe_html(comment.get('display_title') or comment.get('video_caption')
                      or comment.get('video_name') or "فيديو", 80)
    text = (
        "📬 <b>رد جديد على محادثتك!</b>\n\n"
        f"📹 الفيديو: {title}\n"
        f"💬 محادثة #{comment['id']}\n\n"
        f"<b>رد الإدارة:</b>\n<blockquote>{body}</blockquote>\n\n"
        "اكتب /my_comments لمتابعة المحادثة"
    )
    try:
        bot.send_message(int(comment['user_id']), text, parse_mode="HTML")
        return 'delivered'
    except Exception as exc:
        description = str(getattr(exc, 'description', '') or exc).lower()
        if 'blocked' in description or 'chat not found' in description or 'deactivated' in description:
            logger.warning("Reply notification to user %s not delivered (blocked/missing)",
                           comment.get('user_id'))
            return 'blocked'
        logger.error("Reply notification to user %s failed: %s", comment.get('user_id'), exc)
        return 'failed'


def process_reply_text(bot, message, admin_ids):
    """استقبال نص الرد من الأدمن: يحفظ أولاً ثم يبلّغ عن نتيجة الإشعار بصدق."""
    user_id = message.from_user.id
    try:
        if not _message_is_private(message):
            return False
        state = fetch_user_state(user_id)
        if not state or state.get('state') != STATE_REPLYING_COMMENT:
            return False
        if not _is_admin(user_id, admin_ids):
            db.clear_user_state(user_id)
            _safe_send(bot, user_id, "⛔ لم تعد لديك صلاحية الرد. لم يُحفظ أي شيء.")
            return True

        reply_text = (message.text or "").strip()
        if not reply_text:
            _safe_send(bot, user_id, "❌ نص الرد فارغ. أو أرسل /cancel.")
            return True

        context = _state_context(state)
        if not is_bound_to_prompt(message, context):
            _safe_send(
                bot,
                user_id,
                "⚠️ هذه الرسالة غير مرتبطة بطلب الرد الحالي.\n"
                "ردّ على رسالة البوت الأخيرة الخاصة بالمحادثة قبل إرسال الرد.",
            )
            return True

        pending_comment_id = _parse_int(context.get('comment_id'))
        try:
            latest = db.get_latest_thread_message(pending_comment_id) if pending_comment_id else None
        except Exception as exc:
            logger.warning("Could not read thread fingerprint: %s", exc)
            latest = None

        ok, reason, comment_id = evaluate_reply_binding(state, user_id, admin_ids, latest)
        if not ok:
            db.clear_user_state(user_id)
            messages = {
                'not_admin': "⛔ لم تعد لديك صلاحية الرد. لم يُحفظ أي شيء.",
                'other_admin': "⚠️ طلب الرد هذا يخص أدمن آخر. لم يُحفظ أي شيء.",
                'stale': (f"⚠️ وصلت رسالة جديدة إلى المحادثة #{comment_id} بعد فتح طلب الرد.\n"
                          "لم يُحفظ ردك حتى لا يوجَّه للمحادثة الخطأ. راجع المحادثة ثم اضغط «رد» من جديد."),
                'no_comment': "❌ لا توجد محادثة مرتبطة بطلب الرد. لم يُحفظ أي شيء.",
            }
            _safe_send(bot, user_id, messages.get(reason, "❌ لا يوجد طلب رد جارٍ. لم يُحفظ أي شيء."))
            return True

        comment = db.get_comment_by_id(comment_id)
        if not comment:
            db.clear_user_state(user_id)
            _safe_send(bot, user_id, "❌ المحادثة غير موجودة. لم يُحفظ أي شيء.")
            return True

        result = db.reply_in_thread(comment_id, user_id, reply_text) or {}
        db.clear_user_state(user_id)

        if result.get('status') != 'ok':
            _safe_send(bot, user_id, "❌ فشل حفظ الرد - لم يُرسل أي إشعار. حاول مرة أخرى.")
            return True

        delivery = deliver_reply(bot, comment, reply_text)
        if delivery == 'delivered':
            note = "📬 وتم إشعار العضو برسالتك."
        elif delivery == 'blocked':
            note = ("⚠️ <b>الرد محفوظ في المحادثة</b>، لكن تعذّر إشعار العضو "
                    "(ربما أوقف المحادثة مع البوت). سيقرأه عند فتح /my_comments.")
        else:
            note = ("⚠️ <b>الرد محفوظ في المحادثة</b>، لكن فشل إرسال الإشعار للعضو. "
                    "لا تعد إرساله - سيظهر في سجل المحادثة.")
        _safe_send(
            bot,
            user_id,
            f"✅ <b>تم حفظ ردك في المحادثة #{comment_id}</b>\n\n{note}",
        )
        return True

    except Exception as e:
        logger.error("Error in process_reply_text: %s", e, exc_info=True)
        try:
            bot.send_message(user_id, "❌ حدث خطأ، حاول مرة أخرى")
        except Exception:
            pass
        return True


def handle_mark_read(bot, call, admin_ids):
    """تعليم المحادثة كمقروءة مع الإبقاء على أزرار الرد."""
    try:
        user_id = call.from_user.id
        if not _is_admin(user_id, admin_ids):
            _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
            return
        parts = _split_callback(call)
        comment_id = _parse_int(parts[1]) if len(parts) > 1 else None
        if not comment_id:
            _answer(bot, call, "❌ رقم المحادثة غير صالح", alert=True)
            return
        if not db.get_comment_by_id(comment_id):
            _answer(bot, call, "❌ المحادثة غير موجودة", alert=True)
            return

        if db.mark_comment_read_for_admin(comment_id):
            markup = _thread_actions_markup(comment_id, include_read=False)
            message = getattr(call, 'message', None)
            if message is not None:
                _safe_edit(bot, message.chat.id, message.message_id, reply_markup=markup)
            _answer(bot, call, "✅ تم تعليم المحادثة كمقروءة")
        else:
            _answer(bot, call, "❌ فشل تحديث المحادثة")
    except Exception as e:
        logger.error("Error in handle_mark_read: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ")


def handle_delete_comment(bot, call, admin_ids):
    """معالج لحذف تعليق"""
    try:
        user_id = call.from_user.id

        if not _is_admin(user_id, admin_ids):
            _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
            return

        parts = _split_callback(call)
        comment_id = _parse_int(parts[1]) if len(parts) > 1 else None
        if not comment_id:
            _answer(bot, call, "❌ رقم المحادثة غير صالح", alert=True)
            return

        markup = types.InlineKeyboardMarkup()
        markup.row(
            inline_button("🗑️ تأكيد الحذف 🔴", STYLE_DANGER, callback_data=f"confirm_delete_comment::{comment_id}"),
            inline_button("↩️ إلغاء 🟢", STYLE_SUCCESS, callback_data="noop")
        )

        _answer(bot, call)
        _safe_send(
            bot,
            user_id,
            f"⚠️ <b>تأكيد الحذف</b>\n\nهل أنت متأكد من حذف المحادثة #{comment_id} وكل رسائلها؟",
            markup,
        )

    except Exception as e:
        logger.error("Error in handle_delete_comment: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ")


def confirm_delete_comment(bot, call, admin_ids):
    """تأكيد حذف التعليق"""
    try:
        user_id = call.from_user.id

        if not _is_admin(user_id, admin_ids):
            _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
            return

        parts = _split_callback(call)
        comment_id = _parse_int(parts[1]) if len(parts) > 1 else None
        if not comment_id:
            _answer(bot, call, "❌ رقم المحادثة غير صالح", alert=True)
            return

        if db.delete_comment(comment_id):
            _answer(bot, call, "✅ تم حذف المحادثة")
            message = getattr(call, 'message', None)
            if message is not None:
                _safe_edit(bot, message.chat.id, message.message_id,
                           "🗑️ <b>تم حذف المحادثة بنجاح</b>")
        else:
            _answer(bot, call, "❌ فشل حذف المحادثة")

    except Exception as e:
        logger.error("Error in confirm_delete_comment: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ")


# ==============================================================================
# تعديل عنوان العرض (display title) - للأدمن فقط
# ==============================================================================

STATE_EDITING_TITLE = 'editing_display_title'
# انتظار رقم الفيديو لتدفق تعديل العنوان (يحل مكان register_next_step_handler)
STATE_TITLE_VIDEO_ID = 'waiting_title_video_id'
# البحث بالاسم والصيانة: يستقبل «الاسم الحالي» أو «قديم => جديد»
STATE_TITLE_NAME = 'title_rename_search'
# بعد اختيار الفيديو: يستقبل الاسم الجديد
STATE_TITLE_NEW_NAME = 'title_rename_new'
# زر إلغاء طلب رقم الفيديو (قبل معرفة الرقم)
CALLBACK_CANCEL_TITLE_ID = 'admin::cancel_title_id'


def _title_context(state):
    return _state_context(state) if state and state.get('state') == STATE_EDITING_TITLE else {}


def _title_id_context(state):
    return _state_context(state) if state and state.get('state') == STATE_TITLE_VIDEO_ID else {}


_ARABIC_INDIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


def parse_title_video_id(text):
    """تحويل إدخال الأدمن إلى رقم فيديو: يقبل الأرقام العربية-الهندية والمسافات و'#' بادئة."""
    cleaned = str(text or '').translate(_ARABIC_INDIC_DIGITS)
    cleaned = cleaned.replace('#', ' ').replace('＃', ' ')
    cleaned = ' '.join(cleaned.split())
    return _parse_int(cleaned)


def _title_id_prompt(tried=None, error=None):
    """نص طلب رقم الفيديو: يوضّح الأخطاء ويبقي الإدخال متاحاً للمحاولة من جديد."""
    lines = []
    if error:
        lines.append(error)
        lines.append("")
    lines.append("📝 <b>تعديل عنوان فيلم - رقم الفيديو</b>")
    lines.append("")
    if tried:
        lines.append(f"أرسل رقم الفيديو (ID) جديد ({safe_html(tried, 20)}):")
    else:
        lines.append("أرسل رقم الفيديو (ID):")
    lines.append("✏️ اكتب الرقم فقط، مثلاً: <code>12</code> أو <code>#12</code>")
    lines.append("💡 الأرقام تظهر في قائمة الفيديوهات وفي محادثات التعليقات (فيديو #ID).")
    lines.append("↩️ للإلغاء أرسل /cancel")
    return "\n".join(lines)


def _title_video_id_prompt_markup():
    """زر إلغاء مستقل عن dispatch_title_action (لا يعتمد على شكل call.data)."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(inline_button("↩️ إلغاء 🟡", STYLE_DANGER, callback_data=CALLBACK_CANCEL_TITLE_ID))
    return markup


def prompt_title_video_id(bot, user_id):
    """بدء تدفق تعديل العنوان: يحفظ الحالة في DB ويرسل طلب رقم الفيديو."""
    if not _is_admin(user_id, ADMIN_IDS):
        _safe_send(bot, user_id, "⛔ هذا الأمر للإدارة فقط")
        return False
    try:
        db.set_user_state(user_id, STATE_TITLE_VIDEO_ID, {"admin_id": user_id})
    except Exception as exc:
        logger.error("Could not store title video id state for %s: %s", user_id, exc, exc_info=True)
        _safe_send(bot, user_id, "❌ تعذّر بدء تعديل العنوان (خطأ في قاعدة البيانات). حاول مرة أخرى.")
        return False
    _safe_send(bot, user_id, _title_id_prompt(), _title_video_id_prompt_markup())
    return True


def fetch_video_title_source(video_id):
    """قراءة مصدر عنوان الفيديو عبر dunder module (يعمل مع الاستبدال في الاختبارات)."""
    getter = getattr(db, 'get_video_title_source', None)
    if getter is None:
        return None
    return getter(video_id)


def _title_prompt_markup():
    """أزرار طلب العنوان الجديد (إلغاء فقط قبل إدخال نص)."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(inline_button("↩️ إلغاء 🟡", STYLE_DANGER, callback_data=CALLBACK_CANCEL_TITLE_ID))
    return markup


def cancel_title_video_id(bot, call, user_id=None):
    """إلغاء طلب رقم الفيديو (زر أو أمر): يمسح الحالة ويعيد المحادثة لمسارها العادي."""
    has_call = call is not None
    caller = user_id if user_id is not None else call.from_user.id
    if not _is_admin(caller, ADMIN_IDS):
        if has_call:
            _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
        return
    db.clear_user_state(caller)
    if has_call:
        _answer(bot, call, "✅ تم الإلغاء")
    else:
        _safe_send(bot, caller, "✅ تم الإلغاء")


def process_title_video_id(bot, message, admin_ids=None):
    """استقبال رقم الفيديو كنص عادي من الأدمن (بديل next_step)."""
    user_id = message.from_user.id
    resolved_admins = admin_ids if admin_ids is not None else ADMIN_IDS
    try:
        if not _message_is_private(message):
            return False
        state = fetch_user_state(user_id)
        if not _title_id_context(state):
            return False
        if not _is_admin(user_id, resolved_admins):
            db.clear_user_state(user_id)
            _safe_send(bot, user_id, "⛔ لم تعد لديك صلاحية التعديل. لم يُحفظ شيء.")
            return True
        text = (message.text or "")
        if text.strip() == "/cancel":
            cancel_title_video_id(bot, None, user_id)
            return True
        video_id = parse_title_video_id(text)
        if not video_id or video_id <= 0:
            # الحالة تبقى كما هي ليحاول الأدمن مرة أخرى
            _safe_send(bot, user_id,
                       _title_id_prompt(tried=text,
                                        error="❌ الرقم غير صالح: أرسل رقم الفيديو بالأرقام فقط."),
                       _title_video_id_prompt_markup())
            return True

        if not start_title_edit(bot, user_id, video_id, resolved_admins):
            # الرقم لم يُقبل: الرسالة صريحة من start_title_edit، والحالة تبقى إما للمحاولة
            # من جديد (فيديو غير موجود) أو لتنفيذ /cancel عند أي خطأ آخر.
            pass
        return True
    except Exception as e:
        logger.error("Error in process_title_video_id: %s", e, exc_info=True)
        _safe_send(bot, user_id, "❌ حدث خطأ أثناء قراءة رقم الفيديو، أرسل الرقم مرة أخرى أو /cancel")
        return True


def _strip_title_command(text, video_id=None):
    """
    يقبل «/title <ID> <نص>» أو «/title <نص>» أو «عنوان: <نص>» أو النص الحر.

    يحذف الأمر ورقم الفيديو حتى لا يُحفظ الرقم كعنوان أبداً.
    """
    text = (text or "").strip()
    lowered = text.lower()
    if lowered.startswith("/title") or lowered.startswith("/edit_title"):
        command = "/edit_title" if lowered.startswith("/edit_title") else "/title"
        remainder = text[len(command):].strip()
        parts = remainder.split(maxsplit=1)
        if parts and parts[0].isdigit() and (video_id is None or int(parts[0]) == int(video_id)):
            remainder = parts[1] if len(parts) > 1 else ""
        return remainder.strip()
    for prefix in ("عنوان:", "العنوان:"):
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def _title_from_context(context):
    return _strip_title_command(context.get('title') or '', context.get('video_id'))


def _title_keyboard(video_id, video, draft=None):
    """أزرار المعاينة: حفظ/تعديل/إلغاء."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    if draft is not None:
        markup.add(inline_button("✅ حفظ العنوان 🟢", STYLE_SUCCESS,
                                 callback_data=f"admin_title::save::{video_id}"))
        markup.add(inline_button("✏️ تعديل النص 🔵", STYLE_PRIMARY,
                                 callback_data=f"admin_title::edit::{video_id}"))
    if (video or {}).get('display_title'):
        markup.add(inline_button("↩️ استعادة الأصلي 🟡", STYLE_DANGER,
                                 callback_data=f"admin_title::reset::{video_id}"))
    markup.add(inline_button("↩️ إلغاء 🔴", STYLE_DANGER,
                             callback_data=f"admin_title::cancel::{video_id}"))
    return markup


def build_title_preview(video, draft):
    """نص HTML آمن لمعاينة العنوان قبل الحفظ."""
    from utils import video_title
    original = safe_html(video_title(video), 160)
    draft_html = safe_html(draft)
    length = len(draft or '')
    warning = ""
    from utils import MAX_DISPLAY_TITLE_LENGTH
    if length > MAX_DISPLAY_TITLE_LENGTH:
        warning = (f"\n⚠️ العنوان أطول من الحد المسموح "
                   f"({safe_html(length, 6)}/{MAX_DISPLAY_TITLE_LENGTH} حرفاً).")
    return (
        f"👁️ <b>معاينة عنوان الفيديو #{video['id']}</b>\n\n"
        f"<b>العنوان الحالي:</b>\n{original}\n\n"
        f"<b>العنوان الجديد:</b>\n<pre>{draft_html}</pre>\n"
        f"📏 الطول: {safe_html(length, 6)} حرفاً{warning}\n\n"
        "<i>ملاحظة: لا يتم تعديل الكابشن في القناة - العنوان الجديد يظهر فقط داخل البوت.</i>"
    )


# ==============================================================================
# تعديل العنوان بالاسم: بحث يطابق الموجود ثم استبدال نص العنوان داخل البوت
# ==============================================================================

NAME_SEPARATOR_PATTERN = re.compile(r"\s*(?:—>|=>|==>|-{1,2}>|←|->)\s*")


def _split_name_command(text):
    """يعيد (القديم، الجديد): الجديد None إذا لم يوجد فاصل صالح."""
    raw = (text or "").strip()
    if not raw:
        return None, None
    parts = NAME_SEPARATOR_PATTERN.split(raw, maxsplit=1)
    if len(parts) != 2:
        return None, None
    return parts[0].strip(), parts[1].strip()


def parse_title_name_command(text):
    """يفهم «الاسم القديم => الاسم الجديد» (=> أو -> أو —> أو ←)."""
    old_name, new_name = _split_name_command(text)
    return old_name, new_name


def _title_rows(rows):
    """تطبيع نتائج البحث (dict أو tuple) لتحتوي id و caption و display_title."""
    normalized = []
    for row in rows or []:
        try:
            values = dict(row)
        except (TypeError, ValueError):
            values = {}
            for name, value in zip(('id', 'caption', 'file_name', 'display_title'), row):
                values[name] = value
        if values.get('id') is not None:
            normalized.append(values)
    return normalized


def _title_search_terms(query):
    """أزواج نص/كلمة البحث: النص كما هو، ثم بدونه رقم الفيديو الذي يُلحقه الأدمن."""
    terms = []
    raw = (query or "").strip()
    if not raw:
        return terms
    terms.append((raw, raw))
    stripped = _strip_trailing_video_id(raw)
    if stripped:
        terms.append((stripped, stripped))
    return terms


def _strip_trailing_video_id(text):
    """يحذف رقم الفيديو الملحق في نهاية النص («75 - موت الجبار 75») أو يبدأ به."""
    match = re.match(r"^(?P<name>.*?)(?:\s+|^)(?P<num>\d{1,7})$", text)
    if match and match.group("name").strip():
        return match.group("name").strip()
    match = re.match(r"^(?P<num>\d{1,7})\s*[-–—.):]\s*(?P<name>.+)$", text)
    if match and match.group("name").strip():
        return match.group("name").strip()
    return ""


def _search_titles(query, limit=20):
    """بحث في الأسماء الأصلية والعناوين المعدّلة معاً."""
    terms = _title_search_terms(query)
    try:
        rows = []
        for _term_text, term in terms:
            search_term = f"%{term}%"
            found = db.execute_query(
                "SELECT id, caption, file_name, display_title FROM video_archive "
                "WHERE caption ILIKE %s OR display_title ILIKE %s "
                "ORDER BY (CASE WHEN caption ILIKE %s THEN 0 ELSE 1 END), id ASC LIMIT %s",
                (search_term, search_term, search_term, limit),
                fetch='all'
            ) or []
            if found:
                rows = found
                break
        return _title_rows(rows), True
    except Exception as exc:
        message = str(exc).lower()
        if 'display_title' in message and 'column' in message:
            # قاعدة بيانات قديمة بلا عمود العنوان المعدّل: نرجع للكابشن فقط
            try:
                rows = []
                for _term_text, term in terms:
                    search_term = f"%{term}%"
                    found = db.execute_query(
                        "SELECT id, caption, file_name FROM video_archive "
                        "WHERE caption ILIKE %s ORDER BY id ASC LIMIT %s",
                        (search_term, limit), fetch='all'
                    ) or []
                    if found:
                        rows = found
                        break
                return _title_rows(rows), True
            except Exception as inner:
                logger.error("Title search fallback failed: %s", inner, exc_info=True)
                return [], False
        logger.error("Title search failed: %s", exc, exc_info=True)
        return [], False


def _title_matches(query, video):
    from utils import video_title
    for term_text, _term in _title_search_terms(query):
        wanted = term_text.casefold()
        current = video_title(video, fallback='').strip().casefold()
        original_first_line = (video.get('caption') or '').split('\n')[0].strip().casefold()
        if current == wanted or original_first_line == wanted:
            return True
    return False


def _choose_candidate(bot, user_id, video, query):
    """عرض نتيجة واحدة مطابقة تماماً ثم طلب الاسم الجديد مباشرة."""
    from utils import video_title
    title_now = safe_html(video_title(video, fallback=''), 160) or "—"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(inline_button("✅ نعم، هذا هو الفيديو 🟢", STYLE_SUCCESS,
                             callback_data=f"title_name::pick::{video['id']}"))
    markup.add(inline_button("↩️ إلغاء 🔴", STYLE_DANGER, callback_data=CALLBACK_CANCEL_TITLE_ID))
    try:
        db.set_user_state(user_id, STATE_TITLE_NAME, {"query": query, "admin_id": user_id})
    except Exception as exc:
        logger.error("Could not store title name state: %s", exc, exc_info=True)
        _safe_send(bot, user_id, "❌ تعذّر بدء تعديل الاسم (خطأ في قاعدة البيانات). حاول مرة أخرى.")
        return
    _safe_send(
        bot,
        user_id,
        f"🔎 <b>وجدت هذا الفيديو</b>\n\n"
        f"🔢 رقمه: <code>#{video['id']}</code>\n"
        f"📹 اسمه الآن: <b>{title_now}</b>\n\n"
        "أرسل الاسم الجديد الآن، أو اضغط «نعم، هذا هو الفيديو» ثم أرسل الاسم.",
        markup,
    )


def _pick_candidate(bot, call, admin_ids, video_id):
    """اختيار نتيجة من القائمة المقترحة (مع تحقق صلاحية وإعادة قراءة الفيديو)."""
    user_id = call.from_user.id
    if not _is_admin(user_id, admin_ids):
        _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
        return
    if not _call_is_private(call):
        _refuse_non_private(bot, call)
        return
    state = fetch_user_state(user_id)
    context = _title_name_context(state)
    video_id = _parse_int(video_id)
    if not video_id:
        _answer(bot, call, "❌ رقم الفيديو غير صحيح", alert=True)
        return
    try:
        video = db.get_video_title_source(video_id)
    except Exception as exc:
        logger.error("Could not read video %s: %s", video_id, exc, exc_info=True)
        _answer(bot, call, "❌ تعذّر قراءة الفيديو. حاول مرة أخرى.", alert=True)
        return
    if not video:
        _answer(bot, call, "❌ الفيديو لم يعد موجوداً.", alert=True)
        return
    from utils import video_title
    query = (context.get('query') or '').strip() or video_title(video, fallback='')
    target_caption = _replace_first_line(video.get('caption'), query)
    try:
        db.set_user_state(user_id, STATE_TITLE_NEW_NAME, {
            "video_id": video_id,
            "old_query": query,
            "target_caption": target_caption,
            "admin_id": user_id,
        })
    except Exception as exc:
        logger.error("Could not store title new-name state: %s", exc, exc_info=True)
        _answer(bot, call, "❌ فشل تجهيز الطلب. حاول مرة أخرى.", alert=True)
        return
    _answer(bot, call, "✅ تمام")
    _safe_send(
        bot,
        user_id,
        f"✍️ <b>أرسل الاسم الجديد للفيديو #{video_id}</b>\n\n"
        f"<b>الاسم الحالي:</b>\n{safe_html(query, 160)}\n\n"
        "اكتب الاسم فقط (يمكنك إضافة رقم في نهايته).\n"
        "💡 <i>للإلغاء: /cancel</i>",
    )


def _replace_first_line(caption, new_title):
    """يستبدل أول سطر من الكابشن (نص العنوان) بالاسم الجديد مع بقاء الوصف."""
    text = caption or ''
    lines = text.split('\n')
    if not lines:
        return new_title
    prefix = ''
    match = re.match(r"^(\s*[0-9]+[\).\-]?\s+)", lines[0])
    if match:
        prefix = match.group(1)
    lines[0] = f"{prefix}{new_title}"
    return '\n'.join(lines)


def _title_name_context(state):
    if not state or state.get('state') not in (STATE_TITLE_NAME, STATE_TITLE_NEW_NAME):
        return {}
    return _state_context(state)


def prompt_title_name(bot, user_id):
    """زر «تعديل اسم الفيديو»: يطلب الاسم الحالي للبحث عنه."""
    if not _is_admin(user_id, ADMIN_IDS):
        _safe_send(bot, user_id, "⛔ هذا الأمر للإدارة فقط")
        return False
    try:
        db.set_user_state(user_id, STATE_TITLE_NAME, {"admin_id": user_id})
    except Exception as exc:
        logger.error("Could not store title name state for %s: %s", user_id, exc, exc_info=True)
        _safe_send(bot, user_id, "❌ تعذّر بدء تعديل العنوان (خطأ في قاعدة البيانات). حاول مرة أخرى.")
        return False
    _safe_send(
        bot,
        user_id,
        "✏️ <b>تعديل اسم الفيديو الظاهر</b>\n\n"
        "أرسل الاسم الحالي للفيديو كما يظهر وفي نهايته رقمه إن أردت، مثل:\n"
        "<code>75 - موت الجبار</code>\n"
        "<code>75 - موت الجبار 75</code>\n\n"
        "أو مباشرة اكتب القديم والجديد:\n"
        "<code>75 - موت الجبار => الاسم الجديد</code>\n\n"
        "🔢 لم تذكر رقم الفيديو في الفيديو نفسه؟ حتى الرقم وحده بعد الاسم مقبول.\n"
        "💡 <i>للإلغاء: /cancel</i>",
        _title_video_id_prompt_markup(),
    )
    return True


def process_title_name_text(bot, message, admin_ids=None):
    """يستقبل الاسم القديم (بحث) أو «قديم => جديد» من الأدمن."""
    user_id = message.from_user.id
    resolved_admins = admin_ids if admin_ids is not None else ADMIN_IDS
    try:
        if not _message_is_private(message):
            return False
        state = fetch_user_state(user_id)
        if not state or state.get('state') != STATE_TITLE_NAME:
            return False
        if not _is_admin(user_id, resolved_admins):
            db.clear_user_state(user_id)
            _safe_send(bot, user_id, "⛔ لم تعد لديك صلاحية التعديل. لم يُحفظ شيء.")
            return True
        text = (message.text or "").strip()
        if not text:
            return True
        if text == "/cancel":
            cancel_title_video_id(bot, None, user_id)
            return True

        old_name, new_name = _split_name_command(text)
        if new_name is not None:
            if not new_name:
                # «اسم =>» بلا اسم جديد: رسالة واضحة بدل رسالة «لم أجد» المضللة
                _safe_send(
                    bot,
                    user_id,
                    "❌ لم تُكتب قيمة الاسم الجديد بعد الفاصل.\n"
                    "أرسل: <code>الاسم القديم => الاسم الجديد</code>، أو أرسل الاسم الحالي وحده للبحث.",
                )
                return True
            _start_direct_rename(bot, user_id, old_name, new_name, resolved_admins)
            return True

        _search_and_ask(bot, user_id, text)
        return True
    except Exception as exc:
        logger.error("Error in process_title_name_text: %s", exc, exc_info=True)
        _safe_send(bot, user_id, "❌ حدث خطأ، حاول مرة أخرى أو /cancel")
        return True


def _search_and_ask(bot, user_id, query):
    rows, ok = _search_titles(query, limit=10)
    if not ok:
        _safe_send(bot, user_id, "❌ تعذّر البحث الآن (خطأ في قاعدة البيانات). أرسل الاسم مرة أخرى.")
        return
    if not rows:
        _safe_send(
            bot,
            user_id,
            f"🙁 لم أجد أي فيديو باسم يحتوي «{safe_html(query, 80)}».\n\n"
            "جرّب جزءاً أقصر من الاسم، أو أرسل رقم الفيديو مباشرة.",
            _title_video_id_prompt_markup(),
        )
        return
    matches = [row for row in rows if _title_matches(query, row)]
    if len(matches) == 1:
        _choose_candidate(bot, user_id, matches[0], query)
        return
    if len(matches) > 1:
        markup = types.InlineKeyboardMarkup(row_width=1)
        for row in matches[:5]:
            from utils import video_title
            label = f"#{row['id']} — {video_title(row, fallback='')}"
            markup.add(inline_button(label[:60], STYLE_PRIMARY,
                                     callback_data=f"title_name::pick::{row['id']}"))
        markup.add(inline_button("↩️ إلغاء 🔴", STYLE_DANGER, callback_data=CALLBACK_CANCEL_TITLE_ID))
        _safe_send(
            bot,
            user_id,
            f"🔎 وجدت {len(matches)} فيديو يطابق «{safe_html(query, 80)}». اختر واحداً:",
            markup,
        )
        return

    # لا تطابق تام: نعرض أقرب النتائج ونطلب استكمال الاسم/الرقم
    from utils import video_title
    lines = []
    for row in rows[:8]:
        lines.append(f"#{row['id']} — {safe_html(video_title(row, fallback=''), 70)}")
    _safe_send(
        bot,
        user_id,
        f"🔎 لم أجد اسماً مطابقاً تماماً لـ «{safe_html(query, 80)}»، لكن هذه نتائج قريبة:\n\n"
        + "\n".join(lines)
        + "\n\nأرسل رقم الفيديو من القائمة، أو أرسل الاسم كاملاً كما هو.\n"
        "💡 يمكنك أيضاً إرسال: <code>اسم قديم => اسم جديد</code>",
        _title_video_id_prompt_markup(),
    )


def _start_direct_rename(bot, user_id, old_name, new_name, admin_ids):
    """«اسم قديم => اسم جديد»: يبحث عن القديم، ويطلب التأكيد إن تعددت النتائج."""
    rows, ok = _search_titles(old_name, limit=20)
    if not ok:
        _safe_send(bot, user_id, "❌ تعذّر البحث الآن (خطأ في قاعدة البيانات). أرسل الاسم مرة أخرى.")
        return
    matches = [row for row in rows if _title_matches(old_name, row)]
    if not matches:
        _safe_send(
            bot,
            user_id,
            f"🙁 لم أجد فيديو باسم «{safe_html(old_name, 80)}». جرّب اسماً أقصر.",
            _title_video_id_prompt_markup(),
        )
        return
    if len(matches) > 1:
        markup = types.InlineKeyboardMarkup(row_width=1)
        for row in matches[:5]:
            from utils import video_title
            markup.add(inline_button(
                f"#{row['id']} — {video_title(row, fallback='')}"[:60], STYLE_PRIMARY,
                callback_data=f"title_name::pick::{row['id']}"))
        markup.add(inline_button("↩️ إلغاء 🔴", STYLE_DANGER, callback_data=CALLBACK_CANCEL_TITLE_ID))
        _safe_send(
            bot,
            user_id,
            f"🔎 «{safe_html(old_name, 80)}» يطابق {len(matches)} فيديو. اختر واحداً:",
            markup,
        )
        return
    video = matches[0]
    _apply_name_rename(bot, user_id, video['id'], old_name, new_name, video.get('caption'),
                       admin_ids)


def process_title_new_name(bot, message, admin_ids=None):
    """استقبال الاسم الجديد بعد اختيار الفيديو مع إعادة التحقق من قاعدة البيانات."""
    user_id = message.from_user.id
    resolved_admins = admin_ids if admin_ids is not None else ADMIN_IDS
    try:
        if not _message_is_private(message):
            return False
        state = fetch_user_state(user_id)
        context = _title_name_context(state)
        if not context:
            return False
        if not _is_admin(user_id, resolved_admins):
            db.clear_user_state(user_id)
            _safe_send(bot, user_id, "⛔ لم تعد لديك صلاحية التعديل. لم يُحفظ شيء.")
            return True
        text = (message.text or "").strip()
        if text == "/cancel":
            cancel_title_video_id(bot, None, user_id)
            return True
        video_id = _parse_int(context.get('video_id'))
        old_query = context.get('old_query') or ''
        if not video_id or not old_query:
            db.clear_user_state(user_id)
            _safe_send(bot, user_id, "⚠️ انتهت صلاحية الطلب. ابدأ من جديد من لوحة الإدارة.")
            return True
        if not text:
            # الاسم الفارغ: رسالة صريحة والحالة تبقى ليُرسل الاسم من جديد (بدون كتابة في DB)
            _safe_send(
                bot,
                user_id,
                f"❌ الاسم الجديد فارغ. أرسل اسماً صالحاً للفيديو #{video_id} أو /cancel.",
            )
            return True
        if len(text) > MAX_DISPLAY_TITLE_LENGTH:
            _safe_send(
                bot,
                user_id,
                f"❌ الاسم طويل جداً (الحد {MAX_DISPLAY_TITLE_LENGTH} حرفاً). أرسل اسماً أقصر أو /cancel.",
            )
            return True
        video = db.get_video_title_source(video_id)
        if not video:
            db.clear_user_state(user_id)
            _safe_send(bot, user_id, "❌ الفيديو لم يعد موجوداً.")
            return True
        _apply_name_rename(bot, user_id, video_id, old_query, text, video.get('caption'),
                           resolved_admins)
        return True
    except Exception as exc:
        logger.error("Error in process_title_new_name: %s", exc, exc_info=True)
        _safe_send(bot, user_id, "❌ حدث خطأ، حاول مرة أخرى أو /cancel")
        return True


def _apply_name_rename(bot, user_id, video_id, old_query, new_name, caption, admin_ids):
    """
    تنفيذ التعديل: الاسم الجديد يصبح display_title (وعند الحاجة أول سطر الكابشن).

    Returns:
        bool: هل نجح التعديل
    """
    from utils import MAX_DISPLAY_TITLE_LENGTH
    new_name = (new_name or '').strip()
    if not new_name:
        _safe_send(bot, user_id, "❌ الاسم الجديد فارغ. أرسل اسماً صالحاً أو /cancel.")
        return False
    if len(new_name) > MAX_DISPLAY_TITLE_LENGTH:
        _safe_send(bot, user_id, f"❌ الاسم طويل جداً (الحد {MAX_DISPLAY_TITLE_LENGTH} حرفاً).")
        return False
    if not _is_admin(user_id, admin_ids):
        db.clear_user_state(user_id)
        _safe_send(bot, user_id, "⛔ لم تعد لديك صلاحية التعديل. لم يُحفظ شيء.")
        return False
    try:
        ok = db.set_display_title(video_id, new_name)
    except Exception as exc:
        logger.error("Title rename failed for %s: %s", video_id, exc, exc_info=True)
        _safe_send(bot, user_id, "❌ فشل الحفظ في قاعدة البيانات. حاول مرة أخرى.")
        return False
    if not ok:
        _safe_send(bot, user_id, "❌ لم يُعثر على الفيديو في قاعدة البيانات. لم يُحفظ شيء.")
        return False
    try:
        db.clear_user_state(user_id)
    except Exception:
        pass
    _safe_send(
        bot,
        user_id,
        f"✅ <b>تم تعديل اسم الفيديو #{video_id}</b>\n\n"
        f"<b>الاسم القديم:</b>\n{safe_html(old_query, 120)}\n\n"
        f"<b>الاسم الجديد:</b>\n<b>{safe_html(new_name, 160)}</b>\n\n"
        "☝️ داخل البوت فقط (البحث والقوائم وتعليقاته). كابشن القناة لم يُعدَّل.\n"
        f"↩️ للتراجع: أرسل <code>{safe_html(new_name, 60)} => {safe_html(old_query, 60)}</code>",
    )
    return True


def start_title_edit(bot, user_id, video_id, admin_ids, state=None):
    """بدء تدفق تعديل العنوان لفيديو محدد (يتحقق من الحقوق ومن وجود الفيديو)."""
    if not _is_admin(user_id, admin_ids):
        _safe_send(bot, user_id, "⛔ هذا الأمر للإدارة فقط")
        return False
    video_id = _parse_int(video_id)
    if not video_id:
        _safe_send(bot, user_id, "❌ رقم الفيديو غير صحيح")
        return False
    try:
        video = db.get_video_title_source(video_id)
    except Exception as exc:
        logger.error("Could not read video %s for title edit: %s", video_id, exc, exc_info=True)
        _safe_send(bot, user_id, "❌ تعذّر قراءة بيانات الفيديو (خطأ في قاعدة البيانات). حاول مرة أخرى.")
        return False
    if not video:
        _safe_send(
            bot,
            user_id,
            f"❌ لا يوجد فيديو بالرقم #{safe_html(video_id, 12)}\n"
            "💡 الأرقام تظهر في قائمة الفيديوهات وفي محادثات التعليقات. "
            "تحقق من الرقم وأرسله مرة أخرى (أو /cancel للإلغاء).",
        )
        return False

    draft = _title_from_context(state) if state else ''
    if draft:
        try:
            db.set_user_state(user_id, STATE_EDITING_TITLE,
                              {"video_id": video_id, "title": draft, "admin_id": user_id})
        except Exception as exc:
            logger.error("Could not store title draft state: %s", exc, exc_info=True)
            _safe_send(bot, user_id, "❌ تعذّر تجهيز تعديل العنوان (خطأ في قاعدة البيانات). حاول مرة أخرى.")
            return False
        _safe_send(bot, user_id, build_title_preview(video, draft),
                   _title_keyboard(video_id, video, draft=draft))
        return True

    try:
        db.set_user_state(user_id, STATE_EDITING_TITLE, {"video_id": video_id, "admin_id": user_id})
    except Exception as exc:
        logger.error("Could not store title edit state: %s", exc, exc_info=True)
        _safe_send(bot, user_id, "❌ تعذّر بدء تعديل العنوان (خطأ في قاعدة البيانات). حاول مرة أخرى.")
        return False
    prompt = _safe_send(
        bot,
        user_id,
        f"✏️ <b>تعديل عنوان الفيلم #{video_id}</b>\n\n"
        f"العنوان الحالي:\n<b>{safe_html(_current_title(video), 160)}</b>\n\n"
        "أرسل العنوان الجديد الآن (أو /cancel للإلغاء).\n"
        "💡 يمكنك أيضاً إرسال النص مباشرة، أو الرد على هذه الرسالة.",
        _title_prompt_markup(),
    )
    # ربط رسالة الطلب بالحالة: يُقبل النص الحر أو الرد على هذه الرسالة
    prompt_id = _parse_int(getattr(prompt, 'message_id', None))
    if prompt_id:
        try:
            db.set_user_state(user_id, STATE_EDITING_TITLE,
                              {"video_id": video_id, "admin_id": user_id,
                               "prompt_message_id": prompt_id})
        except Exception as exc:
            logger.warning("Could not bind title prompt message: %s", exc)
    return True


def _current_title(video):
    from utils import video_title
    return video_title(video)


def handle_title_video_id_input(bot, message, admin_ids=None):
    """استقبال رقم الفيديو في تدفق تعديل العنوان (متوافق للخلف): يفوض إلى process_title_video_id."""
    if not getattr(message, 'text', None):
        return False
    return process_title_video_id(bot, message, admin_ids)



def dispatch_title_action(bot, call, admin_ids, action, args):
    """موزّع أزرار عنوان الفيديو: preview/start/show/edit/save/reset/cancel."""
    try:
        user_id = call.from_user.id
        if not _is_admin(user_id, admin_ids):
            _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
            return
        video_id = _parse_int(args[0]) if args else None
        if not video_id:
            _answer(bot, call, "❌ رقم الفيديو غير صحيح", alert=True)
            return
        if not _call_is_private(call):
            _refuse_non_private(bot, call)
            return

        if action in ("start", "show", "preview", "edit"):
            state = fetch_user_state(user_id)
            if action == "edit" or not _title_context(state) or (
                _title_context(state).get('video_id') != video_id
            ):
                if action in ("edit",):
                    db.set_user_state(user_id, STATE_EDITING_TITLE,
                                      {"video_id": video_id, "admin_id": user_id})
                _answer(bot, call)
                start_title_edit(bot, user_id, video_id, admin_ids)
                return
            _answer(bot, call)
            start_title_edit(bot, user_id, video_id, admin_ids, state=state)
            return

        if action == "save":
            save_display_title(bot, call, user_id, video_id, admin_ids)
            return

        if action == "reset":
            _apply_title_change(bot, user_id, video_id, None, admin_ids)
            _answer(bot, call, "✅ تمت استعادة العنوان الأصلي")
            return

        if action == "cancel":
            db.clear_user_state(user_id)
            _answer(bot, call, "✅ تم الإلغاء")
            return

        _answer(bot, call, "❌ إجراء غير معروف", alert=True)
    except Exception as e:
        logger.error("Error in dispatch_title_action: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ، حاول مرة أخرى", alert=True)


def save_display_title(bot, call, user_id, video_id, admin_ids):
    """حفظ العنوان: يعيد التحقق من الحقوق والصلاحية ووجود الفيديو عند الحفظ."""
    _answer(bot, call)
    from utils import validate_display_title
    state = fetch_user_state(user_id)
    context = _title_context(state)
    if not context or _parse_int(context.get('video_id')) != video_id:
        _safe_send(bot, user_id, "❌ لا يوجد عنوان قيد التعديل لهذا الفيديو. ابدأ من جديد.")
        return
    ok, value = validate_display_title(_title_from_context(context))
    if not ok:
        video = db.get_video_title_source(video_id) or {'id': video_id}
        _safe_send(bot, user_id, f"❌ {safe_html(value, 200)}", _title_keyboard(video_id, video, draft=context.get('title')))
        return
    _apply_title_change(bot, user_id, video_id, value, admin_ids)


def _apply_title_change(bot, user_id, video_id, value, admin_ids):
    from utils import validate_display_title
    if not _is_admin(user_id, admin_ids):
        db.clear_user_state(user_id)
        _safe_send(bot, user_id, "⛔ لم تعد لديك صلاحية التعديل. لم يُحفظ شيء.")
        return False
    if value is not None:
        ok, result = validate_display_title(value)
        if not ok:
            _safe_send(bot, user_id, f"❌ {safe_html(result, 200)}")
            return False
        value = result
    try:
        existing = db.get_video_title_source(video_id)
    except Exception as exc:
        logger.error("Could not verify video %s before saving title: %s", video_id, exc, exc_info=True)
        _safe_send(bot, user_id, "❌ تعذّر التحقق من الفيديو (خطأ في قاعدة البيانات). لم يُحفظ شيء.")
        return False
    if not existing:
        db.clear_user_state(user_id)
        _safe_send(bot, user_id, "❌ الفيديو لم يعد موجوداً. لم يُحفظ شيء.")
        return False
    if not db.set_display_title(video_id, value):
        _safe_send(bot, user_id, "❌ فشل حفظ العنوان، حاول مرة أخرى.")
        return False
    db.clear_user_state(user_id)
    label = "الاستعادة إلى العنوان الأصلي" if value is None else "تحديث العنوان"
    shown = safe_html(value, 160) if value is not None else "—"
    _safe_send(
        bot,
        user_id,
        f"✅ <b>تم {label} للفيديو #{video_id}</b>\n\n"
        f"<b>العنوان الآن:</b>\n{shown}",
    )
    return True


def process_title_text(bot, message, admin_ids):
    """استقبال نص عنوان جديد من الأدمن: معاينة + تحقق الطول."""
    user_id = message.from_user.id
    try:
        if not _message_is_private(message):
            return False
        state = fetch_user_state(user_id)
        context = _title_context(state)
        if not context:
            return False
        if not _is_admin(user_id, admin_ids):
            db.clear_user_state(user_id)
            _safe_send(bot, user_id, "⛔ لم تعد لديك صلاحية التعديل. لم يُحفظ شيء.")
            return True
        video_id = _parse_int(context.get('video_id'))
        if not video_id:
            db.clear_user_state(user_id)
            return False
        draft = _strip_title_command(message.text, video_id)
        from utils import validate_display_title
        ok, value = validate_display_title(draft)
        try:
            video = db.get_video_title_source(video_id)
        except Exception as exc:
            logger.error("Could not read video %s for title edit: %s", video_id, exc, exc_info=True)
            _safe_send(bot, user_id, "❌ تعذّر قراءة بيانات الفيديو (خطأ في قاعدة البيانات). حاول مرة أخرى.")
            return True
        if not video:
            db.clear_user_state(user_id)
            _safe_send(bot, user_id, "❌ الفيديو لم يعد موجوداً.")
            return True
        if not ok:
            _safe_send(bot, user_id, f"❌ {safe_html(value, 200)}")
            return True
        db.set_user_state(user_id, STATE_EDITING_TITLE,
                          {"video_id": video_id, "title": value, "admin_id": user_id,
                           "prompt_message_id": _bound_reply_message_id(context)})
        _safe_send(bot, user_id, build_title_preview(video, value),
                   _title_keyboard(video_id, video, draft=value))
        return True
    except Exception as e:
        logger.error("Error in process_title_text: %s", e, exc_info=True)
        _safe_send(bot, user_id, "❌ حدث خطأ، حاول مرة أخرى")
        return True

def handle_delete_all_comments(bot, user_id, admin_ids):
    """حذف جميع التعليقات"""
    try:
        if not _is_admin(user_id, admin_ids):
            _safe_send(bot, user_id, "⛔ هذا الأمر للإدارة فقط")
            return

        markup = types.InlineKeyboardMarkup()
        markup.row(
            inline_button("🗑️ حذف الكل 🔴", STYLE_DANGER, callback_data="confirm_delete_all_comments"),
            inline_button("↩️ إلغاء 🟢", STYLE_SUCCESS, callback_data="noop")
        )

        stats = db.get_comments_stats()
        total = stats['total_comments'] if stats else 0

        _safe_send(
            bot,
            user_id,
            f"⚠️ <b>تحذير!</b>\n\n"
            f"أنت على وشك حذف <b>جميع المحادثات</b> ({safe_html(total, 10)})\n"
            f"هذا الإجراء لا يمكن التراجع عنه!\n\n"
            f"هل أنت متأكد؟",
            markup,
        )

    except Exception as e:
        logger.error("Error in handle_delete_all_comments: %s", e, exc_info=True)
        _safe_send(bot, user_id, "❌ حدث خطأ")


def confirm_delete_all_comments(bot, call, admin_ids):
    """تأكيد حذف جميع التعليقات"""
    try:
        user_id = call.from_user.id

        if not _is_admin(user_id, admin_ids):
            _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
            return

        deleted_count = db.delete_all_comments()
        _answer(bot, call, f"✅ تم حذف {deleted_count} محادثة")
        message = getattr(call, 'message', None)
        if message is not None:
            _safe_edit(
                bot,
                message.chat.id,
                message.message_id,
                f"🗑️ <b>تم حذف جميع المحادثات</b>\n\n"
                f"عدد المحادثات المحذوفة: {safe_html(deleted_count, 10)}",
            )

    except Exception as e:
        logger.error("Error in confirm_delete_all_comments: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ")


def handle_delete_user_comments(bot, user_id, admin_ids, target_user_id_str=None):
    """حذف تعليقات مستخدم معين"""
    try:
        if not _is_admin(user_id, admin_ids):
            _safe_send(bot, user_id, "⛔ هذا الأمر للإدارة فقط")
            return

        parts = str(target_user_id_str or "").split()
        if len(parts) < 2:
            _safe_send(
                bot,
                user_id,
                "❌ <b>الاستخدام الصحيح:</b>\n"
                "<code>/delete_user_comments &lt;user_id&gt;</code>\n\n"
                "مثال: <code>/delete_user_comments 123456789</code>",
            )
            return

        try:
            target_user_id = int(parts[1])
        except ValueError:
            _safe_send(bot, user_id, "❌ رقم المستخدم غير صحيح")
            return

        markup = types.InlineKeyboardMarkup()
        markup.row(
            inline_button("🗑️ تأكيد الحذف 🔴", STYLE_DANGER,
                          callback_data=f"confirm_delete_user_comments::{target_user_id}"),
            inline_button("↩️ إلغاء 🟢", STYLE_SUCCESS, callback_data="noop")
        )

        _safe_send(
            bot,
            user_id,
            f"⚠️ <b>تأكيد الحذف</b>\n\n"
            f"هل أنت متأكد من حذف جميع محادثات العضو <code>{target_user_id}</code>؟",
            markup,
        )

    except Exception as e:
        logger.error("Error in handle_delete_user_comments: %s", e, exc_info=True)
        _safe_send(bot, user_id, "❌ حدث خطأ")


def confirm_delete_user_comments(bot, call, admin_ids):
    """تأكيد حذف تعليقات مستخدم"""
    try:
        user_id = call.from_user.id

        if not _is_admin(user_id, admin_ids):
            _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
            return

        parts = _split_callback(call)
        target_user_id = _parse_int(parts[1]) if len(parts) > 1 else None
        if not target_user_id:
            _answer(bot, call, "❌ رقم العضو غير صالح", alert=True)
            return
        deleted_count = db.delete_user_comments(target_user_id)

        _answer(bot, call, f"✅ تم حذف {deleted_count} محادثة")
        message = getattr(call, 'message', None)
        if message is not None:
            _safe_edit(
                bot,
                message.chat.id,
                message.message_id,
                f"🗑️ <b>تم حذف محادثات العضو</b>\n\n"
                f"العضو: <code>{target_user_id}</code>\n"
                f"عدد المحادثات المحذوفة: {safe_html(deleted_count, 10)}",
            )

    except Exception as e:
        logger.error("Error in confirm_delete_user_comments: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ")


def handle_delete_old_comments(bot, user_id, admin_ids, days_str=None):
    """حذف التعليقات القديمة"""
    try:
        if not _is_admin(user_id, admin_ids):
            _safe_send(bot, user_id, "⛔ هذا الأمر للإدارة فقط")
            return

        days = 30
        parts = str(days_str or "").split()
        if len(parts) >= 2:
            try:
                days = int(parts[1])
            except ValueError:
                _safe_send(bot, user_id, "❌ عدد الأيام غير صحيح")
                return
        if days < 0:
            _safe_send(bot, user_id, "❌ عدد الأيام يجب أن يكون صفراً أو أكثر")
            return

        markup = types.InlineKeyboardMarkup()
        markup.row(
            inline_button("🗑️ تأكيد الحذف 🔴", STYLE_DANGER,
                          callback_data=f"confirm_delete_old_comments::{days}"),
            inline_button("↩️ إلغاء 🟢", STYLE_SUCCESS, callback_data="noop")
        )

        _safe_send(
            bot,
            user_id,
            f"⚠️ <b>تأكيد الحذف</b>\n\n"
            f"هل أنت متأكد من حذف التعليقات الأقدم من <b>{safe_html(days, 10)} يوم</b>؟",
            markup,
        )

    except Exception as e:
        logger.error("Error in handle_delete_old_comments: %s", e, exc_info=True)
        _safe_send(bot, user_id, "❌ حدث خطأ")


def confirm_delete_old_comments(bot, call, admin_ids):
    """تأكيد حذف التعليقات القديمة"""
    try:
        user_id = call.from_user.id

        if not _is_admin(user_id, admin_ids):
            _answer(bot, call, "⛔ هذا الأمر للإدارة فقط", alert=True)
            return

        parts = _split_callback(call)
        days = _parse_int(parts[1]) if len(parts) > 1 else None
        if days is None or days < 0:
            _answer(bot, call, "❌ عدد الأيام غير صالح", alert=True)
            return
        deleted_count = db.delete_old_comments(days)

        _answer(bot, call, f"✅ تم حذف {deleted_count} محادثة")
        message = getattr(call, 'message', None)
        if message is not None:
            _safe_edit(
                bot,
                message.chat.id,
                message.message_id,
                f"🗑️ <b>تم حذف المحادثات القديمة</b>\n\n"
                f"الأقدم من: {safe_html(days, 10)} يوم\n"
                f"عدد المحادثات المحذوفة: {safe_html(deleted_count, 10)}",
            )

    except Exception as e:
        logger.error("Error in confirm_delete_old_comments: %s", e, exc_info=True)
        _answer(bot, call, "❌ حدث خطأ")


def handle_comments_stats(bot, user_id, admin_ids):
    """عرض إحصائيات التعليقات"""
    try:
        if not _is_admin(user_id, admin_ids):
            _safe_send(bot, user_id, "⛔ هذا الأمر للإدارة فقط")
            return

        stats = db.get_comments_stats()
        if not stats:
            _safe_send(bot, user_id, "❌ فشل جلب الإحصائيات")
            return

        stats_text = (
            f"📊 <b>إحصائيات التعليقات</b>\n\n"
            f"📝 إجمالي المحادثات: {safe_html(stats.get('total_comments'), 10)}\n"
            f"🔴 غير المقروءة: {safe_html(stats.get('unread_comments'), 10)}\n"
            f"✅ تم الرد عليها: {safe_html(stats.get('replied_comments'), 10)}\n"
            f"👥 عدد المستخدمين: {safe_html(stats.get('unique_users'), 10)}"
        )
        _safe_send(bot, user_id, stats_text)

    except Exception as e:
        logger.error("Error in handle_comments_stats: %s", e, exc_info=True)
        _safe_send(bot, user_id, "❌ حدث خطأ")
