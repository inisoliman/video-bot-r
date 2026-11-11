"""Connection pool: سحب آمن للخيوط، استبعاد الاتصالات التالفة، بلا إعادة كتابة.

أوفلاين بالكامل: لا psycopg2.connect ولا pool حقيقي (fakes فقط).
"""

import support  # noqa: F401  (يحجب شبكة telebot وpsycopg2 قبل الاستيراد)

import unittest

import psycopg2
import psycopg2.extensions
import psycopg2.pool

import db_pool


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.health_calls.append(sql)
        if self.conn.health_error is not None:
            raise self.conn.health_error

    def fetchone(self):
        return (1,) if self.conn.health_error is None else None


class FakeConn:
    def __init__(self, name='c', health_error=None, status=None, closed=0):
        self.name = name
        self.health_error = health_error
        self.status = status
        self.closed = closed
        self.health_calls = []
        self.commits = 0
        self.rollbacks = 0
        self.put_close = None
        self.rollback_error = None

    # API المستخدم أثناء السحب
    def get_transaction_status(self):
        if self.status is None:
            return psycopg2.extensions.TRANSACTION_STATUS_IDLE
        return self.status

    def cursor(self, *args, **kwargs):
        return FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = 1


class FakePool:
    def __init__(self, connections, checkout_error=None):
        self.checkouts = 0
        self.connections = list(connections)
        self.checkout_error = checkout_error
        self.puts = []
        self.served = []
        self.created = []

    def getconn(self):
        self.checkouts += 1
        if self.checkout_error is not None:
            raise self.checkout_error
        conn = self.connections.pop(0)
        self.served.append(conn)
        return conn

    def putconn(self, conn, close=False):
        self.puts.append((conn, close))
        if not close:
            self.connections.append(conn)


