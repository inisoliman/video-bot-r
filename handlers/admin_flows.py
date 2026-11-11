# handlers/admin_flows.py
"""
تدفقات لوحة الأدمن القائمة على حالة مخزّنة في DB (بدل next_step/admin_steps).

كل تدفق: يبدأ بطلب (زر) يحفظ حالة للمستخدم + رقم رسالة التلميح، ثم يُستهلك في
المعالج النصي للأدمن (admin_handlers.register). التحقق في كل استقبال:
أدمن حالياً + محادثة خاصة + صاحب الحالة + نوع الإدخال. الإدخال الخاطئ يُبقى
الحالة مع رسالة إعادة محاولة صريحة، و/cancel أو أزرار الإلغاء تمسحها بلا تعديل.

مخرجات العمليات تُقصّ قبل أي عرض وتُرمز في HTML (لا حقن Markdown/HTML).
"""

import html
import logging
import re

from telebot.types import InlineKeyboardMarkup

from db_manager import (
    add_category, add_required_channel, get_categories_tree, get_category_by_id,
    get_required_channels, get_subscriber_count, get_bot_subscriber_count_or_none,
    remove_required_channel, find_required_channel, set_active_category_id,
    count_existing_video_ids, fetch_all_user_ids,
    delete_videos_by_ids_reported, move_videos_bulk_reported,
    delete_category_by_id_reported, get_bot_setting, set_bot_setting,
    move_videos_from_category_reported, fetch_admin_categories,
    fetch_admin_required_channels, delete_category_with_contents_atomic,
    move_category_contents_atomic,
)
from state_manager import clear_user_waiting_state, read_state_row, set_user_state
from .button_styles import STYLE_DANGER, STYLE_PRIMARY, STYLE_SUCCESS, inline_button

logger = logging.getLogger(__name__)

ST_CATEGORY = 'waiting_category_name'
ST_CHANNEL_ID = 'waiting_channel_id'
ST_CHANNEL_NAME = 'waiting_channel_name'
ST_CHANNEL_REMOVE = 'waiting_remove_channel_id'
ST_MOVE_IDS = 'waiting_move_video_id'
ST_MOVE_TARGET = 'waiting_move_target'
ST_DELETE_IDS = 'waiting_delete_video_ids'
ST_DELETE_CONFIRM = 'waiting_delete_confirm'
ST_MINIAPP_LIMIT = 'waiting_miniapp_limit'
ST_MINIAPP_IP = 'waiting_miniapp_ip'

MINIAPP_HOURLY_SETTING_KEY = 'miniapp_hourly_limit_per_ip'
MAX_MINIAPP_HOURLY_LIMIT = 100000
MAX_CATEGORY_NAME = 80
MAX_CHANNEL_NAME = 100
MAX_FLOW_IDS = 100
_ARABIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')
_ID_SPLIT = re.compile(r'[\s,،;؛]+')
_CHANNEL_ID = re.compile(r'^-?\d{5,20}$')


# ---------------------------------------------------------------------------
# أدوات
# ---------------------------------------------------------------------------

def _safe(value, limit=80):
    return html.escape(str(value if value is not None else ''))[:limit]


def _is_admin(user_id, admin_ids):
    try:
        return int(user_id) in {int(a) for a in admin_ids}
    except (TypeError, ValueError):
        return False


def _is_private(message):
    return getattr(getattr(message, 'chat', None), 'type', None) == 'private'


def _owner_ok(context, user_id):
    """ملكية صريحة: admin_id المخزّن يجب أن يطابق المرسل (None ليس ملكية)."""
    owner = (context or {}).get('admin_id')
    return owner is not None and str(owner) == str(user_id)


def parse_ids(text):
    """أرقام صحيحة موجبة من نص حر (عربية-هندية، فواصل متعددة)؛ None إذا لا شيء صالح."""
    cleaned = str(text or '').translate(_ARABIC_DIGITS)
    ids = []
    for token in _ID_SPLIT.split(cleaned):
        token = token.strip().lstrip('#')
        if not token:
            continue
        if not token.isdigit():
            return None
        value = int(token)
        if value <= 0 or value > 2_000_000_000:
            return None
        ids.append(value)
    unique = list(dict.fromkeys(ids))
    if not unique or len(unique) > MAX_FLOW_IDS:
        return None
    return unique


def normalize_channel_id(text):
    """معرّف قناة رقمي صالح لـ required_channels (``-100...``)؛ None خلاف ذلك.

    الواجهة تقبل ``@username`` أيضاً، لكن الجدول يخزن أرقاماً فقط: يُحل الاسم
    صراحةً برسالة بدل محاولة تخزين نص لا يقبله العمود.
    """
    value = str(text or '').translate(_ARABIC_DIGITS).strip()
    if value.startswith('@') or (value and not value.lstrip('-').isdigit()):
        return None
    return value if _CHANNEL_ID.match(value) else None


