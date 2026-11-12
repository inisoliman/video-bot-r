# handlers/admin_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import functools
import logging
import os
import re
from telebot.apihelper import ApiTelegramException
from telebot.handler_backends import ContinueHandling
from .button_styles import STYLE_DANGER, STYLE_PRIMARY, STYLE_SUCCESS, inline_button
from miniapp import miniapp_url

from db_manager import (
    add_category, get_all_user_ids, add_required_channel, remove_required_channel,
    get_required_channels, get_subscriber_count, get_bot_stats, get_popular_videos,
    delete_videos_by_ids, get_video_by_id, delete_bot_user,
    delete_category_and_contents, move_videos_from_category, delete_category_by_id,
    get_categories_tree, set_active_category_id, get_child_categories,
    move_videos_bulk, get_category_by_id, set_default_thumbnail, update_video_thumbnail  # إضافة الدوال الجديدة
)

from state_manager import state_handler, clear_user_waiting_state, set_user_waiting_for_input, States

from . import admin_broadcast
# تدفق البث (زر -> حالة مخزّنة -> رسالة -> عامل): يبقى مخفياً في module منفصل
from .admin_broadcast import (  # noqa: F401  (أسماء متوافقة مع استيرادات قديمة)
    STATE_BROADCAST as STATE_BROADCAST_MESSAGE,
    CALLBACK_CANCEL_BROADCAST,
    cancel_broadcast,
    handle_broadcast_message,
    handle_rich_broadcast,
    has_active_job as broadcast_active_for,
    prompt_broadcast,
    run_broadcast,
)
# تدفق الحقول الآمن عبر الحالة المخزّنة (بدل next_step/admin_steps)
from . import admin_flows
for _name in ('begin_category', 'receive_category_name', 'begin_channel_add',
              'receive_channel_id', 'receive_channel_name', 'begin_channel_remove',
              'receive_channel_remove', 'remove_channel_now', 'list_channels',
              'begin_move', 'receive_move_ids', 'move_now',
              'begin_delete', 'receive_delete_ids', 'delete_now',
              'begin_miniapp_limit', 'receive_miniapp_limit', 'begin_miniapp_ip',
              'receive_miniapp_ip', 'handle_flow_message', 'cancel_flow',
              'delete_category_with_contents', 'move_category_contents'):
    globals()[_name] = getattr(admin_flows, _name)
del _name

from .helpers import create_categories_keyboard, CALLBACK_DELIMITER, create_hierarchical_category_keyboard

logger = logging.getLogger(__name__)

# ==============================================================================
# التطبيق المصغّر (Mini App) في لوحة الأدمن
# ==============================================================================
MINIAPP_HOURLY_SETTING_KEY = 'miniapp_hourly_limit_per_ip'
DEFAULT_MINIAPP_HOURLY_LIMIT = 0        # 0 = بلا حد للمكتبة
MAX_MINIAPP_HOURLY_LIMIT = 100000
MINIAPP_IP_BUTTON_TEXT = "📱 فتح المكتبة بعنوان IP يدوي"
MINIAPP_IP_PROMPT = "✏️ أرسل عنوان IP (مثال: 203.0.113.7) لعرض زر فتح المكتبة معه:"
_INITDATA_QUERY_PARAM = 'tgWebAppData'  # يفتح الرابط كتطبيق مصغّر داخل تيليجرام


def _miniapp_active():
    """True فقط عندما يكون APP_URL رابط HTTPS صالحاً (وإلا لا يُضاف أي زر)."""
    try:
        return bool(miniapp_url())
    except Exception as exc:
        logger.warning(f"Mini App admin buttons skipped: {type(exc).__name__}")
        return False


def _miniapp_open_url(ip_address=None):
    """رابط التطبيق المصغّر (وخيار IP يدوي لفحص حد الفتح من جهاز الأدمن)."""
    base = miniapp_url()
    if not base:
        return None
    query = f"{_INITDATA_QUERY_PARAM}=%7B%7D"
    if ip_address:
        query += f"&ip={ip_address}"
    url = f"{base}?{query}"
    return url if len(url) <= 512 else None  # أقصى طول يقبله تيليجرام لعناوين Web App


