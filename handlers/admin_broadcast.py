# handlers/admin_broadcast.py
"""
بث رسالة من لوحة الأدمن (نص أو محتوى غني) عبر copy_message بالحالة المخزّنة.

- الحالة محفوظة في DB: ``waiting_broadcast_message`` + سياق صادر عن رسالة التلميح.
- قراءة الجمهور عبر المسار الصارم: فشل القاعدة ليس جمهوراً صفرياً.
- عامل واحد لكل أدمن في العملية (منع التكرار)، بدُفعات محدودة وتأخير لطيف.
- تقرير صادق: نجاح / فشل / محذوفون بعد تأكيد الحظر والحذف الناجح فقط.
- إعادة التشغيل لا تعيد الإرسال تلقائياً: الحالة تُمسح قبل الإرسال، والعامل في
  الذاكرة فقط (لا طابور مهام موزّع بين العمال).
"""

import logging
import threading
import time

import db_manager
from telebot.apihelper import ApiTelegramException
from telebot.types import InlineKeyboardMarkup

from . import rich_reports
from .telegram_rate_limit import sleep_after_429
from db_manager import delete_bot_user
from state_manager import clear_user_waiting_state, read_state_row, set_user_state

logger = logging.getLogger(__name__)

STATE_BROADCAST = 'waiting_broadcast_message'
CALLBACK_CANCEL_BROADCAST = 'admin::cancel_broadcast'
BATCH_SIZE = 20
# 20 رسالة على الأكثر في الثانية: ننام ثانية بعد كل دفعة مكتملة من 20 (بلا نوم بعد
# الدفعة الأخيرة غير المكتملة).
SEND_INTERVAL = 1.0

_BLOCKED_PATTERNS = ("bot was blocked by the user",)
# حساب معطّل فقط: 'chat not found' قد يعني محادثة غير مفتوحة، ولا يُحذف المشترك بسببه
_DEACTIVATED_PATTERNS = (
    "forbidden: user is deactivated",
    "bot can't initiate conversation with a user",
    "forbidden: bot can't initiate conversation with a user",
)

_active_jobs = {}
_JOBS = {}
_jobs_lock = threading.Lock()


# ----------------------------------------------------------------------------
# أدوات تحقق
# ----------------------------------------------------------------------------

def _is_admin(user_id, admin_ids):
    try:
        return int(user_id) in {int(a) for a in admin_ids}
    except (TypeError, ValueError):
        return False


def _is_private(message):
    return getattr(getattr(message, 'chat', None), 'type', None) == 'private'


def _error_text(exc):
    return str(getattr(exc, 'description', None) or exc or '').lower()


def _is_blocked(exc):
    return any(p in _error_text(exc) for p in _BLOCKED_PATTERNS)


def _is_deactivated(exc):
    return any(p in _error_text(exc) for p in _DEACTIVATED_PATTERNS)


def _report_worker_failure(bot, admin_id):
    """تقرير فشل العامل على البوت الذي التُقط وقت الجدولة (لا بحث متأخر عنه)."""
    if bot is None:
        return
    _send(bot, admin_id, "❌ توقف البث بخطأ غير متوقع قبل اكتماله. راجع السجل وأعد المحاولة.")


def _send(bot, chat_id, text, **kwargs):
    """إرسال نص الحالة/التقرير: يُستدعى send_message صراحةً (مصدر واحد للأداء)."""
    try:
        bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as exc:
        logger.warning("Could not send admin broadcast status to %s: %s", chat_id, type(exc).__name__)
        return False


# ----------------------------------------------------------------------------
# منع تكرار المهلة
# ----------------------------------------------------------------------------

def has_active_job(admin_id):
    with _jobs_lock:
        return admin_id in _active_jobs


def _reserve_job(admin_id, runner, bot=None):
    """حجز عملية واحدة لكل أدمن؛ False إذا كان هناك عامل قائم أو فشل التشغيل."""
    with _jobs_lock:
        if admin_id in _active_jobs:
            return False
        _active_jobs[admin_id] = True
        _JOBS[admin_id] = bot
    try:
        threading.Thread(target=_job_wrapper, args=(admin_id, runner), daemon=True,
                         name=f"admin-broadcast-{admin_id}").start()
    except Exception as exc:
        logger.error("Broadcast worker could not start: %s", type(exc).__name__)
        with _jobs_lock:
            _active_jobs.pop(admin_id, None)
            _JOBS.pop(admin_id, None)
        return False
    return True


def _job_wrapper(admin_id, runner):
    try:
        runner()
    except Exception as exc:
        logger.error("Broadcast worker failed: %s", type(exc).__name__, exc_info=True)
        with _jobs_lock:
            job_bot = _JOBS.get(admin_id)
        _report_worker_failure(job_bot, admin_id)
    finally:
        with _jobs_lock:
            _active_jobs.pop(admin_id, None)
            _JOBS.pop(admin_id, None)