def parse_miniapp_limit(text):
    """0..MAX_MINIAPP_HOURLY_LIMIT أو None (لا حد سلبي ولا فوق الأقصى)."""
    value = str(text or '').translate(_ARABIC_DIGITS).strip()
    if not value.isdigit():
        return None
    number = int(value)
    return number if 0 <= number <= MAX_MINIAPP_HOURLY_LIMIT else None


def parse_admin_ip(text):
    """IPv4/IPv6 صالح أو None."""
    value = str(text or '').strip()
    try:
        import ipaddress
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


def _prompt_markup(cancel_callback):
    markup = InlineKeyboardMarkup(row_width=1)
    label = "↩️ إلغاء 🟡" if cancel_callback != 'back_to_main' else "↩️ القائمة الرئيسية 🟡"
    markup.add(inline_button(label, STYLE_DANGER, callback_data=cancel_callback))
    return markup


def _begin(bot, admin_id, admin_ids, state, prompt, context=None, cancel_callback='admin::cancel_flow'):
    """حفظ الحالة ثم طلب الإدخال.

    الحالة السابقة تُمسح فقط إذا كانت هذه العملية أنشأت حالتها فعلاً ثم فشل
    الطلب/الربط لاحقاً. فشل الحفظ الأول يُبلَّغ عنه بلا أي مسح (قد يكون هناك
    تدفق سابق سليم يجب ألا يُطمس).
    """
    if not _is_admin(admin_id, admin_ids):
        return False
    context = dict(context or {})
    context['admin_id'] = admin_id
    try:
        stored = set_user_state(admin_id, state, context)
    except Exception as exc:
        logger.error("Could not store admin flow state %s: %s", state, type(exc).__name__)
        stored = False
    if stored is not True:
        # لا _cleanup_state هنا: لم ننشئ هذه الحالة، وقد يوجد تدفق سابق قائم
        bot.send_message(admin_id, "❌ تعذّر بدء العملية (خطأ في قاعدة البيانات). حاول مجدداً.")
        return False
    try:
        sent = bot.send_message(admin_id, prompt, reply_markup=_prompt_markup(cancel_callback),
                                parse_mode="HTML")
    except Exception as exc:
        logger.warning("Admin flow prompt failed: %s", type(exc).__name__)
        _cleanup_state(bot, admin_id)
        bot.send_message(admin_id, "❌ تعذّر إرسال طلب العملية؛ أُلغيت الحالة. حاول مجدداً.")
        return False
    prompt_id = getattr(sent, 'message_id', None)
    if prompt_id is not None:
        try:
            bound = set_user_state(admin_id, state, dict(context, prompt_message_id=prompt_id))
        except Exception as exc:
            logger.warning("Could not bind admin flow prompt: %s", type(exc).__name__)
            bound = False
        if bound is not True:
            _cleanup_state(bot, admin_id)
            bot.send_message(admin_id, "❌ تعذّر تجهيز العملية (خطأ في قاعدة البيانات)؛ أُلغيت الحالة.")
            return False
    return True


def _cleanup_state(bot, user_id):
    """تنظيف أفضل جهد للحالة بعد فشل بدء التدفق (لا يُخفي سبب الفشل الأصلي)."""
    try:
        if clear_user_waiting_state(user_id) is not True:
            logger.warning("Best-effort state cleanup was not confirmed for %s", user_id)
    except Exception as exc:
        logger.warning("Best-effort state cleanup failed for %s: %s", user_id, type(exc).__name__)
    return True


def _retry(bot, user_id, text):
    bot.send_message(user_id, text, parse_mode="HTML")


def _finish(user_id):
    """True فقط عند تأكيد مسح الحالة من القاعدة (الفشل قيمة False لا استثناء)."""
    try:
        cleared = clear_user_waiting_state(user_id)
    except Exception as exc:
        logger.warning("Could not clear admin flow state: %s", type(exc).__name__)
        return False
    return cleared is True


def _cancel(bot, user_id, admin_ids, message="✅ تم إلغاء العملية."):
    if _finish(user_id):
        bot.send_message(user_id, message)
    else:
        bot.send_message(user_id, "❌ تعذّر إلغاء العملية (خطأ في قاعدة البيانات).")
    return True


# ---------------------------------------------------------------------------
# التصنيفات
# ---------------------------------------------------------------------------

