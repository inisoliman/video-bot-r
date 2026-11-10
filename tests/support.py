"""Offline test support. Import this FIRST: it blocks Telegram HTTP and psycopg2.

``python -m unittest discover -s tests`` puts ``tests/`` on sys.path and may not
import the package, so every test module must call ``_bootstrap()`` before importing
production modules.
"""

import contextlib
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace


class NetworkBlocked(AssertionError):
    """Raised when code under test tries a real Telegram call or DB connection."""


_BOOTSTRAPPED = False


def _bootstrap():
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    import telebot.apihelper

    def _blocked_http(*args, **kwargs):
        raise NetworkBlocked("telebot HTTP must not be reached in offline tests")

    telebot.apihelper._make_request = _blocked_http

    import psycopg2
    import psycopg2.pool

    def _blocked_connect(*args, **kwargs):
        raise NetworkBlocked("psycopg2.connect must not be reached in offline tests")

    def _blocked_pool(*args, **kwargs):
        raise NetworkBlocked("psycopg2 pool must not be reached in offline tests")

    psycopg2.connect = _blocked_connect
    psycopg2.pool.SimpleConnectionPool = _blocked_pool
    _BOOTSTRAPPED = True


_bootstrap()

ADMIN_ID = 900
OTHER_ADMIN_ID = 901
MEMBER_ID = 500
OTHER_MEMBER_ID = 501


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------

ENV_KEYS = (
    "BOT_TOKEN", "DATABASE_URL", "CHANNEL_ID", "ADMIN_IDS", "APP_URL",
    "BASE_URL", "WEBHOOK_SECRET", "MANAGEMENT_TOKEN", "WEBHOOK_DROP_PENDING", "PORT",
)


@contextlib.contextmanager
def env(**values):
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    try:
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        for key, value in values.items():
            if value is not None:
                os.environ[key] = value
        yield
    finally:
        for key in ENV_KEYS:
            if saved[key] is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = saved[key]


# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, conn, cursor_factory=None):
        self.conn = conn
        self.cursor_factory = cursor_factory
        self.rowcount = 0
        self._rows = []

    @property
    def connection(self):
        """psycopg2 cursors expose the DBAPI connection; sql.Identifier needs it."""
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def execute(self, sql, params=None):
        return self.conn.on_execute(self, sql, params)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class FakeConnection:
    """Records (sql, params, cursor_factory); rows/rowcount scripted per statement index."""

    def __init__(self, db):
        self.db = db
        self.calls = []
        self.committed = 0
        self.rolledback = 0
        self.fail_on = None
        self.rows_by_index = db.rows_by_index
        self.rowcount_by_index = db.rowcount_by_index
        self.always = db.always

    def cursor(self, cursor_factory=None):
        return FakeCursor(self, cursor_factory)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolledback += 1

    def on_execute(self, cursor, sql, params):
        index = len(self.calls)
        text = str(sql)
        self.calls.append((text, params, cursor.cursor_factory))
        if self.fail_on is not None:
            exc = self.fail_on(" ".join(text.split()), params)
            if exc is not None:
                raise exc
        lookup = 0 if self.always else index
        cursor._rows = self.rows_by_index.get(lookup, self.rows_by_index.get(index, []))
        cursor.rowcount = self.rowcount_by_index.get(lookup, self.rowcount_by_index.get(index, len(cursor._rows) or 1))

    def normalized(self):
        return [" ".join(statement.split()) for statement, _p, _f in self.calls]

    def index_of(self, fragment, start=0):
        for index, sql in enumerate(self.normalized()):
            if index >= start and fragment in sql:
                return index
        raise AssertionError(f"no statement containing {fragment!r}")

    def params_for(self, fragment):
        for sql, params, _f in self.calls:
            if fragment in " ".join(str(sql).split()):
                return params
        raise AssertionError(f"no params for {fragment!r}")


class ComposedSQL:
    """Wraps a psycopg2.sql.Composable and renders it with the live fake cursor."""

    def __init__(self, rendered):
        self.rendered = rendered

    def __str__(self):
        return self.rendered


class FakeDb:
    def __init__(self, rows_by_index=None, rowcount_by_index=None, fail_on=None, always=False):
        self.rows_by_index = dict(rows_by_index or {})
        self.rowcount_by_index = dict(rowcount_by_index or {})
        self.always = always
        self.connection = FakeConnection(self)
        self.connection.fail_on = fail_on

    @contextlib.contextmanager
    def connect(self):
        yield self.connection


class ScriptedExecuteQuery:
    def __init__(self, module):
        self.module = module
        self.original = module.execute_query
        self.calls = []
        self.script = {}
        self.fail = None

    def install(self):
        self.module.execute_query = self
        return self

    def restore(self):
        self.module.execute_query = self.original

    def __call__(self, query, params=None, fetch=None, commit=False):
        index = len(self.calls)
        normalized = " ".join(str(query).split())
        self.calls.append((normalized, params, fetch, commit))
        if self.fail is not None:
            exc = self.fail(normalized, params)
            if exc is not None:
                raise exc
        rows = self.script.get(index, [])
        if fetch == "one":
            return rows[0] if rows else None
        if fetch == "all":
            return list(rows)
        if fetch is None:
            return True if commit else None
        return None

    def normalized(self):
        return [call[0] for call in self.calls]

    def index_of(self, fragment):
        for index, sql in enumerate(self.normalized()):
            if fragment in sql:
                return index
        raise AssertionError(f"no call containing {fragment!r}")


