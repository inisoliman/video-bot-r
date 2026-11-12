"""تصحيح موجة B: صدق نتائج القاعدة، معاملات ذرّية، وملكية/محادثة خاصة صريحة.

أوفلاين بالكامل: اتصالات/pools وهمية فقط (لا PostgreSQL ولا Telegram).
"""

import support  # noqa: F401  (يحجب telebot HTTP وpsycopg2 قبل استيراد الإنتاج)

import importlib
import unittest

import psycopg2

import db_manager
from handlers import admin_handlers
from handlers import admin_flows
from handlers import callback_handlers
from handlers import helpers
from handlers import thumbnails


ADMINS = [support.ADMIN_ID, support.OTHER_ADMIN_ID]


def _db_attr_restore(name):
    original = getattr(db_manager, name)
    return original, lambda: setattr(db_manager, name, original)


def _flow_patch(flows, name, replacement):
    original = getattr(flows, name, None)
    had = hasattr(flows, name)
    setattr(flows, name, replacement)
    return lambda: (setattr(flows, name, original) if had else delattr(flows, name))


def _legacy_execute_query_would_return_empty(query, params=None, fetch=None, commit=False):
    """محاكاة سلوك execute_query القديم: الفشل يصبح [] فيبدو «لا صفوف»."""
    raise psycopg2.OperationalError('SSL connection has been closed unexpectedly')


from contextlib import contextmanager


@contextmanager
def _real_db_manager_bindings():
    """يعيد ربط execute_query* في db_manager لدواله الحقيقية.

    اختبارات أخرى تستبدل db_manager.execute_query* بـ MagicMock، فتختبر الدوال
    المُعاد تصديرها تلك النسخة لا المسار الصارم الحقيقي. هنا نستعيد المُعرَّفات
    الحقيقية من كود الوحدة ونعيد القيم السابقة بعد الاختبار.
    """
    import types
    namespace = dict(importlib.import_module('db_manager').__dict__)
    namespace.update({'__name__': 'db_manager', '__file__': db_manager.__file__,
                      '__builtins__': __builtins__})
    real = types.ModuleType('db_manager_real')
    real.__dict__.update(namespace)
    saved = {name: getattr(db_manager, name)
             for name in ('execute_query', 'execute_query_strict', 'get_db_connection')}
    for name, value in saved.items():
        setattr(db_manager, name, namespace[name])
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(db_manager, name, value)


