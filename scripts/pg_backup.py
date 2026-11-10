"""Create a plain-SQL backup of the production database.

Equivalent intent to ``pg_dump -F p`` (schema columns + COPY data, UTF-8), written with
psycopg2 because this machine has no ``pg_dump`` binary. The dump is read-only against
the source database and streams rows straight to disk in ``pg_dump``-like format:

    -- table summary header
    ALTER TABLE ... ALTER COLUMN ... SET DEFAULT ...;   -- identity defaults only
    BEGIN;
    TRUNCATE TABLE <table> RESTART IDENTITY;            -- a restore target, see README
    COPY <table> (<columns>) FROM stdin;
    ...
    \\.
    COMMIT;

Usage:
    python scripts/pg_backup.py <output.sql>
    python scripts/pg_backup.py <output.sql> --data-only   # no TRUNCATE lines

The file contains every file_id and user row of the archive: never commit it.
"""

import sys
from datetime import datetime, timezone

import psycopg2

import config

SKIP_TABLES = {"playing_with_neon"}
BATCH = 100

# Explicit column lists so a schema change never silently shifts COPY data.
# Old Title column is intentionally not dumped because the bot never uses it.
TABLE_COLUMNS = {
    "bot_settings": "setting_key, setting_value",
    "bot_users": "user_id, username, first_name, join_date",
    "categories": "id, name, parent_id, full_path",
    "required_channels": "channel_id, channel_name",
    "user_favorites": "id, user_id, video_id, date_added",
    "user_history": "id, user_id, video_id, last_watched",
    "user_states": "user_id, state, context, last_update",
    "video_archive": (
        "id, message_id, caption, chat_id, file_name, file_id, category_id, metadata, "
        "view_count, grouping_key, upload_date, thumbnail_file_id, content_type, display_title"
    ),
    "video_comment_messages": "id, comment_id, author_role, author_id, body, origin, created_at",
    "video_comments": (
        "id, video_id, user_id, username, comment_text, admin_reply, is_read, replied_at, "
        "created_at, thread_updated_at, member_read_at, admin_read_at"
    ),
    "video_ratings": "id, video_id, user_id, rating",
}


def _connect():
    if not config.DB_CONFIG:
        raise SystemExit("DATABASE_URL is not set")
    conn = psycopg2.connect(
        connect_timeout=30,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
        **config.DB_CONFIG,
    )
    conn.set_session(readonly=True, autocommit=False, isolation_level="REPEATABLE READ")
    return conn


def _columns(cur, table):
    if table in TABLE_COLUMNS:
        return TABLE_COLUMNS[table]
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    )
    return ", ".join(row[0] for row in cur.fetchall())


def _literal(value):
    """COPY text-format value: NULL, escaped tabs/newlines/backslashes."""
    if value is None:
        return "\\N"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="microseconds")
    if isinstance(value, bool):
        return "t" if value else "f"
    text = str(value).replace("\\", "\\\\").replace("\t", "\\t")
    return text.replace("\n", "\\n").replace("\r", "\\r")


def _write_table(cur, out, table, data_only):
    columns = _columns(cur, table)
    cur.execute(f'SELECT count(*) FROM {table}')  # noqa: S608 - table whitelist
    total = cur.fetchone()[0]
    out.write(f'\n-- {table}: {total} rows (one statement per {BATCH} rows)\n')
    if not data_only:
        out.write(f"TRUNCATE TABLE {table} RESTART IDENTITY;\n")
    if total == 0:
        print(f"{table}: 0 rows", flush=True)
        return 0

    cur.execute(f'SELECT {columns} FROM {table} ORDER BY 1')  # noqa: S608 - table whitelist
    written = 0
    while True:
        rows = cur.fetchmany(BATCH)
        if not rows:
            break
        values = "".join("\t(" + ", ".join(_literal(v) for v in row) + ")\n" for row in rows)
        out.write(f"COPY {table} ({columns}) FROM stdin;\n{values}\n\\.\n")
        out.flush()
        written += len(rows)
    print(f"{table}: {written} rows", flush=True)
    return written


def main(argv):
    if len(argv) < 2:
        raise SystemExit("usage: python scripts/pg_backup.py <output.sql> [--data-only]")
    out_path = argv[1]
    data_only = "--data-only" in argv

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version(), current_database(), now()")
            server, database, started = cur.fetchone()
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
            tables = [row[0] for row in cur.fetchall() if row[0] not in SKIP_TABLES]

            with open(out_path, "w", encoding="utf-8", newline="") as out:
                out.write("-- video-bot-r database backup\n")
                out.write(f"-- created:  {datetime.now(timezone.utc).isoformat()}\n")
                out.write(f"-- snapshot: {started.isoformat()} (single REPEATABLE READ, read-only)\n")
                out.write(f"-- database: {database}\n")
                out.write(f"-- server:   {server}\n")
                out.write("-- tool:     scripts/pg_backup.py (psycopg2)\n")
                out.write("-- RESTORE WARNING: TRUNCATE lines are a restore target; the file\n")
                out.write("-- replaces the tables it contains. Take a fresh backup before restoring.\n\n")
                out.write("BEGIN;\n")
                for table in tables:
                    _write_table(cur, out, table, data_only)
                out.write("\nCOMMIT;\n")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    main(sys.argv)