# ----------------------------------------------------------------------------
# التدفق: زر -> حالة -> رسالة -> عامل
# ----------------------------------------------------------------------------

def prompt_broadcast(bot, admin_id, admin_ids):
    """زر «📢 بث رسالة»: يبدأ حالة مخزّنة ويطلب رسالة البث التالية من الأدمن."""
    if not _is_admin(admin_id, admin_ids):
        return False
    if has_active_job(admin_id):
        _send(bot, admin_id, "⏳ يوجد بث قيد التنفيذ حالياً. انتظر انتهاءه.")
        return False
    state = read_state_row(admin_id)
    if state and state.get('state') == STATE_BROADCAST:
        _send(bot, admin_id, "📢 يوجد طلب بث مفتوح: أرسل الرسالة أو /cancel.")
        return False
    markup = InlineKeyboardMarkup(row_width=1)
    from .button_styles import STYLE_DANGER, inline_button
    markup.add(inline_button("↩️ إلغاء البث 🟡", STYLE_DANGER,
                             callback_data=CALLBACK_CANCEL_BROADCAST))
    prompt = bot.send_message(
        admin_id,
        "📢 أرسل الرسالة التي تريد بثها (نص أو صورة أو فيديو أو ملف). "
        "ستُنسخ كما هي لكل المشتركين. (أو /cancel)",
        reply_markup=markup)
    try:
        stored = set_user_state(admin_id, STATE_BROADCAST, {
            'admin_id': admin_id,
            'prompt_message_id': getattr(prompt, 'message_id', None),
        })
    except Exception as exc:
        logger.error("Could not store broadcast state: %s", type(exc).__name__)
        stored = False
    if stored is not True:
        _send(bot, admin_id, "❌ تعذّر بدء البث (خطأ في قاعدة البيانات).")
        return False
    return True


def cancel_broadcast(bot, user_id, admin_ids, call=None):
    """زر الإلغاء: يمسح الحالة ولا يرسل شيئاً (فشل المسح يُبلغ عنه، لا نجاح وهمي)."""
    if not _is_admin(user_id, admin_ids):
        return False
    try:
        cleared = clear_user_waiting_state(user_id)
    except Exception as exc:
        logger.error("Could not clear broadcast state: %s", type(exc).__name__)
        cleared = False
    if cleared is not True:
        if call is not None:
            bot.answer_callback_query(call.id, "❌ تعذّر إلغاء العملية (قاعدة البيانات)", show_alert=True)
        return False
    if call is not None:
        bot.answer_callback_query(call.id, "✅ تم إلغاء البث")
    else:
        _send(bot, user_id, "✅ تم إلغاء البث")
    return True


def _state_owned_by(state, user_id, message):
    """الحالة تخص هذا الأدمن تحديداً؛ الرد الاختياري يجب أن يستأنف التلميح نفسه."""
    if not state or state.get('state') != STATE_BROADCAST:
        return False
    context = state.get('context') or {}
    owner = context.get('admin_id')
    if owner is None or str(owner) != str(user_id):
        return False
    prompt_id = context.get('prompt_message_id')
    reply_to = getattr(message, 'reply_to_message', None)
    if reply_to is not None and prompt_id is not None:
        # بالرد: يجب أن يكون الرد على رسالة التلميح نفسها (لا على رسالة أخرى)
        if str(getattr(reply_to, 'message_id', None)) != str(prompt_id):
            return False
    return True


def handle_broadcast_message(bot, admin_ids, message, sleeper=None):
    """رسالة الأدمن التالية بعد طلب البث: يحفظ مصدر الرسالة ويشغّل عامل الإرسال."""
    if not message:
        return False
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    state = read_state_row(user_id)
    if not state or state.get('state') != STATE_BROADCAST:
        return False
    if not _is_private(message) or not _is_admin(user_id, admin_ids):
        return False
    if not _state_owned_by(state, user_id, message):
        _send(bot, user_id, "📢 أرسل رسالة البث مباشرة أو بالرد على رسالة الطلب.")
        return True
    source_chat_id = getattr(getattr(message, 'chat', None), 'id', None)
    source_message_id = getattr(message, 'message_id', None)
    if not source_chat_id or not source_message_id:
        _send(bot, user_id, "❌ تعذّر تحديد رسالة البث. أرسلها مرة أخرى.")
        return True
    if has_active_job(user_id):
        _send(bot, user_id, "⏳ يوجد بث قيد التنفيذ حالياً.")
        return True

    # نمسح الحالة قبل الإرسال: فلا يُعاد بث رسالة قديمة بعد انتهاء العملية.
    # الفشل (بلا استثناء) يعيد False => لا يبدأ بث ولا يُعلن نجاح.
    try:
        cleared = clear_user_waiting_state(user_id)
    except Exception as exc:
        logger.error("Could not clear broadcast state: %s", type(exc).__name__)
        cleared = False
    if cleared is not True:
        _send(bot, user_id, "❌ تعذّر بدء البث (خطأ في قاعدة البيانات). لم يُرسل شيء.")
        return True

    user_ids, error = _fetch_audience()
    if error is not None:
        _send(bot, user_id, "❌ تعذّر جلب قائمة المشتركين من قاعدة البيانات. لم يُرسل شيء.")
        return True

    # يُلتقط البوت الحقيقي هنا (وقت الجدولة) ويُستخدم للتقارير الصادقة من الخيط؛
    # في الاختبارات يبقى البوت الحالي نفسه.
    worker_bot = bot
    runner = lambda: run_broadcast(worker_bot, user_id, source_chat_id, source_message_id,
                                   user_ids, sleeper=sleeper)
    if not _reserve_job(user_id, runner, bot=worker_bot):
        _send(bot, user_id, "❌ لم يبدأ البث (تعذّر تشغيل العامل). لم يُرسل شيء.")
    return True