class ReportedHelpersTest(support.OfflineTestCase):
    """الدوال *_reported لا تبلّغ (0, None) عن فشل اتصال."""

    def setUp(self):
        super().setUp()
        self.cleaned = []

    def _strict_fails(self):
        original, restore = _db_attr_restore('execute_query_strict')
        db_manager.execute_query_strict = _legacy_execute_query_would_return_empty
        self.addCleanup(restore)

    def test_legacy_fetch_all_contract_and_strict_reported_helpers(self):
        self._strict_fails()
        cases = (
            (db_manager.delete_videos_by_ids_reported, ([5, 6],)),
            (db_manager.move_videos_bulk_reported, ([5, 6], 3)),
            (db_manager.move_videos_from_category_reported, (4, 5)),
            (db_manager.delete_category_by_id_reported, (4,)),
        )
        for function, args in cases:
            with self.subTest(function=function.__name__):
                count, error = function(*args)
                self.assertIsNone(count)
                self.assertIsInstance(error, psycopg2.OperationalError)
    def test_empty_returning_is_a_legitimate_zero(self):
        original, restore = _db_attr_restore('execute_query_strict')
        db_manager.execute_query_strict = lambda *a, **k: []
        self.addCleanup(restore)
        self.assertEqual(db_manager.delete_videos_by_ids_reported([1]), (0, None))
        self.assertEqual(db_manager.move_videos_bulk_reported([1], 2), (0, None))
        self.assertEqual(db_manager.move_videos_from_category_reported(1, 2), (0, None))
        self.assertEqual(db_manager.delete_category_by_id_reported(1), (0, None))

    def test_strict_admin_readers_raise_instead_of_empty_lists(self):
        self._strict_fails()
        for reader in (db_manager.fetch_admin_categories,
                       db_manager.fetch_admin_required_channels,
                       db_manager.fetch_document_videos_for_admin):
            with self.subTest(reader=reader.__name__):
                with self.assertRaises(psycopg2.OperationalError):
                    reader()

    def test_add_required_channel_requires_an_inserted_row(self):
        original, restore = _db_attr_restore('execute_query_strict')
        self.addCleanup(restore)
        db_manager.execute_query_strict = lambda *a, **k: [{'id': 1}]
        self.assertTrue(db_manager.add_required_channel('-1001234567890', 'قناة'))
        db_manager.execute_query_strict = lambda *a, **k: []  # ON CONFLICT DO NOTHING
        self.assertFalse(db_manager.add_required_channel('-1001234567890', 'قناة'))
        db_manager.execute_query_strict = _legacy_execute_query_would_return_empty
        self.assertFalse(db_manager.add_required_channel('-1001234567890', 'قناة'))

    def test_update_video_thumbnail_true_only_when_row_updated(self):
        original, restore = _db_attr_restore('execute_query_strict')
        self.addCleanup(restore)
        calls = []

        def fake(query, params=None, fetch=None, commit=False):
            calls.append(' '.join(query.split()))
            return [{'id': params[1]}] if params[1] == 12 else []
        db_manager.execute_query_strict = fake
        self.assertTrue(db_manager.update_video_thumbnail(12, 'file-id'))
        self.assertFalse(db_manager.update_video_thumbnail(99, 'file-id'))
        db_manager.execute_query_strict = _legacy_execute_query_would_return_empty
        self.assertFalse(db_manager.update_video_thumbnail(12, 'file-id'))
        self.assertTrue(all('RETURNING id' in sql for sql in calls))


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((' '.join(str(sql).split()), params))
        normalized = ' '.join(str(sql).split()).upper()
        # fail_on مقارن بالبادئة فقط (لا 'DELETE FROM CATEGORIES' داخل عبارة أخرى)
        if self.conn.fail_on and self.conn.fail_on.upper() in normalized:
            raise psycopg2.OperationalError('forced failure')
        if normalized.startswith('SELECT') and 'PARENT_ID' in normalized:
            # فحص التصنيفات الفرعية: لا أطفال افتراضياً
            self._rows = [{'id': child} for child in self.conn.children.get(
                (params or (None,))[0], ())]
            self.rowcount = len(self._rows)
        elif normalized.startswith('SELECT'):
            self._rows = [{'id': cid} for cid in (params or ()) if cid in self.conn.categories]
            self.rowcount = len(self._rows)
        elif normalized.startswith('UPDATE'):
            self.rowcount = 3
        elif normalized.startswith('DELETE FROM VIDEO_ARCHIVE'):
            self.rowcount = 2
        else:   # DELETE FROM CATEGORIES
            self.rowcount = 1
        return None

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, fail_on=None, categories=(5, 7), children=None, fail_commit=False):
        self.categories = set(categories)
        self.children = dict(children or {})
        self.fail_commit = fail_commit
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0
        self.fail_on = fail_on

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise psycopg2.OperationalError('commit outcome unknown')

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = 1


class FakeContext:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *args):
        return False


