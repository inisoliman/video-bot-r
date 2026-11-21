"""أزرار القائمة الرئيسية في الجروب: Inline فقط، ولا زر web_app خارج الخاص.

سببه: تيليجرام يرفض زر ``web_app`` خارج المحادثة الخاصة (400: web App buttons can
be used in private chats only) فيُسقط الرسالة كاملة، وهو ما منع أزرار الجروب من الظهور.
هنا: ``main_menu(chat_type=...)``، ``inline_main_menu()``، ومعالج callback القائمة.
"""

import json
import os
import sys
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode
    from . import support
except ImportError:  # top-level mode
    import support

import db_manager  # noqa: E402
import handlers as handlers_package  # noqa: E402
import telebot  # noqa: E402
from handlers import callback_handlers, helpers, user_handlers  # noqa: E402
from miniapp import MINIAPP_KEYBOARD_TEXT  # noqa: E402
from telebot import types  # noqa: E402

APP_URL = "https://library.example.com"
BOT_USERNAME = "orsozoxmoviebot"
GROUP_CHAT_ID = -1009876543210
MENU_TEXTS = ("🎬 عرض كل الفيديوهات 🟢", "🔥 الفيديوهات الشائعة 🔴", "⭐ المفضلة 🟡",
              "📺 سجل المشاهدة 🔵", "🍿 اقترح لي فيلم 🟣", "🔍 بحث 🟠")
BASE_ENV = {
    "BOT_TOKEN": "123456:SECRET-BOT-TOKEN",
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
    "CHANNEL_ID": "-1001234567890",
    "ADMIN_IDS": str(support.ADMIN_ID),
    "APP_URL": APP_URL,
}


def keyboard_payload(markup):
    return json.loads(markup.to_json())


class MenuMarkupTest(support.OfflineTestCase):
    def test_private_menu_keeps_web_app_miniapp_button(self):
        with support.env(**BASE_ENV):
            payload = keyboard_payload(helpers.main_menu(BOT_USERNAME))
        texts = [b["text"] for row in payload["keyboard"] for b in row]
        self.assertIn(MINIAPP_KEYBOARD_TEXT, texts)
        for text in MENU_TEXTS:
            self.assertIn(text, texts)

    def test_group_menu_has_no_web_app_button(self):
        for chat_type in ("group", "supergroup"):
            with self.subTest(chat_type=chat_type):
                with support.env(**BASE_ENV):
                    payload = keyboard_payload(helpers.main_menu(BOT_USERNAME,
                                                                 chat_type=chat_type))
                texts = [b["text"] for row in payload["keyboard"] for b in row]
                # زر Mini App يظهر في الجروبات كزر نصي عادي (بدون web_app)
                self.assertIn(MINIAPP_KEYBOARD_TEXT, texts)
                for text in MENU_TEXTS:
                    self.assertIn(text, texts)
                for row in payload["keyboard"]:
                    for button in row:
                        self.assertNotIn("web_app", button)

    def test_inline_menu_targets_the_same_texts(self):
        payload = keyboard_payload(helpers.inline_main_menu())
        buttons = [b for row in payload["inline_keyboard"] for b in row]
        self.assertEqual(len(buttons), len(MENU_TEXTS))
        expected = {text: key for text, key in zip(
            MENU_TEXTS, ("videos", "popular", "favorites", "history", "random", "search"))}
        for button in buttons:
            self.assertIn(button["text"], expected)
            self.assertEqual(button["callback_data"], f"menu::{expected[button['text']]}")
            self.assertNotIn("web_app", button)


class _RecordingBot:
    def __init__(self):
        self.calls = []
        self.handlers = []
        self._next = 100

    def callback_query_handler(self, *args, **kwargs):
        def decorator(func):
            self.handlers.append(func)
            return func
        return decorator

    def send_message(self, chat_id, text=None, **kwargs):
        self._next += 1
        self.calls.append(("send_message", chat_id, text, kwargs.get("reply_markup")))
        return support.make_message(text="ok", message_id=self._next, chat_id=chat_id)

    def answer_callback_query(self, *args, **kwargs):
        self.calls.append(("answer",) + args)

    def delete_message(self, *args):
        return True

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: True


class MenuCallbackTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.bot = _RecordingBot()
        patcher = mock.patch.object(helpers, "check_subscription",
                                    lambda bot, user_id: (True, []))
        patcher.start()
        self.addCleanup(patcher.stop)
        db_patch = mock.patch.object(db_manager, "add_bot_user", lambda *a, **k: True)
        db_patch.start()
        self.addCleanup(db_patch.stop)
        with support.env(**BASE_ENV):
            callback_handlers.register(self.bot, [support.ADMIN_ID])
        self.bot.calls.clear()

    def press(self, data, chat_type="group"):
        call = support.make_call(data, user_id=support.MEMBER_ID, chat_type=chat_type,
                                 chat_id=GROUP_CHAT_ID if chat_type != "private"
                                 else support.MEMBER_ID)
        with support.env(**BASE_ENV):
            self.bot.handlers[0](call)
        return self.bot.calls

    def test_group_menu_press_replays_the_button_text(self):
        calls = self.press("menu::search")
        sends = [c for c in calls if c[0] == "send_message"]
        self.assertEqual(len(sends), 1)
        self.assertIn("كلمة البحث", sends[0][2])
        self.assertIsNone(sends[0][3])

    def test_group_menu_press_runs_the_action_directly(self):
        calls = self.press("menu::popular")
        sends = [c for c in calls if c[0] == "send_message"]
        self.assertEqual(len(sends), 1)
        markup = sends[0][3]
        self.assertIsInstance(markup, types.InlineKeyboardMarkup)
        payload = keyboard_payload(markup)
        buttons = [b for row in payload["inline_keyboard"] for b in row]
        self.assertEqual([b["callback_data"] for b in buttons],
                         ["popular::most_viewed", "popular::highest_rated"])

    def test_back_to_main_in_group_uses_inline_menu(self):
        calls = self.press("back_to_main")
        sends = [c for c in calls if c[0] == "send_message"]
        self.assertEqual(len(sends), 1)
        markup = sends[0][3]
        self.assertIsInstance(markup, types.InlineKeyboardMarkup)
        payload = keyboard_payload(markup)
        buttons = [b for row in payload["inline_keyboard"] for b in row]
        self.assertEqual(len(buttons), len(MENU_TEXTS))
        for button in buttons:
            self.assertNotIn("web_app", button)

    def test_back_to_main_in_private_keeps_reply_keyboard(self):
        calls = self.press("back_to_main", chat_type="private")
        sends = [c for c in calls if c[0] == "send_message"]
        self.assertEqual(len(sends), 1)
        markup = sends[0][3]
        self.assertIsInstance(markup, types.ReplyKeyboardMarkup)
        payload = keyboard_payload(markup)
        texts = [b["text"] for row in payload["keyboard"] for b in row]
        self.assertIn(MINIAPP_KEYBOARD_TEXT, texts)


class GroupStartButtonsTest(support.OfflineTestCase):
    def test_group_start_sends_inline_menu_without_web_app(self):
        bot = _RecordingBot()
        captured = []

        def message_handler(*args, **kwargs):
            def decorator(func):
                captured.append(func)
                return func
            return decorator

        bot.message_handler = message_handler
        bot.reply_to = lambda message, text=None, **kwargs: bot.calls.append(
            ("send_message", message.chat.id, text, kwargs.get("reply_markup")))
        bot.get_me = lambda: support.make_user(1, username=BOT_USERNAME)
        bot.get_chat = lambda chat_id: support.SimpleNamespace(
            permissions=support.SimpleNamespace(can_add_bots=True))
        db_patch = mock.patch.object(db_manager, "add_bot_user", lambda *a, **k: True)
        db_patch.start()
        self.addCleanup(db_patch.stop)
        with support.env(**BASE_ENV):
            user_handlers.register(bot, "-1001234567890", [support.ADMIN_ID])
        bot.calls.clear()
        with support.env(**BASE_ENV):
            captured[0](support.make_message(text="/start", chat_type="group",
                                             chat_id=GROUP_CHAT_ID))
        sends = [c for c in bot.calls if c[0] == "send_message"]
        self.assertGreaterEqual(len(sends), 2)
        for _kind, _chat, _text, markup in sends:
            if isinstance(markup, types.ReplyKeyboardMarkup):
                payload = keyboard_payload(markup)
                for row in payload["keyboard"]:
                    for button in row:
                        self.assertNotIn("web_app", button)
        markup = sends[1][3]
        self.assertIsInstance(markup, types.InlineKeyboardMarkup)
        payload = keyboard_payload(markup)
        self.assertEqual(len(payload["inline_keyboard"]), 3)