class PoolTestBase(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.pool = FakePool([])
        self.made = []
        saved_threaded = psycopg2.pool.ThreadedConnectionPool
        saved_simple = psycopg2.pool.SimpleConnectionPool

        def fake_threaded(*args, **kwargs):
            created = type('Created', (), {'args': args, 'kwargs': kwargs})()
            self.made.append(created)
            return self.pool

        psycopg2.pool.ThreadedConnectionPool = fake_threaded
        psycopg2.pool.SimpleConnectionPool = fake_threaded
        self.addCleanup(setattr, psycopg2.pool, 'ThreadedConnectionPool', saved_threaded)
        self.addCleanup(setattr, psycopg2.pool, 'SimpleConnectionPool', saved_simple)
        saved_pool = db_pool._connection_pool
        saved_config = db_pool.DB_CONFIG
        db_pool._connection_pool = None
        db_pool.DB_CONFIG = {'user': 'u', 'password': 'p', 'host': 'h', 'port': 1, 'dbname': 'd'}
        self.addCleanup(setattr, db_pool, '_connection_pool', saved_pool)
        self.addCleanup(setattr, db_pool, 'DB_CONFIG', saved_config)

    def got_connection(self):
        with db_pool.get_db_connection() as conn:
            return conn


class PoolSelectionTest(PoolTestBase):
    def test_uses_threaded_pool_for_concurrent_threads(self):
        """الفئة المختارة آمنة للخيوط: ThreadedConnectionPool لا SimpleConnectionPool."""
        db_pool.get_connection_pool()
        self.assertEqual(len(self.made), 1)
        self.assertEqual(self.made[0].args, (1, 10))
        self.assertEqual(self.made[0].kwargs.get('dbname'), 'd')
        self.assertIs(db_pool._threaded_pool_class(), psycopg2.pool.ThreadedConnectionPool)

    def test_stale_connection_is_discarded_and_replaced(self):
        stale = FakeConn('stale', health_error=psycopg2.OperationalError('SSL connection has been closed unexpectedly'))
        fresh = FakeConn('fresh')
        self.pool.connections = [stale, fresh]
        self.assertIs(self.got_connection(), fresh)
        self.assertEqual([(c.name, close) for c, close in self.pool.puts], [('stale', True), ('fresh', False)])
        self.assertEqual(stale.closed, 1)
        self.assertEqual(fresh.commits, 0)

    def test_closed_connection_is_replaced(self):
        dead = FakeConn('dead', closed=1)
        fresh = FakeConn('fresh')
        self.pool.connections = [dead, fresh]
        self.assertIs(self.got_connection(), fresh)
        self.assertEqual([(c.name, close) for c, close in self.pool.puts], [('dead', True), ('fresh', False)])

    def test_checkout_validates_connection_with_select_1(self):
        """التحقق عند السحب فقط: استعلام SELECT 1 قراءة فقط ثم تراجع."""
        fresh = FakeConn('fresh')
        self.pool.connections = [fresh]
        self.got_connection()
        self.assertEqual(fresh.health_calls, ['SELECT 1'])

    def test_all_candidates_unhealthy_raises_without_retrying_operations(self):
        bad = [FakeConn(f'b{i}', health_error=psycopg2.InterfaceError('closed'))
               for i in range(db_pool.CHECKOUT_ATTEMPTS)]
        self.pool.connections = list(bad)
        with self.assertRaises(psycopg2.Error):
            self.got_connection()
        self.assertEqual(self.pool.checkouts, db_pool.CHECKOUT_ATTEMPTS)
        self.assertTrue(all(close for _c, close in self.pool.puts))

    def test_pool_creation_failure_is_raised(self):
        psycopg2.pool.ThreadedConnectionPool = lambda *a, **k: (_ for _ in ()).throw(
            psycopg2.OperationalError('no server'))
        saved = db_pool._threaded_pool_class
        db_pool._threaded_pool_class = lambda: psycopg2.pool.ThreadedConnectionPool
        self.addCleanup(setattr, db_pool, '_threaded_pool_class', saved)
        with self.assertRaises(psycopg2.OperationalError):
            self.got_connection()

    def test_missing_database_url_fails_without_pool(self):
        db_pool.DB_CONFIG = None
        with self.assertRaises(psycopg2.Error):
            self.got_connection()


class PoolLifecycleTest(PoolTestBase):
    def test_normal_operation_reuses_connection(self):
        conn = FakeConn('ok')
        self.pool.connections = [conn]
        with db_pool.get_db_connection() as got:
            got.commit()
        self.assertEqual(conn.commits, 1)
        self.assertEqual(self.pool.puts, [(conn, False)])

    def test_operation_error_rolls_back_and_reuses_connection(self):
        conn = FakeConn('ok')
        self.pool.connections = [conn]
        with self.assertRaises(ValueError):
            with db_pool.get_db_connection() as got:
                got.cursor().execute('UPDATE x', ('a',))
                raise ValueError('write failed')
        # تراجع واحد للعملية + تراجع الفحص عند السحب التالي (idle نظيف)
        self.assertEqual(self.pool.puts, [(conn, False)])

    def test_ssl_operational_error_discards_connection(self):
        conn = FakeConn('broken')
        self.pool.connections = [conn]
        with self.assertRaises(psycopg2.OperationalError):
            with db_pool.get_db_connection() as got:
                raise psycopg2.OperationalError('SSL connection has been closed unexpectedly')
        self.assertEqual([(c.name, close) for c, close in self.pool.puts], [('broken', True)])
        self.assertEqual(conn.closed, 1)

    def test_failing_rollback_also_discards(self):
        """اتصال يجتاز فحص SELECT 1، ثم يفشل جسم العملية ويفشل تراجعه => يُغلق ويُستبعد."""
        conn = FakeConn('valid-then-broken')
        original_rollback = conn.rollback
        rollback_calls = []

        def rollback_once_then_fail():
            # التحقق عند السحب يستدعي rollback() النظيف، ثم يفشل الجسم ويفشل التراجع
            rollback_calls.append(1)
            original_rollback()
            if len(rollback_calls) > 1:
                raise psycopg2.InterfaceError('gone')
        conn.rollback = rollback_once_then_fail
        self.pool.connections = [conn]
        body = []

        def operation(got):
            body.append(got)
            raise psycopg2.OperationalError('server closed the connection unexpectedly')

        with self.assertRaises(psycopg2.OperationalError):
            with db_pool.get_db_connection() as got:
                operation(got)
        self.assertEqual(body, [conn])                    # اجتاز التحقق ودخل الجسم
        self.assertEqual(conn.health_calls, ['SELECT 1'])  # التحقق تم عند السحب
        self.assertEqual(len(rollback_calls), 2)          # تراجع الفحص + محاولة تراجع فاشلة
        self.assertEqual(conn.commits, 0)                 # بلا التزام
        self.assertEqual(self.pool.puts, [(conn, True)])  # أُعيد للإغلاق لا للـ pool
        self.assertEqual(conn.closed, 1)                  # أُغلق فعلياً
        self.assertEqual(self.pool.connections, [])       # لا اتصال تالف متبقٍ للـ pool
        self.assertEqual(self.pool.checkouts, 1)          # لا إعادة محاولة للعملية

    def test_unfinished_transaction_on_checkout_is_discarded(self):
        busy = FakeConn('busy', status=psycopg2.extensions.TRANSACTION_STATUS_INTRANS)
        fresh = FakeConn('fresh')
        self.pool.connections = [busy, fresh]
        self.assertIs(self.got_connection(), fresh)
        self.assertEqual([(c.name, close) for c, close in self.pool.puts],
                         [('busy', True), ('fresh', False)])
        self.assertEqual(busy.commits, 0)


if __name__ == '__main__':
    unittest.main()
