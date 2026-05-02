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


class StyledInlineKeyboardButton(InlineKeyboardButton):
    """Inline button that always serializes Telegram's ``style`` field.

    Some pyTelegramBotAPI releases may not yet expose ``style`` in the
    constructor or in ``to_dict``. This subclass keeps the bot compatible with
    those releases while still sending the official Bot API field to Telegram.
    """

    def __init__(self, text, style=None, **kwargs):
        self._button_style = _normalize_style(style)
        try:
            super().__init__(text, style=self._button_style, **kwargs)
        except TypeError:
            super().__init__(text, **kwargs)

    def to_dict(self):
        data = super().to_dict()
        if self._button_style:
            data["style"] = self._button_style
        return data


class StyledKeyboardButton(KeyboardButton):
    """Reply keyboard button that always serializes Telegram's ``style`` field."""

    def __init__(self, text, style=None, **kwargs):
        self._button_style = _normalize_style(style)
        try:
            super().__init__(text, style=self._button_style, **kwargs)
        except TypeError:
            super().__init__(text, **kwargs)

    def to_dict(self):
        data = super().to_dict()
        if self._button_style:
            data["style"] = self._button_style
        return data


def inline_button(text, style=None, **kwargs):
    """Create an InlineKeyboardButton with optional Telegram color style.

    The returned object always includes ``style`` in its serialized JSON when a
    valid style is provided, even on pyTelegramBotAPI versions that don't yet
    expose the field directly.
    """
    return StyledInlineKeyboardButton(text, style=style, **kwargs)


def keyboard_button(text, style=None, **kwargs):
    """Create a Reply KeyboardButton with optional Telegram color style."""
    return StyledKeyboardButton(text, style=style, **kwargs)
