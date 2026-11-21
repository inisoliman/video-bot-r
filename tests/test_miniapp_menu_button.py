"""زر لوحة المفاتيح «مكتبة الميني أب»: الفتح الأصلي في الخاص ورابط الجروب.

سبب الخطأ المُصلَح هنا: في الجروب كان الضغط على الزر يرسل نصاً يعالجه
``user_handlers.handle_miniapp_button`` بردّ يحمل زر ``web_app``، والتيليجرام يرفض
أزرار ``web_app`` خارج المحادثة الخاصة (BUTTON_TYPE_INVALID) فلا يظهر أي رد.

المسار الآن: زر لوحة المفاتيح يستخدم الحقل الرسمي ``web_app`` (يفتح المكتبة في
العملاء الداعمة)، والعملاء التي ترسل النص بدلاً من ذلك تحصل في الجروب على رابط
``https://t.me/<bot>?startapp=library`` أو نص إرشادي — دائماً بردّ واحد.

كل شيء أوفلاين: ``support`` يحجب HTTP وقاعدة البيانات، والبوت كائن يسجل الاستدعاءات.
"""

import json
import os
import sys
from contextlib import nullcontext
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode
    from . import support
except ImportError:  # top-level mode
    import support

import db_manager  # noqa: E402
from handlers import button_styles, helpers, user_handlers  # noqa: E402
from miniapp import MINIAPP_KEYBOARD_TEXT  # noqa: E402
from telebot import types  # noqa: E402

APP_URL = "https://library.example.com"
BOT_USERNAME = "video_library_bot"
GROUP_CHAT_ID = -1009876543210
GROUP_LINK = f"https://t.me/{BOT_USERNAME}?startapp=library"
BASE_ENV = {
    "BOT_TOKEN": "123456:SECRET-BOT-TOKEN",
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
    "CHANNEL_ID": "-1001234567890",
    "ADMIN_IDS": str(support.ADMIN_ID),
    "APP_URL": APP_URL,
}


class ScopedEnv:
    """ضبط متغير لا يشمله ``support.env`` (اسم البوت) ثم إرجاعه كما كان."""

    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __enter__(self):
        self.saved = os.environ.get(self.name)
        if self.value is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.value
        return self

    def __exit__(self, *exc_info):
        if self.saved is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.saved
        return False


class RecordingBot:
    """بوت بلا شبكة: يسجل كل نداء ويعيد معرّف رسالة وهمياً."""

    def __init__(self, username=BOT_USERNAME):
        self.username = username
        self.calls = []
        self.handlers = []
        self._next_id = 100

    def message_handler(self, *args, **kwargs):
        def decorator(func):
            self.handlers.append((kwargs.get("func"), func))
            return func
        return decorator

    def get_me(self):
        return support.make_user(777, username=self.username)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def recorder(*args, **kwargs):
            self._next_id += 1
            self.calls.append((name, args, kwargs))
            return support.make_message(text="sent", message_id=self._next_id,
                                        chat_id=args[0] if args else None)
        return recorder

    def replies(self):
        return [(args[1], kwargs) for name, args, kwargs in self.calls
                if name == "reply_to"]

    def set_username(self, username):
        self.username = username
        user_handlers._MINUSERS_CACHE.clear()

    def miniapp_handler(self, text=MINIAPP_KEYBOARD_TEXT):
        message = support.make_message(text=text)
        for predicate, func in self.handlers:
            if predicate is not None and predicate(message):
                return func
        raise AssertionError("no message handler matched the Mini App button text")