def begin_category(bot, admin_id, admin_ids, parent_id=None):
    """إضافة تصنيف (رئيسي عند parent_id=None أو فرعي تحت تصنيف موجود)."""
    parent = None
    if parent_id is not None:
        try:
            parent = get_category_by_id(parent_id)
        except Exception as exc:
            logger.error("Category lookup failed: %s", type(exc).__name__)
            bot.send_message(admin_id, "❌ تعذّر قراءة التصنيف الأب (خطأ في قاعدة البيانات).")
            return False
        if not parent:
            bot.send_message(admin_id, f"❌ التصنيف الأب #{_safe(parent_id, 12)} غير موجود.")
            return False
    where = f' تحت 📂 "{_safe(parent.get("name"))}"' if parent else ""
    prompt = f"📝 أرسل اسم التصنيف الجديد{where} (أو /cancel):"
    return _begin(bot, admin_id, admin_ids, ST_CATEGORY, prompt,
                  {'parent_id': parent_id}, cancel_callback='admin::cancel_flow')


def receive_category_name(bot, admin_ids, message):
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    state = read_state_row(user_id)
    if not state or state.get('state') != ST_CATEGORY:
        return False
    context = state.get('context') or {}
    if not _is_private(message) or not _is_admin(user_id, admin_ids):
        return False
    if not _owner_ok(context, user_id):
        return False
    name = ' '.join(str(getattr(message, 'text', None) or '').split())
    if not name:
        _retry(bot, user_id, "❌ أرسل اسم تصنيف نصياً غير فارغ (أو /cancel).")
        return True
    if len(name) > MAX_CATEGORY_NAME:
        _retry(bot, user_id, f"❌ الاسم طويل جداً (الحد {MAX_CATEGORY_NAME} حرفاً). أرسل اسماً أقصر.")
        return True
    if name.startswith('/'):
        _retry(bot, user_id, "❌ الأوامر لا تُحفظ كاسم تصنيف. أرسل الاسم أو /cancel.")
        return True
    parent_id = context.get('parent_id')
    try:
        success, result = add_category(name, parent_id=parent_id)
    except Exception as exc:
        logger.error("Category insert failed: %s", type(exc).__name__)
        success, result = False, None
    if not success:
        reason = result if isinstance(result, str) else "خطأ في قاعدة البيانات"
        _retry(bot, user_id, f"❌ تعذّر إنشاء التصنيف: {_safe(reason, 120)}")
        return True
    cleared = _finish(user_id)
    parent_note = ""
    if parent_id is not None:
        try:
            parent = get_category_by_id(parent_id)
        except Exception:
            parent = None
        if parent:
            parent_note = f' تحت 📂 "{_safe(parent.get("name"))}"'
    _after_success(bot, user_id, f'✅ تم إنشاء التصنيف "{_safe(name)}"{parent_note}.',
                   state_cleared=cleared)
    return True


# ---------------------------------------------------------------------------
# قنوات الاشتراك
# ---------------------------------------------------------------------------

def begin_channel_add(bot, admin_id, admin_ids):
    return _begin(bot, admin_id, admin_ids, ST_CHANNEL_ID,
                  "📝 أرسل معرّف القناة الرقمي (مثال: <code>-1001234567890</code>).\n"
                  "⚠️ اسم القناة @username غير مدعوم في قنوات الاشتراك هذه. (أو /cancel)")


def _existing_channel(channel_id):
    """(channel_or_None, error): فشل الاستعلام يُعاد صراحةً ولا يُعامل كـ«غير مضافة»."""
    try:
        return find_required_channel(channel_id=channel_id), None
    except Exception as exc:
        logger.error("Required channel lookup failed: %s", type(exc).__name__)
        return None, exc


def receive_channel_id(bot, admin_ids, message):
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    state = read_state_row(user_id)
    if not state or state.get('state') != ST_CHANNEL_ID:
        return False
    context = state.get('context') or {}
    if not _is_private(message) or not _is_admin(user_id, admin_ids) or not _owner_ok(context, user_id):
        return False
    text = getattr(message, 'text', None)
    channel_id = normalize_channel_id(text)
    if channel_id is None:
        _retry(bot, user_id,
               "❌ معرّف غير صالح. أرسل رقماً فقط يبدأ بـ <code>-100</code>. "
               "اسم @username غير مدعوم. (أو /cancel)")
        return True
    existing, lookup_error = _existing_channel(channel_id)
    if lookup_error is not None:
        _retry(bot, user_id, "❌ تعذّر التحقق من القنوات (خطأ في قاعدة البيانات). حاول مجدداً أو /cancel.")
        return True
    if existing:
        _retry(bot, user_id, f"ℹ️ القناة <code>{_safe(channel_id, 24)}</code> مضافة مسبقاً. "
                             "أرسل معرّفاً آخر أو /cancel.")
        return True
    context['channel_id'] = channel_id
    try:
        prompt = bot.send_message(user_id,
                                  "📝 الآن أرسل اسم القناة كما سيظهر للمشتركين. (أو /cancel)",
                                  reply_markup=_prompt_markup('admin::cancel_flow'))
        context['prompt_message_id'] = getattr(prompt, 'message_id', None)
        stored = set_user_state(user_id, ST_CHANNEL_NAME, context)
    except Exception as exc:
        logger.error("Could not move to channel name state: %s", type(exc).__name__)
        stored = False
    if stored is not True:
        _cleanup_state(bot, user_id)
        _retry(bot, user_id, "❌ تعذّر متابعة الإضافة (خطأ في قاعدة البيانات). استخدم /cancel.")
    return True


