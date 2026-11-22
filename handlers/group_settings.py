# handlers/group_settings.py
"""إعدادات سلوك رسائل الجروب المحفوظة في جدول ``bot_settings``.

الافتراضي «مفعّل» حتى يبقى السلوك الحالي كما هو لمن لم يغيّر الإعداد.
قراءة الإعداد لا تُسقط /start: أي فشل في قاعدة البيانات يعيد الافتراضي.
"""

import logging

from db_manager import get_bot_setting, set_bot_setting

logger = logging.getLogger(__name__)

GROUP_MENU_AUTODELETE_KEY = 'group_menu_autodelete_enabled'
DEFAULT_GROUP_MENU_AUTODELETE = True

_TRUE_VALUES = {'1', 'true', 'yes', 'on', 'enabled'}
_FALSE_VALUES = {'0', 'false', 'no', 'off', 'disabled'}


def _as_bool(value, default):
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return default


def group_menu_autodelete_enabled():
    """هل تُحذف رسائل قائمة الجروب بعد ساعة ثم تُعاد لوحة الرد؟ (افتراضياً نعم)."""
    default = 'true' if DEFAULT_GROUP_MENU_AUTODELETE else 'false'
    try:
        value = get_bot_setting(GROUP_MENU_AUTODELETE_KEY, default)
    except Exception as exc:  # noqa: BLE001 - الإعداد لا يعطّل /start
        logger.warning(
            "Group menu autodelete setting unavailable: %s", type(exc).__name__)
        return DEFAULT_GROUP_MENU_AUTODELETE
    return _as_bool(value, DEFAULT_GROUP_MENU_AUTODELETE)


def set_group_menu_autodelete(enabled):
    """حفظ الإعداد؛ True فقط عند تأكيد قاعدة البيانات."""
    try:
        return set_bot_setting(
            GROUP_MENU_AUTODELETE_KEY, 'true' if enabled else 'false') is True
    except Exception as exc:  # noqa: BLE001 - نبلّغ الفشل بدل إسقاط الرد
        logger.error(
            "Group menu autodelete save failed: %s", type(exc).__name__)
        return False
