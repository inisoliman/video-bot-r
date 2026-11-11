"""Offline tests for the Telegram Mini App (miniapp.py) and its bot entry points.

No network: tests/support.py blocks telebot HTTP and psycopg2, and every DB call
is a fake injected into ``db_manager``.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode (`python -m unittest discover -s tests` with tests/__init__.py)
    from . import support
except ImportError:  # top-level mode
    import support

import hashlib
import hmac
import importlib
import json
import logging
import unittest
from types import SimpleNamespace
from urllib.parse import quote
from unittest import mock

from flask import Flask
from telebot import types

import db_manager
import miniapp
from handlers import commands as command_menu
from handlers import helpers, user_handlers

BOT_TOKEN = "123456:SECRET-BOT-TOKEN"
APP_URL = "https://example.onrender.com:8443"
APP_ORIGIN = "example.onrender.com"
NOW = 1_700_000_000  # وقت ثابت للاختبارات (auth_date)
BASE_ENV = {
    "BOT_TOKEN": BOT_TOKEN,
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
    "CHANNEL_ID": "-1001234567890",
    "ADMIN_IDS": f"{support.ADMIN_ID},{support.OTHER_ADMIN_ID}",
    "APP_URL": APP_URL,
}


# ---------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------

def init_data(bot_token, user_id=support.MEMBER_ID, auth_date=NOW, extra=None):
    """بناء initData صالح تماماً وفق مواصفة Telegram WebApp."""
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "مستخدم", "username": "member"},
                           ensure_ascii=False),
    }
    fields.update(extra or {})
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return "&".join([f"{key}={quote(fields[key], safe='')}" for key in sorted(fields)]
                    + [f"hash={signature}"])


def video_row(video_id=1, caption="سفينة الصحراء", display_title=None, category_id=1,
              category_name=None, view_count=12, thumbnail="thumb-file-id",
              duration=754, file_id="FILE-ID-MUST-NOT-LEAK", chat_id="-100999"):
    return {
        "id": video_id,
        "message_id": 500 + video_id,
        "chat_id": chat_id,
        "file_id": file_id,
        "file_name": f"video-{video_id}.mp4",
        "caption": caption,
        "display_title": display_title,
        "metadata": {"duration": duration},
        "view_count": view_count,
        "thumbnail_file_id": thumbnail,
        "category_id": category_id,
        "category_name": category_name,
    }


class FakeTelebotApi:
    """Stand-in for db_manager's read/write functions; records every call."""

    def __init__(self):
        self.calls = []
        self.videos = [video_row(i, caption=f"فيلم {i}") for i in range(1, 6)]
        self.page_size = 5
        self.search_total = 30
        self.categories = [
            {"id": 1, "name": "أفلام", "parent_id": None, "count": 4},
            {"id": 2, "name": "<b>مسلسلات</b>", "parent_id": None, "count": 1},
        ]
        self.video_detail = video_row(1, category_name="أفلام")
        self.rating = {"avg": 4.25, "count": 8}
        self.thumb_file_id = "thumb-file-id"
        self.favorites = {support.MEMBER_ID: set()}

    # reads ---------------------------------------------------------------
    def search_videos(self, query, page=0, category_id=None, quality=None, status=None):
        self.calls.append(("search_videos", query, page, category_id))
        return self.videos[: self.page_size], self.search_total

    def get_videos(self, category_id, page=0):
        self.calls.append(("get_videos", category_id, page))
        return self.videos[: self.page_size], self.search_total

    def get_categories_tree(self):
        self.calls.append(("get_categories_tree",))
        return [dict(row) for row in self.categories]

    def get_video_by_id(self, video_id):
        self.calls.append(("get_video_by_id", video_id))
        return video_row(video_id) if video_id <= 5 else None

    def get_miniapp_categories(self):
        self.calls.append(("get_miniapp_categories",))
        return [dict(row) for row in self.categories]

    def get_miniapp_video(self, video_id):
        self.calls.append(("get_miniapp_video", video_id))
        if video_id > 5:
            return None
        row = dict(self.video_detail)
        row["id"] = video_id
        return row

    def get_miniapp_video_thumbnail(self, video_id):
        self.calls.append(("get_miniapp_video_thumbnail", video_id))
        return self.thumb_file_id if video_id <= 5 else None

    def get_video_rating_stats(self, video_id):
        self.calls.append(("get_video_rating_stats", video_id))
        return dict(self.rating)

    # writes --------------------------------------------------------------
    def add_to_favorites(self, user_id, video_id):
        self.calls.append(("add_to_favorites", user_id, video_id))
        self.favorites.setdefault(user_id, set()).add(video_id)
        return True

    def remove_from_favorites(self, user_id, video_id):
        self.calls.append(("remove_from_favorites", user_id, video_id))
        self.favorites.setdefault(user_id, set()).discard(video_id)
        return True

    def writes(self):
        return [c for c in self.calls if c[0] in ("add_to_favorites", "remove_from_favorites")]

    def kind(self, name):
        return [c for c in self.calls if c[0] == name]