def receive_channel_name(bot, admin_ids, message):
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    state = read_state_row(user_id)
    if not state or state.get('state') != ST_CHANNEL_NAME:
        return False
    context = state.get('context') or {}
    if not _is_private(message) or not _is_admin(user_id, admin_ids) or not _owner_ok(context, user_id):
        return False
    channel_id = normalize_channel_id(context.get('channel_id'))
    if channel_id is None:
        _finish(user_id)
        bot.send_message(user_id, "❌ انتهت صلاحية الطلب. ابدأ الإضافة من جديد.")
        return True
    name = ' '.join(str(getattr(message, 'text', None) or '').split())
    if not name or name.startswith('/') or len(name) > MAX_CHANNEL_NAME:
        _retry(bot, user_id, f"❌ أرسل اسماً نصياً (حتى {MAX_CHANNEL_NAME} حرفاً). (أو /cancel)")
        return True
    try:
        saved = add_required_channel(channel_id, name) is True
    except Exception as exc:
        logger.error("Required channel insert failed: %s", type(exc).__name__)
        saved = False
    if not saved:
        _retry(bot, user_id, "❌ تعذّر حفظ القناة. تحقق من المعرّف وحاول مجدداً أو /cancel.")
        return True
    _after_success(bot, user_id, f'✅ تمت إضافة القناة "{_safe(name)}" '
                                 f'(<code>{_safe(channel_id, 24)}</code>).')
    return True


def begin_channel_remove(bot, admin_id, admin_ids):
    if not _is_admin(admin_id, admin_ids):
        return False
    try:
        channels = fetch_admin_required_channels()
    except Exception as exc:
        logger.error("Admin channel list failed: %s", type(exc).__name__)
        channels = None
    if channels is None:
        bot.send_message(admin_id, "❌ تعذّر قراءة القنوات من قاعدة البيانات.")
        return False
    if not channels:
        bot.send_message(admin_id, "ℹ️ لا توجد قنوات اشتراك مضافة.")
        return False
    markup = InlineKeyboardMarkup(row_width=1)
    for channel in channels:
        markup.add(inline_button(f"➖ {str(channel.get('channel_name') or channel.get('channel_id'))} 🔴",
                                 STYLE_DANGER,
                                 callback_data=f"admin::remove_channel_confirm::{channel.get('channel_id')}"))
    markup.add(inline_button("↩️ إلغاء 🟡", STYLE_DANGER, callback_data='back_to_main'))
    bot.send_message(admin_id, "🗑️ اختر القناة التي تريد إزالتها:", reply_markup=markup)
    return True


def remove_channel_now(bot, admin_id, admin_ids, channel_id):
    if not _is_admin(admin_id, admin_ids):
        return False
    if normalize_channel_id(channel_id) is None:
        bot.send_message(admin_id, "❌ رقم قناة غير صالح.")
        return True
    try:
        removed = bool(remove_required_channel(int(channel_id)))
    except Exception as exc:
        logger.error("Required channel delete failed: %s", type(exc).__name__)
        removed = False
    if removed:
        bot.send_message(admin_id, f"✅ تمت إزالة القناة <code>{_safe(channel_id, 24)}</code>.")
    else:
        bot.send_message(admin_id, "❌ لم تُزل أي قناة (غير موجودة أو تعذّر التحقق). تحقق من الرقم.")
    return True


def receive_channel_remove(bot, admin_ids, message):
    """إدخال رقم قناة نصياً (مسار بديل عن الأزرار)."""
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    state = read_state_row(user_id)
    if not state or state.get('state') != ST_CHANNEL_REMOVE:
        return False
    context = state.get('context') or {}
    if not _is_private(message) or not _is_admin(user_id, admin_ids) or not _owner_ok(context, user_id):
        return False
    channel_id = normalize_channel_id(getattr(message, 'text', None))
    if channel_id is None:
        _retry(bot, user_id, "❌ أرسل معرّفاً رقمياً للقناة (أو /cancel).")
        return True
    cleared = _finish(user_id)
    remove_channel_now(bot, user_id, admin_ids, channel_id)
    if cleared is not True:
        bot.send_message(user_id, "⚠️ نُفّذت الإزالة، لكن تعذّر تنظيف حالة الطلب؛ أرسل /cancel.")
    return True


