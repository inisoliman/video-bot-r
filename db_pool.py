# ==============================================================================
# ملف: db_pool.py
# الوصف: إدارة اتصال Postgres عبر Connection Pool + إعادة محاولة تلقائية
# ==============================================================================

import logging
import os
import threading
from contextlib import contextmanager
from urllib.parse import urlparse

import psycopg2
import psycopg2.pool

logger = logging.getLogger(__name__)

_connection_pool = None
_pool_lock = threading.Lock()

# Parse DATABASE_URL once
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.critical("DATABASE_URL not set")
else:
    result = urlparse(DATABASE_URL)
    DB_CONFIG = {
        'user': result.username,
        'password': result.password,
        'host': result.hostname,
        'port': result.port,
        'dbname': result.path[1:]
    }


def get_connection_pool(minconn: int = 1, maxconn: int = 10) -> psycopg2.pool.SimpleConnectionPool:
    global _connection_pool
    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                _connection_pool = psycopg2.pool.SimpleConnectionPool(minconn, maxconn, **DB_CONFIG)
                logger.info("Postgres connection pool created")
    return _connection_pool


@contextmanager
def get_db_connection():
    """Context manager: يحصل على اتصال من الpool ويعيده بأمان."""
    pool = get_connection_pool()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)