FAKE_NAMES = (
    "search_videos", "get_videos", "get_categories_tree", "get_video_by_id",
    "get_miniapp_categories", "get_miniapp_video", "get_miniapp_video_thumbnail",
    "get_video_rating_stats", "add_to_favorites", "remove_from_favorites",
)

# الدوال الأصلية تُستعاد بعد كل عينة بدقة (بلا mock.patch)
_REAL_DB = {name: getattr(db_manager, name) for name in FAKE_NAMES}


class swapped_db:
    """استبدال مؤقت لدوال db_manager وإرجاعها للأصل عند الخروج.

    نستخدم setattr/restore بدل mock.patch لأن replace_all خاطئاً («غير موجود =
    غياب») يخفي الانحدارات؛ هنا الاستعادة صريحة ولن تتحول دالة إلى MagicMock.
    """

    def __init__(self, **overrides):
        self.overrides = overrides

    def start(self):
        for name, value in self.overrides.items():
            setattr(db_manager, name, value)
        return self

    def stop(self):
        for name in self.overrides:
            setattr(db_manager, name, _REAL_DB[name])

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.stop()
        return False


class MiniAppTestBase(support.OfflineTestCase):
    env_values = dict(BASE_ENV)

    def setUp(self):
        super().setUp()
        self.fake = FakeTelebotApi()
        patcher = mock.patch.dict(os.environ, self.env_values)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in FAKE_NAMES:
            setattr(db_manager, name, getattr(self.fake, name))
            self.addCleanup(setattr, db_manager, name, _REAL_DB.get(name))
        self.addCleanup(miniapp.set_hourly_limit_provider, None)
        miniapp._OPEN_COUNTERS.clear()
        miniapp._READ_LOCKS.clear()

    def make_app(self):
        app = Flask("miniapp-test")
        app.config["TESTING"] = True
        self.assertTrue(miniapp.register_miniapp(app))
        return app

    @property
    def client(self):
        if not hasattr(self, "_client"):
            self._client = self.make_app().test_client()
        return self._client

    def post_favorite(self, payload, env_values=None):
        app = self.make_app()
        values = {k: v for k, v in os.environ.items() if not k.startswith("MINIAPP_")}
        values.update(self.env_values)
        values.update(env_values or {})
        with mock.patch.dict(os.environ, values, clear=True):
            return app.test_client().post("/app/api/favorite", json=payload)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

class PageTest(MiniAppTestBase):
    def test_page_is_rtl_arabic_html_without_secrets_or_cdn(self):
        with mock.patch.dict(os.environ, {"MINIAPP_BOT_USERNAME": ""}):
            response = self.client.get("/app")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["Content-Type"])
        body = response.get_data(as_text=True)
        self.assertIn('dir="rtl"', body)
        self.assertIn('lang="ar"', body)
        self.assertIn("مكتبة الفيديو", body)
        self.assertIn("@media (prefers-color-scheme: light)", body)
        # الصفحة تقرأ SDK تيليجرام الرسمي (يحقنه تيليجرام داخل WebView، لا نتحايل عليه)
        self.assertIn("window.Telegram && window.Telegram.WebApp", body)
        self.assertIn("tg.close()", body)
        self.assertIn('fetch("/app/api/library', body)
        self.assertIn("/app/api/favorite", body)
        self.assertIn("/app/api/open-video", body)
        # لا أسرار ولا موارد خارجية
        self.assertNotIn("SECRET-BOT-TOKEN", body)
        self.assertNotIn("http://", body)
        self.assertNotIn("https://", body)
        self.assertNotIn("//cdn", body)
        self.assertNotIn("<script src", body)

    def test_page_never_embeds_file_id_or_chat_id(self):
        body = self.client.get("/app").get_data(as_text=True)
        self.assertNotIn("FILE-ID-MUST-NOT-LEAK", body)
        self.assertNotIn("-100999", body)

    def test_bot_deep_link_is_validated_before_embedding(self):
        with mock.patch.dict(os.environ, {"MINIAPP_BOT_USERNAME": 'evil";alert(1)//'}):
            body = miniapp.render_page()
        self.assertNotIn("alert(1)", body)
        with mock.patch.dict(os.environ, {"MINIAPP_BOT_USERNAME": "good_bot"}):
            body = miniapp.render_page()
        self.assertIn('"https://t.me/good_bot"', body)

    def test_html_is_stable_without_request_data(self):
        first = self.client.get("/app?x=<script>").get_data(as_text=True)
        self.assertNotIn("<script>x", first)

    def test_page_response_headers(self):
        response = self.client.get("/app")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("private", response.headers["Cache-Control"])


# ---------------------------------------------------------------------------
# Library API
# ---------------------------------------------------------------------------

