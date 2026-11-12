"""Phase 1 من تحديث تيليجرام: أزرار معطّلة حقيقية، /myid مع copy_text، رسائل HTML.

أوفلاين بالكامل: ``support`` يُستورد أولاً فيحجب HTTP الخاص بـ telebot وpsycopg2 قبل
استيراد أي وحدة إنتاج. لا يُلمس تيليجرام/PostgreSQL الحقيقيان، ولا يُنشأ أي شبكة.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode (`python -m unittest discover -s tests` with tests/__init__.py)
    from . import support
except ImportError:  # top-level mode (`python -m unittest discover -s tests`)
    import support

import importlib
import re
import unittest
from types import SimpleNamespace

import db_manager
from handlers import (
    admin_handlers,
    button_styles,
    callback_handlers,
    comment_handlers,
    group_handlers,
    helpers,
    user_handlers,
)


ADMINS = [support.ADMIN_ID, support.OTHER_ADMIN_ID]

PHASE_ENV = {
    "BOT_TOKEN": "123456:PHASE1-TEST-TOKEN",
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
    "CHANNEL_ID": "-1001234567890",
    "ADMIN_IDS": str(support.ADMIN_ID),
    "APP_URL": "https://example.onrender.com",
}
MANAGEMENT_TOKEN = "phase1-management-token"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def flat_buttons(markup):
    """الأزرار كما ستُسلَّم لتيليجرام (بعد التسلسل إلى JSON)."""
    return [button for row in markup.to_dict()["inline_keyboard"] for button in row]


def raw_buttons(markup):
    """كائنات الأزرار الأصلية (لفحص الخصائص مثل copy_text)."""
    return [button for row in markup.keyboard for button in row]


def button_data(markup, needle):
    for button in flat_buttons(markup):
        if needle in button.get("text", ""):
            return button
    raise AssertionError(f"no button containing {needle!r}: {flat_buttons(markup)!r}")


def video_row(video_id=7):
    return {
        "id": video_id,
        "message_id": 11,
        "chat_id": -1001234567890,
        "caption": "cap",
        "file_name": "file.mp4",
        "view_count": 3,
        "display_title": None,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class HandlerBot:
    """بوت وهمي يسجّل المعالجات المسجّلة والاستدعاءات (بلا شبكة)."""

    def __init__(self, me=None):
        self.me = me or SimpleNamespace(id=42, username="sample_bot", first_name="Bot")
        self.registrations = []
        self.calls = []

    # تسجيل المعالجات -----------------------------------------------------
    def message_handler(self, *args, **kwargs):
        def decorator(func):
            self.registrations.append((dict(kwargs), func))
            return func
        return decorator

    def callback_query_handler(self, *args, **kwargs):
        def decorator(func):
            self.registrations.append((dict(kwargs, callback=True), func))
            return func
        return decorator

    def handler_for(self, command):
        for kwargs, func in self.registrations:
            if command in (kwargs.get("commands") or []):
                return func
        raise AssertionError(f"no handler registered for command {command!r}")

    def handler_with_content_type(self, content_type):
        for kwargs, func in self.registrations:
            if content_type in (kwargs.get("content_types") or []):
                return func
        raise AssertionError(f"no handler registered for content type {content_type!r}")

    def callback_handler(self):
        for kwargs, func in self.registrations:
            if kwargs.get("callback"):
                return func
        raise AssertionError("callback handler was not registered")

    # استدعاءات تيليجرام ---------------------------------------------------
    def _record(self, name, text=None, **kwargs):
        self.calls.append((name, text, kwargs))
        return SimpleNamespace(message_id=len(self.calls) + 1,
                               chat=SimpleNamespace(id=1, type="private"))

    def get_me(self):
        return self.me

    def reply_to(self, message, text, **kwargs):
        return self._record("reply_to", text, **kwargs)

    def send_message(self, chat_id, text, **kwargs):
        return self._record("send_message", text, **kwargs)

    def answer_callback_query(self, call_id, text=None, **kwargs):
        return self._record("answer_callback_query", text, **kwargs)

    def delete_message(self, *args, **kwargs):
        return self._record("delete_message", None, **kwargs)

    def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        return self._record("edit_message_text", text, **kwargs)

    def register_next_step_handler(self, *args, **kwargs):
        return self._record("register_next_step_handler", None, **kwargs)

    def call_names(self):
        return [name for name, _text, _kwargs in self.calls]

    def last(self):
        return self.calls[-1]


class FakeThread:
    """بديل threading.Thread: لا يشغّل حذف الرسائل المؤجّل (لا sleep حقيقي)."""

    started = []

    def __init__(self, target=None, daemon=None, args=()):
        self.target = target
        self.daemon = daemon
        self.args = args
        FakeThread.started.append(self)

    def start(self):
        return None


class DiagnosisBot(HandlerBot):
    """بوت تشخيص file_id: نتائج التمرير مُحاكاة لكل رسالة."""

    def __init__(self, forwards):
        super().__init__()
        self.forwards = forwards

    def forward_message(self, chat_id, from_chat_id, message_id):
        self.calls.append(("forward_message", None, {"message_id": message_id}))
        outcome = self.forwards[message_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def fresh_webhook_module(token=MANAGEMENT_TOKEN):
    """إعادة استيراد webhook_bot ببيئة معزولة (مثل نمط tests/test_webhook.py)."""
    saved = os.environ.get("WEBHOOK_MANAGEMENT_TOKEN")
    os.environ.pop("WEBHOOK_MANAGEMENT_TOKEN", None)
    if token:
        os.environ["WEBHOOK_MANAGEMENT_TOKEN"] = token
    sys.modules.pop("webhook_bot", None)
    try:
        with support.env(**PHASE_ENV):
            return importlib.import_module("webhook_bot")
    finally:
        if saved is None:
            os.environ.pop("WEBHOOK_MANAGEMENT_TOKEN", None)
        else:
            os.environ["WEBHOOK_MANAGEMENT_TOKEN"] = saved


# ---------------------------------------------------------------------------
# 1) أزرار العرض المعطّلة
# ---------------------------------------------------------------------------

class DisabledInformationalButtonTest(support.OfflineTestCase):
    """أزرار العرض فقط تُسلسل ``disabled={}`` بلا callback_data مع بقاء التنسيق/الترتيب."""

    def assert_display_only(self, data, label):
        self.assertEqual(data.get("disabled"), {},
                         f"{label} should be a genuinely disabled button: {data!r}")
        self.assertNotIn("callback_data", data,
                         f"{label} must not carry callback_data: {data!r}")
        self.assertNotIn("url", data, f"{label} must not carry url: {data!r}")

    def patch(self, module, name, replacement):
        original = getattr(module, name)
        setattr(module, name, replacement)
        self.addCleanup(setattr, module, name, original)

    def test_disabled_button_helper_exists_and_serializes(self):
        factory = getattr(button_styles, "disabled_button", None)
        self.assertIsNotNone(factory, "handlers.button_styles must expose disabled_button()")
        payload = factory("عنوان القسم", button_styles.STYLE_PRIMARY).to_dict()
        self.assertEqual(payload.get("disabled"), {})
        self.assertNotIn("callback_data", payload)
        self.assertEqual(payload.get("style"), "primary")
        self.assertEqual(payload.get("text"), "عنوان القسم")

    def test_list_page_counter_is_disabled_while_navigation_stays_actionable(self):
        self.patch(helpers, "get_videos_ratings_bulk", lambda video_ids: {})
        total = helpers.VIDEOS_PER_PAGE * 3
        keyboard = helpers.create_paginated_keyboard([video_row()], total, 1,
                                                     "search_all", "all")
        counter = button_data(keyboard, "📄 2/3")
        self.assert_display_only(counter, "page counter")
        self.assertEqual(button_data(keyboard, "السابق")["callback_data"],
                         "search_all::all::0")
        self.assertEqual(button_data(keyboard, "التالي")["callback_data"],
                         "search_all::all::2")
        self.assertEqual(button_data(keyboard, "القائمة الرئيسية")["callback_data"],
                         "back_to_main")

    def test_category_keyboard_section_headings_and_counter_are_disabled(self):
        self.patch(helpers, "get_videos_ratings_bulk", lambda video_ids: {})
        self.patch(helpers, "get_category_by_id",
                   lambda category_id: {"id": category_id, "parent_id": None})
        keyboard = helpers.create_combined_keyboard(
            [{"id": 3, "name": "أفلام", "parent_id": None}],
            [video_row()], helpers.VIDEOS_PER_PAGE * 2, 0, 1)
        self.assert_display_only(button_data(keyboard, "الأقسام الفرعية"),
                                 "subcategories heading")
        self.assert_display_only(button_data(keyboard, "الفيديوهات ─╮"),
                                 "videos heading")
        self.assert_display_only(button_data(keyboard, "📄 1/2"), "page counter")
        self.assertEqual(button_data(keyboard, "أفلام")["callback_data"], "cat::3::0")

    def test_no_categories_display_is_disabled(self):
        self.patch(helpers, "get_categories_tree", lambda: [])
        keyboard = helpers.create_hierarchical_category_keyboard("admin::move_confirm")
        self.assert_display_only(button_data(keyboard, "لا توجد تصنيفات"),
                                 "no categories display")

    def test_rating_heading_and_statistics_are_disabled(self):
        self.patch(helpers, "get_user_video_rating", lambda video_id, user_id: None)
        self.patch(helpers, "is_video_favorite", lambda user_id, video_id: False)
        self.patch(helpers, "get_video_rating_stats",
                   lambda video_id: {"avg": 4.2, "count": 5})
        keyboard = helpers.create_video_action_keyboard(7, support.MEMBER_ID)
        self.assert_display_only(button_data(keyboard, "قيّم الفيديو"),
                                 "rating heading")
        stats = button_data(keyboard, "المتوسط")
        self.assert_display_only(stats, "rating statistics")
        self.assertIn("4.2", stats["text"])
        self.assertIn("5", stats["text"])
        self.assertEqual(button_data(keyboard, "التعليقات والردود")["callback_data"],
                         "add_comment::7")
        callbacks = [data.get("callback_data") for data in flat_buttons(keyboard)]
        self.assertIn("rate::7::1", callbacks)

    def test_admin_section_headings_are_disabled_but_actions_kept(self):
        self.patch(admin_handlers, "_miniapp_active", lambda: False)
        keyboard = admin_handlers.build_admin_panel()
        buttons = flat_buttons(keyboard)
        headings = [data for data in buttons if data["text"].startswith("╭─")]
        self.assertEqual(len(headings), 7, headings)
        for data in headings:
            self.assert_display_only(data, data["text"])
        callbacks = {data["callback_data"] for data in buttons if "callback_data" in data}
        for expected in ("admin::add_new_cat", "admin::delete_category_select",
                         "admin::set_active", "admin::view_comments",
                         "admin::broadcast", "admin::stats"):
            self.assertIn(expected, callbacks)
        self.assertEqual(len(buttons), len(headings) + len(callbacks))

    def test_miniapp_limit_display_is_disabled_but_setter_stays_actionable(self):
        self.patch(admin_handlers, "_miniapp_active", lambda: True)
        self.patch(admin_handlers, "_miniapp_open_url",
                   lambda ip_address=None: "https://example.com/app?tgWebAppData=%7B%7D")
        self.patch(admin_handlers, "miniapp_hourly_limit_by_ip", lambda: 5)
        keyboard = admin_handlers.build_admin_panel()
        current = button_data(keyboard, "الحد الحالي")
        self.assert_display_only(current, "current miniapp limit")
        self.assertIn("5", current["text"])
        self.assertEqual(button_data(keyboard, "حد فتح المكتبة")["callback_data"],
                         "admin::set_miniapp_limit")

    def test_comment_thread_page_counter_is_disabled(self):
        markup = comment_handlers._thread_nav_markup(7, 1, 3, "thread")
        self.assert_display_only(button_data(markup, "📄 2/3"), "thread page counter")
        self.assertEqual(button_data(markup, "السابق")["callback_data"], "thread::7::0")
        self.assertEqual(button_data(markup, "التالي")["callback_data"], "thread::7::2")

    def test_member_comments_page_counter_is_disabled(self):
        scripted = support.ScriptedExecuteQuery(db_manager).install()
        self.addCleanup(scripted.restore)
        scripted.script = {
            0: [support.comment_row()],
            1: [{"count": 25}],
            2: [support.thread_row(1, "member", "hi")],
        }
        bot = support.FakeBot()
        comment_handlers.show_user_comments(
            bot, support.MEMBER_ID, 0,
            message=support.make_message("/my_comments", user_id=support.MEMBER_ID))
        markups = bot.markup_for()
        self.assertTrue(markups, "the member list must send a navigation keyboard")
        self.assert_display_only(button_data(markups[-1], "📄 1/3"),
                                 "member comments page counter")
        self.assertEqual(button_data(markups[-1], "التالي")["callback_data"],
                         "my_comments::1")

    def test_actionable_cancel_buttons_keep_noop_callback(self):
        bot = support.FakeBot()
        comment_handlers.handle_delete_comment(bot, support.make_call("delete_comment::7"),
                                               ADMINS)
        markup = bot.markup_for()[-1]
        cancel = button_data(markup, "إلغاء")
        self.assertNotIn("disabled", cancel, "an actionable cancel must stay clickable")
        self.assertEqual(cancel.get("callback_data"), "noop")
        callbacks = [data.get("callback_data") for data in flat_buttons(markup)]
        self.assertIn("confirm_delete_comment::7", callbacks)

    def test_noop_callback_branch_still_answers_old_messages(self):
        bot = HandlerBot()
        original = helpers.check_subscription
        helpers.check_subscription = lambda bot_, user_id: (True, [])
        self.addCleanup(setattr, helpers, "check_subscription", original)
        callback_handlers.register(bot, ADMINS)
        callback_handlers.ADMIN_READ_ONLY_ACTIONS  # noqa: B018  (kept for old messages)
        bot.callback_handler()(support.make_call("noop"))
        self.assertEqual(bot.call_names()[0], "answer_callback_query")
        self.assertNotIn("register_next_step_handler", bot.call_names())


# ---------------------------------------------------------------------------
# 2) /myid: HTML + copy_text
# ---------------------------------------------------------------------------

class MyIdCommandTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.bot = HandlerBot()
        original_add = user_handlers.add_bot_user
        user_handlers.add_bot_user = lambda *args, **kwargs: True
        self.addCleanup(setattr, user_handlers, "add_bot_user", original_add)
        original_check = user_handlers.check_subscription
        user_handlers.check_subscription = lambda bot_, user_id: (True, [])
        self.addCleanup(setattr, user_handlers, "check_subscription", original_check)
        original_admins = list(comment_handlers.ADMIN_IDS)
        self.addCleanup(comment_handlers.ADMIN_IDS.__setitem__,
                        slice(None), original_admins)
        with support.env():
            user_handlers.register(self.bot, "-1001234567890", ADMINS)

    def test_myid_replies_html_id_with_copy_text_button(self):
        self.bot.handler_for("myid")(support.make_message("/myid", user_id=support.MEMBER_ID))
        name, text, kwargs = self.bot.last()
        self.assertEqual(name, "reply_to")
        self.assertEqual(kwargs.get("parse_mode"), "HTML")
        self.assertIn(f"<code>{support.MEMBER_ID}</code>", text)
        self.assertNotIn("`", text)
        markup = kwargs.get("reply_markup")
        self.assertIsNotNone(markup, "/myid must attach the copy_text keyboard")
        payload = flat_buttons(markup)
        self.assertEqual(len(payload), 1, payload)
        self.assertEqual(payload[0]["copy_text"], {"text": str(support.MEMBER_ID)})
        self.assertNotIn("callback_data", payload[0])
        self.assertEqual(raw_buttons(markup)[0].copy_text.text, str(support.MEMBER_ID))

    def test_start_uses_html_and_escapes_bot_username(self):
        self.bot.me = SimpleNamespace(id=42, username='bot<&>"x', first_name="Bot")
        with support.env():
            self.bot.handler_for("start")(
                support.make_message("/start", user_id=support.MEMBER_ID))
        name, text, kwargs = self.bot.last()
        self.assertEqual(name, "reply_to")
        self.assertEqual(kwargs.get("parse_mode"), "HTML")
        self.assertIn("<code>@bot&lt;&amp;&gt;&quot;x كلمة البحث</code>", text)
        self.assertNotIn("bot<&>", text)
        self.assertNotIn("*بحث سريع", text)
        self.assertNotIn("`", text)


# ---------------------------------------------------------------------------
# 3) رسائل الجروبات: HTML + هروب القيم الديناميكية
# ---------------------------------------------------------------------------

class GroupHtmlMessageTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.bot = HandlerBot(SimpleNamespace(id=42, username="group_bot<&>",
                                              first_name="Bot"))
        FakeThread.started = []
        original_threading = group_handlers.threading
        group_handlers.threading = SimpleNamespace(Thread=FakeThread)
        self.addCleanup(setattr, group_handlers, "threading", original_threading)
        group_handlers.register(self.bot, ADMINS)

    def test_search_help_uses_html_and_escapes_bot_username(self):
        self.bot.handler_for("search_help")(
            support.make_message("/search_help", user_id=support.MEMBER_ID))
        name, text, kwargs = self.bot.last()
        self.assertEqual(name, "send_message")
        self.assertEqual(kwargs.get("parse_mode"), "HTML")
        self.assertIn("<b>كيفية البحث عن الفيديوهات</b>", text)
        self.assertIn("<code>@group_bot&lt;&amp;&gt;</code>", text)
        self.assertNotIn("group_bot<&>", text)
        self.assertNotIn("*كيفية البحث", text)

    def test_group_welcome_uses_html_and_escapes_bot_username(self):
        message = support.make_message(user_id=support.MEMBER_ID, chat_type="group",
                                       content_type="new_chat_members")
        message.new_chat_members = [SimpleNamespace(id=42)]
        self.bot.handler_with_content_type("new_chat_members")(message)
        name, text, kwargs = self.bot.last()
        self.assertEqual(name, "send_message")
        self.assertEqual(kwargs.get("parse_mode"), "HTML")
        self.assertIn("<b>مرحباً! تم إضافتي للجروب</b>", text)
        self.assertIn("<code>@group_bot&lt;&amp;&gt; أكشن</code>", text)
        self.assertNotIn("group_bot<&>", text)
        self.assertNotIn("*مرحباً", text)


# ---------------------------------------------------------------------------
# 4) تقرير تشخيص file_id: HTML + blockquote قابلة للطي + هروب كامل
# ---------------------------------------------------------------------------

HOSTILE_CAPTION = '</blockquote><b>شر</b>&<script>alert(1)</script>'
HOSTILE_CONTENT_TYPE = "VIDEO<script>&"
HOSTILE_ERROR = 'forward failed: </blockquote><b>خطأ</b> & <i>x</i>'


class DiagnosisReportTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.module = fresh_webhook_module()
        forwards = {
            11: SimpleNamespace(video=SimpleNamespace(file_id="AAA"), document=None,
                                message_id=91),
            13: SimpleNamespace(video=None, document=None, message_id=93),
            15: RuntimeError(HOSTILE_ERROR),
        }
        self.bot = DiagnosisBot(forwards)
        self.module.bot = self.bot
        self.client = self.module.app.test_client()
        self.module.app.config["TESTING"] = True
        self.scripted = support.ScriptedExecuteQuery(db_manager).install()
        self.addCleanup(self.scripted.restore)
        self.scripted.script = {0: [
            {"id": 1, "message_id": 11, "chat_id": -1001, "file_id": "AAA",
             "content_type": HOSTILE_CONTENT_TYPE, "caption": HOSTILE_CAPTION},
            {"id": 2, "message_id": 13, "chat_id": -1001, "file_id": "BBB",
             "content_type": None, "caption": "عادي"},
            {"id": 3, "message_id": 15, "chat_id": -1001, "file_id": "CCC",
             "content_type": "DOCUMENT", "caption": None},
        ]}

    def tearDown(self):
        sys.modules.pop("webhook_bot", None)
        super().tearDown()

    def test_report_is_html_with_expandable_blockquote_and_escaped_values(self):
        response = self.client.get(
            f"/admin/diagnose_file_ids?admin_id={support.ADMIN_ID}",
            headers={"X-Admin-Token": MANAGEMENT_TOKEN})
        self.assertEqual(response.status_code, 200)
        sends = self.bot.calls_of("send_message") if hasattr(self.bot, "calls_of") else []
        if not sends:
            sends = [call for call in self.bot.calls if call[0] == "send_message"]
        self.assertEqual(len(sends), 1, self.bot.calls)
        text, kwargs = sends[0][1], sends[0][2]
        self.assertEqual(kwargs.get("parse_mode"), "HTML")

        self.assertTrue(text.startswith("🔍 <b>تقرير التشخيص</b>"), text)
        self.assertIn("<blockquote expandable>", text)
        self.assertEqual(text.count("<blockquote"), 1, "exactly one collapsible quote")
        self.assertEqual(text.count("</blockquote>"), 1, "caption must be escaped")
        self.assertTrue(text.rstrip().endswith("</blockquote>"), text)
        # فصل واضح: سطر فارغ بين العنوان والاقتباس
        self.assertIn("</b>\n\n<blockquote expandable>", text)

        self.assertIn("&lt;/blockquote&gt;", text)
        self.assertIn("&lt;b&gt;شر&lt;/b&gt;", text)
        self.assertNotIn("<b>شر</b>", text)
        self.assertNotIn("<script>", text)
        self.assertIn(HOSTILE_CONTENT_TYPE.replace("<", "&lt;").replace(">", "&gt;"), text)
        self.assertIn("&lt;b&gt;خطأ&lt;/b&gt;", text)
        self.assertIn("&lt;i&gt;x&lt;/i&gt;", text)
        self.assertNotIn("<i>x</i>", text)
        # الصف الثالث بلا وسائط
        self.assertIn("UNKNOWN", text)


# ---------------------------------------------------------------------------
# 5) لا parse_mode قديم من Markdown في الإنتاج + لا تدخّل خارج النطاق
# ---------------------------------------------------------------------------

LEGACY_PARSE_MODE = re.compile(r"parse_mode\s*=\s*['\"]Markdown", re.IGNORECASE)
_SKIP_DIRS = {"tests", ".git", ".brain", ".agents", "__pycache__", "docs", ".vscode",
              ".cline", ".codex"}


def production_python_files():
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


class LegacyMarkdownRemovalTest(unittest.TestCase):
    def test_no_legacy_markdown_parse_mode_in_production_python(self):
        offenders, scanned = [], []
        for path in production_python_files():
            relative = os.path.relpath(path, _ROOT).replace("\\", "/")
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                source = handle.read()
            scanned.append(relative)
            if LEGACY_PARSE_MODE.search(source):
                offenders.append(relative)
        self.assertGreater(len(scanned), 20, "source scan must not be vacuous")
        for expected in ("webhook_bot.py", "handlers/user_handlers.py",
                         "handlers/group_handlers.py"):
            self.assertIn(expected, scanned)
        self.assertEqual(offenders, [], f"legacy Markdown parse_mode remains in {offenders}")


if __name__ == "__main__":
    unittest.main()
