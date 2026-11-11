#!/usr/bin/env python3
# ==============================================================================
# ملف: commands.py
# الوصف: قائمة الأوامر الأصلية في تيليجرام (BotCommand) ونشرها لكل المستخدمين
#        وللأدمن فقط لكل أدمن على حدة، دون كشف أي أسرار في السجلات.
# ==============================================================================

import logging

from telebot import types

logger = logging.getLogger(__name__)

# حدود تيليجرام: اسم الأمر من 1 إلى 32 حرفاً بدون "/" وحروف لاتينية صغيرة فقط،
# والوصف حتى 64 حرفاً. الأسماء كلها مسجّلة فعلياً فلا تُقص عمداً.
MAX_COMMAND_LENGTH = 32
MAX_DESCRIPTION_LENGTH = 64

# أوامر الأعضاء - كل أمر هنا له معالج مسجّل فعلياً:
#   user_handlers: start, search, library, my_comments, myid, cancel
#   group_handlers: search_help
# (المفضلة/السجل/الشائع متاحة كأزرار لوحة مفاتيح، لا كأوامر، فلا تُسجّل هنا)
# library يفتح التطبيق المصغّر، ويحذفه user_handlers.register تلقائياً إن غاب معالجه.
MEMBER_COMMANDS = [
    ("start", "بدء البوت والقائمة الرئيسية 🎬"),
    ("search", "البحث عن فيديو 🔍"),
    ("library", "مكتبة الفيديوهات المصغّرة 📱"),
    ("my_comments", "محادثاتي وردود الإدارة 💬"),
    ("myid", "عرض معرّف حسابي 🆔"),
    ("search_help", "طريقة البحث في المجموعات 💡"),
    ("cancel", "إلغاء العملية الحالية ↩️"),
]

# أوامر الأدمن - إضافة إلى أوامر الأعضاء (كلها مسجّلة في user_handlers/admin_handlers:
# comments, comments_stats, delete_old_comments, delete_all_comments,
# delete_user_comments, title, admin)
ADMIN_ONLY_COMMANDS = [
    ("admin", "لوحة تحكم الآدمن 🎛️"),
    ("comments", "عرض جميع التعليقات 📬"),
    ("comments_stats", "إحصائيات التعليقات 📊"),
    ("delete_old_comments", "حذف التعليقات القديمة 🧹"),
    ("delete_all_comments", "حذف جميع التعليقات 🗑️"),
    ("delete_user_comments", "حذف تعليقات عضو 🚫"),
    ("title", "تعديل عنوان فيديو ✏️"),
]

ADMIN_COMMANDS = MEMBER_COMMANDS + ADMIN_ONLY_COMMANDS


def _normalize_command(command):
    return str(command or "").strip().lstrip("/").lower()


def build_commands(pairs):
    """تحويل أزواج (الأمر، الوصف) إلى list[BotCommand] صالحة لتيليجرام."""
    commands = []
    for command, description in pairs:
        name = _normalize_command(command)
        if not name:
            continue
        commands.append(types.BotCommand(
            name[:MAX_COMMAND_LENGTH],
            str(description or "")[:MAX_DESCRIPTION_LENGTH],
        ))
    return commands


def build_member_commands():
    """أوامر عامة لكل المستخدمين (BotCommandScopeDefault)."""
    return build_commands(MEMBER_COMMANDS)


def build_admin_commands():
    """أوامر الأدمن في المحادثة الخاصة: قائمة الأعضاء + أوامر الإدارة."""
    return build_commands(ADMIN_COMMANDS)


def _admin_ids(admin_ids):
    result = []
    for value in admin_ids or []:
        try:
            admin_id = int(value)
        except (TypeError, ValueError):
            continue
        if admin_id not in result:
            result.append(admin_id)
    return result


def _describe_failure(exc):
    """وصف آمن للخطأ للتسجيل: النوع فقط لتجنّب أي نص قد يحوي الرابط/التوكن."""
    return type(exc).__name__


def publish_command_menu(bot, admin_ids):
    """
    نشر قائمة الأوامر: عامة لكل المستخدمين، وخاصة لكل أدمن، وزر القائمة = الأوامر.

    لا ترمي استثناءات أبداً: فشل نطاق واحد (مثلاً بوت محظور أو محادثة لم تُبدأ)
    يُسجَّل كتحذير ويظهر False في التقرير فقط، ولا يُسجَّل نص الخطأ الخام.
    Report: {"default": bool, "admins": {chat_id: bool}, "menu_button": bool}
    """
    report = {"default": False, "admins": {}, "menu_button": False}

    try:
        report["default"] = bool(bot.set_my_commands(
            build_member_commands(), scope=types.BotCommandScopeDefault()))
    except Exception as exc:
        logger.warning("⚠️ Could not publish default command menu: %s", _describe_failure(exc))

    admin_commands = build_admin_commands()
    for admin_id in _admin_ids(admin_ids):
        try:
            ok = bool(bot.set_my_commands(
                admin_commands, scope=types.BotCommandScopeChat(chat_id=admin_id)))
        except Exception as exc:
            logger.warning("⚠️ Could not publish command menu for admin %s: %s",
                           admin_id, _describe_failure(exc))
            ok = False
        report["admins"][admin_id] = ok

    try:
        report["menu_button"] = bool(bot.set_chat_menu_button(
            menu_button=types.MenuButtonCommands()))
    except Exception as exc:
        logger.warning("⚠️ Could not set chat menu button: %s", _describe_failure(exc))

    return report