class LibraryApiTest(MiniAppTestBase):
    def setUp(self):
        super().setUp()
        self.fake.search_total = 40
        # حجم الصفحة من طبقة قاعدة البيانات نفسها (انظر test_miniapp_pagination)
        self.fake.page_size = db_manager.VIDEOS_PER_PAGE
        self.fake.videos = [video_row(i, caption=f"فيلم {i}", category_name="أفلام")
                            for i in range(1, self.fake.page_size + 1)]

    def test_library_shape_hides_file_id_and_chat_id(self):
        response = self.client.get("/app/api/library")
        self.assertEqual(response.status_code, 200)
        # نتائج مقطّعة: لا تُخدَم من كاش وسيط مشترك
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        payload = response.get_json()
        self.assertEqual(sorted(payload), ["has_more", "items", "page", "page_size", "total"])
        self.assertEqual(payload["page"], 0)
        self.assertEqual(payload["page_size"], miniapp.LIBRARY_PAGE_SIZE)
        self.assertEqual(payload["total"], 40)
        item = payload["items"][0]
        self.assertEqual(sorted(item),
                         ["category", "duration", "id", "thumbnail_url", "title", "views"])
        self.assertEqual(item["id"], 1)
        self.assertEqual(item["thumbnail_url"], "/app/thumb/1")
        self.assertEqual(item["duration"], "12:34")
        self.assertEqual(item["views"], 12)
        self.assertEqual(item["category"], "أفلام")  # من JOIN categories في الاستعلام
        body = response.get_data(as_text=True)
        for secret in ("file_id", "chat_id", "FILE-ID-MUST-NOT-LEAK", "-100999",
                       "thumbnail_file_id", "message_id", "SECRET-BOT-TOKEN"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, body)

    def test_display_title_wins_over_caption(self):
        self.fake.videos[0] = video_row(1, caption="caption text", display_title="عنوان معدّل")
        payload = self.client.get("/app/api/library").get_json()
        self.assertEqual(payload["items"][0]["title"], "عنوان معدّل")

    def test_category_names_come_from_categories_tree(self):
        self.fake.videos[0]["category_name"] = None
        self.fake.videos[0]["category_id"] = 2
        payload = self.client.get("/app/api/library").get_json()
        self.assertEqual(payload["items"][0]["category"], "<b>مسلسلات</b>")
        self.assertTrue(self.fake.kind("get_categories_tree"))

    def test_category_name_from_join_wins(self):
        self.fake.videos[0]["category_name"] = "أفلام"
        payload = self.client.get("/app/api/library").get_json()
        self.assertEqual(payload["items"][0]["category"], "أفلام")
        self.assertEqual(self.fake.kind("get_categories_tree"), [])

    def test_search_uses_search_videos(self):
        response = self.client.get("/app/api/library?query=%3Cb%3E%D9%81%D9%8A%D9%84%D9%85")
        self.assertEqual(response.status_code, 200)
        search = self.fake.kind("search_videos")[0]
        self.assertEqual(search[1], "<b>فيلم")
        self.assertEqual(search[2], 0)

    def test_category_filter_uses_get_videos(self):
        response = self.client.get("/app/api/library?category_id=2&page=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake.kind("get_videos"), [("get_videos", 2, 1)])
        self.assertEqual(self.fake.kind("search_videos"), [])

    def test_invalid_category_is_rejected_without_db_access(self):
        response = self.client.get("/app/api/library?category_id=1;DROP")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.fake.calls, [])

    def test_has_more_false_when_total_equals_page(self):
        # صفحة واحدة كاملة = لا زر «التالي»
        self.fake.search_total = db_manager.VIDEOS_PER_PAGE
        payload = self.client.get("/app/api/library?page=0").get_json()
        self.assertEqual(len(payload["items"]), db_manager.VIDEOS_PER_PAGE)
        self.assertFalse(payload["has_more"])

    def test_has_more_true_when_more_pages_exist(self):
        payload = self.client.get("/app/api/library?page=0").get_json()
        self.assertTrue(payload["has_more"])
        payload = self.client.get("/app/api/library?page=1").get_json()
        self.assertTrue(payload["has_more"])  # 10 + 10 < 40

    def test_has_more_false_on_last_page(self):
        payload = self.client.get("/app/api/library?page=3").get_json()
        self.assertEqual(payload["page"], 3)
        self.assertFalse(payload["has_more"])  # 3*10 + 10 = 40 >= 40

    def test_short_last_page_never_reports_more(self):
        # صفحة أخيرة قصيرة داخل النطاق (Total أكبر من ما وُجلب فعلاً)
        self.fake.search_total = 12
        self.fake.videos = self.fake.videos[:2]
        payload = self.client.get("/app/api/library?page=1").get_json()
        self.assertFalse(payload["has_more"])

    def test_query_is_bounded(self):
        self.client.get("/app/api/library?query=" + "x" * 500)
        search = self.fake.kind("search_videos")[0]
        self.assertEqual(len(search[1]), miniapp.MAX_QUERY_LENGTH)

    def test_db_failure_is_json_500_without_details(self):
        def boom(*args, **kwargs):
            raise RuntimeError("password=hidden-db-detail")

        with mock.patch.object(db_manager, "search_videos", boom):
            response = self.client.get("/app/api/library")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "server_error"})
        self.assertNotIn("hidden-db-detail", response.get_data(as_text=True))