def _setting(key, default=''):
    """قراءة إعداد من bot_settings بأمان (فشل القاعدة لا يعطّل اللوحة)."""
    try:
        from db_manager import get_bot_setting
        value = get_bot_setting(key, default)
    except Exception as exc:
        logger.warning(f"Mini App setting {key} unavailable: {type(exc).__name__}")
        return default
    return value


def _int_setting(key, default, maximum):
    try:
        value = int(str(_setting(key, str(default)) or default).strip())
    except (TypeError, ValueError):
        return default
    return max(0, min(value, maximum))


def miniapp_hourly_limit_by_ip():
    """حد فتح المكتبة لكل IP في الساعة (0 = بلا حد) - يقرأه miniapp لكل صفحة."""
    return _int_setting(MINIAPP_HOURLY_SETTING_KEY, DEFAULT_MINIAPP_HOURLY_LIMIT,
                        MAX_MINIAPP_HOURLY_LIMIT)


def parse_admin_ip(text):
    """عنوان IPv4/IPv6 صالح من نص الأدمن، أو None."""
    value = (text or '').strip()
    try:
        import ipaddress
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


def _set_miniapp_hourly_limit(bot, chat_id, value):
    """حفظ حد فتح المكتبة وإرسال تأكيد."""
    from db_manager import set_bot_setting
    if set_bot_setting(MINIAPP_HOURLY_SETTING_KEY, str(value)):
        suffix = " (بلا حد)" if value == 0 else ""
        bot.send_message(chat_id, f"✅ حد فتح المكتبة لكل IP أصبح {value} في الساعة.{suffix}")
    else:
        bot.send_message(chat_id, "⚠️ تعذر حفظ الحد في قاعدة البيانات. حاول مجدداً.")
    return True


def miniapp_limit_prompt(bot, chat_id):
    """طلب قيمة الحد من الأدمن (الرقم التالي من الأدمن يُحفظ)."""
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(inline_button("↩️ إلغاء 🟡", STYLE_DANGER, callback_data="back_to_main"))
    bot.send_message(chat_id,
                     f"⏱️ أرسل حد فتح المكتبة لكل IP في الساعة (0 = بلا حد، "
                     f"الحد الأقصى {MAX_MINIAPP_HOURLY_LIMIT}):",
                     reply_markup=keyboard)
    return True


