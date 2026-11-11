# 🎬 Video Bot - Telegram Video Archive Bot

## 📋 Overview
Telegram bot for managing and organizing a video archive. Production runs as a Flask
webhook service on Render (`python webhook_bot.py`), with PostgreSQL for storage.

## 🔧 Features
- ✅ **Webhook Mode**: Flask entry point, `POST /bot<token>` receives Telegram updates
- ✅ **Inquiry threads**: private member/admin conversations per video comment, with
  unread badges, pagination and legacy replies kept readable
- ✅ **Instant admin alerts**: every new inquiry/follow-up pings the admins with a
  one-tap "رد الآن" button (reply) and "المحادثة كاملة"
- ✅ **Display titles**: admins can set an archive-only title without renaming the
  channel message (`caption`/`file_name`/`file_id` stay untouched): by name
  (`old => new`) or by ID
- ✅ **Native command menu**: `setMyCommands` publishes Arabic descriptions for members
  and an extended admin-only list per admin chat; the ☰ menu button opens them
- ✅ **Mini App library** (`/app`): in-Telegram grid of cards with thumbnails, live
  search, category chips, paging and favorites (identity from validated `initData`)
- ✅ **Connection Pooling**: optimized PostgreSQL connections
- ✅ **Additive auto-migration**: schema bootstrap runs on every boot
  (`verify_and_repair_schema`), only `CREATE ... IF NOT EXISTS` / `ADD COLUMN IF NOT
  EXISTS`; nothing is dropped or renamed automatically
- ✅ **Health endpoints**: `/`, `/health`, `/live`, `/ready` for monitoring
- ✅ **Favorites, history, ratings, categories, search, inline mode**
- ✅ **Admin tools**: `/admin/*` maintenance routes (authenticated, see below)
- ✅ **Offline test suite**: `tests/` runs without network, database or Telegram

### Library (pyTelegramBotAPI 4.37.1)
The client library is pinned to `4.37.1` (Bot API 10.2/10.3 era). The upgrade was
audited against the previous `4.33.0` before applying: the only API removals are
inline-result thumbnail aliases, a `Bot(...)` constructor flag and the old
`MenuButtonWebApp` shape — none of them used by this bot. `4.33.0` already supported
`style=`, `WebAppInfo`, `MenuButton*` and `set_my_commands`, so the upgrade by itself
adds no user-facing feature; it mainly keeps the client current and adds `aiohttp`
as a hard dependency (installed by `pip install -r requirements.txt`). HTML Widgets
(Bot API 10.4) are **not** exposed by the library yet, so the bot does not use them.
Details: `docs/pytba-upgrade-audit.md`.

## 🚀 Deployment (Render.com)

### Required Environment Variables
```env
BOT_TOKEN=your_telegram_bot_token
DATABASE_URL=postgresql://user:password@host:port/database
CHANNEL_ID=-1001234567890
ADMIN_IDS=123456789,987654321
APP_URL=https://your-app-name.onrender.com
```

`APP_URL` must use `https://`; the process refuses to boot otherwise. `BASE_URL` is
accepted as an alternative name in older deployments.

### Optional Environment Variables
| Variable | Default | Purpose |
| --- | --- | --- |
| `WEBHOOK_MANAGEMENT_TOKEN` | unset | Bearer token for `/set_webhook`, `/webhook_info` and `/admin/*`. Unset ⇒ those routes answer **503** and the bot still runs. |
| `WEBHOOK_SECRET` | `default_secret` | Telegram `secret_token` for the webhook. Also used as the management token **only** when set to a real (non-default) value. |
| `WEBHOOK_DROP_PENDING` | `0` | `1` makes boot-time `setWebhook` drop pending updates. Leave unset to keep queued updates. |
| `MINIAPP_BOT_USERNAME` | bot `@username` from `getMe` | Username used for the "open in Telegram" deep link inside the Mini App. |
| `PORT` | `10000` | HTTP port (Render sets this automatically). |

The literal default `default_secret` is never accepted as a credential, so an
existing deployment that never set `WEBHOOK_SECRET` keeps booting and serving updates.

### Render Settings
- **Runtime**: Python 3.11.9 (`runtime.txt`)
- **Build Command**: `pip install -r requirements.txt`
  (`build.sh` additionally installs the `mediainfo` system package, which
  `pymediainfo` needs; use `bash build.sh` if you rely on video metadata)