# ---------------------------------------------------------------------------
# Telegram fakes
# ---------------------------------------------------------------------------

def make_user(user_id, username=None, first_name=None):
    return SimpleNamespace(id=user_id, username=username, first_name=first_name)


def make_chat(chat_id, chat_type="private"):
    return SimpleNamespace(id=chat_id, type=chat_type, title=None)


def make_message(text=None, user_id=ADMIN_ID, chat_type="private", chat_id=None,
                 message_id=1, content_type="text", reply_to_message=None,
                 photo=None, username=None):
    return SimpleNamespace(
        text=text,
        from_user=make_user(user_id, username=username),
        chat=make_chat(chat_id if chat_id is not None else user_id, chat_type),
        message_id=message_id,
        content_type=content_type,
        reply_to_message=reply_to_message,
        photo=photo,
    )


def make_call(data, user_id=ADMIN_ID, chat_type="private", call_id="cb-1",
              chat_id=None, message_id=1, no_message=False):
    message = None
    if not no_message:
        message = SimpleNamespace(chat=make_chat(chat_id if chat_id is not None else user_id,
                                                 chat_type),
                                  message_id=message_id, text="prompt")
    return SimpleNamespace(id=call_id, data=data, from_user=make_user(user_id),
                           message=message, chat_instance="ci")


class FakeBot:
    def __init__(self, raise_on=(), fail_user_ids=()):
        self.calls = []
        self.raise_on = set(raise_on)
        self.fail_user_ids = set(fail_user_ids)
        self.next_message_id = 1000

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name in self.raise_on:
            raise RuntimeError(f"forced failure in {name}")
        if name == "send_message" and args and args[0] in self.fail_user_ids:
            raise RuntimeError("forced delivery failure")
        if name in ("send_message", "edit_message_text", "reply_to", "copy_message"):
            self.next_message_id += 1
            chat_id = args[0] if args else kwargs.get("chat_id", 0)
            return SimpleNamespace(message_id=self.next_message_id, chat=make_chat(chat_id))
        return True

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def recorder(*args, **kwargs):
            return self._record(name, *args, **kwargs)

        return recorder

    def calls_of(self, name):
        return [c for c in self.calls if c[0] == name]

    def sent_texts(self):
        out = []
        for name, args, kwargs in self.calls:
            if name in ("send_message", "edit_message_text", "reply_to"):
                out.append(args[1] if len(args) > 1 else kwargs.get("text"))
        return out

    def sent_to(self, chat_id):
        return [args[1] for name, args, kwargs in self.calls
                if name == "send_message" and args and args[0] == chat_id]

    def answers(self):
        return [(args[1] if len(args) > 1 else kwargs.get("text"), kwargs.get("show_alert", False))
                for name, args, kwargs in self.calls if name == "answer_callback_query"]

    def last_text(self):
        texts = self.sent_texts()
        return texts[-1] if texts else None

    def markup_for(self, name="send_message"):
        return [c[2].get("reply_markup") for c in self.calls_of(name)
                if c[2].get("reply_markup") is not None]


def button_callbacks(markup):
    if markup is None:
        return []
    return [b.callback_data for row in markup.keyboard for b in row
            if getattr(b, "callback_data", None)]


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def comment_row(comment_id=7, user_id=MEMBER_ID, text="original", admin_reply=None,
                member_read_at=None, admin_read_at=None, is_read=False,
                caption="cap", display_title=None, username="member", video_id=3):
    return {
        "id": comment_id, "video_id": video_id, "user_id": user_id, "username": username,
        "comment_text": text, "admin_reply": admin_reply, "is_read": is_read,
        "replied_at": None, "created_at": datetime(2024, 1, 1, 10, 0, 0),
        "thread_updated_at": datetime(2024, 1, 1, 10, 0, 0),
        "member_read_at": member_read_at, "admin_read_at": admin_read_at,
        "video_caption": caption, "video_name": "file.mp4",
        "display_title": display_title, "video_missing": False,
    }


def thread_row(message_id, author_role, body, minutes=0, comment_id=7):
    return {
        "id": message_id, "comment_id": comment_id, "author_role": author_role,
        "author_id": MEMBER_ID if author_role == "member" else ADMIN_ID,
        "body": body, "origin": "thread",
        "created_at": datetime(2024, 1, 1, 10, 0, 0) + timedelta(minutes=minutes),
    }


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        _bootstrap()
        import psycopg2
        import psycopg2.pool
        import telebot.apihelper

        telebot.apihelper._make_request = lambda *a, **k: (_ for _ in ()).throw(
            NetworkBlocked("telebot HTTP blocked"))
        psycopg2.connect = lambda *a, **k: (_ for _ in ()).throw(
            NetworkBlocked("psycopg2 blocked"))
        psycopg2.pool.SimpleConnectionPool = lambda *a, **k: (_ for _ in ()).throw(
            NetworkBlocked("psycopg2 pool blocked"))
