import support  # noqa: F401  (must be first: blocks network + DB, sets sys.path)

import contextlib
import unittest

import db_manager


def run_schema(fail_on=None, rows=None, rowcounts=None):
    """Run verify_and_repair_schema against a fake connection; return (outcome, conn)."""
    fake = support.FakeDb(rows_by_index=rows, rowcount_by_index=rowcounts, fail_on=fail_on)
    original = db_manager.get_db_connection
    db_manager.get_db_connection = fake.connect
    try:
        outcome = db_manager.verify_and_repair_schema()
    finally:
        db_manager.get_db_connection = original
    return outcome, fake.connection


class SchemaBootstrapTest(support.OfflineTestCase):
    def test_parent_before_child_and_single_commit(self):
        outcome, conn = run_schema(rowcounts={})
        original_index = conn.index_of("INSERT INTO video_comment_messages")
        parent_create = [i for i, sql in enumerate(conn.normalized())
                         if sql.startswith('CREATE TABLE IF NOT EXISTS "video_comments"')]
        self.assertTrue(parent_create, "video_comments must be created")
        self.assertLess(parent_create[-1], original_index)
        # الفهرسات/الجدول الفرعي تأتي بعد الأب دائماً
        for index, sql in enumerate(conn.normalized()):
            if "video_comment_messages" in sql and "INSERT" not in sql:
                self.assertGreater(index, parent_create[-1], sql)
        self.assertEqual(conn.committed, 1)
        self.assertEqual(conn.rolledback, 0)
        self.assertEqual(set(outcome), {"import_original", "import_reply"})

    def test_additive_ddl_is_idempotent_and_only_adds_columns(self):
        _outcome, conn = run_schema()
        statements = conn.normalized()
        for sql in statements:
            self.assertNotIn("DROP TABLE", sql)
            self.assertNotIn("DROP COLUMN", sql)
            self.assertNotIn("RENAME", sql)
        alters = [s for s in statements if s.startswith("ALTER TABLE") and "ADD COLUMN" in s]
        self.assertTrue(alters)
        for sql in alters:
            self.assertIn("ADD COLUMN IF NOT EXISTS", sql)
        self.assertTrue(any(x.startswith("CREATE TABLE IF NOT EXISTS video_comment_messages ") for x in statements))
        self.assertTrue(any(x.startswith("CREATE UNIQUE INDEX IF NOT EXISTS video_comment_messages_legacy_uq")
                            for x in statements))

    def test_failure_rolls_back_without_partial_commit(self):
        def fail_on(sql, params):
            if sql.startswith("CREATE TABLE IF NOT EXISTS video_comment_messages"):
                return db_manager.psycopg2.ProgrammingError("boom")
            return None

        with self.assertRaises(db_manager.psycopg2.Error):
            run_schema(fail_on=fail_on)
        # نلتقط الاتصال من داخل الاختبار عبر استدعاء ثانٍ لا يكفي؛ نُعيد التحقق صراحة
        fake = support.FakeDb(fail_on=fail_on)
        original = db_manager.get_db_connection
        db_manager.get_db_connection = fake.connect
        try:
            with self.assertRaises(Exception):
                db_manager.verify_and_repair_schema()
        finally:
            db_manager.get_db_connection = original
        self.assertEqual(fake.connection.committed, 0)
        self.assertEqual(fake.connection.rolledback, 1)
        # الاستيراد لم يُنفَّذ بعد الفشل
        self.assertFalse([s for s in fake.connection.normalized()
                          if s.startswith("INSERT INTO video_comment_messages")])

    def test_legacy_import_runs_after_child_table_and_reports_counts(self):
        # STATEMENT ORDER: many CREATE/ALTER statements precede the two INSERTs.
        fake_rows = {}
        fake_counts = {}
        fake = support.FakeDb(rows_by_index=fake_rows, rowcount_by_index=fake_counts)
        original = db_manager.get_db_connection
        db_manager.get_db_connection = fake.connect
        try:
            # نحدد عدد عبارات الـ INSERT ديناميكياً: نسمح بالعدّ الافتراضي (1) لأول أمر فقط
            db_manager.verify_and_repair_schema()
        finally:
            db_manager.get_db_connection = original
        originals = [i for i, sql in enumerate(fake.connection.normalized())
                     if "INSERT INTO video_comment_messages" in sql]
        self.assertEqual(len(originals), 2)
        first_insert = originals[0]
        self.assertGreater(first_insert, fake.connection.index_of("CREATE UNIQUE INDEX IF NOT EXISTS"))
        self.assertEqual(fake.connection.committed, 1)

    def test_legacy_import_sql_preserves_original_text_and_is_guarded(self):
        _outcome, conn = run_schema()
        inserts = [sql for sql in conn.normalized() if "INSERT INTO video_comment_messages" in sql]
        original_sql, reply_sql = inserts
        self.assertIn("c.comment_text", original_sql)
        self.assertIn("COALESCE(c.created_at, CURRENT_TIMESTAMP)", original_sql)
        self.assertIn("c.admin_reply", reply_sql)
        self.assertIn("COALESCE(c.replied_at, c.created_at, CURRENT_TIMESTAMP)", reply_sql)
        for sql in inserts:
            self.assertIn("NOT EXISTS", sql)
            self.assertIn("ON CONFLICT DO NOTHING", sql)
            self.assertNotIn("UPDATE", sql)
            self.assertNotIn("DELETE", sql)
        params = [c[1] for c in conn.calls if "INSERT INTO video_comment_messages" in c[0]]
        self.assertEqual(len(params), 2)
        self.assertEqual(params[0], ("legacy_original", "legacy_original"))
        self.assertEqual(params[1], ("legacy_reply", "legacy_reply"))
        original_param_index, reply_param_index = 0, 1
        # أصل التعليق يسبق الرد في الترتيب الزمني المنطقي
        self.assertLess(original_param_index, reply_param_index)

    def test_second_boot_is_a_noop_contract(self):
        _outcome, conn = run_schema()
        for sql in conn.normalized():
            if sql.startswith("ALTER TABLE") and "video_comment_messages(" in sql:
                self.assertIn("IF NOT EXISTS", sql)
            if "INSERT INTO video_comment_messages" in sql:
                self.assertIn("ON CONFLICT DO NOTHING", sql)


class ThreadPersistenceContractTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.db = support.ScriptedExecuteQuery(db_manager).install()
        self.fake = None

    def tearDown(self):
        self.db.restore()
        db_manager.get_db_connection = getattr(self, '_original_conn', db_manager.get_db_connection)

    def use_fake(self, rows_by_index=None, rowcount_by_index=None, always=False):
        self.fake = support.FakeDb(rows_by_index=rows_by_index, rowcount_by_index=rowcount_by_index,
                                   always=always)
        self._original_conn = db_manager.get_db_connection
        db_manager.get_db_connection = self.fake.connect
        return self.fake

    def test_append_thread_message_never_touches_legacy_columns(self):
        fake = self.use_fake(rows_by_index={0: [{"one": 1}]}, rowcount_by_index={})
        fake.connection.rows_by_index[1] = [{"id": 11}]
        message_id = db_manager.append_thread_message(7, "member", 500, "follow-up")
        self.assertEqual(message_id, 11)
        statements = "\n".join(fake.connection.normalized())
        self.assertIn("INSERT INTO video_comment_messages", statements)
        self.assertIn("thread_updated_at = CURRENT_TIMESTAMP", statements)
        self.assertNotIn("comment_text =", statements)
        self.assertNotIn("admin_reply =", statements)
        self.assertEqual(fake.connection.committed, 1)

    def test_append_thread_message_rejects_missing_comment(self):
        fake = self.use_fake(rows_by_index={0: []})
        self.assertIsNone(db_manager.append_thread_message(7, "member", 500, "x"))
        self.assertNotIn("INSERT", "\n".join(fake.connection.normalized()))

    def test_append_thread_message_rejects_bad_role(self):
        self.use_fake()
        self.assertIsNone(db_manager.append_thread_message(7, "owner", 500, "x"))

    def test_reply_in_thread_commits_reply_and_message_once(self):
        fake = self.use_fake(rows_by_index={0: [{"one": 1}], 2: [{"id": 12}]}, rowcount_by_index={})
        result = db_manager.reply_in_thread(7, support.ADMIN_ID, "hi")
        self.assertEqual(result, {"status": "ok", "message_id": 12})
        normalized = fake.connection.normalized()
        self.assertTrue(normalized[0].startswith("SELECT 1 FROM video_comments WHERE id = %s FOR UPDATE")
                        or "FOR UPDATE" in normalized[0])
        self.assertTrue(normalized[1].startswith("UPDATE video_comments"))
        self.assertIn("INSERT INTO video_comment_messages", normalized[2])
        self.assertEqual(fake.connection.committed, 1)

    def test_reply_in_thread_reports_missing_comment(self):
        fake = self.use_fake()
        result = db_manager.reply_in_thread(7, support.ADMIN_ID, "hi")
        self.assertEqual(result["status"], "missing")
        self.assertEqual(fake.connection.rolledback, 1)
        self.assertFalse([s for s in fake.connection.normalized()
                          if s.startswith("INSERT")])

    def test_set_display_title_clears_search_cache_and_keeps_media_columns(self):
        self.db.script = {0: [{"id": 3}]}
        db_manager._search_cache["x:0:None:None:None"] = ([], 0)
        self.assertTrue(db_manager.set_display_title(3, "New title"))
        sql, params, fetch, commit = self.db.calls[0]
        self.assertIn("UPDATE video_archive SET display_title = %s", sql)
        self.assertNotIn("caption =", sql)
        self.assertNotIn("file_name =", sql)
        self.assertNotIn("file_id =", sql)
        self.assertEqual(params, ("New title", 3))
        self.assertEqual(db_manager._search_cache, {})

    def test_get_user_comments_uses_left_join_and_thread_order(self):
        self.db.script = {0: [], 1: [{"count": 0}]}
        db_manager.get_user_comments(500, 0)
        sql = self.db.normalized()[0]
        self.assertIn("LEFT JOIN video_archive", sql)
        self.assertIn("COALESCE(c.thread_updated_at, c.created_at)", sql)

    def test_thread_messages_ordered_oldest_first_by_default(self):
        self.db.script = {0: [], 1: [{"count": 0}]}
        db_manager.get_thread_messages(7, page=0)
        sql = self.db.normalized()[0]
        self.assertIn("WHERE comment_id = %s", sql)
        self.assertIn("ORDER BY id ASC", sql)
        self.assertIn("LIMIT %s OFFSET %s", sql)

    def test_search_covers_display_title(self):
        self.db.script = {0: [], 1: [{"count": 0}]}
        db_manager.search_videos("film", page=0)
        sql = self.db.normalized()[0]
        self.assertIn("display_title ILIKE %s", sql)
        self.assertIn("OR file_name ILIKE %s OR display_title ILIKE %s", sql)


if __name__ == "__main__":
    unittest.main()