- **Start Command**: `python webhook_bot.py`
- **Health Check Path**: `/live` (does not touch the database)

## 🔐 Webhook Management Security
`/set_webhook`, `/webhook_info` and every `/admin/*` route are protected:

- No credential configured ⇒ **503** (`Management API disabled`), never an open door.
- Wrong or missing credential ⇒ **401**.
- Correct credential ⇒ the route runs. Send it as the `X-Admin-Token` header
  (preferred) or as a `token` query argument; the comparison is constant-time.
- Credential resolution order: `WEBHOOK_MANAGEMENT_TOKEN`, else a manually configured
  `WEBHOOK_SECRET`.
- `admin_id` alone is **not** authentication anymore.

```bash
curl -X POST -H "X-Admin-Token: $WEBHOOK_MANAGEMENT_TOKEN" \
  https://your-app-name.onrender.com/set_webhook
```

`/set_webhook` is POST-only and safe by default: it does not drop pending updates and
does not remove the existing webhook first. Both need an explicit, confirmed request:

```bash
# keep pending updates (default), include channel_post in allowed_updates
curl -X POST -H "X-Admin-Token: $TOKEN" https://app.onrender.com/set_webhook
# explicitly drop pending updates
curl -X POST -H "X-Admin-Token: $TOKEN" \
  "https://app.onrender.com/set_webhook?drop_pending_updates=1&confirm=1"
# explicitly remove the old webhook before setting the new one
curl -X POST -H "X-Admin-Token: $TOKEN" \
  "https://app.onrender.com/set_webhook?remove_first=1&confirm=1"
```

`allowed_updates` covers `message`, `edited_message`, `channel_post`,
`edited_channel_post`, `callback_query` and `inline_query`. If `WEBHOOK_SECRET` is
configured, the same value is still sent to Telegram as `secret_token`, and the webhook
route rejects requests without the matching `X-Telegram-Bot-Api-Secret-Token` header
with **403**.

Thumbnails are served through `/thumbnail/<file_id>` → `/telegram_file/<path>`; the bot
token is added server-side and never appears in a client-visible URL or in the logs.