def run_broadcast(bot, admin_id, source_chat_id, source_message_id, user_ids, sleeper=None):
    """إرسال نسخة من رسالة المصدر لكل مشترك وتقرير أرقام حقيقية.

    النجاح يُحتسب فقط عندما تُرجع copy_message نتيجة غير None (تأكيد تيليجرام).
    الإيقاع: 20 رسالة كحد أقصى في الثانية — نوم واحد بعد كل دفعة مكتملة من 20،
    ولا نوم بعد الدفعة الأخيرة غير المكتملة.
    """
    sleep = sleeper or time.sleep
    _send(bot, admin_id, f"🚀 بدء البث إلى {len(user_ids)} مشترك...")
    total = len(user_ids)
    sent = failed = removed = 0
    for index, user_id in enumerate(user_ids):
        try:
            result = bot.copy_message(user_id, source_chat_id, source_message_id)
            if result is None:
                failed += 1
                logger.warning("Broadcast unconfirmed for %s (copy_message returned None)", user_id)
            else:
                sent += 1
        except ApiTelegramException as exc:
            # 429 رسمي: ننتظر retry_after قبل المستلم التالي (لا إعادة إرسال، ولا
            # تغيير للأرقام أو التنظيف). القيمة الغائبة لا تضيف نوماً.
            sleep_after_429(exc, sleep)
            cleanup_ok = (_is_blocked(exc) or _is_deactivated(exc)) and _delete_user(user_id)
            if cleanup_ok:
                removed += 1
            else:
                failed += 1
                logger.warning("Broadcast failed for %s: %s", user_id, type(exc).__name__)
        except Exception as exc:
            failed += 1
            logger.warning("Broadcast unexpected error for %s: %s", user_id, type(exc).__name__)
        if (index + 1) % BATCH_SIZE == 0 and (index + 1) < total:
            sleep(SEND_INTERVAL)
    # تقرير الإكمال: Rich مهيكل RTL مرة واحدة، وإن فشل/لم يُؤكد فالنص النصي نفسه
    # (send_message بعرض HTML) مرة واحدة فقط. فشل التقرير لا يغيّر الأرقام.
    rich_reports.send_rich_or_plain(
        bot, admin_id,
        rich_reports.build_broadcast_report(sent, failed, removed),
        "📢 اكتمل البث!\n"
        f"- ✅ نجح: {sent}\n"
        f"- ❌ فشل: {failed}\n"
        f"- 🗑️ حُذفوا (حظر/حساب معطّل): {removed}")
    return {'sent': sent, 'failed': failed, 'removed': removed}


def _fetch_audience():
    """(user_ids, error): فشل القاعدة يُعرف صراحةً ولا يُخلط بقائمة فارغة حقيقية."""
    fetch = getattr(db_manager, 'fetch_all_user_ids', None)
    if fetch is None:
        return None, RuntimeError('audience query unavailable')
    try:
        user_ids, error = fetch()
    except Exception as exc:
        logger.error("Broadcast audience query failed: %s", type(exc).__name__)
        return None, exc
    if error is not None:
        return None, error
    if not isinstance(user_ids, (list, tuple)):
        return None, RuntimeError('malformed audience result')
    return list(user_ids), None


def _delete_user(user_id):
    deleter = getattr(db_manager, 'delete_bot_user', None)
    if deleter is None:
        return False
    try:
        return bool(deleter(user_id))
    except Exception as exc:
        logger.warning("Could not delete blocked user %s: %s", user_id, type(exc).__name__)
        return False


def handle_rich_broadcast(message, bot):
    """توافق خلفي (message, bot): يفوّض لتدفق الحالة المخزّنة دون next_step.

    صلاحية الأدمن تُفحص داخل التدفق نفسه (الحالة مملوكة للأدمن في القاعدة)،
    لذلك لا نمنح صلاحية هنا: نستخدم قائمة الأدمن الحقيقية فقط.
    """
    try:
        from config import ADMIN_IDS as CONFIG_ADMINS
        admin_ids = list(CONFIG_ADMINS)
    except Exception:
        admin_ids = []
    return handle_broadcast_message(bot, admin_ids, message)
