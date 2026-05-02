"""Centralized helpers for Telegram button colors/styles.

Telegram Bot API now supports the optional ``style`` field on
``InlineKeyboardButton`` and ``KeyboardButton`` with these values:

- ``primary``: blue
- ``success``: green
- ``danger``: red

These helpers keep style usage consistent across the bot and include a safe
fallback for environments running an older pyTelegramBotAPI version that does
not yet accept the ``style`` keyword.
"""

from telebot.types import InlineKeyboardButton, KeyboardButton


STYLE_PRIMARY = "primary"
STYLE_SUCCESS = "success"
STYLE_DANGER = "danger"

VALID_STYLES = {STYLE_PRIMARY, STYLE_SUCCESS, STYLE_DANGER}


def _normalize_style(style):
    """Return a valid Telegram button style or ``None``."""
    return style if style in VALID_STYLES else None


def inline_button(text, style=None, **kwargs):
    """Create an InlineKeyboardButton with optional Telegram color style.

    If the installed pyTelegramBotAPI version does not support ``style`` yet,
    the button is created without style so the bot remains operational.
    """
    normalized_style = _normalize_style(style)
    if normalized_style:
        try:
            return InlineKeyboardButton(text, style=normalized_style, **kwargs)
        except TypeError:
            pass
    return InlineKeyboardButton(text, **kwargs)


def keyboard_button(text, style=None, **kwargs):
    """Create a Reply KeyboardButton with optional Telegram color style."""
    normalized_style = _normalize_style(style)
    if normalized_style:
        try:
            return KeyboardButton(text, style=normalized_style, **kwargs)
        except TypeError:
            pass
    return KeyboardButton(text, **kwargs)