## 📡 Endpoints
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/` | – | Health check |
| `GET` | `/health` | – | Health + DB status (always 200; `database` reports the error) |
| `GET` | `/live` | – | Liveness (always 200 while the process runs) |
| `GET` | `/ready` | – | Readiness; 503 until boot migration succeeds |
| `POST` | `/bot<token>` | Telegram secret (if configured) | Telegram webhook |
| `POST` | `/set_webhook` | management token | Configure the webhook |
| `GET` | `/webhook_info` | management token | Webhook status (URL redacted) |
| `GET` | `/thumbnail/<file_id>` | – | Redirect to the token-free file proxy |
| `GET` | `/telegram_file/<path>` | – | Server-side Telegram file redirect |
| `GET` | `/app` | – | Mini App library page (inside Telegram) |
| `GET` | `/app/api/library`, `/app/api/categories`, `/app/api/video/<id>` | – | Mini App read APIs (no `file_id`/`chat_id` in responses) |
| `POST` | `/app/api/favorite` | Telegram `initData` (validated) | Add/remove a favorite for the signed-in user |
| `GET` | `/app/thumb/<video_id>` | – | Redirect to `/thumbnail/...` without exposing `file_id` |
| `POST` | `/admin/publish_commands` | management token | Re-publish the bot command menu |
| `GET/POST` | `/admin/*` | management token | Maintenance tools |

No global rate limit is applied: the webhook has a per-IP guard, management routes have
their own limit (unauthorised attempts are not counted), and health probes are exempt.

## 🗄️ Database
PostgreSQL, accessed through a connection pool. Tables: `video_archive`,
`video_comment_messages`, `video_comments`, `categories`, `bot_users`, `user_favorites`,
`user_history`, `video_ratings`, `user_states`, `bot_settings`, `required_channels`.

### Migrations on boot
`verify_and_repair_schema()` runs at startup inside one transaction and is idempotent:

- creates missing tables, additive columns (`video_archive.display_title`,
  `video_comments.thread_updated_at`, `member_read_at`, `admin_read_at`) and indexes;
- imports legacy `comment_text`/`admin_reply` into `video_comment_messages` at most
  once (guarded by a unique partial index + `ON CONFLICT DO NOTHING`);
- never drops, renames or rewrites existing rows or media columns.

Any failure rolls the whole bootstrap back and re-raises, so a half-applied schema is
not left behind.

### Backup before deploying
Take a database backup before the first deploy of a new version:

```bash
pg_dump --no-owner --format=custom "$DATABASE_URL" > backup_before_deploy.dump
```

If `pg_dump` is not installed on the machine, this repository ships an equivalent
read-only plain-SQL dump (single `REPEATABLE READ` snapshot, `COPY` data):

```bash
DATABASE_URL=... python scripts/pg_backup.py docs/backup.sql
```

`docs/backup.sql` contains every `file_id` and user row: it is **gitignored** and must
never be committed or shared.

### Manual rollback
The changes are additive, so a rollback is a code rollback plus *optional* cleanup —
never an automatic downgrade:

1. Redeploy the previous commit/release and restart the service.
2. Old code keeps working against the new schema (new tables/columns are simply
   unused); no data is lost and no migration must be undone.
3. Only if you explicitly want the new objects gone, remove them by hand after taking
   a fresh backup:

```sql
-- optional, manual, destructive: only after a verified backup
DROP TABLE IF EXISTS video_comment_messages;
ALTER TABLE video_comments DROP COLUMN IF EXISTS thread_updated_at;
ALTER TABLE video_comments DROP COLUMN IF EXISTS member_read_at;
ALTER TABLE video_comments DROP COLUMN IF EXISTS admin_read_at;
ALTER TABLE video_archive  DROP COLUMN IF EXISTS display_title;
```

Legacy `video_comments.comment_text` / `admin_reply` values are preserved throughout, so
removing the thread table does not remove the original inquiries or replies.

## ✅ Telegram Smoke Checklist (after deploy)
Run once in a staging chat/channel; these checks require a live bot and are **not**
covered by the offline suite:

1. `GET /` and `GET /live` return 200; `GET /ready` returns 200 after boot.
2. `POST /set_webhook` with the management header returns 200 and reports
   `drop_pending_updates: false`.
3. `GET /webhook_info` shows the expected URL (redacted) and no recent error.
4. Send `/start` to the bot in private: the main menu appears within a few seconds.
5. Search a known title: results render with the display title and thumbnails load
   (no bot token in the image URL).
6. Open a video → comment → confirm the admin receives the inquiry; reply from the
   admin side and confirm the member sees the answer.
7. Send `/cancel` while a title/reply prompt is open: the prompt is cancelled and the
   text is not saved anywhere.
8. Post a video in the archive channel and confirm the bot indexes it
   (`channel_post` handling) without dropping other updates.
9. Confirm `/admin/db_stats` returns 401 without the management token and 200 with it.
10. Open the ☰ menu: member commands appear with Arabic descriptions; as an admin the
    extended list appears in your private chat (re-publish with
    `POST /admin/publish_commands` if it looks stale).
11. Admin panel → «✏️ تعديل اسم الفيديو»: type an existing name, confirm the match, send
    the new name; the new title shows in search/lists and the channel caption is
    unchanged.
12. A member sends a new inquiry: every admin receives an alert with «رد الآن» that
    opens the reply prompt for that exact thread.
13. Send `/library` in private (or press «📱 مكتبة الفيديو»): the Mini App opens with
    thumbnail cards; search, a category chip, next/previous, and the favorite button
    persist after reloading the app.

## 🛠️ Development
```bash
python -m unittest discover -s tests -v
python -m compileall -q handlers db_manager.py state_manager.py webhook_bot.py utils.py miniapp.py tests scripts
```

The suite is offline by design: `tests/__init__.py` sets `TELEGRAM_HTTP_BLOCKED=1` and
`tests/support.py` blocks `telebot` HTTP plus `psycopg2.connect` before production
modules are imported. No live database or Telegram call is made, and **no live
PostgreSQL/Telegram integration is verified by these tests** — the smoke checklist above
is the only end-to-end verification path.

`legacy/` contains the original polling-mode files and is not used in production.

---
*Version: 2.0.0 - Webhook Mode*