class MenuKeyboardTest(support.OfflineTestCase):
    def menu_json(self):
        return json.loads(helpers.main_menu(BOT_USERNAME).to_json())

    def miniapp_buttons(self, keyboard_json):
        return [button for row in keyboard_json["keyboard"] for button in row
                if button.get("text") == MINIAPP_KEYBOARD_TEXT]

    def test_https_menu_button_opens_web_app(self):
        with support.env(**BASE_ENV):
            keyboard = self.menu_json()["keyboard"]
        buttons = self.miniapp_buttons({"keyboard": keyboard})
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]["web_app"]["url"], f"{APP_URL}/app")
        self.assertNotIn("request_contact", buttons[0])
        # الزر في صفّه وحده
        self.assertEqual(len([row for row in keyboard
                              if any(b.get("text") == MINIAPP_KEYBOARD_TEXT for b in row)]), 1)

    def test_no_miniapp_button_without_https(self):
        for env_values in (dict(BASE_ENV, APP_URL="http://insecure.example"),
                           {"BOT_TOKEN": "123456:X", "APP_URL": None}):
            with self.subTest(env=env_values):
                with support.env(**env_values):
                    keyboard = self.menu_json()["keyboard"]
                self.assertEqual(self.miniapp_buttons({"keyboard": keyboard}), [])

    def test_style_and_web_app_survive_together(self):
        button = button_styles.web_app_keyboard_button(
            "زر", f"{APP_URL}/app", button_styles.STYLE_PRIMARY)
        data = button.to_dict()
        self.assertEqual(data["web_app"], {"url": f"{APP_URL}/app"})
        self.assertEqual(data["style"], button_styles.STYLE_PRIMARY)
        plain = button_styles.keyboard_button("زر", button_styles.STYLE_PRIMARY).to_dict()
        self.assertNotIn("web_app", plain)
        self.assertEqual(plain["style"], button_styles.STYLE_PRIMARY)


class GroupButtonPressTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        user_handlers._MINUSERS_CACHE.clear()
        self.addCleanup(user_handlers._MINUSERS_CACHE.clear)
        self.bot = RecordingBot()
        db_manager.add_bot_user = lambda *args, **kwargs: None
        with support.env(**BASE_ENV), ScopedEnv("MINIAPP_BOT_USERNAME", BOT_USERNAME):
            user_handlers.register(self.bot, "-1001234567890", [support.ADMIN_ID])
        self.bot.calls.clear()
        # الاسم يُقرأ من get_me لكل بوت مرة واحدة (ذاكرة في user_handlers)
        self.bot.set_username(BOT_USERNAME)

    def press(self, chat_type, chat_id=GROUP_CHAT_ID, env_values=None, username=None,
              env_username=BOT_USERNAME, link=None):
        self.bot.set_username(username)
        self.bot.calls.clear()
        message = support.make_message(text=MINIAPP_KEYBOARD_TEXT,
                                       user_id=support.MEMBER_ID,
                                       chat_type=chat_type, chat_id=chat_id)
        values = dict(BASE_ENV)
        values.update(env_values or {})
        patch = (mock.patch.object(user_handlers, "library_group_link", lambda: link)
                 if link is not None else nullcontext())
        with support.env(**values), ScopedEnv("MINIAPP_BOT_USERNAME", env_username), patch:
            self.bot.miniapp_handler()(message)
        return self.bot.replies()

    def only_button(self, markup):
        self.assertIsNotNone(markup)
        buttons = [button for row in markup.keyboard for button in row]
        self.assertEqual(len(buttons), 1)
        return buttons[0]

    def test_group_and_supergroup_get_direct_link_not_web_app(self):
        expected = f"https://t.me/{BOT_USERNAME}?startapp=library"
        for chat_type in ("group", "supergroup"):
            for env_username in (BOT_USERNAME, None):
                with self.subTest(chat_type=chat_type, env=env_username):
                    # الرابط نفسه يُختبر في SerializedMarkupTest/module linkage؛
                    # هنا نتحقق أن مسار الجروب يستخدمه ولا يُرسل web_app
                    replies = self.press(chat_type, env_username=env_username,
                                         link=expected)
                    self.assertEqual(len(replies), 1)
                    text, kwargs = replies[0]
                    self.assertEqual(text, user_handlers.GROUP_LIBRARY_HINT)
                    button = self.only_button(kwargs.get("reply_markup"))
                    self.assertIsInstance(button, types.InlineKeyboardButton)
                    self.assertEqual(button.url, expected)
                    self.assertIsNone(button.web_app)
                    serialized = button.to_dict()
                    self.assertEqual(serialized["url"], expected)
                    self.assertNotIn("web_app", serialized)

    def test_group_without_link_still_answers_with_hint(self):
        # لا رابط متاح (APP_URL بلا https أو غائب، أو اسم بوت غير صالح):
        # المسار يفرّع على build_group_library_markup نفسها
        cases = (dict(BASE_ENV, APP_URL="http://insecure.example"),
                 dict(BASE_ENV, APP_URL=None),
                 dict(BASE_ENV, **{"MINIAPP_BOT_USERNAME": 'bad name;'}))
        for env_values in cases:
            with self.subTest(env=env_values):
                with support.env(**env_values), ScopedEnv("MINIAPP_BOT_USERNAME", None):
                    self.assertIsNone(user_handlers.build_group_library_markup())
                    self.assertIsNone(user_handlers.library_group_link())
                with mock.patch.object(user_handlers, "build_group_library_markup",
                                       lambda: None):
                    replies = self.press("group")
                self.assertEqual(len(replies), 1)
                text, kwargs = replies[0]
                self.assertEqual(text, user_handlers.GROUP_LIBRARY_HINT_NO_LINK)
                self.assertNotIn("reply_markup", kwargs)

    def test_private_press_is_ignored_because_the_button_is_web_app(self):
        # في الخاص الزر نفسه web_app يفتح المكتبة مباشرة؛ وصول النص يعني عميلاً
        # قديماً لا يدعم web_app، ولا نرسل رداً حتى لا نزحم المحادثة (المسار /library).
        self.assertEqual(self.press("private", chat_id=support.MEMBER_ID), [])
        # عقد رابط الزر نفسه مُختبَر في MenuKeyboardTest.test_https_menu_button_opens_web_app

    def test_other_chat_types_get_no_reply(self):
        # لا لوحة مفاتيح ثابتة في القنوات/الأنواع الأخرى، ووصول النص هناك لا يُعالَج
        for chat_type in ("channel", "sender"):
            with self.subTest(chat_type=chat_type):
                self.assertEqual(self.press(chat_type, chat_id=-100555), [])

    def test_replies_never_raise_for_group_messages(self):
        message = support.make_message(text=MINIAPP_KEYBOARD_TEXT, chat_type="group",
                                       chat_id=GROUP_CHAT_ID)
        self.bot.calls.clear()
        with support.env(**BASE_ENV), ScopedEnv("MINIAPP_BOT_USERNAME", BOT_USERNAME):
            self.bot.miniapp_handler()(message)
        self.assertEqual(len(self.bot.replies()), 1)


def library_markups():
    """لوحتا /library في الجروب والخاص كما يبنيهما user_handlers."""
    with support.env(**BASE_ENV), ScopedEnv("MINIAPP_BOT_USERNAME", BOT_USERNAME):
        return {"group": user_handlers.build_group_library_markup(),
                "private": user_handlers.build_library_markup()}


class SerializedMarkupTest(support.OfflineTestCase):
    def test_group_markup_is_plain_url_without_web_app(self):
        markup = library_markups()["group"]
        payload = json.loads(markup.to_json())
        buttons = [button for row in payload["inline_keyboard"] for button in row]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]["url"], GROUP_LINK)
        self.assertNotIn("web_app", buttons[0])

    def test_private_markup_is_web_app_button(self):
        markup = library_markups()["private"]
        payload = json.loads(markup.to_json())
        buttons = [button for row in payload["inline_keyboard"] for button in row]
        self.assertEqual(len(buttons), 1)
        self.assertIn("web_app", buttons[0])
        self.assertEqual(buttons[0]["web_app"]["url"], f"{APP_URL}/app")
        self.assertNotIn("tgWebAppData=", buttons[0]["web_app"]["url"])
        self.assertNotIn("url", buttons[0])
