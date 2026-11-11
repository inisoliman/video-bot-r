"""Pagination regression tests: the Mini App server page size must match the DB offsets.

Reproduces the defect where ``/app/api/library`` reported ``has_more`` using the
client's ``page_size`` (``LIBRARY_PAGE_SIZE`` = 12) while ``db_manager`` pages with
its own fixed ``VIDEOS_PER_PAGE`` (10). With that mismatch the same videos were
re-served, NEXT stayed enabled on the final page and the header advertised a size
that was never used.

Every DB call is faked (``tests/support.py`` blocks psycopg2 and telebot HTTP).
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

import unittest
from unittest import mock

from flask import Flask

import db_manager
import miniapp

BOT_TOKEN = "123456:PAGINATION-BOT-TOKEN"
APP_URL = "https://example.onrender.com"
BASE_ENV = {
    "BOT_TOKEN": BOT_TOKEN,
    "APP_URL": APP_URL,
}

# 25 videos → three pages of 10 / 10 / 5 with the DB page size.
VIDEO_IDS = list(range(25, 0, -1))  # ORDER BY id DESC, as db_manager returns them
CATEGORY_A = 7
CATEGORY_B = 8
CATEGORY_C = 9
CATEGORY_SET = {CATEGORY_A: [9, 5, 1], CATEGORY_B: list(range(11, 1, -1)),
                CATEGORY_C: list(range(30, 13, -1))}


def row(video_id):
    return {
        "id": video_id,
        "message_id": 1000 + video_id,
        "chat_id": "-100999",
        "file_id": "FILE-ID-MUST-NOT-LEAK",
        "file_name": f"v{video_id}.mp4",
        "caption": f"فيلم {video_id}",
        "display_title": None,
        "metadata": {"duration": 60},
        "view_count": 1,
        "thumbnail_file_id": None,
        "category_id": CATEGORY_A,
    }


def page_of(ids, page, category_id=None):
    size = db_manager.VIDEOS_PER_PAGE
    rows = []
    for video_id in ids[page * size:(page + 1) * size]:
        item = row(video_id)
        if category_id is not None:
            # get_videos يصطاف من قاعدة البيانات صفوف التصنيف المطلوب فعلاً
            item["category_id"] = category_id
        rows.append(item)
    return rows


class PaginationDb:
    """Replays the real db_manager contract: fixed VIDEOS_PER_PAGE, page * size offset."""

    def __init__(self):
        self.calls = []
        self.search_ids = VIDEO_IDS

    def search_videos(self, query, page=0, category_id=None, quality=None, status=None):
        self.calls.append(("search_videos", query, page, category_id))
        ids = self.search_ids
        if category_id is not None:
            ids = CATEGORY_SET.get(category_id, [])
        return page_of(ids, page, category_id), len(ids)

    def get_videos(self, category_id, page=0):
        self.calls.append(("get_videos", category_id, page))
        ids = CATEGORY_SET.get(category_id, [])
        return page_of(ids, page, category_id), len(ids)

    def get_categories_tree(self):
        self.calls.append(("get_categories_tree",))
        return [{"id": CATEGORY_A, "name": "أفلام"}, {"id": CATEGORY_B, "name": "مسلسلات"}]

    def get_miniapp_categories(self):
        return [{"id": CATEGORY_A, "name": "أفلام", "count": 3},
                {"id": CATEGORY_B, "name": "مسلسلات", "count": 10}]


FAKE_NAMES = ("search_videos", "get_videos", "get_categories_tree", "get_miniapp_categories")
_REAL_DB = {name: getattr(db_manager, name) for name in FAKE_NAMES}


class LibraryPaginationTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.fake = PaginationDb()
        for name in FAKE_NAMES:
            setattr(db_manager, name, getattr(self.fake, name))
            self.addCleanup(setattr, db_manager, name, _REAL_DB.get(name))
        patcher = mock.patch.dict(os.environ, BASE_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = Flask("miniapp-pagination")
        app.config["TESTING"] = True
        self.assertTrue(miniapp.register_miniapp(app))
        self.client = app.test_client()

    def get(self, query_string=""):
        response = self.client.get("/app/api/library" + query_string)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store",
                         "paged results must not be served from a shared cache")
        return response.get_json()

    def ids(self, payload):
        return [item["id"] for item in payload["items"]]

    # -- the contract itself ------------------------------------------------

    def test_page_size_is_the_database_page_size(self):
        self.assertEqual(db_manager.VIDEOS_PER_PAGE, 10)
        payload = self.get()
        self.assertEqual(payload["page_size"], db_manager.VIDEOS_PER_PAGE)
        self.assertEqual(len(payload["items"]), db_manager.VIDEOS_PER_PAGE)
        self.assertEqual(self.ids(payload), VIDEO_IDS[:10])

    def test_client_page_size_cannot_desynchronise_offsets(self):
        for requested in ("12", "5", "48", "abc", "0", "-1", "9999"):
            with self.subTest(page_size=requested):
                payload = self.get(f"?page=1&page_size={requested}")
                self.assertEqual(payload["page_size"], db_manager.VIDEOS_PER_PAGE)
                self.assertEqual(self.ids(payload), VIDEO_IDS[10:20])
        # page_size لم يُمرَّر إلى قاعدة البيانات أصلاً، لكن الصفحة الثانية تبقى صحيحة
        self.assertEqual(self.fake.calls, [("search_videos", "", 1, None)] * 7)

    def test_three_pages_never_repeat_a_video(self):
        seen = []
        flags = []
        for page in range(3):
            payload = self.get(f"?page={page}")
            self.assertEqual(payload["page"], page)
            seen.extend(self.ids(payload))
            flags.append(payload["has_more"])
        self.assertEqual(seen, VIDEO_IDS)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(flags, [True, True, False])

    def test_final_short_page_disables_next(self):
        payload = self.get("?page=2")
        self.assertEqual(self.ids(payload), VIDEO_IDS[20:])
        self.assertEqual(len(payload["items"]), 5)
        self.assertFalse(payload["has_more"])

    def test_exactly_full_last_page_disables_next(self):
        # 20 فيديو = صفحتان ممتلئتان تماماً
        self.fake.search_ids = list(range(20, 0, -1))
        self.assertTrue(self.get("?page=0")["has_more"])
        self.assertFalse(self.get("?page=1")["has_more"])

    def test_page_beyond_the_end_is_empty_and_stops(self):
        payload = self.get("?page=5")
        self.assertEqual(payload["items"], [])
        self.assertFalse(payload["has_more"])

    def test_query_is_forwarded_and_paged(self):
        payload = self.get("?query=%D9%81%D9%8A%D9%84%D9%85&page=1")
        self.assertEqual(self.fake.calls[0], ("search_videos", "فيلم", 1, None))
        self.assertEqual(self.ids(payload), VIDEO_IDS[10:20])
        self.assertTrue(payload["has_more"])

    def test_query_with_category_is_forwarded_together(self):
        # البحث كان يتجاهل category_id تماماً
        payload = self.get("?query=%D9%81%D9%8A%D9%84%D9%85&category_id=7&page=0")
        self.assertEqual(self.fake.calls[0], ("search_videos", "فيلم", 0, CATEGORY_A))
        self.assertEqual(self.ids(payload), [9, 5, 1])
        self.assertFalse(payload["has_more"])

    def test_query_with_wide_category_pages_through_its_own_total(self):
        first = self.get(f"?query=x&category_id={CATEGORY_C}&page=0")
        self.assertEqual(len(first["items"]), 10)
        self.assertEqual(first["total"], 17)
        self.assertTrue(first["has_more"])
        second = self.get(f"?query=x&category_id={CATEGORY_C}&page=1")
        self.assertEqual(len(second["items"]), 7)
        self.assertFalse(second["has_more"])

    def test_category_without_query_pages_through_get_videos(self):
        payload = self.get(f"?category_id={CATEGORY_C}&page=0")
        self.assertEqual(self.fake.calls, [("get_videos", CATEGORY_C, 0)])
        self.assertEqual(len(payload["items"]), 10)
        self.assertTrue(payload["has_more"])
        tail = self.get(f"?category_id={CATEGORY_C}&page=1")
        self.assertFalse(tail["has_more"])

    def test_small_category_has_no_next_page(self):
        payload = self.get(f"?category_id={CATEGORY_A}")
        self.assertEqual(self.ids(payload), [9, 5, 1])
        self.assertFalse(payload["has_more"])

    def test_page_size_constant_is_derived_not_independent(self):
        # لا يوجد ثابت حجم صفحة مستقل يتباعد عن قاعدة البيانات
        self.assertEqual(miniapp.LIBRARY_PAGE_SIZE, db_manager.VIDEOS_PER_PAGE)

    def test_page_renders_the_server_page_size(self):
        body = self.client.get("/app").get_data(as_text=True)
        self.assertIn(f"var PAGE_SIZE = {db_manager.VIDEOS_PER_PAGE};", body)
        self.assertNotIn("page_size=", body)


if __name__ == "__main__":
    unittest.main()