# ---------------------------------------------------------------------------
# Categories and single video APIs
# ---------------------------------------------------------------------------

class CategoriesAndVideoApiTest(MiniAppTestBase):
    def test_categories_shape(self):
        response = self.client.get("/app/api/categories")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["items"][0], {"id": 1, "name": "أفلام", "count": 4})
        self.assertEqual(sorted(payload["items"][1]), ["count", "id", "name"])

    def test_categories_fall_back_to_tree_without_counts(self):
        lookup = self.fake.get_miniapp_categories
        del db_manager.get_miniapp_categories  # بنية أقدم: لا دالة عدّ مخصصة
        try:
            response = self.client.get("/app/api/categories")
        finally:
            db_manager.get_miniapp_categories = lookup
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["items"],
                         [{"id": 1, "name": "أفلام", "count": 0},
                          {"id": 2, "name": "<b>مسلسلات</b>", "count": 0}])

    def test_video_detail_has_rating_and_excerpt_without_secrets(self):
        response = self.client.get("/app/api/video/1")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["id"], 1)
        self.assertEqual(payload["rating"], {"avg": 4.25, "count": 8})
        self.assertEqual(payload["views"], 12)
        self.assertEqual(payload["category"], "أفلام")
        self.assertLessEqual(len(payload["caption"]), 400)
        body = response.get_data(as_text=True)
        self.assertNotIn("file_id", body)
        self.assertNotIn("chat_id", body)
        self.assertNotIn("FILE-ID-MUST-NOT-LEAK", body)

    def test_unknown_video_is_404(self):
        response = self.client.get("/app/api/video/99")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["Cache-Control"], "no-store")


# ---------------------------------------------------------------------------
# Thumbnail proxy
# ---------------------------------------------------------------------------

class ThumbnailProxyTest(MiniAppTestBase):
    def test_known_video_redirects_to_existing_thumbnail_route(self):
        response = self.client.get("/app/thumb/1")
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertTrue(location.startswith("/thumbnail/"), location)
        self.assertTrue(location.endswith(quote("thumb-file-id", safe="")))
        self.assertNotIn("FILE-ID-MUST-NOT-LEAK", response.get_data(as_text=True))

    def test_unknown_video_is_404_without_body(self):
        response = self.client.get("/app/thumb/99")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_data(as_text=True).strip(), "")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_missing_thumbnail_is_404_without_body(self):
        self.fake.thumb_file_id = None
        response = self.client.get("/app/thumb/1")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_data(as_text=True).strip(), "")

    def test_falls_back_when_storage_free_lookup_is_absent(self):
        # محاكاة بنية أقدم: الدالة غائبة (غياب الخاصية لا يُغطّى بـ MagicMock)
        from types import SimpleNamespace

        lookup = self.fake.get_miniapp_video_thumbnail
        del db_manager.get_miniapp_video_thumbnail
        try:
            response = self.client.get("/app/thumb/1")
        finally:
            db_manager.get_miniapp_video_thumbnail = lookup
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].startswith("/thumbnail/"))
        self.assertTrue(response.headers["Location"].endswith("thumb-file-id"))
        self.assertEqual(self.fake.kind("get_video_by_id"), [("get_video_by_id", 1)])

    def test_fallback_still_404s_for_unknown_video(self):
        lookup = self.fake.get_miniapp_video_thumbnail
        del db_manager.get_miniapp_video_thumbnail
        try:
            response = self.client.get("/app/thumb/99")
        finally:
            db_manager.get_miniapp_video_thumbnail = lookup
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# initData signature and favorites
# ---------------------------------------------------------------------------

class InitDataTest(MiniAppTestBase):
    def test_module_validator_accepts_valid_signature(self):
        verified = miniapp.validate_init_data(init_data(BOT_TOKEN), bot_token=BOT_TOKEN, now=NOW)
        self.assertIsNotNone(verified)
        self.assertEqual(verified["user"]["id"], support.MEMBER_ID)

    def test_module_validator_rejects_changed_auth_date(self):
        forged = init_data(BOT_TOKEN) + "&auth_date=%d" % (NOW + 1)
        self.assertIsNone(miniapp.validate_init_data(forged, bot_token=BOT_TOKEN, now=NOW))

    def test_expired_and_future_auth_date_are_rejected(self):
        stale = init_data(BOT_TOKEN, auth_date=NOW - 24 * 3600 - 1)
        self.assertIsNone(miniapp.validate_init_data(stale, bot_token=BOT_TOKEN, now=NOW))
        fresh = init_data(BOT_TOKEN, auth_date=NOW - 23 * 3600)
        self.assertIsNotNone(miniapp.validate_init_data(fresh, bot_token=BOT_TOKEN, now=NOW))
        future = init_data(BOT_TOKEN, auth_date=NOW + 3600)
        self.assertIsNone(miniapp.validate_init_data(future, bot_token=BOT_TOKEN, now=NOW))

    def test_garbage_initdata_is_rejected(self):
        for value in ("", "hash=abc", "a=b&hash=", "no-equals-sign", "user=%ZZ&hash=ab",
                      None):
            with self.subTest(value=value):
                self.assertIsNone(miniapp.validate_init_data(value, bot_token=BOT_TOKEN, now=NOW))
        with self.assertRaises(AttributeError):
            # الأنواع غير النصية لا تصل هنا: المسار يرفضها قبل التحقق
            miniapp.validate_init_data(12, bot_token=BOT_TOKEN, now=NOW)

    def test_duplicate_hash_is_rejected(self):
        valid = init_data(BOT_TOKEN)
        self.assertIsNone(miniapp.validate_init_data(valid + "&hash=deadbeef",
                                                     bot_token=BOT_TOKEN, now=NOW))

    def test_wrong_bot_token_is_rejected(self):
        self.assertIsNone(miniapp.validate_init_data(init_data("1:other"), bot_token=BOT_TOKEN,
                                                     now=NOW))


