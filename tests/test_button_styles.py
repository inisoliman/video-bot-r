import support  # noqa: F401

import unittest

from handlers import button_styles
from handlers.button_styles import (
    STYLE_DANGER,
    STYLE_PRIMARY,
    STYLE_SUCCESS,
    inline_button,
    keyboard_button,
)


class _FakeBot:
    """Records send_message calls without touching the Telegram network."""

    def __init__(self):
        self.calls = []

    def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.calls.append((chat_id, text, reply_markup))


class InlineButtonStyleTest(unittest.TestCase):
    def test_primary_style_is_serialized(self):
        button = inline_button("زر", style=STYLE_PRIMARY, callback_data="menu::home")
        self.assertEqual(
            button.to_dict(),
            {"text": "زر", "callback_data": "menu::home", "style": "primary"},
        )

    def test_success_style_is_serialized(self):
        button = inline_button("زر", style=STYLE_SUCCESS, callback_data="menu::ok")
        self.assertEqual(button.to_dict()["style"], "success")
        self.assertEqual(button.to_dict()["callback_data"], "menu::ok")

    def test_danger_style_is_serialized(self):
        button = inline_button("زر", style=STYLE_DANGER, callback_data="menu::delete")
        self.assertEqual(button.to_dict()["style"], "danger")
        self.assertEqual(button.to_dict()["callback_data"], "menu::delete")

    def test_invalid_style_is_omitted(self):
        for bad in ("Secondary", "", None, "red", 123):
            with self.subTest(style=bad):
                button = inline_button("زر", style=bad, callback_data="menu::x")
                self.assertNotIn("style", button.to_dict())
                self.assertNotIn("style", button.to_json())

    def test_no_style_key_for_unstyled_button(self):
        button = inline_button("زر", callback_data="menu::x")
        self.assertEqual(button.to_dict(), {"text": "زر", "callback_data": "menu::x"})

    def test_url_button_keeps_url_and_style(self):
        button = inline_button("افتح", style=STYLE_PRIMARY, url="https://example.com/v")
        data = button.to_dict()
        self.assertEqual(data["url"], "https://example.com/v")
        self.assertEqual(data["style"], "primary")
        self.assertNotIn("callback_data", data)

    def test_callback_data_and_url_are_separate_fields(self):
        callback = inline_button("cb", style=STYLE_SUCCESS, callback_data="a::b")
        url = inline_button("u", style=STYLE_SUCCESS, url="https://example.com")
        self.assertEqual(callback.to_dict()["callback_data"], "a::b")
        self.assertNotIn("url", callback.to_dict())
        self.assertEqual(url.to_dict()["url"], "https://example.com")
        self.assertNotIn("callback_data", url.to_dict())

    def test_valid_styles_are_the_three_api_values(self):
        self.assertEqual(button_styles.VALID_STYLES, {"primary", "success", "danger"})

    def test_markup_serializes_styled_buttons(self):
        from telebot.types import InlineKeyboardMarkup

        markup = InlineKeyboardMarkup()
        markup.add(inline_button("a", style=STYLE_PRIMARY, callback_data="menu::a"),
                   inline_button("b", style=STYLE_DANGER, callback_data="menu::b"))
        sent = markup.to_dict()["inline_keyboard"]
        self.assertEqual(sent[0][0]["style"], "primary")
        self.assertEqual(sent[0][1]["style"], "danger")
        self.assertEqual(sent[0][1]["callback_data"], "menu::b")

    def test_send_message_payload_carries_style(self):
        bot = _FakeBot()
        bot.send_message(1, "hi", reply_markup=None)
        from telebot.types import InlineKeyboardMarkup

        markup = InlineKeyboardMarkup()
        markup.add(inline_button("زر", style=STYLE_SUCCESS, callback_data="menu::s"))
        bot.send_message(1, "hi", reply_markup=markup)
        payload = bot.calls[-1][2].to_dict()
        self.assertEqual(payload["inline_keyboard"][0][0]["style"], "success")


class ReplyKeyboardButtonStyleTest(unittest.TestCase):
    def test_keyboard_button_style_is_serialized(self):
        button = keyboard_button("ابدأ", style=STYLE_PRIMARY)
        self.assertEqual(button.to_dict(), {"text": "ابدأ", "style": "primary"})

    def test_keyboard_button_invalid_style_is_omitted(self):
        self.assertNotIn("style", keyboard_button("ابدأ", style="unknown").to_dict())


class UpgradeTypeSupportTest(unittest.TestCase):
    """Types the bot relies on after the 4.37.1 upgrade (skim-proof regressions)."""

    def test_webapp_info_to_dict(self):
        from telebot.types import WebAppInfo

        self.assertEqual(WebAppInfo("https://example.com/app").to_dict(),
                         {"url": "https://example.com/app"})

    def test_webapp_info_inside_inline_button(self):
        from telebot.types import WebAppInfo

        button = inline_button("افتح التطبيق", style=STYLE_PRIMARY,
                               web_app=WebAppInfo("https://example.com/app"))
        self.assertEqual(button.to_dict()["web_app"], {"url": "https://example.com/app"})
        self.assertEqual(button.to_dict()["style"], "primary")

    def test_menu_button_web_app(self):
        from telebot.types import MenuButtonWebApp, WebAppInfo

        button = MenuButtonWebApp(text="تطبيق", web_app=WebAppInfo("https://example.com/app"))
        self.assertEqual(button.to_dict(), {
            "type": "web_app",
            "text": "تطبيق",
            "web_app": {"url": "https://example.com/app"},
        })

    def test_bot_command_and_scopes(self):
        from telebot.types import (BotCommand, BotCommandScopeAllPrivateChats,
                                   BotCommandScopeChat, BotCommandScopeDefault)

        self.assertEqual(BotCommand("start", "بدء").to_dict(),
                         {"command": "start", "description": "بدء"})
        self.assertEqual(BotCommandScopeDefault().to_json(), '{"type": "default"}')
        self.assertEqual(BotCommandScopeAllPrivateChats().to_json(),
                         '{"type": "all_private_chats"}')
        self.assertEqual(BotCommandScopeChat(-100123).to_json(),
                         '{"type": "chat", "chat_id": -100123}')

    def test_bot_exposes_menu_and_webapp_methods(self):
        import telebot

        for name in ("set_chat_menu_button", "set_my_commands", "answer_web_app_query"):
            self.assertTrue(callable(getattr(telebot.TeleBot, name, None)), name)

    def test_inline_query_result_serialization_used_by_bot(self):
        from handlers.inline_handlers import create_inline_result

        video = {"id": 7, "file_id": "BAACAgIAAxkBAAI", "caption": "cap", "content_type": "VIDEO"}
        result = create_inline_result(video, use_document=False)
        self.assertIsNotNone(result)
        payload = result.to_json()
        self.assertIn('"video_file_id": "BAACAgIAAxkBAAI"', payload)
        self.assertIn('"parse_mode": "HTML"', payload)
        doc = create_inline_result(video, use_document=True)
        self.assertIsNotNone(doc)
        self.assertIn('"document_file_id": "BAACAgIAAxkBAAI"', doc.to_json())


if __name__ == "__main__":
    unittest.main()