class AtomicCategoryTest(support.OfflineTestCase):
    def _install(self, conn):
        original, restore = _db_attr_restore('get_db_connection')
        db_manager.get_db_connection = lambda: FakeContext(conn)
        self.addCleanup(restore)

    def test_delete_with_contents_commits_once(self):
        conn = FakeConn()
        self._install(conn)
        outcome = db_manager.delete_category_with_contents_atomic(5)
        self.assertEqual(outcome['status'], 'deleted')
        self.assertEqual(outcome['videos'], 2)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)
        self.assertEqual([statement[0].split()[0] for statement in conn.statements],
                         ['SELECT', 'SELECT', 'DELETE', 'DELETE'])   # قفل + أطفال + حذف

    def test_delete_failure_rolls_back_both_statements(self):
        conn = FakeConn(fail_on='DELETE FROM CATEGORIES')
        self._install(conn)
        with self.assertRaises(psycopg2.OperationalError):
            db_manager.delete_category_with_contents_atomic(5)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        statements = [statement[0] for statement in conn.statements]
        self.assertTrue(any(stmt.startswith('DELETE FROM video_archive') for stmt in statements), statements)

    def test_missing_category_changes_nothing(self):
        conn = FakeConn()
        self._install(conn)
        outcome = db_manager.delete_category_with_contents_atomic(404)  # غير موجود
        self.assertEqual(outcome['status'], 'missing')
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertFalse([s for s in conn.statements if s[0].startswith('DELETE')])

    def test_move_then_delete_is_one_transaction(self):
        conn = FakeConn()
        self._install(conn)
        outcome = db_manager.move_category_contents_atomic(5, 7)
        self.assertEqual(outcome, {'status': 'moved', 'videos': 3})
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)

    def test_move_failure_rolls_back_and_reports_no_success(self):
        conn = FakeConn(fail_on='DELETE FROM CATEGORIES')
        self._install(conn)
        with self.assertRaises(psycopg2.OperationalError):
            db_manager.move_category_contents_atomic(5, 7)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)

    def test_flow_reports_rollback_instead_of_partial_success(self):
        bot = support.FakeBot()
        patches = []
        original = getattr(admin_flows, 'delete_category_with_contents_atomic')
        admin_flows.delete_category_with_contents_atomic = lambda cid: (_ for _ in ()).throw(
            psycopg2.OperationalError('rollback'))
        self.addCleanup(_flow_patch, admin_flows, 'delete_category_with_contents_atomic', original)
        del patches
        admin_flows.delete_category_with_contents(bot, support.ADMIN_ID, ADMINS, 5, call=None)
        self.assertIn('تراجعت المعاملة', bot.last_text())
        self.assertFalse(any('✅' in (t or '') for t in bot.sent_texts()))

    def test_children_block_both_operations_without_dml(self):
        for operation, args in ((db_manager.delete_category_with_contents_atomic, (5,)),
                                (db_manager.move_category_contents_atomic, (5, 7))):
            with self.subTest(operation=operation.__name__):
                conn = FakeConn(children={5: (91,)})
                self._install(conn)
                outcome = operation(*args)
                self.assertEqual(outcome['status'], 'has_children')
                self.assertEqual(conn.commits, 0)
                self.assertEqual(conn.rollbacks, 1)
                self.assertFalse([statement for statement in conn.statements
                                  if statement[0].startswith(('DELETE', 'UPDATE'))],
                                 conn.statements)

    def test_commit_failure_is_distinguished_from_statement_failure(self):
        for operation, args in ((db_manager.delete_category_with_contents_atomic, (5,)),
                                (db_manager.move_category_contents_atomic, (5, 7))):
            with self.subTest(operation=operation.__name__):
                conn = FakeConn(fail_commit=True)
                self._install(conn)
                with self.assertRaises(db_manager.CommitOutcomeUnknown):
                    operation(*args)
                self.assertEqual(conn.commits, 1)
                self.assertEqual(conn.rollbacks, 0)   # لا تراجع بعد التزام غير مؤكد
        bot = support.FakeBot()
        original = getattr(admin_flows, 'delete_category_with_contents_atomic')
        admin_flows.delete_category_with_contents_atomic = lambda cid: (_ for _ in ()).throw(
            db_manager.CommitOutcomeUnknown('commit failed'))
        self.addCleanup(_flow_patch, admin_flows, 'delete_category_with_contents_atomic', original)
        admin_flows.delete_category_with_contents(bot, support.ADMIN_ID, ADMINS, 5, call=None)
        self.assertIn('غير مؤكدة', bot.last_text())
        self.assertNotIn('تراجعت المعاملة', bot.last_text())
        self.assertFalse(any('✅' in (t or '') for t in bot.sent_texts()))

    def test_category_actions_do_not_clear_an_unrelated_pending_workflow(self):
        bot = support.FakeBot()
        cleared = []
        original_clear = getattr(admin_flows, 'clear_user_waiting_state')
        admin_flows.clear_user_waiting_state = lambda uid: (cleared.append(uid), True)[1]
        self.addCleanup(_flow_patch, admin_flows, 'clear_user_waiting_state', original_clear)
        # تدفق معلّق غير متعلق (اسم فيلم/تعليق) موجود قبل زر التصنيف
        original_reader = getattr(admin_flows, 'read_state_row')
        admin_flows.read_state_row = lambda uid: {'state': 'title_rename_search',
                                                  'context': {'admin_id': support.ADMIN_ID}}
        self.addCleanup(_flow_patch, admin_flows, 'read_state_row', original_reader)
        originals = {name: getattr(admin_flows, name) for name in
                     ('delete_category_with_contents_atomic', 'move_category_contents_atomic')}
        admin_flows.delete_category_with_contents_atomic = lambda cid: {
            'status': 'deleted', 'videos': 3, 'category': 1}
        admin_flows.move_category_contents_atomic = lambda a, b: {'status': 'moved', 'videos': 2}
        for name, value in originals.items():
            self.addCleanup(_flow_patch, admin_flows, name, value)
        admin_flows.delete_category_with_contents(bot, support.ADMIN_ID, ADMINS, 5, call=None)
        admin_flows.move_category_contents(bot, support.ADMIN_ID, ADMINS, 5, 7, call=None)
        self.assertEqual(cleared, [])
        self.assertTrue(any('✅' in (t or '') for t in bot.sent_texts()))


