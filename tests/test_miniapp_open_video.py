"""Offline tests for ``POST /app/api/open-video`` (send the selected video to the
authenticated user's private chat).

No network: ``tests/support.py`` blocks telebot HTTP and psycopg2; the bot and every
DB function are fakes. Telegram is never called and no token/file id may leak.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode
    from . import support
except ImportError:  # top-level mode
    import support

import hashlib
import hmac
import json
import threading
import unittest
from urllib.parse import quote
from unittest import mock

from flask import Flask
from telebot import apihelper

import db_manager
import miniapp
from handlers import helpers

BOT_TOKEN = "123456:OPEN-VIDEO-BOT-TOKEN"
APP_URL = "https://example.onrender.com"
NOW = 1_700_000_000
CHANNEL = 1234567890
VIRAL_TITLE = "عنوان ظهور الفيديو"
BASE_ENV = {"BOT_TOKEN": BOT_TOKEN, "APP_URL": APP_URL}

MEMBER = support.MEMBER_ID
OTHER = support.OTHER_MEMBER_ID


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def make_init_data(bot_token=BOT_TOKEN, user_id=MEMBER, auth_date=NOW, extra=None):
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "عضو", "username": "member"},
                           ensure_ascii=False),
    }
    fields.update(extra or {})
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return "&".join([f"{key}={quote(fields[key], safe='')}" for key in sorted(fields)]
                    + [f"hash={digest}"])


def video_row(video_id=3, content_type="VIDEO", caption=f"{VIRAL_TITLE}\nسطر ثانٍ",
              display_title=None, file_id="FILE-ID-MUST-NOT-LEAK", chat_id="-100999",
              message_id=777):
    return {
        "id": video_id, "message_id": message_id, "chat_id": chat_id,
        "file_id": file_id, "file_name": f"video-{video_id}.mp4", "caption": caption,
        "display_title": display_title, "metadata": {"duration": 90},
        "view_count": 4, "thumbnail_file_id": None, "content_type": content_type,
        "category_id": 1,
    }


class OpenVideoDb:
    def __init__(self):
        self.calls = []
        self.video = video_row()
        self.required_channels = []
        self.view_error = None
        self.history_error = None
        self.view_calls = []
        self.history_calls = []

    # reads -------------------------------------------------------------
    def get_video_by_id(self, video_id):
        self.calls.append(("get_video_by_id", video_id))
        return dict(self.video) if self.video and video_id == self.video["id"] else None

    def get_required_channels(self):
        self.calls.append(("get_required_channels",))
        return [dict(channel) for channel in self.required_channels]

    # writes ------------------------------------------------------------
    def increment_video_view_count(self, video_id):
        self.calls.append(("increment_video_view_count", video_id))
        self.view_calls.append(video_id)
        if self.view_error:
            raise self.view_error
        return True

    def add_to_history(self, user_id, video_id):
        self.calls.append(("add_to_history", user_id, video_id))
        self.history_calls.append((user_id, video_id))
        if self.history_error:
            raise self.history_error
        return True

    def writes(self):
        return [c for c in self.calls
                if c[0] in ("increment_video_view_count", "add_to_history")]


class DeliveryBot:
    """Fake bot: records delivery calls; ``fail_client`` raises a blocked-user error."""

    def __init__(self):
        self.calls = []
        self.fail_copy = False
        self.fail_send = False
        self.fail_get_chat_member = None
        self.member_status = "member"

    def copy_message(self, chat_id, from_chat_id, message_id):
        self.calls.append(("copy_message", chat_id, from_chat_id, message_id))
        if self.fail_copy:
            raise apihelper.ApiTelegramException(
                "copyMessage", {"ok": False}, {"error_code": 400,
                                               "description": "Bad Request: message not found"})
        return support.make_message(message_id=900, chat_id=chat_id)

    def _blocked(self, name):
        raise apihelper.ApiTelegramException(
            "sendVideo", {"ok": False}, {"error_code": 403,
                                         "description": "Forbidden: bot was blocked by the user"})

    def _sender(self, name, chat_id, media, kwargs):
        self.calls.append((name, chat_id, media, kwargs))
        if self.fail_send:
            self._blocked(name)
        return support.make_message(message_id=901 if name == "send_video" else 902,
                                    chat_id=chat_id)

    def send_video(self, chat_id, video, **kwargs):
        return self._sender("send_video", chat_id, video, kwargs)

    def send_document(self, chat_id, document, **kwargs):
        return self._sender("send_document", chat_id, document, kwargs)

    def get_chat_member(self, channel_id, user_id):
        self.calls.append(("get_chat_member", channel_id, user_id))
        if self.fail_get_chat_member is not None:
            raise self.fail_get_chat_member
        return type("Member", (), {"status": self.member_status})()

    def delivered(self):
        return [c for c in self.calls if c[0] in ("copy_message", "send_video", "send_document")]


FAKE_NAMES = ("get_video_by_id", "increment_video_view_count", "add_to_history")
_REAL_DB = {name: getattr(db_manager, name) for name in FAKE_NAMES}


class OpenVideoTestBase(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.db = OpenVideoDb()
        self.bot = DeliveryBot()
        for name in FAKE_NAMES:
            setattr(db_manager, name, getattr(self.db, name))
            self.addCleanup(setattr, db_manager, name, _REAL_DB.get(name))
        # helpers يستورد get_required_channels بالاسم في المستوى الأعلى: نستبدل
        # المرجع المستخدم فعلاً في helpers ثم نعيده أصلاً بعد الاختبار.
        patcher = mock.patch.dict(os.environ, BASE_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        # المشترك يقيس "متى" يُستدعى check_subscription دون تغيير سلوكه
        self.subscription_calls = []
        original = helpers.check_subscription
        fake_db = self.db

        def spy(bot, user_id):
            self.subscription_calls.append((bot, user_id))
            return original(bot, user_id)

        self.addCleanup(setattr, helpers, "check_subscription", original)
        helpers.check_subscription = spy
        self._original_channels = helpers.get_required_channels
        helpers.get_required_channels = fake_db.get_required_channels
        self.addCleanup(setattr, helpers, "get_required_channels",
                        self._original_channels)
        miniapp.reset_open_video_guards()
        self.addCleanup(miniapp.reset_open_video_guards)

    def make_app(self, bot="default"):
        app = Flask("open-video-test")
        app.config["TESTING"] = True
        target = self.bot if bot == "default" else bot
        self.assertTrue(miniapp.register_miniapp(app, target))
        return app

    def post(self, payload, bot="default", now=NOW):
        app = self.make_app(bot)
        with mock.patch.object(miniapp, "_now", return_value=now):
            response = app.test_client().post("/app/api/open-video", json=payload)
        return response

    def ok_payload(self, **overrides):
        payload = {"video_id": 3, "initData": make_init_data()}
        payload.update(overrides)
        return payload

    def assert_no_secret(self, response):
        body = response.get_data(as_text=True)
        for secret in ("FILE-ID-MUST-NOT-LEAK", "-100999", "BOT_TOKEN", BOT_TOKEN,
                       "bot was blocked", "copyMessage"):
            self.assertNotIn(secret, body)


# ---------------------------------------------------------------------------
# Happy path and delivery conventions
# ---------------------------------------------------------------------------

class HappyPathTest(OpenVideoTestBase):
    def test_delivers_to_private_chat_of_signed_user(self):
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["video_id"], 3)
        self.assert_no_secret(response)
        self.assertEqual(self.bot.delivered(),
                         [("copy_message", MEMBER, -100999, 777)])
        self.assertIn(("get_required_channels",), self.db.calls)

    def test_reports_success_only_after_telegram_confirms(self):
        class Unconfirmed(DeliveryBot):
            def copy_message(self, *args, **kwargs):
                self.calls.append(("copy_message",) + args)
                return None

            def send_video(self, *args, **kwargs):
                self.calls.append(("send_video",) + args)
                return None
        bot = Unconfirmed()
        self.bot = bot
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 502)
        self.assertIn("delivery_unavailable", response.get_data(as_text=True))
        self.assertEqual(self.db.writes(), [])

    def test_document_content_type_uses_send_document(self):
        row = video_row(content_type="DOCUMENT", file_id="DOC-FILE-ID")
        del row["chat_id"]
        del row["message_id"]  # لا رسالة أصلية: الإرسال من file_id
        self.db.video = row
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 200)
        name, chat_id, document = self.bot.delivered()[0][:3]
        caption = self.bot.delivered()[0][3]["caption"]
        self.assertEqual((name, chat_id, document, caption),
                         ("send_document", MEMBER, "DOC-FILE-ID",
                          f"<b>{VIRAL_TITLE}</b>"))
        self.assertEqual(self.bot.delivered()[0][3]["parse_mode"], "HTML")
        self.assertNotIn("DOC-FILE-ID", response.get_data(as_text=True))

    def test_video_content_type_uses_send_video_with_display_title_caption(self):
        row = video_row(display_title="عنوان معدّل", file_id="VIDEO-FILE-ID")
        del row["chat_id"]
        del row["message_id"]
        self.db.video = row
        self.assertEqual(self.post(self.ok_payload()).status_code, 200)
        name, chat_id, video = self.bot.delivered()[0][:3]
        caption = self.bot.delivered()[0][3]["caption"]
        self.assertEqual((name, chat_id, video, caption),
                         ("send_video", MEMBER, "VIDEO-FILE-ID", "<b>عنوان معدّل</b>"))
        self.assertEqual(self.bot.delivered()[0][3]["parse_mode"], "HTML")

    def test_caption_is_html_escaped(self):
        row = video_row(caption="<script> & co")
        del row["chat_id"]
        del row["message_id"]
        self.db.video = row
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.bot.delivered()[0][3]["caption"],
                         "<b>&lt;script&gt; &amp; co</b>")
        self.assertEqual(self.bot.delivered()[0][3]["parse_mode"], "HTML")

    def test_missing_file_id_is_a_truthful_failure(self):
        # لا file_id ولا رسالة أصلية: لا شيء لإرساله
        row = video_row(file_id=None)
        row["message_id"] = None
        row["chat_id"] = None
        self.db.video = row
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 409)
        self.assertIn("video_unavailable", response.get_data(as_text=True))
        self.assertEqual(self.bot.delivered(), [])
        self.assertEqual(self.db.writes(), [])

    def test_original_message_without_file_id_still_uses_copy(self):
        self.db.video = video_row(file_id=None)
        self.assertEqual(self.post(self.ok_payload()).status_code, 200)
        self.assertEqual([c[0] for c in self.bot.delivered()], ["copy_message"])

    def test_copy_unavailable_without_file_id_is_a_truthful_failure(self):
        self.db.video = video_row(file_id=None)
        self.bot.fail_copy = True
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.db.writes(), [])

    def test_no_file_id_and_no_original_message_is_409(self):
        row = video_row(file_id=None)
        row["message_id"] = None
        row["chat_id"] = None
        self.db.video = row
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.bot.delivered(), [])
        self.assertEqual(self.db.writes(), [])

    def test_string_video_id_is_accepted(self):
        self.assertEqual(self.post(self.ok_payload(video_id="3")).status_code, 200)
        self.assertEqual(self.bot.delivered()[0][1], MEMBER)


# ---------------------------------------------------------------------------
# Authentication and destination
# ---------------------------------------------------------------------------

class AuthTest(OpenVideoTestBase):
    def test_missing_init_data_is_401_without_side_effects(self):
        for payload in ({"video_id": 3}, {"video_id": 3, "initData": ""},
                        {"video_id": 3, "initData": 12}):
            with self.subTest(payload=payload):
                response = self.post(payload)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.db.calls, [])
        self.assertEqual(self.bot.calls, [])

    def test_tampered_signature_is_401(self):
        valid = make_init_data()
        tampered = valid[:-1] + ("0" if valid[-1] != "0" else "1")
        self.assertEqual(self.post(self.ok_payload(initData=tampered)).status_code, 401)
        self.assertEqual(self.bot.delivered(), [])
        self.assertEqual(self.db.calls, [])

    def test_other_bot_token_is_401(self):
        forged = make_init_data(bot_token="999:OTHER")
        self.assertEqual(self.post(self.ok_payload(initData=forged)).status_code, 401)
        self.assertEqual(self.db.calls, [])

    def test_stale_auth_date_is_401(self):
        stale = make_init_data(auth_date=NOW - 24 * 3600 - 10)
        response = self.post(self.ok_payload(initData=stale), now=NOW)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.db.calls, [])

    def test_forged_destination_is_ignored(self):
        payload = self.ok_payload(chat_id=OTHER, user_id=OTHER, destination=OTHER,
                                  to_user_id=OTHER)
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(self.bot.delivered()[0][1], MEMBER)
        self.assertEqual(self.db.history_calls, [(MEMBER, 3)])

    def test_unsigned_init_data_payload_cannot_swap_identity(self):
        # user داخل الحمولة غير الموقّعة لا يُعتمد: الهوية من الحمولة الموقّعة فقط
        fields = {"auth_date": str(NOW), "user": json.dumps({"id": OTHER}),
                  "hash": "deadbeef"}
        forged = "&".join(f"{k}={quote(v, safe='')}" for k, v in fields.items())
        self.assertEqual(self.post(self.ok_payload(initData=forged)).status_code, 401)
        self.assertEqual(self.bot.delivered(), [])

    def test_no_token_configured_fails_closed(self):
        values = {k: v for k, v in os.environ.items() if not k.startswith("MINIAPP_")}
        values.pop("BOT_TOKEN", None)
        app = self.make_app()
        with mock.patch.dict(os.environ, values, clear=True), \
                mock.patch.object(miniapp, "_now", return_value=NOW):
            response = app.test_client().post("/app/api/open-video", json=self.ok_payload())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.db.calls, [])
        self.assertEqual(self.bot.delivered(), [])


# ---------------------------------------------------------------------------
# Validation, video presence, subscription
# ---------------------------------------------------------------------------

class ValidationTest(OpenVideoTestBase):
    def test_invalid_video_ids_are_400_without_db_access(self):
        for value in (0, -1, True, False, "0", "-3", "abc", None, 3.5, [], {}):
            with self.subTest(value=value):
                response = self.post(self.ok_payload(video_id=value))
                self.assertEqual(response.status_code, 400)
                self.assertIn("invalid_video_id", response.get_data(as_text=True))
        self.assertEqual(self.db.calls, [])

    def test_missing_video_is_404(self):
        response = self.post(self.ok_payload(video_id=42))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.bot.delivered(), [])

    def test_db_error_looking_up_video_is_json_500_without_details(self):
        def boom(video_id):
            raise RuntimeError("password=hidden-db-detail")
        db_manager.get_video_by_id = boom
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("hidden-db-detail", response.get_data(as_text=True))

    def test_subscription_denial_blocks_delivery_without_leaking_channel(self):
        self.db.required_channels = [{"channel_id": CHANNEL, "channel_name": "قناة الاختبار"}]
        self.bot.member_status = "left"
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 403)
        self.assertIn("subscription_required", response.get_data(as_text=True))
        self.assertNotIn("قناة الاختبار", response.get_data(as_text=True))
        self.assertEqual(self.bot.delivered(), [])
        self.assertEqual(self.db.writes(), [])
        self.assertEqual(self.subscription_calls, [(self.bot, MEMBER)])

    def test_subscription_check_exception_fails_closed(self):
        self.db.required_channels = [{"channel_id": CHANNEL, "channel_name": "قناة"}]
        self.bot.fail_get_chat_member = RuntimeError("network down token leak")
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("network down", response.get_data(as_text=True))
        self.assertEqual(self.bot.delivered(), [])

    def test_subscription_channels_lookup_error_fails_closed(self):
        def boom():
            raise RuntimeError("db down")
        helpers.get_required_channels = boom
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.bot.delivered(), [])


# ---------------------------------------------------------------------------
# Telegram failures and truthful history/view accounting
# ---------------------------------------------------------------------------

class DeliveryFailureTest(OpenVideoTestBase):
    def test_blocked_user_is_502_and_nothing_is_recorded(self):
        # copy_message مرفوض وsend_video يفشل أيضاً (المستخدم حظر البوت)
        self.bot.fail_copy = True
        self.bot.fail_send = True
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "delivery_unavailable")
        self.assert_no_secret(response)
        self.assertEqual([c[0] for c in self.bot.delivered()],
                         ["copy_message", "send_video"])
        self.assertEqual(self.db.view_calls, [])
        self.assertEqual(self.db.history_calls, [])

    def test_copy_failure_falls_back_to_stored_media_then_succeeds(self):
        self.bot.fail_copy = True
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c[0] for c in self.bot.delivered()], ["copy_message", "send_video"])
        self.assertEqual(self.bot.delivered()[1][1], MEMBER)  # الوجهة من التوقيع
        self.assertEqual(self.db.view_calls, [3])

    def test_unconfirmed_copy_falls_back_to_stored_media(self):
        class UnconfirmedCopy(DeliveryBot):
            def copy_message(self, *args):
                self.calls.append(("copy_message",) + args)
                return None

        self.bot = UnconfirmedCopy()
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c[0] for c in self.bot.delivered()],
                         ["copy_message", "send_video"])
        self.assertEqual(self.db.view_calls, [3])

    def test_history_and_views_are_written_only_after_success(self):
        self.post(self.ok_payload())
        self.assertEqual(self.db.view_calls, [3])
        self.assertEqual(self.db.history_calls, [(MEMBER, 3)])

    def test_metrics_failure_never_turns_a_sent_video_into_an_error(self):
        self.db.view_error = RuntimeError("view counter down")
        self.db.history_error = RuntimeError("history down")
        response = self.post(self.ok_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.bot.delivered()), 1)

    def test_metrics_are_not_duplicated_for_a_repeated_send_of_the_same_video(self):
        first = self.post(self.ok_payload(), now=NOW)
        later = NOW + miniapp.OPEN_VIDEO_COOLDOWN + 1
        second = self.post(self.ok_payload(), now=later)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(len(self.bot.delivered()), 2)  # الإرسال متاح بعد فترة التهدئة
        self.assertEqual(self.db.view_calls, [3])
        self.assertEqual(self.db.history_calls, [(MEMBER, 3)])

    def test_metrics_are_refreshed_after_the_window(self):
        self.post(self.ok_payload(), now=NOW)
        self.post(self.ok_payload(), now=NOW + miniapp.OPEN_VIDEO_METRIC_WINDOW + 600)
        self.assertEqual(len(self.db.view_calls), 2)


# ---------------------------------------------------------------------------
# Duplicate clicks / concurrency
# ---------------------------------------------------------------------------

class DuplicateClickTest(OpenVideoTestBase):
    def test_second_request_with_same_request_id_does_not_resend(self):
        request_id = "req-abc-123"
        first = self.post(self.ok_payload(request_id=request_id))
        second = self.post(self.ok_payload(request_id=request_id))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json().get("duplicate"))
        self.assertEqual(len(self.bot.delivered()), 1)
        self.assertEqual(self.db.view_calls, [3])

    def test_cooldown_blocks_a_different_request_id_within_the_window(self):
        first = self.post(self.ok_payload(request_id="a"), now=NOW)
        second = self.post(self.ok_payload(request_id="b"), now=NOW + 1)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers["Cache-Control"], "no-store")
        self.assertEqual(len(self.bot.delivered()), 1)

    def test_retry_after_cooldown_delivers_normally(self):
        self.post(self.ok_payload(request_id="a"), now=NOW)
        later = NOW + miniapp.OPEN_VIDEO_COOLDOWN + 1
        response = self.post(self.ok_payload(request_id="b"), now=later)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.bot.delivered()), 2)

    def test_failed_send_can_be_retried_immediately(self):
        # إبقاء النسخ مرفوضاً يجعل المحاولتين تمران عبر send_video فعلاً.
        self.bot.fail_copy = True
        self.bot.fail_send = True
        self.assertEqual(self.post(self.ok_payload(request_id="r1"), now=NOW).status_code, 502)
        self.bot.fail_send = False
        response = self.post(self.ok_payload(request_id="r1"), now=NOW + 1)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(self.db.view_calls, [3])
        self.assertEqual(len([c for c in self.bot.calls if c[0] == "send_video"]), 2)

    def test_concurrent_clicks_send_exactly_once(self):
        results = []
        errors = []
        result_lock = threading.Lock()
        entered = threading.Event()
        gate = threading.Event()
        original = self.bot.copy_message

        def slow_copy(*args, **kwargs):
            entered.set()
            gate.wait(2.0)
            return original(*args, **kwargs)

        self.bot.copy_message = slow_copy
        app = self.make_app()
        clients = [app.test_client() for _ in range(5)]

        def worker(index, client):
            try:
                response = client.post("/app/api/open-video",
                                       json=self.ok_payload(request_id=f"click-{index}"))
                with result_lock:
                    results.append(response.status_code)
            except Exception as exc:  # لا تختفي أخطاء الخيوط خلف نتيجة جزئية
                with result_lock:
                    errors.append(f"{type(exc).__name__}: {exc}")

        with mock.patch.object(miniapp, "_now", return_value=NOW):
            first = threading.Thread(target=worker, args=(0, clients[0]))
            first.start()
            self.assertTrue(entered.wait(2.0), "first delivery never started")
            others = [threading.Thread(target=worker, args=(i, c))
                      for i, c in enumerate(clients[1:], start=1)]
            threads = [first] + others
            try:
                for thread in others:
                    thread.start()
                for thread in others:
                    thread.join(5)
            finally:
                gate.set()
                for thread in threads:
                    thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [200, 429, 429, 429, 429])
        self.assertEqual(len(self.bot.delivered()), 1)
        self.assertEqual([c for c in self.bot.delivered()][0][1], MEMBER)
        self.assertEqual(self.db.view_calls, [3])

    def test_request_id_is_bounded_and_optional(self):
        for request_id in ("x" * 500, {"a": 1}, True, "bad id", "bad/slash", "ط"):
            with self.subTest(request_id=request_id):
                response = self.post(self.ok_payload(request_id=request_id))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["error"], "invalid_request_id")
        self.assertEqual(self.bot.delivered(), [])
        self.assertEqual(self.post(self.ok_payload(request_id="ok-1")).status_code, 200)
        self.assertLessEqual(miniapp._open_video_guard_size(), miniapp.OPEN_VIDEO_MAX_ENTRIES)

    def test_capacity_guard_never_evicts_a_pending_send(self):
        with mock.patch.object(miniapp, "OPEN_VIDEO_MAX_ENTRIES", 2):
            self.assertEqual(miniapp._reserve_send(MEMBER, 3, "a", NOW),
                             ("send", True))
            self.assertEqual(miniapp._reserve_send(MEMBER, 4, "b", NOW),
                             ("send", True))
            self.assertEqual(miniapp._reserve_send(MEMBER, 5, "c", NOW),
                             ("cooldown", True))
            self.assertEqual(miniapp._reserve_send(MEMBER, 3, "retry", NOW),
                             ("in_flight", True))
            self.assertEqual(miniapp._open_video_guard_size(), 2)

    def test_metric_claims_stay_bounded_and_keep_recent_window(self):
        with mock.patch.object(miniapp, "OPEN_VIDEO_MAX_ENTRIES", 2):
            self.assertTrue(miniapp._claim_metrics(MEMBER, 3, NOW))
            self.assertTrue(miniapp._claim_metrics(MEMBER, 4, NOW))
            self.assertTrue(miniapp._claim_metrics(MEMBER, 5, NOW))
            self.assertEqual(miniapp._open_video_guard_size(), 2)
            self.assertFalse(miniapp._claim_metrics(MEMBER, 4, NOW + 1))
            self.assertEqual(miniapp._open_video_guard_size(), 2)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

class WiringTest(OpenVideoTestBase):
    def test_missing_bot_fails_explicitly_without_fake_success(self):
        response = self.post(self.ok_payload(), bot=None)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"], "delivery_unavailable")
        self.assertEqual(self.db.calls, [("get_video_by_id", 3)])
        self.assertEqual(self.bot.calls, [])

    def test_webhook_bot_passes_its_bot_into_the_open_video_route(self):
        calls = []
        app = Flask("wiring")

        def fake_register(app_, bot=None, **kwargs):
            calls.append(bot)
            return True

        with mock.patch.object(miniapp, "register_miniapp", fake_register):
            sys.modules.pop("webhook_bot", None)
            import importlib
            with support.env(**BASE_ENV, DATABASE_URL="postgresql://u:p@localhost/db",
                             CHANNEL_ID="-1001234567890", ADMIN_IDS="900"):
                module = importlib.import_module("webhook_bot")
        self.assertEqual(calls, [module.bot])

    def test_endpoint_is_post_only(self):
        app = self.make_app()
        self.assertEqual(app.test_client().get("/app/api/open-video").status_code, 405)

    def test_route_is_registered(self):
        app = self.make_app()
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertIn("/app/api/open-video", rules)


if __name__ == "__main__":
    unittest.main()