class FavoriteApiTest(MiniAppTestBase):
    def favorite(self, payload, env_values=None, now=NOW):
        with mock.patch.object(miniapp, "_now", return_value=now):
            return self.post_favorite(payload, env_values)

    def test_valid_init_data_adds_favorite_for_signed_user(self):
        response = self.favorite({"video_id": 2, "action": "add",
                                  "initData": init_data(BOT_TOKEN, user_id=support.MEMBER_ID)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ok"], True)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.fake.writes(), [("add_to_favorites", support.MEMBER_ID, 2)])

    def test_remove_uses_signed_user(self):
        self.fake.favorites[support.MEMBER_ID].add(3)
        response = self.favorite({"video_id": 3, "action": "remove",
                                  "initData": init_data(BOT_TOKEN, user_id=support.MEMBER_ID)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake.writes(), [("remove_from_favorites", support.MEMBER_ID, 3)])

    def test_client_supplied_user_id_is_ignored(self):
        response = self.favorite({"video_id": 2, "action": "add",
                                  "user_id": support.OTHER_MEMBER_ID,
                                  "initData": init_data(BOT_TOKEN, user_id=support.MEMBER_ID)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake.writes(), [("add_to_favorites", support.MEMBER_ID, 2)])

    def test_tampered_signature_is_401_and_writes_nothing(self):
        valid = init_data(BOT_TOKEN)
        tampered = valid[:-1] + ("0" if valid[-1] != "0" else "1")
        response = self.favorite({"video_id": 2, "action": "add", "initData": tampered})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_initdata_for_another_bot_is_rejected(self):
        other = init_data("999999:OTHER-TOKEN", user_id=support.OTHER_MEMBER_ID)
        response = self.favorite({"video_id": 2, "action": "add", "initData": other})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.fake.writes(), [])

    def test_stale_auth_date_is_401_and_writes_nothing(self):
        stale = init_data(BOT_TOKEN, auth_date=NOW - 24 * 3600 - 5)
        response = self.favorite({"video_id": 2, "action": "add",
                                  "initData": stale}, now=NOW + 60)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.fake.writes(), [])

    def test_missing_init_data_is_401(self):
        response = self.favorite({"video_id": 2, "action": "add"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.fake.writes(), [])

    def test_non_string_init_data_is_401(self):
        response = self.favorite({"video_id": 2, "action": "add", "initData": 12345})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.fake.writes(), [])

    def test_invalid_action_and_video_are_400(self):
        data = init_data(BOT_TOKEN)
        for payload in ({"video_id": 2, "action": "toggle", "initData": data},
                        {"video_id": "abc", "action": "add", "initData": data},
                        {"action": "add", "initData": data}):
            with self.subTest(payload=payload):
                response = self.favorite(payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.fake.writes(), [])

    def test_unknown_video_after_auth_is_404(self):
        response = self.favorite({"video_id": 99, "action": "add",
                                  "initData": init_data(BOT_TOKEN)})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.fake.writes(), [])

    def test_no_bot_token_configured_fails_closed(self):
        # لا BOT_TOKEN ولا MINIAPP_BOT_TOKEN: الرفض آمن (401) بلا أي كتابة
        values = {k: v for k, v in os.environ.items() if not k.startswith("MINIAPP_")}
        values.pop("BOT_TOKEN", None)
        app = self.make_app()
        with mock.patch.dict(os.environ, values, clear=True), \
                mock.patch.object(miniapp, "_now", return_value=NOW):
            response = app.test_client().post(
                "/app/api/favorite",
                json={"video_id": 2, "action": "add", "initData": init_data(BOT_TOKEN)})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.fake.writes(), [])

    def test_miniapp_bot_token_overrides_environment(self):
        env = {"APP_URL": APP_URL, "MINIAPP_BOT_TOKEN": BOT_TOKEN}
        response = self.favorite({"video_id": 2, "action": "add",
                                  "initData": init_data(BOT_TOKEN)}, env_values=env)
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Registration, caching policy, hourly limit
# ---------------------------------------------------------------------------