class OwnershipAndPrivateTest(support.OfflineTestCase):
    def test_flows_owner_requires_admin_id_equality(self):
        self.assertFalse(admin_flows._owner_ok({}, support.ADMIN_ID))
        self.assertFalse(admin_flows._owner_ok({'admin_id': None}, support.ADMIN_ID))
        self.assertFalse(admin_flows._owner_ok({'admin_id': support.OTHER_ADMIN_ID}, support.ADMIN_ID))
        self.assertTrue(admin_flows._owner_ok({'admin_id': support.ADMIN_ID}, support.ADMIN_ID))
        self.assertFalse(thumbnails._owner({}, support.ADMIN_ID))
        self.assertFalse(thumbnails._owner({'admin_id': None}, support.ADMIN_ID))
        self.assertTrue(thumbnails._owner({'admin_id': support.ADMIN_ID}, support.ADMIN_ID))

    def test_flow_state_without_owner_is_not_consumed(self):
        original = admin_flows.read_state_row
        admin_flows.read_state_row = lambda uid: {'state': admin_flows.ST_CATEGORY,
                                                  'context': {'parent_id': None}}
        self.addCleanup(_flow_patch, admin_flows, 'read_state_row', original)
        writes = []
        creator = getattr(admin_flows, 'add_category')
        admin_flows.add_category = lambda name, parent_id=None: (writes.append(name), (True, 1))[1]
        self.addCleanup(_flow_patch, admin_flows, 'add_category', creator)
        message = support.make_message('اسم', user_id=support.ADMIN_ID, message_id=5)
        self.assertFalse(admin_flows.receive_category_name(support.FakeBot(), ADMINS, message))
        self.assertEqual(writes, [])

    def test_admin_commands_reject_group_chats(self):
        bot = support.FakeBot()
        wrappers = []
        real_message_handler = bot.message_handler

        def recorder(*args, **kwargs):
            def decorator(func):
                if 'admin' in (kwargs.get('commands') or []):
                    wrappers.append(func)
                return func
            return decorator
        bot.message_handler = recorder
        try:
            admin_handlers.register(bot, ADMINS)
        finally:
            bot.message_handler = real_message_handler
        self.assertTrue(wrappers)
        group = support.make_message('/admin', chat_type='group', chat_id=-1001)
        wrappers[0](group)
        self.assertIn('المحادثة الخاصة', bot.sent_texts()[-1])
        # المرحلة 4: إشعار المجموعة رسالة ephemeral مستهدفة بالأدمن، لا رد عام
        sends = bot.calls_of('send_message')
        self.assertEqual(len(sends), 1)
        _name, args, kwargs = sends[0]
        chat_id, text = args
        self.assertEqual(chat_id, -1001)
        self.assertEqual(text, admin_handlers.ADMIN_PRIVATE_ONLY_NOTICE)
        self.assertEqual(kwargs.get('parse_mode'), 'HTML')
        self.assertEqual(kwargs.get('ephemeral_message_parameters').to_dict(),
                         {'receiver_user_id': support.ADMIN_ID})
        self.assertNotIn('receiver_user_id', kwargs)
        self.assertEqual(bot.calls_of('reply_to'), [])

    def test_callback_without_chat_is_not_treated_as_private(self):
        original_check = helpers.check_subscription
        helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(lambda: setattr(helpers, 'check_subscription', original_check))
        handlers = []
        recorder = support.FakeBot()
        real = recorder.callback_query_handler

        def capture(*args, **kwargs):
            def decorator(func):
                handlers.append(func)
                return func
            return decorator
        recorder.callback_query_handler = capture
        try:
            callback_handlers.register(recorder, ADMINS)
        finally:
            recorder.callback_query_handler = real
        self.assertTrue(handlers)
        call = support.make_call('admin::confirm_delete_ids')
        call.message = None
        writes = []
        original = getattr(admin_flows, 'clear_user_waiting_state')
        admin_flows.clear_user_waiting_state = lambda uid: (writes.append(uid), True)[1]
        self.addCleanup(_flow_patch, admin_flows, 'clear_user_waiting_state', original)
        handlers[0](call)
        self.assertIn('المحادثة الخاصة', recorder.answers()[-1][0] or '')
        self.assertEqual(writes, [])

class StateCompletionTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.writes = []
        self.clears = []
        self.store_results = [True]
        self.clear_result = True
        original = (admin_flows.read_state_row, admin_flows.set_user_state,
                    admin_flows.clear_user_waiting_state)
        admin_flows.read_state_row = lambda uid: None

        def writer(uid, state, context=None):
            result = self.store_results[min(len(self.writes), len(self.store_results) - 1)]
            self.writes.append((uid, state, context, result))
            return result

        def clearer(uid):
            self.clears.append(uid)
            return self.clear_result
        admin_flows.set_user_state = writer
        admin_flows.clear_user_waiting_state = clearer
        self.addCleanup(self._restore, original)
        self.bot = support.FakeBot()

    def _restore(self, original):
        (admin_flows.read_state_row, admin_flows.set_user_state,
         admin_flows.clear_user_waiting_state) = original

    def test_prompt_binding_failure_cleans_state_and_reports(self):
        self.store_results = [True, False]
        self.assertFalse(admin_flows.begin_move(self.bot, support.ADMIN_ID, ADMINS))
        self.assertIn('تعذّر تجهيز العملية', self.bot.last_text())
        self.assertEqual(self.clears, [support.ADMIN_ID])

    def test_prompt_send_failure_cleans_state_and_reports(self):
        self.store_results = [True]
        original = self.bot.send_message
        attempts = []

        def fail_once(*args, **kwargs):
            attempts.append(args)
            if len(attempts) == 1:
                raise RuntimeError('api')
            return original(*args, **kwargs)
        self.bot.send_message = fail_once
        try:
            self.assertFalse(admin_flows.begin_move(self.bot, support.ADMIN_ID, ADMINS))
        finally:
            self.bot.send_message = original
        self.assertEqual(self.clears, [support.ADMIN_ID])
        self.assertEqual(len(attempts), 2)  # التلميح ثم رسالة الفشل الصريحة
        self.assertIn('أُلغيت الحالة', self.bot.last_text())

    def test_state_store_failure_is_not_reported_as_started(self):
        self.store_results = [False]
        self.assertFalse(admin_flows.begin_miniapp_limit(self.bot, support.ADMIN_ID, ADMINS))
        self.assertIn('تعذّر بدء العملية', self.bot.last_text())
        # الحفظ الأول فشل: لا نمسح حالة، فقد يوجد تدفق سابق سليم
        self.assertEqual(self.clears, [])

    def test_move_without_ids_is_rejected_without_mutation(self):
        reads = []
        original_read = getattr(admin_flows, 'read_state_row')
        admin_flows.read_state_row = lambda uid: {'state': admin_flows.ST_MOVE_TARGET,
                                                  'context': {'admin_id': support.ADMIN_ID}}
        self.addCleanup(_flow_patch, admin_flows, 'read_state_row', original_read)
        original_move = getattr(admin_flows, 'move_videos_bulk_reported')
        admin_flows.move_videos_bulk_reported = lambda ids, target: (reads.append(ids), (0, None))[1]
        self.addCleanup(_flow_patch, admin_flows, 'move_videos_bulk_reported', original_move)
        admin_flows.move_now(self.bot, support.ADMIN_ID, ADMINS, 'none', call=None)
        self.assertEqual(reads, [])   # لم يُستدعَ أي نقل
        self.assertIn('غير صالح', self.bot.last_text())
        self.assertFalse(any('✅' in (t or '') for t in self.bot.sent_texts()))

    def test_delete_without_ids_is_rejected_without_mutation(self):
        deletes = []
        original_read = getattr(admin_flows, 'read_state_row')
        admin_flows.read_state_row = lambda uid: {'state': admin_flows.ST_DELETE_CONFIRM,
                                                  'context': {'admin_id': support.ADMIN_ID}}
        self.addCleanup(_flow_patch, admin_flows, 'read_state_row', original_read)
        original_delete = getattr(admin_flows, 'delete_videos_by_ids_reported')
        admin_flows.delete_videos_by_ids_reported = lambda ids: (deletes.append(ids), (0, None))[1]
        self.addCleanup(_flow_patch, admin_flows, 'delete_videos_by_ids_reported', original_delete)
        admin_flows.delete_now(self.bot, support.ADMIN_ID, ADMINS, call=None)
        self.assertEqual(deletes, [])
        self.assertIn('غير صالح', self.bot.last_text())

    def test_completed_mutation_with_clear_failure_says_cancel_needed(self):
        self.clear_result = False
        admin_flows.read_state_row = lambda uid: {'state': admin_flows.ST_MOVE_TARGET,
                                                  'context': {'admin_id': support.ADMIN_ID,
                                                              'video_ids': [3]}}
        original = getattr(admin_flows, 'move_videos_bulk_reported')
        admin_flows.move_videos_bulk_reported = lambda ids, target: (1, None)
        self.addCleanup(_flow_patch, admin_flows, 'move_videos_bulk_reported', original)
        admin_flows.move_now(self.bot, support.ADMIN_ID, ADMINS, 'none', call=None)
        texts = [t for t in self.bot.sent_texts() if t]
        self.assertTrue(any('تم نقل 1' in t for t in texts), texts)
        self.assertIn('/cancel', texts[-1])

    def test_miniapp_ip_surfaces_clear_failure(self):
        self.clear_result = False
        admin_flows.read_state_row = lambda uid: {'state': admin_flows.ST_MINIAPP_IP,
                                                  'context': {'admin_id': support.ADMIN_ID}}
        admin_flows.receive_miniapp_ip(self.bot, ADMINS,
                                       support.make_message('203.0.113.7',
                                                            user_id=support.ADMIN_ID,
                                                            message_id=12))
        self.assertTrue(any('/cancel' in (t or '') for t in self.bot.sent_texts()))

    def test_channel_lookup_failure_stops_before_insert(self):
        original = getattr(admin_flows, 'find_required_channel')
        admin_flows.find_required_channel = lambda channel_id=None, name=None: (_ for _ in ()).throw(
            psycopg2.OperationalError('down'))
        self.addCleanup(_flow_patch, admin_flows, 'find_required_channel', original)
        admin_flows.read_state_row = lambda uid: {'state': admin_flows.ST_CHANNEL_ID,
                                                  'context': {'admin_id': support.ADMIN_ID}}
        inserted = []
        original_add = getattr(admin_flows, 'add_required_channel')
        admin_flows.add_required_channel = lambda cid, name: (inserted.append(cid), True)[1]
        self.addCleanup(_flow_patch, admin_flows, 'add_required_channel', original_add)
        message = support.make_message('-1001234567890', user_id=support.ADMIN_ID, message_id=9)
        self.assertTrue(admin_flows.receive_channel_id(self.bot, ADMINS, message))
        self.assertIn('تعذّر التحقق', self.bot.last_text())
        self.assertEqual(inserted, [])

    def test_mutation_success_with_clear_failure_is_stated(self):
        self.clear_result = False
        admin_flows.read_state_row = lambda uid: {'state': admin_flows.ST_MINIAPP_LIMIT,
                                                  'context': {'admin_id': support.ADMIN_ID}}
        original = getattr(admin_flows, 'set_bot_setting')
        admin_flows.set_bot_setting = lambda key, value: True
        self.addCleanup(_flow_patch, admin_flows, 'set_bot_setting', original)
        message = support.make_message('50', user_id=support.ADMIN_ID, message_id=10)
        admin_flows.receive_miniapp_limit(self.bot, ADMINS, message)
        texts = [t for t in self.bot.sent_texts() if t]
        self.assertTrue(any('أصبح 50' in t for t in texts))
        self.assertIn('/cancel', texts[-1])