def list_channels(bot, chat_id):
    try:
        channels = fetch_admin_required_channels()
    except Exception as exc:
        logger.error("Admin channel list failed: %s", type(exc).__name__)
        channels = None
    if channels is None:
        bot.send_message(chat_id, "❌ تعذّر جلب قنوات الاشتراك من قاعدة البيانات.", parse_mode="HTML")
        return False
    if not channels:
        bot.send_message(chat_id, "ℹ️ لا توجد قنوات اشتراك مضافة حالياً.")
        return True
    lines = [f"📋 <b>قنوات الاشتراك ({len(channels)}):</b>"]
    for channel in channels[:50]:
        lines.append(f"• {_safe(channel.get('channel_name'), 60)} "
                     f"(<code>{_safe(channel.get('channel_id'), 24)}</code>)")
    bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
    return True


# ---------------------------------------------------------------------------
# نقل / حذف بالفيديو
# ---------------------------------------------------------------------------

def begin_move(bot, admin_id, admin_ids):
    return _begin(bot, admin_id, admin_ids, ST_MOVE_IDS,
                  "📝 أرسل رقم الفيديو أو أرقاماً متعددة مفصولة بمسافة/فاصلة. (أو /cancel)")


def receive_move_ids(bot, admin_ids, message):
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    state = read_state_row(user_id)
    if not state or state.get('state') != ST_MOVE_IDS:
        return False
    context = state.get('context') or {}
    if not _is_private(message) or not _is_admin(user_id, admin_ids) or not _owner_ok(context, user_id):
        return False
    ids = parse_ids(getattr(message, 'text', None))
    if ids is None:
        _retry(bot, user_id, "❌ أرقام غير صالحة. أرسل أرقاماً موجبة فقط مفصولة بمسافة. (أو /cancel)")
        return True
    try:
        existing = count_existing_video_ids(ids)
        categories = fetch_admin_categories()
    except Exception as exc:
        logger.error("Move preparation failed: %s", type(exc).__name__)
        _retry(bot, user_id, "❌ تعذّرت قراءة قاعدة البيانات. حاول مجدداً أو /cancel.")
        return True
    if existing is None or categories is None:
        _retry(bot, user_id, "❌ تعذّرت قراءة قاعدة البيانات. حاول مجدداً أو /cancel.")
        return True
    if not existing:
        _retry(bot, user_id, "❌ لا يوجد فيديو بهذه الأرقام. أرسل أرقاماً صحيحة أو /cancel.")
        return True
    context.update({'video_ids': ids, 'resolved_ids': ids[:existing]})
    try:
        stored = set_user_state(user_id, ST_MOVE_TARGET, context)
    except Exception as exc:
        logger.error("Could not store move target state: %s", type(exc).__name__)
        stored = False
    if stored is not True:
        _retry(bot, user_id, "❌ تعذّر متابعة النقل (خطأ في قاعدة البيانات).")
        return True
    markup = InlineKeyboardMarkup(row_width=1)
    for category in categories:
        markup.add(inline_button(f"📂 {str(category.get('name'))}", STYLE_PRIMARY,
                                 callback_data=f"admin::move_confirm::{category.get('id')}"))
    markup.add(inline_button("📦 بلا تصنيف 🟡", STYLE_PRIMARY, callback_data="admin::move_confirm::none"))
    markup.add(inline_button("↩️ إلغاء 🟡", STYLE_DANGER, callback_data="admin::cancel_flow"))
    bot.send_message(user_id,
                     f"🎯 اختر التصنيف الجديد للفيديو: {', '.join(str(i) for i in ids[:20])}",
                     reply_markup=markup)
    return True


def move_now(bot, admin_id, admin_ids, new_category_id, call=None, answer=None):
    """تأكيد النقل من الزر: يقرأ الأرقام من الحالة نفسها (لا ذاكرة عملية)."""
    if not _is_admin(admin_id, admin_ids):
        return False
    state = read_state_row(admin_id)
    context = (state or {}).get('context') or {}
    if not state or state.get('state') != ST_MOVE_TARGET or not _owner_ok(context, admin_id):
        _notify(bot, answer, admin_id, "⌛ انتهت صلاحية طلب النقل. أعد المحاولة من لوحة الأدمن.")
        return True
    ids = context.get('video_ids') or []
    if not ids:
        _notify(bot, answer, admin_id, "⌛ طلب النقل غير صالح (لا أرقام). أعد المحاولة من لوحة الأدمن.")
        return True
    target = None
    if str(new_category_id).lower() not in ('none', '0', ''):
        try:
            target = int(new_category_id)
            category = get_category_by_id(target)
        except Exception:
            category = None
        if not category:
            _notify(bot, answer, admin_id, "❌ التصنيف الهدف غير موجود. لم يُنقل شيء.")
            return True
    try:
        moved, error = move_videos_bulk_reported(ids, target)
    except Exception as exc:
        logger.error("Move failed: %s", type(exc).__name__)
        moved, error = None, exc
    if error is not None:
        _notify(bot, answer, admin_id, "❌ تعذّر نقل الفيديوهات (خطأ في قاعدة البيانات).")
        return True
    cleared = _finish(admin_id)
    missing = max(len(ids) - (moved or 0), 0)
    where = "بلا تصنيف" if target is None else "التصنيف المطلوب"
    text = f"✅ تم نقل {moved} فيديو إلى {where}."
    if missing:
        text += f"\n⚠️ لم يُنقل {missing} رقم (غير موجود أو لم يتغير)."
    _after_success(bot, admin_id, text, state_cleared=cleared)
    return True


