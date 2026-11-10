import support  # noqa: F401

import unittest
from datetime import datetime

from handlers import thread_format


class ThreadFormatTest(support.OfflineTestCase):
    def test_html_escaped_not_truncated(self):
        body = "a & b <script> \"q\" 'z' 🎬"
        rendered = thread_format.format_thread_message({"author_role": "member", "body": body})
        self.assertIn("&amp;", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("🎬", rendered)
        # النص الأصلي كامل (بعد فك التهريب)
        self.assertIn("a &amp; b &lt;script&gt;", rendered)

    def test_safe_html_keeps_entities_for_long_text(self):
        text = "A&B " * 2000
        rendered = thread_format.safe_html(text)
        self.assertIn("&amp;", rendered)
        # النص الكامل مهروباً، لا قطع لكيانات HTML
        self.assertEqual(rendered, text.replace("&", "&amp;"))
        self.assertEqual(rendered.count("&amp;"), text.count("&"))

    def test_chunk_text_loses_no_characters(self):
        text = " ".join(f"w{i}&<{i}>" for i in range(1500))
        chunks = thread_format.chunk_text(text, 500)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks).replace("\n", ""), text.replace("\n", ""))

    def test_long_message_is_complete_across_pages(self):
        long_body = "".join(f"part{i}&x " for i in range(600))
        messages = [
            {"id": 1, "author_role": "member", "body": "first"},
            {"id": 2, "author_role": "admin", "body": long_body},
        ]
        pages = thread_format.paginate_messages(messages, budget=800)
        self.assertGreater(len(pages), 1)
        rebuilt = "".join(item["chunk"] for page in pages for item in page)
        for part in ("first", "part0&amp;x", "part599&amp;x"):
            self.assertIn(part, rebuilt)
        self.assertNotIn("&amp;amp;", rebuilt)

    def test_build_thread_text_header_and_escaped_title(self):
        comment = {"id": 7, "display_title": "A&B <title>", "user_id": 500, "username": "u"}
        text = thread_format.build_thread_text(comment, [], 0, 1, "admin")
        self.assertIn("محادثة #7", text)
        self.assertIn("A&amp;B &lt;title&gt;", text)

    def test_status_read_is_not_waiting(self):
        latest = {"id": 5, "author_role": "member", "created_at": datetime(2024, 1, 1, 10, 0)}
        self.assertEqual(thread_format.compute_thread_state(latest, None), thread_format.STATUS_WAITING)
        self.assertEqual(thread_format.compute_thread_state(latest, datetime(2024, 1, 1, 11, 0)),
                         thread_format.STATUS_READ)
        answer = {"id": 6, "author_role": "admin", "created_at": datetime(2024, 1, 1, 12, 0)}
        self.assertEqual(thread_format.compute_thread_state(answer, None), thread_format.STATUS_ANSWERED)


if __name__ == "__main__":
    unittest.main()
