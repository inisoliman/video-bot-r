"""Offline regression: opening the full conversation (thread view) must render a page.

Reported production defect: the Render log showed
``handlers/comment_handlers.py line 598 in _paginate_thread`` calling an undefined
``paginate_messages`` -> ``NameError: name 'paginate_messages' is not defined``, so
both the admin conversation view (``thread_view`` callback) and the member view
(``thread`` callback) answered "حدث خطأ، حاول مرة أخرى" instead of the page.

The tests drive the REAL handlers (``view_admin_thread`` / ``open_member_thread``,
and the real callback dispatch) with explicit fakes only: the DB layer is delegated
to explicit fakes recorded here, and the Telegram side is ``support.FakeBot``.
``support`` blocks HTTP/psycopg2 before anything else is imported.
"""

import json
import unittest

import support

from telebot import types

import db_manager
import handlers as handlers_package
from handlers import comment_handlers as ch

CATEGORY_ID = 1
VIDEO_ID = 3
THREAD_ID = 7
ERROR_TEXT = "حدث خطأ"


class _FakeCommentDb:
    """Explicit fake of the DB functions used by the two thread-open handlers."""

    def __init__(self):
        self.comment = support.comment_row(comment_id=THREAD_ID, user_id=support.MEMBER_ID,
                                           caption="فيلم المخزون")
        self.messages = [
            support.thread_row(11, "member", "سؤال أول & <واضح>", minutes=0, comment_id=THREAD_ID),
            support.thread_row(12, "admin", "رد الإدارة الأول", minutes=5, comment_id=THREAD_ID),
        ]
        self.calls = []

    def get_comment_by_id(self, comment_id):
        self.calls.append(("get_comment_by_id", comment_id))
        return dict(self.comment) if int(comment_id) == THREAD_ID else None

    def get_thread_messages(self, comment_id, page=0, page_size=10, order="asc"):
        self.calls.append(("get_thread_messages", comment_id, page, page_size, order))
        return [dict(row) for row in self.messages], len(self.messages)

    def mark_thread_preferred(self, comment_id, role):
        self.calls.append(("mark_thread_preferred", comment_id, role))
        return True


class _FakeVideoDb:
    """Explicit fake of the video/category search used to press the real button."""

    def __init__(self, video_id=VIDEO_ID):
        self.video_id = video_id

    def get_video_by_id(self, video_id):
        if int(video_id) != self.video_id:
            return None
        return {"id": self.video_id, "caption": "فيلم المخزون", "category_id": CATEGORY_ID,
                "file_id": "FILE", "content_type": "video"}

    def get_child_categories(self, category_id):
        return []

    def get_videos(self, category_id, page=0):
        return ([{"id": self.video_id, "caption": "فيلم المخزون"}], 1)


class _HandlerGlobalsCase(support.OfflineTestCase):
    """Temporary module-global stubs (handler window) restored in tearDown."""

    def setUp(self):
        super().setUp()
        self._patches = []
        self.admins = [support.ADMIN_ID, support.OTHER_ADMIN_ID]
        self.bot = support.FakeBot()

    def tearDown(self):
        while self._patches:
            self._patches.pop()()
        super().tearDown()

    def stub(self, module, **values):
        for name, value in values.items():
            had = name in module.__dict__
            original = module.__dict__.get(name)
            module.__dict__[name] = value
            self._patches.append(
                lambda n=name, h=had, o=original, m=module: (
                    m.__dict__.__setitem__(n, o) if h else m.__dict__.pop(n, None)))

    def texts(self):
        return [text for text in self.bot.sent_texts() if text]

    def joined(self):
        return "\n".join(self.texts())


class AdminThreadViewTest(_HandlerGlobalsCase):
    def open_admin_thread(self, fake_db, data="thread_view::7::0"):
        self.stub(db_manager,
                  get_comment_by_id=fake_db.get_comment_by_id,
                  get_thread_messages=fake_db.get_thread_messages,
                  mark_thread_preferred=fake_db.mark_thread_preferred,
                  get_latest_thread_message=lambda comment_id: fake_db.messages[-1])
        call = support.make_call(data, user_id=support.ADMIN_ID)
        ch.view_admin_thread(self.bot, call, self.admins)
        return call

    def test_admin_thread_view_renders_page(self):
        fake_db = _FakeCommentDb()
        self.open_admin_thread(fake_db)
        joined = self.joined()
        self.assertNotIn(ERROR_TEXT, joined, joined)
        self.assertNotIn(ERROR_TEXT, "\n".join(a[0] or "" for a in self.bot.answers()))
        self.assertIn("محادثة #7", joined)
        self.assertIn("سؤال أول &amp; &lt;واضح&gt;", joined)
        self.assertIn("رد الإدارة الأول", joined)
        self.assertIn("صفحة 1/1", joined)
        callbacks = [cb for markup in self.bot.markup_for() for cb in support.button_callbacks(markup)]
        self.assertIn("reply_comment::7", callbacks)
        self.assertIn(("get_thread_messages", THREAD_ID, 0, 10000, "asc"), fake_db.calls)
        self.assertIn(("mark_thread_preferred", THREAD_ID, db_manager.ROLE_ADMIN), fake_db.calls)

    def test_admin_thread_view_second_page_renders(self):
        fake_db = _FakeCommentDb()
        self.open_admin_thread(fake_db, "thread_view::7::1")
        joined = self.joined()
        self.assertNotIn(ERROR_TEXT, joined, joined)
        # صفحة واحدة فقط: لا صفحة ثانية، لكن لا خطأ ولا شاشة فارغة
        self.assertIn("محادثة #7", joined)