def _notify(bot, call, admin_id, text):
    """إبلاغ الأدمن بفشل/رفض: رد الـ callback إن وُجد، ورسالة للأدمن دائماً."""
    if call is not None:
        try:
            bot.answer_callback_query(call.id, text, show_alert=True)
        except Exception as exc:
            logger.warning("Could not answer callback query: %s", type(exc).__name__)
    chat_id = getattr(getattr(getattr(call, 'message', None), 'chat', None), 'id', None) or admin_id
    if chat_id is not None:
        try:
            bot.send_message(chat_id, text)
        except Exception:
            pass


def begin_delete(bot, admin_id, admin_ids):
    return _begin(bot, admin_id, admin_ids, ST_DELETE_IDS,
                  "📝 أرسل أرقام الفيديوهات التي تريد حذفها، مفصولة بمسافة أو فاصلة. (أو /cancel)")


def receive_delete_ids(bot, admin_ids, message):
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    state = read_state_row(user_id)
    if not state or state.get('state') != ST_DELETE_IDS:
        return False
    context = state.get('context') or {}
    if not _is_private(message) or not _is_admin(user_id, admin_ids) or not _owner_ok(context, user_id):
        return False
    ids = parse_ids(getattr(message, 'text', None))
    if ids is None:
        _retry(bot, user_id, "❌ أرقام غير صالحة. أرسل أرقاماً موجبة فقط مفصولة بمسافة. (أو /cancel)")
        return True
    try:
        existing = count_existing_video_ids(ids)
    except Exception as exc:
        logger.error("Delete preparation failed: %s", type(exc).__name__)
        _retry(bot, user_id, "❌ تعذّر تجهيز الحذف (خطأ في قاعدة البيانات).")
        return True
    if not existing:
        _retry(bot, user_id, "❌ لا يوجد فيديو بهذه الأرقام. أرسل أرقاماً صحيحة أو /cancel.")
        return True
    try:
        stored = set_user_state(user_id, ST_DELETE_CONFIRM, dict(context, video_ids=ids))
    except Exception as exc:
        logger.error("Could not store delete confirmation state: %s", type(exc).__name__)
        stored = False
    if stored is not True:
        _retry(bot, user_id, "❌ تعذّر تجهيز الحذف (خطأ في قاعدة البيانات).")
        return True
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(inline_button(f"🗑️ تأكيد حذف {existing} فيديو 🔴", STYLE_DANGER,
                             callback_data="admin::confirm_delete_ids"))
    markup.add(inline_button("↩️ إلغاء 🟢", STYLE_SUCCESS, callback_data="admin::cancel_flow"))
    bot.send_message(user_id,
                     "⚠️ <b>تأكيد الحذف</b>\n"
                     f"الأرقام: {', '.join(str(i) for i in ids[:30])}\n"
                     "الحذف من الأرشيف لا يمكن التراجع عنه.",
                     reply_markup=markup, parse_mode="HTML")
    return True


def delete_now(bot, admin_id, admin_ids, call=None):
    if not _is_admin(admin_id, admin_ids):
        return False
    state = read_state_row(admin_id)
    context = (state or {}).get('context') or {}
    if not state or state.get('state') != ST_DELETE_CONFIRM or not _owner_ok(context, admin_id):
        _notify(bot, call, admin_id, "⌛ انتهت صلاحية طلب الحذف. أعد المحاولة من لوحة الأدمن.")
        return True
    ids = context.get('video_ids') or []
    if not ids:
        _notify(bot, call, admin_id, "⌛ طلب الحذف غير صالح (لا أرقام). أعد المحاولة من لوحة الأدمن.")
        return True
    try:
        deleted, error = delete_videos_by_ids_reported(ids)
    except Exception as exc:
        logger.error("Delete failed: %s", type(exc).__name__)
        deleted, error = None, exc
    if error is not None:
        _notify(bot, call, admin_id, "❌ تعذّر حذف الفيديوهات (خطأ في قاعدة البيانات).")
        return True
    cleared = _finish(admin_id)
    missing = max(len(ids) - (deleted or 0), 0)
    text = f"✅ تم حذف {deleted} فيديو."
    if missing:
        text += f"\n⚠️ {missing} رقم لم يُحذف (غير موجود)."
    _after_success(bot, admin_id, text, state_cleared=cleared)
    return True


# ---------------------------------------------------------------------------
# التطبيق المصغّر
# ---------------------------------------------------------------------------

