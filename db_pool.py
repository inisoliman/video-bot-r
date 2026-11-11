# ==============================================================================
# ملف: db_pool.py
# الوصف: إدارة اتصال Postgres عبر Connection Pool آمن للخيوط + فحص صحة عند السحب
# ==============================================================================

import logging
import os
import threading
from contextlib import contextmanager
from urllib.parse import urlparse

import psycopg2
import psycopg2.extensions
import psycopg2.pool

logger = logging.getLogger(__name__)

_connection_pool = None
_pool_lock = threading.Lock()

# عدد محاولات الحصول على اتصال سليم عند السحب فقط (لا إعادة محاولة للتنفيذ أو الحفظ)
CHECKOUT_ATTEMPTS = 3
# فحص صحة قراءة فقط عند السحب: لا يبدأ أي التزام ولا يعيد كتابة أي شيء
CHECKOUT_HEALTH_QUERY = "SELECT 1"
# أخطاء تعني أن هذا الاتصال (أو الكائن نفسه) لم يعد صالحاً ولا يجوز إعادته للـ pool
FATAL_DB_ERRORS = (
    psycopg2.OperationalError,
    psycopg2.InterfaceError,
    psycopg2.InternalError,
)

DB_CONFIG = None
if not os.getenv("DATABASE_URL"):
    logger.critical("DATABASE_URL not set")
else:
    _result = urlparse(os.getenv("DATABASE_URL"))
    DB_CONFIG = {
        'user': _result.username,
        'password': _result.password,
        'host': _result.hostname,
        'port': _result.port,
        'dbname': _result.path[1:]
    }


def _threaded_pool_class():
    """ThreadedConnectionPool (آمن للخيوط، يمنع نفس الاتصال لخيطين معاً).

    يُقرأ عبر الوحدة psycopg2.pool وقت الاستدعاء حتى يمكن استبداله في الاختبارات.
    """
    return psycopg2.pool.ThreadedConnectionPool


def get_connection_pool(minconn: int = 1, maxconn: int = 10):
    """الـ pool المشترك (يُنشأ مرة واحدة). SimpleConnectionPool غير آمن للخيوط."""
    global _connection_pool
    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                if DB_CONFIG is None:
                    raise psycopg2.OperationalError("DATABASE_URL is not configured")
                pool_class = _threaded_pool_class()
                _connection_pool = pool_class(minconn, maxconn, **DB_CONFIG)
                logger.info("Postgres connection pool created (%s)", getattr(pool_class, "__name__", "pool"))
    return _connection_pool


def _needs_commit(conn) -> bool:
    """هل على الاتصال معاملة غير منتهية (غير idle)؟

    عند السحب لا يجوز الالتزام ولا التراجع بصمت: اتصال بعمل غير مكتمل يُعد غير
    صالح ويُستبعد بدل إعادة استخدامه.
    """
    getter = getattr(conn, "get_transaction_status", None)
    if getter is None:
        return False
    try:
        status = getter()
        idle = getattr(psycopg2.extensions, "TRANSACTION_STATUS_IDLE", 0)
        return status != idle
    except Exception:
        return True


def _connection_is_healthy(conn) -> bool:
    """فحص قراءة فقط (SELECT 1) على الاتصال + التراجع عن أي معاملة فُتحت للفحص."""
    try:
        if getattr(conn, "closed", 0):
            return False
        if _needs_commit(conn):
            logger.warning("Discarding pooled connection with an unfinished transaction")
            return False
        with conn.cursor() as cursor:
            cursor.execute(CHECKOUT_HEALTH_QUERY)
            cursor.fetchone()
        # الفحص نفسه يفتح معاملة ضمنية: نرجع الاتصال لحالة idle نظيفة
        conn.rollback()
        return True
    except Exception as exc:
        logger.warning("Pooled connection health check failed (%s)", type(exc).__name__)
        return False


def _discard(pool, conn):
    """إغلاق اتصال تالف حقيقياً وإزالته من الـ pool (لا يعاد أبداً للقائمة)."""
    try:
        conn.close()
    except Exception as exc:
        logger.warning("Could not close broken connection: %s", type(exc).__name__)
    try:
        pool.putconn(conn, close=True)
    except Exception as exc:
        logger.warning("Could not remove broken connection from pool: %s", type(exc).__name__)


def _checked_out_connection(pool):
    """اتصال سليم من الـ pool، أو رفع آخر خطأ بعد استبعاد الاتصالات التالفة.

    المحاولات محدودة (CHECKOUT_ATTEMPTS) وتهتم بالسحب فقط: أي خطأ أثناء استخدام
    الاتصال نفسه لا يُعاد تلقائياً لأنه قد يكون قد نفّذ جزءاً من عملية.
    """
    last_error = None
    for _attempt in range(CHECKOUT_ATTEMPTS):
        try:
            conn = pool.getconn()
        except Exception as exc:
            last_error = exc
            logger.error("Could not check out a connection: %s", type(exc).__name__)
            break
        if _connection_is_healthy(conn):
            return conn
        _discard(pool, conn)
        last_error = psycopg2.OperationalError("pooled connection was not healthy")
    if last_error is None:
        last_error = psycopg2.OperationalError("no healthy connection available")
    raise last_error


@contextmanager
def get_db_connection():
    """Context manager: يحصل على اتصال سليم من الـ pool ويعيده بأمان.

    - الاتصال التالف (بداية مرفوضة/اتصال مغلق/معاملة غير منتهية) يُستبعد ولا يعاد.
    - نجاح: commit/rollback مسؤولية المستدعي؛ لا التزام تلقائي.
    - خطأ داخل العملية: تراجع؛ ولو كان الخطأ قاتلاً (OperationalError للـ SSL مثلاً)
      يُستبعد الاتصال ولا يُعاد للـ pool.
    """
    pool = get_connection_pool()
    conn = _checked_out_connection(pool)
    broken = False
    try:
        yield conn
    except Exception as exc:
        if isinstance(exc, FATAL_DB_ERRORS):
            broken = True
        try:
            conn.rollback()
        except Exception as rollback_error:
            broken = True
            logger.error(f"Failed to rollback database connection: {rollback_error}")
        raise
    finally:
        if broken:
            _discard(pool, conn)
        else:
            try:
                pool.putconn(conn)
            except Exception as exc:
                logger.error("Could not return connection to pool: %s", type(exc).__name__)
                _discard(pool, conn)