class RouteRegistrationTest(MiniAppTestBase):
    def test_registering_twice_does_not_duplicate_routes(self):
        app = Flask("miniapp-twice")
        self.assertTrue(miniapp.register_miniapp(app))
        self.assertFalse(miniapp.register_miniapp(app))

    def test_webhook_bot_import_registers_miniapp_routes(self):
        module = fresh_webhook_module()
        rules = {rule.rule for rule in module.app.url_map.iter_rules()}
        for expected in ("/app", "/app/api/library", "/app/api/categories",
                         "/app/api/favorite", "/app/api/video/<int:video_id>",
                         "/app/thumb/<int:video_id>"):
            with self.subTest(route=expected):
                self.assertIn(expected, rules)

    def test_favorite_route_is_never_cached(self):
        response = self.client.post("/app/api/favorite", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_favorite_route_works_with_the_client_fixture(self):
        with mock.patch.object(miniapp, "_now", return_value=NOW):
            response = self.client.post(
                "/app/api/favorite",
                json={"video_id": 1, "action": "add", "initData": init_data(BOT_TOKEN)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake.writes(), [("add_to_favorites", support.MEMBER_ID, 1)])

    def test_read_routes_are_get_only(self):
        self.assertEqual(self.client.post("/app").status_code, 405)
        self.assertEqual(self.client.post("/app/api/library").status_code, 405)

    def test_hourly_limit_is_off_by_default(self):
        for _ in range(5):
            self.assertEqual(self.client.get("/app").status_code, 200)

    def test_hourly_limit_provider_can_cap_page_loads(self):
        app = Flask("miniapp-limit")
        calls = []
        miniapp.register_miniapp(app, hourly_limit_provider=lambda: calls.append(1) or 1)
        client = app.test_client()
        self.assertEqual(client.get("/app").status_code, 200)
        self.assertEqual(client.get("/app").status_code, 429)
        # واجهات القراءة لا تتأثر بالحد
        self.assertEqual(client.get("/app/api/library").status_code, 200)


def fresh_webhook_module(extra_env=None):
    """Import a fresh ``webhook_bot`` with an isolated environment."""
    sys.modules.pop("webhook_bot", None)
    values = dict(BASE_ENV)
    values.update(extra_env or {})
    with support.env(**values):
        return importlib.import_module("webhook_bot")


# ---------------------------------------------------------------------------
# Bot entry points
# ---------------------------------------------------------------------------

class RecordingBot:
    """Fake bot recording message_handler registrations and their callbacks."""

    def __init__(self):
        self.registered = {}
        self.calls = []

    def message_handler(self, *args, **kwargs):
        names = kwargs.get("commands") or ()

        def decorator(func):
            for name in names:
                self.registered[name] = func
            return func
        return decorator

    def reply_to(self, message, text, **kwargs):
        self.calls.append(("reply_to", message.chat.id, text, kwargs))
        return SimpleNamespace(message_id=1, chat=message.chat)

    def send_message(self, chat_id, text, **kwargs):
        self.calls.append(("send_message", chat_id, text, kwargs))
        return SimpleNamespace(message_id=2, chat=support.make_chat(chat_id))

    def replies(self):
        return [(c[2], c[3]) for c in self.calls if c[0] == "reply_to"]


def library_buttons(markup):
    return [button for row in markup.keyboard for button in row
            if getattr(button, "web_app", None) is not None]


QUIET_DB_VALUES = {
    "get_user_state": None,           # لا حالة معلّقة
    "clear_user_state": True,
    "set_user_state": True,
    "add_bot_user": True,
    "get_required_channels": [],      # لا قنوات اشتراك إلزامية
    "get_bot_setting": "0",           # لا حدود
    "set_bot_setting": True,
    "get_user_favorites": ([], 0),
    "get_user_history": ([], 0),
    "get_categories_tree": [],
}


class QuietDbMixin:
    """يستبدل دوال db_manager (والمراجع المستوردة في user_handlers) بلا أي شبكة."""

    def quiet_db(self):
        for name, value in QUIET_DB_VALUES.items():
            for module in (db_manager, user_handlers):
                if hasattr(module, name):
                    original = getattr(module, name)
                    setattr(module, name, (lambda v: (lambda *a, **k: v))(value))
                    self.addCleanup(setattr, module, name, original)


class LibraryCommandTest(QuietDbMixin, support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.bot = RecordingBot()
        self.quiet_db()
        with support.env(**BASE_ENV):
            user_handlers.register(self.bot, "-1001234567890",
                                   [support.ADMIN_ID, support.OTHER_ADMIN_ID])
        self.handler = self.bot.registered["library"]

    def send(self, text="/library", chat_type="private"):
        message = support.make_message(text=text, user_id=support.MEMBER_ID,
                                       chat_type=chat_type, chat_id=support.MEMBER_ID)
        with support.env(**BASE_ENV):
            self.handler(message)
        return self.bot.replies()

    def test_https_app_url_sends_single_web_app_button(self):
        replies = self.send()
        self.assertEqual(len(replies), 1)
        text, kwargs = replies[0]
        self.assertIn("مكتبة", text)
        markup = kwargs.get("reply_markup")
        self.assertIsNotNone(markup)
        buttons = [b for row in markup.keyboard for b in row]
        self.assertEqual(len(buttons), 1, "Telegram rejects web_app buttons mixed with others")
        button = buttons[0]
        self.assertIsInstance(button, types.InlineKeyboardButton)
        self.assertEqual(button.text, user_handlers.LIBRARY_BUTTON_TEXT)
        self.assertIsInstance(button.web_app, types.WebAppInfo)
        self.assertTrue(button.web_app.url.startswith(f"{APP_URL}/app?"))
        self.assertIn("tgWebAppData=", button.web_app.url)
        self.assertLessEqual(len(button.web_app.url), 512)
        self.assertIsNone(button.callback_data)
        serialized = button.to_dict()
        self.assertNotIn("style", serialized)
        self.assertEqual(serialized["web_app"]["url"], button.web_app.url)

    def test_http_app_url_sends_plain_text_and_no_button(self):
        with support.env(**dict(BASE_ENV, APP_URL="http://insecure.example")):
            self.assertIsNone(user_handlers.library_webapp_url())
            self.assertIsNone(user_handlers.build_library_markup())
        message = support.make_message(text="/library", user_id=support.MEMBER_ID)
        with support.env(**dict(BASE_ENV, APP_URL="http://insecure.example")):
            self.handler(message)
        replies = self.bot.replies()
        self.assertEqual(len(replies), 1)
        text, kwargs = replies[0]
        self.assertNotIn("reply_markup", kwargs)
        self.assertIn("غير متاحة", text)
        self.assertNotIn("https", text)

    def test_missing_app_url_is_plain_text(self):
        message = support.make_message(text="/library", user_id=support.MEMBER_ID)
        with support.env(BOT_TOKEN=BOT_TOKEN):
            self.handler(message)
        text, kwargs = self.bot.replies()[0]
        self.assertNotIn("reply_markup", kwargs)
        self.assertIn("غير متاحة", text)

    def test_group_chat_gets_no_web_app_button(self):
        replies = self.send(chat_type="group")
        self.assertEqual(len(replies), 1)
        text, kwargs = replies[0]
        self.assertNotIn("reply_markup", kwargs)
        self.assertIn("الخاصة", text)

    def test_keyboard_button_text_opens_library(self):
        bot = RecordingBot()
        captured = {}

        def message_handler(*args, **kwargs):
            def decorator(func):
                captured.setdefault("handlers", []).append((kwargs.get("func"), func))
                return func
            return decorator

        bot.message_handler = message_handler
        with support.env(**BASE_ENV):
            user_handlers.register(bot, "-1001234567890", [support.ADMIN_ID])
        handler = [func for predicate, func in captured["handlers"]
                   if predicate is not None and predicate(
                       support.make_message(text=miniapp.MINIAPP_KEYBOARD_TEXT))][0]
        before = len(bot.calls)
        with support.env(**BASE_ENV):
            handler(support.make_message(text=miniapp.MINIAPP_KEYBOARD_TEXT,
                                         user_id=support.MEMBER_ID))
        reply = bot.calls[before]
        self.assertEqual(reply[0], "reply_to")
        buttons = [b for row in reply[3]["reply_markup"].keyboard for b in row]
        self.assertEqual(len(buttons), 1)
        self.assertIsInstance(buttons[0].web_app, types.WebAppInfo)


class StartTextTest(QuietDbMixin, support.OfflineTestCase):
    def start_text(self, env_values):
        bot = RecordingBot()
        bot.get_me = lambda: SimpleNamespace(username="video_bot")
        self.quiet_db()
        original_subscription = user_handlers.check_subscription
        user_handlers.check_subscription = lambda b, uid: (True, [])
        self.addCleanup(setattr, user_handlers, "check_subscription", original_subscription)
        with support.env(**env_values):
            user_handlers.register(bot, "-1001234567890", [support.ADMIN_ID])
            bot.registered["start"](
                support.make_message(text="/start", user_id=support.MEMBER_ID))
        return bot.replies()

    def test_start_mentions_mini_app_when_active(self):
        replies = self.start_text(BASE_ENV)
        text, _kwargs = replies[0]
        self.assertIn("/library", text)
        self.assertEqual(text.count("/library"), 1)

    def test_start_has_no_mini_app_line_without_https(self):
        replies = self.start_text(dict(BASE_ENV, APP_URL="http://nope.example"))
        text, _kwargs = replies[0]
        self.assertNotIn("/library", text)


def registered_commands_via_filters(bot):
    """نفس منطق user_handlers._registered_command_names على كائن بسيط."""
    return user_handlers._registered_command_names(bot)


def registered_commands_via_filters(bot):
    """نسخة دورية مستقلة من منطق فحص message_handlers في user_handlers.

    الشكل في pyTelegramBotAPI 4.x: قائمة قواميس {"function", "pass_bot",
    "filters"} حيث filters["commands"] قائمة أسماء أسطر.
    """
    registered = set()
    for handler in getattr(bot, "message_handlers", None) or []:
        filters = handler.get("filters") if isinstance(handler, dict) else None
        if not isinstance(filters, dict):
            continue
        for name in filters.get("commands") or []:
            registered.add(str(name).lower())
    return registered


class MainMenuTest(support.OfflineTestCase):
    def menu_texts(self, markup):
        return [button["text"] for row in json.loads(markup.to_json())["keyboard"]
                for button in row]

    def test_main_menu_has_miniapp_button_only_with_https(self):
        with support.env(**BASE_ENV):
            markup = helpers.main_menu("video_bot")
        self.assertIn(miniapp.MINIAPP_KEYBOARD_TEXT, self.menu_texts(markup))
        with support.env(APP_URL="http://insecure.example"):
            markup = helpers.main_menu("video_bot")
        self.assertNotIn(miniapp.MINIAPP_KEYBOARD_TEXT, self.menu_texts(markup))

    def test_main_menu_keeps_original_rows(self):
        with support.env(**BASE_ENV):
            markup = helpers.main_menu("video_bot")
        texts = self.menu_texts(markup)
        for expected in ("🎬 عرض كل الفيديوهات 🟢", "🔥 الفيديوهات الشائعة 🔴",
                         "⭐ المفضلة 🟡", "📺 سجل المشاهدة 🔵", "🍿 اقترح لي فيلم 🟣",
                         "🔍 بحث 🟠"):
            with self.subTest(text=expected):
                self.assertIn(expected, texts)


class CommandMenuTest(support.OfflineTestCase):
    def test_library_is_listed_and_has_a_registered_handler(self):
        names = [name for name, _d in command_menu.MEMBER_COMMANDS]
        self.assertIn("library", names)
        bot = RecordingBot()
        with support.env(**BASE_ENV):
            user_handlers.register(bot, "-1001234567890", [support.ADMIN_ID])
        self.assertIn("library", bot.registered)

    def test_library_is_dropped_when_no_handler_was_registered(self):
        # نسخة من القائمة حتى لا نمس القائمة الحقيقية
        pairs = [pair for pair in command_menu.MEMBER_COMMANDS]
        bot = SimpleNamespace(message_handlers=[{"filters": {"commands": ["start", "cancel"]}}])
        with mock.patch.object(command_menu, "MEMBER_COMMANDS", pairs):
            user_handlers._reconcile_published_menu(bot)
        self.assertNotIn("library", [name for name, _d in pairs])
        self.assertIn("search_help", [name for name, _d in pairs],
                      "commands registered elsewhere (group_handlers) are untouched")

    def test_reconcile_keeps_menu_when_bot_has_no_handler_table(self):
        pairs = [pair for pair in command_menu.MEMBER_COMMANDS]
        with mock.patch.object(command_menu, "MEMBER_COMMANDS", pairs):
            self.assertIsNone(user_handlers._reconcile_published_menu(support.FakeBot()))
        self.assertEqual(pairs, command_menu.MEMBER_COMMANDS)

    def test_reconcile_reads_real_telebot_handler_shape(self):
        from types import SimpleNamespace as NS

        bot = NS(message_handlers=[
            {"function": object(), "pass_bot": False,
             "filters": {"content_types": ["text"], "commands": ["start", "library"]}},
            {"function": object(), "pass_bot": False, "filters": {"commands": ["cancel"]}},
            {"function": object(), "pass_bot": False, "filters": {"content_types": ["text"]}},
        ])
        self.assertEqual(sorted(registered_commands_via_filters(bot)),
                         ["cancel", "library", "start"])

    def test_library_kept_when_real_registration_exposes_it(self):
        from types import SimpleNamespace as NS

        pairs = [pair for pair in command_menu.MEMBER_COMMANDS]
        bot = NS(message_handlers=[{"filters": {"commands": [name for name, _d in pairs]}}])
        with mock.patch.object(command_menu, "MEMBER_COMMANDS", pairs):
            user_handlers._reconcile_published_menu(bot)
        self.assertIn("library", [name for name, _d in pairs])

    def test_library_dropped_with_real_shape_when_missing(self):
        from types import SimpleNamespace as NS

        pairs = [pair for pair in command_menu.MEMBER_COMMANDS if pair[0] != "library"]
        bot = NS(message_handlers=[{"filters": {"commands": [name for name, _d in pairs]}},
                                   {"filters": {"commands": ["library"]}}][:0])
        with mock.patch.object(command_menu, "MEMBER_COMMANDS", pairs):
            user_handlers._reconcile_published_menu(bot)
        self.assertNotIn("library", [name for name, _d in pairs])

if __name__ == "__main__":
    unittest.main()
