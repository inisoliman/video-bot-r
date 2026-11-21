"""سلوك /start: لا يمرّ صامتاً في الجروبات، والخاص يبقى كما هو.

في الجروب يجب ألا تُرسَل لوحة الرد (main_menu) ولا قائمة الاشتراك (أسماء القنوات)
لكل الأعضاء؛ يُرسَل شرح مختصر وزر يفتح الخاص، وكل إرسال مستقل. أي فشل في قاعدة
البيانات أو فحص الاشتراك أو ``get_me`` لا يمنع رسالة الرد الظاهرة.

كل شيء أوفلاين: ``support`` يحجب HTTP وقاعدة البيانات.
"""

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode
    from . import support
except ImportError:  # top-level mode
    import support

import db_manager  # noqa: E402
import telebot  # noqa: E402
from handlers import helpers, user_handlers  # noqa: E402
from telebot import types  # noqa: E402

BOT_USERNAME = "orsozoxmoviebot"
GROUP_USERNAME = "orsozox_group"
GROUP_CHAT_ID = -1009876543210
APP_URL = "https://library.example.com"
BASE_ENV = {
    "BOT_TOKEN": "123456:SECRET-BOT-TOKEN",
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
    "CHANNEL_ID": "-1001234567890",
    "ADMIN_IDS": str(support.ADMIN_ID),
    "APP_URL": APP_URL,
}


class RecordingBot:
    """بوت بلا شبكة يسجل كل نداء؛ يخزّن كل معالج مسجّل بالترتيب."""

    def __init__(self, username=BOT_USERNAME, me_error=None, chat_permissions=False):
        self.username = username
        self.me_error = me_error
        self.chat_permissions = chat_permissions
        self.calls = []
        self.handlers = []
        self._next = 100

    def message_handler(self, *args, **kwargs):
        def decorator(func):
            self.handlers.append(func)
            return func
        return decorator

    def get_me(self):
        if self.me_error is not None:
            raise self.me_error
        return support.make_user(777, username=self.username)

    def get_chat(self, chat_id):
        return support.SimpleNamespace(
            permissions=support.SimpleNamespace(can_add_bots=self.chat_permissions))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def recorder(*args, **kwargs):
            self._next += 1
            self.calls.append((name, args, kwargs))
            return support.make_message(text="ok", message_id=self._next)
        return recorder

    def replies(self):
        return [(args[1], kwargs) for name, args, kwargs in self.calls
                if name == "reply_to"]


def buttons(markup):
    return [button for row in markup.keyboard for button in row]


class StartTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self._add = db_manager.add_bot_user
        self.addCleanup(setattr, db_manager, "add_bot_user", self._add)
        db_manager.add_bot_user = lambda *args, **kwargs: True
        self._sub = helpers.check_subscription
        self.addCleanup(setattr, helpers, "check_subscription", self._sub)
        helpers.check_subscription = lambda bot, user_id: (True, [])

    def registered_bot(self, bot):
        """يسجّل user_handlers على كائن الاختبار ضمن بيئة كاملة."""
        with support.env(**BASE_ENV):
            user_handlers.register(bot, "-1001234567890", [support.ADMIN_ID])
        return bot

    def press_start(self, bot, chat_type="group", user_id=support.MEMBER_ID,
                    chat_username=None, env=None, chat_id=None, times=1):
        message = support.make_message(
            text="/start", user_id=user_id, chat_type=chat_type,
            chat_id=chat_id if chat_id is not None else (user_id if chat_type == "private" else GROUP_CHAT_ID))
        message.chat.username = chat_username
        with support.env(**(env or BASE_ENV)):
            for _ in range(times):
                bot.handlers[0](message)  # أول معالج مسجّل هو /start
        return bot.replies()

    def test_repeated_group_start_keeps_answering(self):
        bot = self.registered_bot(RecordingBot(chat_permissions=True))
        replies = self.press_start(bot, chat_username=GROUP_USERNAME, times=2)
        self.assertEqual(len(replies), 6)
        self.assertEqual(replies[0][0], replies[3][0])

    # --- الجروب ------------------------------------------------------------

    def test_group_start_replies_with_guide_inline_menu_and_private_button(self):
        bot = self.registered_bot(RecordingBot(chat_permissions=True))
        replies = self.press_start(bot, chat_username=GROUP_USERNAME)
        self.assertEqual(len(replies), 3)
        text, kwargs = replies[0]
        # الرسالة الأولى تحتوي على أزرار Reply Keyboard للجروب (بدون web_app)
        self.assertIn("reply_markup", kwargs)
        reply_keyboard = kwargs["reply_markup"]
        self.assertIsInstance(reply_keyboard, types.ReplyKeyboardMarkup)
        serialized_reply = json.loads(reply_keyboard.to_json())
        for row in serialized_reply["keyboard"]:
            for button in row:
                self.assertNotIn("web_app", button, "group reply keyboard must not have web_app buttons")
        self.assertIn("/library", text)
        # أزرار القائمة Inline تعمل داخل الجروب (للعملاء التي لا تدعم reply keyboard)
        menu = replies[1][1]["reply_markup"]
        self.assertIsInstance(menu, types.InlineKeyboardMarkup)
        serialized = json.loads(menu.to_json())
        self.assertEqual(len(serialized["inline_keyboard"]), 3)
        for row in serialized["inline_keyboard"]:
            for item in row:
                self.assertNotIn("web_app", item)
        button = buttons(replies[2][1]["reply_markup"])
        self.assertEqual(len(button), 1)
        self.assertIsInstance(button[0], types.InlineKeyboardButton)
        self.assertEqual(button[0].url,
                         f"https://t.me/{BOT_USERNAME}?startgroup={GROUP_USERNAME}")
        self.assertIsNone(button[0].web_app)

    def test_group_start_button_is_plain_link_without_permission(self):
        bot = self.registered_bot(RecordingBot(chat_permissions=False))
        replies = self.press_start(bot, chat_username=GROUP_USERNAME)
        self.assertEqual(buttons(replies[2][1]["reply_markup"])[0].url,
                         f"https://t.me/{BOT_USERNAME}")

    def test_group_start_survives_database_failure(self):
        bot = self.registered_bot(RecordingBot(chat_permissions=True))

        def boom(*args, **kwargs):
            raise RuntimeError("db down")

        db_manager.add_bot_user = boom
        replies = self.press_start(bot, chat_username=GROUP_USERNAME)
        self.assertEqual(len(replies), 3)

    def test_group_start_never_checks_subscription(self):
        called = []
        helpers.check_subscription = lambda bot, user_id: called.append(user_id) or (False, [])
        bot = self.registered_bot(RecordingBot(chat_permissions=True))
        replies = self.press_start(bot, chat_username=GROUP_USERNAME)
        self.assertEqual(called, [])
        self.assertEqual(len(replies), 3)
        self.assertNotIn("قناة", " ".join(text for text, _kwargs in replies))

    def test_channel_start_sends_the_same_guide(self):
        bot = self.registered_bot(RecordingBot(chat_permissions=True))
        replies = self.press_start(bot, chat_type="channel")
        self.assertGreaterEqual(len(replies), 1)
        self.assertIn("مكتبة", replies[0][0])

    def test_start_without_from_user_is_ignored(self):
        bot = self.registered_bot(RecordingBot())
        message = support.make_message(text="/start", chat_type="group")
        message.from_user = None
        with support.env(**BASE_ENV):
            bot.handlers[0](message)
        self.assertEqual(bot.replies(), [])

    def test_parse_error_falls_back_to_plain_text(self):
        bot = self.registered_bot(RecordingBot(chat_permissions=True))

        def reply_to(message, text, **kwargs):
            bot.calls.append(("reply_to", (message, text), kwargs))
            if kwargs.get("parse_mode"):
                raise telebot.apihelper.ApiTelegramException(
                    "sendMessage", {},
                    {"error_code": 400,
                     "description": "Bad Request: can't parse entities"})
            return None

        bot.reply_to = reply_to
        self.press_start(bot, chat_username=GROUP_USERNAME)
        plain = [kwargs for _message, _text, kwargs in bot.calls
                 if "parse_mode" not in kwargs]
        html = [kwargs for _message, _text, kwargs in bot.calls
                if kwargs.get("parse_mode") == "HTML"]
        self.assertEqual(len(html), 1)
        self.assertGreaterEqual(len(plain), 1,
                         "the guide must be re-sent as plain text after a parse error")

    # --- الخاص يبقى كما هو -------------------------------------------------

    def test_private_start_keeps_welcome_and_menu(self):
        bot = self.registered_bot(RecordingBot())
        replies = self.press_start(bot, chat_type="private")
        self.assertEqual(len(replies), 1)
        text, kwargs = replies[0]
        self.assertIn("أهلاً", text)
        self.assertIsInstance(kwargs["reply_markup"], types.ReplyKeyboardMarkup)

    def test_private_start_survives_subscription_failure(self):
        def boom(bot, user_id):
            raise RuntimeError("getChatMember down")

        helpers.check_subscription = boom
        bot = self.registered_bot(RecordingBot())
        replies = self.press_start(bot, chat_type="private")
        self.assertEqual(len(replies), 1)
        self.assertIn("أهلاً", replies[0][0])