def begin_miniapp_limit(bot, admin_id, admin_ids):
    return _begin(bot, admin_id, admin_ids, ST_MINIAPP_LIMIT,
                  f"⏱️ أرسل حد فتح المكتبة لكل IP في الساعة (0 = بلا حد، الأقصى "
                  f"{MAX_MINIAPP_HOURLY_LIMIT}). (أو /cancel)")


def receive_miniapp_limit(bot, admin_ids, message):
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    state = read_state_row(user_id)
    if not state or state.get('state') != ST_MINIAPP_LIMIT:
        return False
    context = state.get('context') or {}
    if not _is_private(message) or not _is_admin(user_id, admin_ids) or not _owner_ok(context, user_id):
        return False
    value = parse_miniapp_limit(getattr(message, 'text', None))
    if value is None:
        _retry(bot, user_id, f"❌ قيمة غير صالحة: أرسل رقماً بين 0 و{MAX_MINIAPP_HOURLY_LIMIT}. (أو /cancel)")
        return True
    try:
        saved = set_bot_setting(MINIAPP_HOURLY_SETTING_KEY, str(value)) is True
    except Exception as exc:
        logger.error("Mini App limit save failed: %s", type(exc).__name__)
        saved = False
    if not saved:
        _retry(bot, user_id, "⚠️ تعذّر حفظ الحد في قاعدة البيانات. حاول مجدداً أو /cancel.")
        return True
    cleared = _finish(user_id)
    suffix = " (بلا حد)" if value == 0 else ""
    _after_success(bot, user_id, f"✅ حد فتح المكتبة لكل IP أصبح {value} في الساعة.{suffix}",
                   state_cleared=cleared)
    return True


def begin_miniapp_ip(bot, admin_id, admin_ids):
    return _begin(bot, admin_id, admin_ids, ST_MINIAPP_IP,
                  "✏️ أرسل عنوان IP (مثال: <code>203.0.113.7</code>) لعرض زر فتح المكتبة معه. (أو /cancel)")


def receive_miniapp_ip(bot, admin_ids, message):
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    state = read_state_row(user_id)
    if not state or state.get('state') != ST_MINIAPP_IP:
        return False
    context = state.get('context') or {}
    if not _is_private(message) or not _is_admin(user_id, admin_ids) or not _owner_ok(context, user_id):
        return False
    ip = parse_admin_ip(getattr(message, 'text', None))
    if ip is None:
        _retry(bot, user_id, "❌ عنوان IP غير صالح (مثال: 203.0.113.7). (أو /cancel)")
        return True
    cleared = _finish(user_id)
    if cleared is not True:
        bot.send_message(user_id, "⚠️ تعذّر تنظيف حالة الطلب؛ أرسل /cancel بعد إكمال العملية.")
    try:
        from .admin_handlers import _miniapp_open_url
        from telebot.types import WebAppInfo
        url = _miniapp_open_url(ip)
    except Exception as exc:
        logger.warning("Mini App URL build failed: %s", type(exc).__name__)
        url = None
    if not url:
        bot.send_message(user_id, "⚠️ المكتبة غير متاحة: تحقق من APP_URL.")
        return True
    markup = InlineKeyboardMarkup(row_width=1)
    from telebot.types import InlineKeyboardButton
    markup.add(InlineKeyboardButton(f"📱 فتح المكتبة لـ {ip}", web_app=WebAppInfo(url=url)))
    bot.send_message(user_id, "📱 افتح المكتبة من الزر أدناه:", reply_markup=markup)
    return True


def delete_category_with_contents(bot, admin_id, admin_ids, category_id, call=None):
    """حذف تصنيف ومحتوياته في معاملة واحدة (تراجع كامل عند أي فشل)."""
    if not _is_admin(admin_id, admin_ids):
        return False
    try:
        cid = int(category_id)
    except (TypeError, ValueError):
        _notify(bot, call, admin_id, "❌ رقم التصنيف غير صالح. لم يتغير شيء.")
        return True
    from db_manager import CommitOutcomeUnknown
    try:
        outcome = delete_category_with_contents_atomic(cid)
    except CommitOutcomeUnknown:
        _notify(bot, call, admin_id,
                "⚠️ نتيجة الحذف غير مؤكدة (فشل الالتزام). راجع التصنيف قبل أي إعادة محاولة.")
        return True
    except Exception as exc:
        logger.error("Atomic category delete failed: %s", type(exc).__name__)
        _notify(bot, call, admin_id, "❌ تعذّر حذف التصنيف. تراجعت المعاملة ولم يتغير شيء.")
        return True
    if outcome.get('status') == 'missing':
        _notify(bot, call, admin_id, "❌ التصنيف غير موجود. لم يُحذف شيء.")
        return True
    if outcome.get('status') == 'has_children':
        _notify(bot, call, admin_id,
                "❌ التصنيف يحتوي تصنيفات فرعية. انقل/احذف التصنيفات الفرعية أولاً. لم يتغير شيء.")
        return True
    # هذه الأزرار لا تملك حالة تدفق خاصة: لا نمسح تدفقاً آخر معلّقاً
    bot.send_message(
        admin_id, f'✅ تم حذف التصنيف و {outcome.get("videos", 0)} فيديو من محتوياته.')
    return True