def build_admin_panel():
    """لوحة تحكم الأدمن كاملة (تُبنى عند كل طلب، فلا حالة مشتركة).

    أزرار التطبيق المصغّر تُضاف فقط عندما يكون APP_URL رابط HTTPS صالحاً.
    """
    keyboard = InlineKeyboardMarkup(row_width=2)

    # ─── قسم: إدارة التصنيفات ───
    keyboard.add(inline_button("╭─ 🗂️ إدارة التصنيفات ─╮", STYLE_PRIMARY, callback_data="noop"))
    keyboard.add(inline_button("➕ إضافة تصنيف 🟢", STYLE_SUCCESS, callback_data="admin::add_new_cat"),
                 inline_button("🗑️ حذف تصنيف 🔴", STYLE_DANGER, callback_data="admin::delete_category_select"))
    keyboard.add(inline_button("🔘 تعيين التصنيف النشط 🟡", STYLE_PRIMARY, callback_data="admin::set_active"))

    # ─── قسم: إدارة الفيديوهات ───
    keyboard.add(inline_button("╭─ 🎬 إدارة الفيديوهات ─╮", STYLE_PRIMARY, callback_data="noop"))
    keyboard.add(inline_button("➡️ نقل فيديو بالرقم 🔵", STYLE_PRIMARY, callback_data="admin::move_video_by_id"),
                 inline_button("❌ حذف فيديوهات 🔴", STYLE_DANGER, callback_data="admin::delete_videos_by_ids"))
    keyboard.add(inline_button("✏️ تعديل اسم الفيديو 🟣", STYLE_PRIMARY, callback_data="admin::rename_title"))
    keyboard.add(inline_button("🔢 تعديل عنوان فيلم برقم 🟣", STYLE_PRIMARY, callback_data="admin::edit_title"))
    keyboard.add(inline_button("🔄 تحديث البيانات القديمة 🟣", STYLE_PRIMARY, callback_data="admin::update_metadata"))

    # ─── قسم: إدارة التعليقات ───
    keyboard.add(inline_button("╭─ 💬 إدارة التعليقات ─╮", STYLE_PRIMARY, callback_data="noop"))
    keyboard.add(inline_button("💬 عرض التعليقات 🔵", STYLE_PRIMARY, callback_data="admin::view_comments"),
                 inline_button("📊 الإحصائيات 🟣", STYLE_PRIMARY, callback_data="admin::comments_stats"))
    keyboard.add(inline_button("🧹 حذف التعليقات القديمة 🟡", STYLE_DANGER, callback_data="admin::delete_old_comments"),
                 inline_button("🗑️ حذف الكل 🔴", STYLE_DANGER, callback_data="admin::delete_all_comments"))

    # ─── قسم: قنوات الاشتراك ───
    keyboard.add(inline_button("╭─ 📢 قنوات الاشتراك ─╮", STYLE_PRIMARY, callback_data="noop"))
    keyboard.add(inline_button("➕ إضافة قناة 🟢", STYLE_SUCCESS, callback_data="admin::add_channel"),
                 inline_button("➖ إزالة قناة 🔴", STYLE_DANGER, callback_data="admin::remove_channel"))
    keyboard.add(inline_button("📋 عرض القنوات 🔵", STYLE_PRIMARY, callback_data="admin::list_channels"))

    # ─── قسم: الصور المصغرة ───
    keyboard.add(inline_button("╭─ 🖼️ الصور المصغرة ─╮", STYLE_PRIMARY, callback_data="noop"))
    keyboard.add(inline_button("🖼️ صورة افتراضية 🟡", STYLE_PRIMARY, callback_data="admin::set_default_thumb"),
                 inline_button("📸 تحديث يدوي 🔵", STYLE_PRIMARY, callback_data="admin::manual_thumb"))

    # ─── قسم: أدوات الصيانة ───
    keyboard.add(inline_button("╭─ 🛠️ أدوات الصيانة ─╮", STYLE_PRIMARY, callback_data="noop"))
    keyboard.add(inline_button("✨ إصلاح شامل للأرشيف 🟣", STYLE_PRIMARY, callback_data="admin::heal_archive"))
    keyboard.add(inline_button("📄 عرض المستندات 🔵", STYLE_PRIMARY, callback_data="admin::list_documents"),
                 inline_button("🔄 تحويل → فيديو 🟢", STYLE_SUCCESS, callback_data="admin::convert_all_docs"))

    # ─── قسم: البث والإحصائيات ───
    keyboard.add(inline_button("╭─ 📊 البث والإحصائيات ─╮", STYLE_PRIMARY, callback_data="noop"))
    keyboard.add(inline_button("📢 بث رسالة 🟠", STYLE_SUCCESS, callback_data="admin::broadcast"))
    keyboard.add(inline_button("📊 إحصائيات البوت 🟣", STYLE_PRIMARY, callback_data="admin::stats"),
                 inline_button("👤 عدد المشتركين 🔵", STYLE_PRIMARY, callback_data="admin::sub_count"))

    # ─── التطبيق المصغّر (Mini App): يظهر فقط مع APP_URL https ───
    if _miniapp_active():
        # InlineKeyboardButton المباشر: تيليجرام لا يقبل style مع web_app
        open_url = _miniapp_open_url()
        if open_url:
            keyboard.add(InlineKeyboardButton("📱 مكتبة الميني أب 🟣", web_app=WebAppInfo(url=open_url)),
                         row_width=1)
        keyboard.add(inline_button("⏱️ حد فتح المكتبة/ساعة لكل IP 🔵", STYLE_PRIMARY,
                                   callback_data="admin::set_miniapp_limit"), row_width=1)
        keyboard.add(inline_button(f"⏱️ الحد الحالي: {miniapp_hourly_limit_by_ip()} في الساعة 🟣",
                                   STYLE_PRIMARY, callback_data="noop"), row_width=1)
    return keyboard


ADMIN_PANEL_TEXT = "🎛️ أهلاً بك في لوحة تحكم الآدمن. اختر أحد الخيارات:"


def send_admin_panel(bot, chat_id):
    """إرسال لوحة الأدمن (وتحمل أزرار التطبيق المصغّر عند تفعيله)."""
    bot.send_message(chat_id, ADMIN_PANEL_TEXT, reply_markup=build_admin_panel())
    return True