class MemberThreadViewTest(_HandlerGlobalsCase):
    def open_member_thread(self, fake_db, user_id=support.MEMBER_ID, data="thread::7::0"):
        self.stub(db_manager,
                  get_comment_by_id=fake_db.get_comment_by_id,
                  get_thread_messages=fake_db.get_thread_messages,
                  mark_thread_preferred=fake_db.mark_thread_preferred)
        call = support.make_call(data, user_id=user_id)
        ch.open_member_thread(self.bot, call)
        return call

    def test_member_thread_renders_page(self):
        fake_db = _FakeCommentDb()
        self.open_member_thread(fake_db)
        joined = self.joined()
        self.assertNotIn(ERROR_TEXT, joined, joined)
        self.assertIn("محادثة #7", joined)
        self.assertIn("سؤال أول &amp; &lt;واضح&gt;", joined)
        callbacks = [cb for markup in self.bot.markup_for() for cb in support.button_callbacks(markup)]
        self.assertIn("thread_write::7", callbacks)
        self.assertIn(("mark_thread_preferred", THREAD_ID, db_manager.ROLE_MEMBER), fake_db.calls)

    def test_member_cannot_open_other_members_thread(self):
        fake_db = _FakeCommentDb()
        self.open_member_thread(fake_db, user_id=support.OTHER_MEMBER_ID)
        self.assertIn("صلاحية", self.joined() + "".join(a[0] or "" for a in self.bot.answers()))
        self.assertNotIn(("get_thread_messages", THREAD_ID, 0, 10000, "asc"), fake_db.calls)


class _ProbeBot:
    """Real TeleBot (handlers registered on it) with recorded, network-free sends."""

    def __init__(self):
        import telebot
        self.__dict__["sent"] = []
        self.__dict__["answers"] = []
        self.__dict__["_inner"] = telebot.TeleBot(
            "123456:TEST-TOKEN-NOT-REAL", threaded=False, validate_token=False,
            parse_mode="HTML")

    def send_message(self, chat_id, text=None, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return types.Message.de_json({
            "message_id": 9000 + len(self.sent), "date": 1,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": 1, "is_bot": True, "first_name": "bot"}, "text": text or "x"})

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.answers.append((text, show_alert))
        return True

    def get_me(self):
        return types.User.de_json({"id": 1, "is_bot": True, "first_name": "b",
                                   "username": "probe_bot"})

    def __getattr__(self, name):
        return getattr(self.__dict__["_inner"], name)

    def texts(self):
        return [text for _chat, text, _kw in self.__dict__["sent"] if text]


class RealCallbackThreadOpenTest(_HandlerGlobalsCase):
    """End-to-end through the real registered callback dispatch (open button -> page)."""

    def setUp(self):
        super().setUp()
        self.probe = _ProbeBot()
        self._real_patches = []

    def tearDown(self):
        while self._real_patches:
            self._real_patches.pop()()
        super().tearDown()

    def real_stub(self, module, **values):
        for name, value in values.items():
            had = name in module.__dict__
            original = module.__dict__.get(name)
            module.__dict__[name] = value
            self._real_patches.append(
                lambda n=name, h=had, o=original, m=module: (
                    m.__dict__.__setitem__(n, o) if h else m.__dict__.pop(n, None)))

    def press(self, data, user_id, chat_id, message_id=50):
        update = types.Update.de_json({
            "update_id": 1,
            "callback_query": {
                "id": "cb-open", "chat_instance": "ci", "data": data,
                "from": {"id": user_id, "is_bot": False, "first_name": "U"},
                "message": {
                    "message_id": message_id, "date": 1700000000, "text": "menu",
                    "chat": {"id": chat_id, "type": "private"},
                    "from": {"id": 1, "is_bot": True, "first_name": "bot"},
                },
            },
        }).callback_query
        self.probe.process_new_callback_query([update])

    def install(self, fake_db):
        handlers_package.register_all_handlers(self.probe, "-1001234567890", self.admins)
        callback_handlers = handlers_package.callback_handlers
        self.real_stub(db_manager,
                       get_comment_by_id=fake_db.get_comment_by_id,
                       get_thread_messages=fake_db.get_thread_messages,
                       mark_thread_preferred=fake_db.mark_thread_preferred,
                       get_latest_thread_message=lambda comment_id: fake_db.messages[-1])
        # البحث عن الفيديو (لAlt إظهار كلمة "تعليقات" الزر) بديل صريح
        videos = _FakeVideoDb()
        for module in {callback_handlers, handlers_package.helpers}:
            for name in ("get_video_by_id", "get_child_categories", "get_videos"):
                if hasattr(module, name):
                    self.real_stub(module, **{name: getattr(videos, name)})

    def test_admin_button_opens_rendered_page(self):
        fake_db = _FakeCommentDb()
        self.install(fake_db)
        self.press("thread_view::7::0", support.ADMIN_ID, support.ADMIN_ID)
        joined = "\n".join(self.probe.texts())
        self.assertNotIn(ERROR_TEXT, joined, joined)
        self.assertNotIn(ERROR_TEXT, "\n".join(text or "" for text, _a in self.probe.__dict__["answers"]))
        self.assertIn("محادثة #7", joined)
        self.assertIn("رد الإدارة الأول", joined)

    def test_member_button_opens_rendered_page(self):
        fake_db = _FakeCommentDb()
        self.install(fake_db)
        self.press("thread::7::0", support.MEMBER_ID, support.MEMBER_ID)
        joined = "\n".join(self.probe.texts())
        self.assertNotIn(ERROR_TEXT, joined, joined)
        self.assertIn("محادثة #7", joined)


if __name__ == "__main__":
    unittest.main()