class DispatchAssignmentTest(support.OfflineTestCase):
    def test_state_reader_does_not_swallow_unrelated_text(self):
        import telebot
        from handlers import admin_handlers as handlers_admin, helpers as handlers_helpers
        bot = telebot.TeleBot('123456:TOKEN', threaded=False, validate_token=False)
        original_check = handlers_helpers.check_subscription
        handlers_helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(lambda: setattr(handlers_helpers, 'check_subscription', original_check))
        recorder = []

        class Recorder:
            def message_handler(self, *args, **kwargs):
                def decorator(func):
                    recorder.append(getattr(func, '__name__', ''))
                    return func
                return decorator
        handlers_admin.register(Recorder(), ADMINS)
        self.assertIn('admin_state_input', recorder)
        self.assertIn('admin_panel', recorder)
        del bot


class MaintenanceAssignmentTest(support.OfflineTestCase):
    def test_no_webhook_import_in_admin_broadcast(self):
        source = importlib.import_module('handlers.admin_broadcast')
        self.assertFalse(hasattr(source, '_registered_bot'))
        self.assertFalse(hasattr(source, '_real_bot'))
        with open(source.__file__, encoding='utf-8') as handle:
            self.assertNotIn('from webhook_bot import', handle.read())


if __name__ == '__main__':
    unittest.main()