def handle_set_miniapp_limit(bot, chat_id):
    """زر حد فتح المكتبة: يعيد اللوحة ويطلب الرقم التالي (0 = بلا حد)."""
    send_admin_panel(bot, chat_id)
    miniapp_limit_prompt(bot, chat_id)
    return True


def _open_library_for_ip(bot, admin_ids, message):
    """رسالة IP من الأدمن -> زر web_app للمكتبة بعنوانه."""
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⛔ هذا الأمر للإدارة فقط")
        return False
    ip_address = parse_admin_ip(message.text)
    if not ip_address:
        bot.send_message(message.chat.id, "❌ عنوان IP غير صالح (مثال: 203.0.113.7).")
        return False
    url = _miniapp_open_url(ip_address)
    if not url:
        bot.send_message(message.chat.id, "⚠️ المكتبة غير متاحة: تحقق من APP_URL.")
        return False
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton(f"📱 فتح المكتبة لـ {ip_address}",
                                      web_app=WebAppInfo(url=url)))
    bot.send_message(message.chat.id, "📱 افتح المكتبة من الزر أدناه:", reply_markup=keyboard)
    return True



# --- Handler Registration ---

def register(bot, admin_ids):
    def check_admin(func):
        @functools.wraps(func)
        def wrapper(message):
            # لوحة الأدمن خاصة فقط: لا أوامر إدارة من مجموعات/قنوات
            if getattr(getattr(message, 'chat', None), 'type', None) != 'private':
                bot.reply_to(message, "⛔ أوامر الإدارة متاحة في المحادثة الخاصة فقط")
                return
            if message.from_user.id in admin_ids:
                return func(message)
            else:
                bot.reply_to(message, "ليس لديك صلاحية الوصول إلى هذا الأمر.")
        return wrapper

    @bot.message_handler(commands=["admin"])
    @check_admin
    def admin_panel(message):
        send_admin_panel(bot, message.chat.id)

    @bot.message_handler(func=lambda message: message.text == MINIAPP_IP_BUTTON_TEXT)
    @check_admin
    def miniapp_open_by_ip(message):
        """بديل الأجهزة التي لا تفتح Web App: يطلب IP بحالة مخزّنة ثم يعرض زر الفتح."""
        begin_miniapp_ip(bot, message.from_user.id, admin_ids)

    @bot.message_handler(commands=["cancel"])
    @check_admin
    def cancel_step(message):
        cleared = clear_user_waiting_state(message.chat.id)
        if cleared is True:
            bot.send_message(message.chat.id, "✅ تم إلغاء العملية الحالية بنجاح.")
        else:
            bot.send_message(message.chat.id, "❌ تعذّر إلغاء العملية (خطأ في قاعدة البيانات). حاول مرة أخرى.")

    @bot.message_handler(
        content_types=["text", "photo", "video", "document", "audio", "voice",
                       "animation", "sticker"],
        func=lambda message: message.chat.type == "private",
    )
    @check_admin
    def admin_state_input(message):
        """رسائل الأدمن التالية لتدفق مخزّن (بث/تصنيف/قناة/نقل/حذف/إعدادات/صور مصغرة).

        التسجيل: handlers/__init__.register_all_handlers يسجّل admin_handlers قبل
        user_handlers، وTeleBot يمرّ على bot.message_handlers بترتيب الإضافة نفسه
        (ليس معكوساً) ويتوقف عند أول معالج مطابق لا يرجع ContinueHandling. أي أن هذا
        المعالج يصل قبل معالج النص العام في user_handlers. لذلك عندما لا تخصّ الرسالة
        أي تدفق جديد نرجع ContinueHandling كي تصل للعناوين/التعليقات/البحث، وعندما
        تخصّ تدفقاً نعالجها ونتوقف (لا تمرير مزدوج).
        """
        handled = bool(handle_broadcast_message(bot, admin_ids, message))
        if not handled:
            handled = bool(handle_flow_message(bot, admin_ids, message))
        if not handled:
            try:
                from . import thumbnails as thumbnail_flows
            except ImportError:
                thumbnail_flows = None
            if thumbnail_flows is not None:
                handled = bool(thumbnail_flows.handle_message(bot, admin_ids, message))
        if handled:
            return None
        return ContinueHandling()
