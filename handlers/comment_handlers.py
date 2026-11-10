#!/usr/bin/env python3
# ==============================================================================
# ملف: comment_handlers.py
# الوصف: معالجات نظام التعليقات الخاصة بين المستخدمين والأدمن
#        (محادثات خاصة مستمرة - كل الرسائل تُحفظ في سجل مرتب، والتاريخ القديم محفوظ)
# ==============================================================================

import json
import logging
import uuid
from telebot import types
import db_manager as db
from .button_styles import STYLE_DANGER, STYLE_PRIMARY, STYLE_SUCCESS, inline_button
from .thread_format import (
    STATUS_ANSWERED,
    STATUS_READ,
    STATUS_WAITING,
    build_thread_text,
    compute_thread_state,
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


def _safe_send(bot, chat_id, text, reply_markup=None):
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
            "💡 <i>ردّ على هذه الرسالة لإضافة رسالة إلى هذه المحادثة.</i>\n"
            "💡 <i>للإلغاء، اضغط /cancel</i>\n"
            + _prompt_line(prompt_id),
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
    _notify_admins_new_comment(comment_id, followup=True)
    _safe_send(
        bot,
        user_id,
        "✅ <b>تم إرسال رسالتك إلى الإدارة</b>\n\n"
        f"<blockquote>{safe_html(text)}</blockquote>\n\n"
        f"💬 محادثة #{comment_id} - تابع الردود من /my_comments",
    )
    return True


def _notify_admins_new_comment(comment_id, followup=False):
    """علامة لطبقة المستخدمين: تُستدعى من هناك بعد جلب قائمة الأدمن (لا يوجد دخول عالمي هنا)."""
    return None


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
        nav.append(inline_button(f"📄 {page + 1}/{total_pages}", STYLE_PRIMARY, callback_data="noop"))
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
    nav.append(inline_button(f"📄 {page + 1}/{total_pages}", STYLE_PRIMARY, callback_data="noop"))
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
        nav.append(inline_button(f"📄 {page + 1}/{total_pages}", STYLE_PRIMARY, callback_data="noop"))
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


def _title_context(state):
    return _state_context(state) if state and state.get('state') == STATE_EDITING_TITLE else {}


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


def start_title_edit(bot, user_id, video_id, admin_ids, state=None):
    """بدء تدفق تعديل العنوان لفيديو محدد (يتحقق من الحقوق ومن وجود الفيديو)."""
    if not _is_admin(user_id, admin_ids):
        _safe_send(bot, user_id, "⛔ هذا الأمر للإدارة فقط")
        return False
    video_id = _parse_int(video_id)
    if not video_id:
        _safe_send(bot, user_id, "❌ رقم الفيديو غير صحيح")
        return False
    video = db.get_video_title_source(video_id)
    if not video:
        _safe_send(bot, user_id, f"❌ لا يوجد فيديو بالرقم {safe_html(video_id, 12)}")
        return False

    draft = _title_from_context(state) if state else ''
    if draft:
        db.set_user_state(user_id, STATE_EDITING_TITLE,
                          {"video_id": video_id, "title": draft, "admin_id": user_id})
        _safe_send(bot, user_id, build_title_preview(video, draft),
                   _title_keyboard(video_id, video, draft=draft))
        return True

    db.set_user_state(user_id, STATE_EDITING_TITLE, {"video_id": video_id, "admin_id": user_id})
    _safe_send(
        bot,
        user_id,
        f"✏️ <b>تعديل عنوان الفيلم #{video_id}</b>\n\n"
        f"العنوان الحالي:\n<b>{safe_html(_current_title(video), 160)}</b>\n\n"
        "أرسل العنوان الجديد الآن (أو /cancel للإلغاء).\n"
        "💡 يمكنك أيضاً إرسال <code>/title العنوان الجديد</code> في أي وقت.",
        _title_keyboard(video_id, video, draft=None),
    )
    return True


def _current_title(video):
    from utils import video_title
    return video_title(video)


def handle_title_video_id_input(bot, message, admin_ids=None):
    """استقبال رقم الفيديو في تدفق تعديل العنوان (next_step: (message, bot))."""
    user_id = message.from_user.id
    resolved_admins = admin_ids if admin_ids is not None else ADMIN_IDS
    if getattr(message, 'text', None) == "/cancel":
        db.clear_user_state(user_id)
        _safe_send(bot, user_id, "✅ تم الإلغاء")
        return
    if not _message_is_private(message):
        return
    if not start_title_edit(bot, user_id, message.text, resolved_admins):
        return



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
    if not db.get_video_title_source(video_id):
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
        video = db.get_video_title_source(video_id)
        if not video:
            db.clear_user_state(user_id)
            _safe_send(bot, user_id, "❌ الفيديو لم يعد موجوداً.")
            return True
        if not ok:
            _safe_send(bot, user_id, f"❌ {safe_html(value, 200)}")
            return True
        db.set_user_state(user_id, STATE_EDITING_TITLE,
                          {"video_id": video_id, "title": value, "admin_id": user_id})
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