def _after_success(bot, admin_id, text, state_cleared=None):
    """إعلان نجاح العملية مع بيان صريح إن تعذّر تنظيف حالة الطلب."""
    if state_cleared is None:
        state_cleared = _finish(admin_id)
    bot.send_message(admin_id, text)
    if state_cleared is not True:
        bot.send_message(admin_id, "⚠️ العملية اكتملت، لكن تعذّر تنظيف حالة الطلب؛ أرسل /cancel.")


def move_category_contents(bot, admin_id, admin_ids, old_id, new_id, call=None):
    """نقل فيديوهات تصنيف ثم حذفه في معاملة واحدة؛ لا دفع جزئي ولا حذف قبل نقل مؤكد."""
    if not _is_admin(admin_id, admin_ids):
        return False
    try:
        source_id = int(old_id)
        target_id = int(new_id)
    except (TypeError, ValueError):
        _notify(bot, call, admin_id, "❌ أرقام التصنيفات غير صالحة. لم يتغير شيء.")
        return True
    if source_id == target_id:
        _notify(bot, call, admin_id, "❌ لا يمكن نقل الفيديوهات إلى التصنيف نفسه. لم يتغير شيء.")
        return True
    from db_manager import CommitOutcomeUnknown
    try:
        outcome = move_category_contents_atomic(source_id, target_id)
    except CommitOutcomeUnknown:
        _notify(bot, call, admin_id,
                "⚠️ نتيجة النقل والحذف غير مؤكدة (فشل الالتزام). راجع التصنيفات قبل أي إعادة محاولة.")
        return True
    except Exception as exc:
        logger.error("Atomic category move failed: %s", type(exc).__name__)
        _notify(bot, call, admin_id, "❌ تعذّر النقل والحذف. تراجعت المعاملة ولم يتغير شيء.")
        return True
    if outcome.get('status') == 'missing':
        _notify(bot, call, admin_id, "❌ أحد التصنيفين غير موجود. لم ينقل ولم يُحذف شيء.")
        return True
    if outcome.get('status') == 'has_children':
        _notify(bot, call, admin_id,
                "❌ التصنيف يحتوي تصنيفات فرعية. انقل/احذف التصنيفات الفرعية أولاً. لم يتغير شيء.")
        return True
    # بلا حالة خاصة لهذه الأزرار: يُعرض النجاح بلا مسح لأي تدفق معلّق
    bot.send_message(admin_id, f'✅ نُقل {outcome.get("videos", 0)} فيديو إلى التصنيف المطلوب '
                               "وحُذف التصنيف الأصلي.")
    return True

# ---------------------------------------------------------------------------
# التوجيه الموحّد لرسائل الأدمن في حالة تدفق
# ---------------------------------------------------------------------------

FLOW_RECEIVERS = {
    ST_CATEGORY: receive_category_name,
    ST_CHANNEL_ID: receive_channel_id,
    ST_CHANNEL_NAME: receive_channel_name,
    ST_CHANNEL_REMOVE: receive_channel_remove,
    ST_MOVE_IDS: receive_move_ids,
    ST_DELETE_IDS: receive_delete_ids,
    ST_MINIAPP_LIMIT: receive_miniapp_limit,
    ST_MINIAPP_IP: receive_miniapp_ip,
}


def handle_flow_message(bot, admin_ids, message):
    """True إذا كانت الرسالة تخص حالة تدفق مفتوحة لهذا الأدمن (وإلا False)."""
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    if not _is_admin(user_id, admin_ids):
        return False
    state = read_state_row(user_id)
    receiver = FLOW_RECEIVERS.get((state or {}).get('state'))
    if receiver is None:
        return False
    try:
        return receiver(bot, admin_ids, message)
    except Exception as exc:
        logger.error("Admin flow handler failed: %s", type(exc).__name__, exc_info=True)
        _retry(bot, user_id, "❌ حدث خطأ غير متوقع. أعد المحاولة أو /cancel.")
        return True


def cancel_flow(bot, admin_id, admin_ids, call=None):
    """زر إلغاء التدفقات: مسح بلا تعديل. False إذا لم يكن المستخدم أدمن."""
    if not _is_admin(admin_id, admin_ids):
        return False
    cleared = _finish(admin_id)
    _notify(bot, call, admin_id, "✅ تم الإلغاء" if cleared else "❌ تعذّر إلغاء العملية")
    return True
