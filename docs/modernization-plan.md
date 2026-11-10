# Incremental Modernization — Design & Plan

Scope approved: (A) private continuous inquiry threads, (B) admin display-title rename,
(C) concrete webhook reliability/security bugs. Deferred: Mini App / widgets, package
upgrades, project rewrite.

Constraints: additive and idempotent database changes, legacy data preserved, offline
unittest suite only (no live DB or Telegram), production entry point unchanged
(`python webhook_bot.py`).

## A. Private inquiry threads (`video_comments`)

Existing table keeps `comment_text` (original inquiry) and `admin_reply` (legacy single
reply). Both stay read-only for new code paths so old data always renders.

Additive schema (created by `verify_and_repair_schema()` on every boot, all
`IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`, membership checked from
`information_schema`; no drop/rename/backfill-in-place):

| object | purpose |
| --- | --- |
| `video_comment_messages(id, comment_id, author_role, author_id, body, origin, created_at)` | ordered thread timeline; `author_role` in `member`/`admin`; `origin` in `thread`/`legacy_original`/`legacy_reply` |
| `video_comments.thread_updated_at` | ordering/badge for "needs reply" |
| `video_comments.member_read_at` | member read marker |
| `video_comments.admin_read_at` | admin read marker |
| index on `video_comment_messages(comment_id, id)` | ordered page reads |
| unique index `(comment_id, origin)` where `origin <> 'thread'` | legacy history imported at most once |

Legacy import: one set-based `INSERT ... SELECT` guarded by
`ON CONFLICT DO NOTHING`, so repeated boot is idempotent and pre-existing history is
never duplicated or overwritten. Outcome is reported as counts
(`legacy_originals_imported`, `legacy_replies_imported`) which are 0 on the second run.

New messages append `origin='thread'` rows only; nothing in the thread path updates
`comment_text` or `admin_reply`, so follow-ups can never overwrite history.
`reply_to_comment()` stores the latest reply in `admin_reply` for legacy compatibility
*and* appends a thread message in the same transaction.

State model (read/waiting) per role:
* member: `needs_reply` (last thread message is theirs) vs `answered`; `member_read_at`
  newer than latest message → `read`.
* admin: same with `admin_read_at` (feeds the unread badge).

Handler rules (`handlers/comment_handlers.py`):
* every callback re-checks admin rights; member callbacks re-check ownership
  (`comment.user_id == caller`), otherwise `answer(..., show_alert=True)`.
* interaction is private-chat only (`call.message.chat.type == 'private'`,
  `message.chat.type == 'private'`).
* output is HTML (not Markdown): all user/admin text goes through `html.escape`,
  clipped to bounded length; pages of `COMMENTS_PER_PAGE_DEFAULT` thread rows.
* reply input is bound by a prompt token stored in `user_states.context`
  (`prompt_id`, `comment_id`, `updated_before`). A prompt is only honoured when it is
  the active state and the thread has not changed since the prompt was rendered; stale
  callbacks never silently redirect the text to a newer inquiry.
* delivery truthfulness: the reply is persisted first; the notification result
  (`delivered` / `blocked` / `failed`) is reported separately to the admin, so a
  failed notification is never reported as delivered and a retry never duplicates the
  persisted reply (`already_answered` guard).
* legacy inline callback data (`reply_comment::<id>`, `my_comments::<page>`, ...) keeps
  working; new flows use additional `::` fields that old data never had.

## B. Admin display title

Additive `video_archive.display_title TEXT NULL`; `caption`, `file_name`, `file_id`,
`message_id` are never modified (no physical rename, no channel mutation).
`utils.video_title(video)` is the single display resolver: `display_title` →
first caption line → file name → fallback. Applied in list/category/search/favorites/
history (via `helpers`), detail text (`/video`), inline results, and comment headers.
Search adds `display_title` to caption/file_name patterns (`normalized_search_patterns`
handles wildcard escaping). Saving invalidates the search cache
(`clear_search_cache`). Flow: admin sends id → preview → edit (`/title <text>` or plain
text in the bound state) → save/cancel buttons; empty/overlong (`> MAX_DISPLAY_TITLE`)
rejected at input, preview and **again** at save (rights re-checked at every step, and
the video must still exist).

## C. Webhook (`webhook_bot.py`)

Concrete fixes only:

1. `HTTPException` re-raised in every route except the Telegram webhook handler, so
   `abort(400/403/404/429)` keeps its status instead of becoming 500.
2. Mutating management routes (`/set_webhook`, `/admin/*`) fail closed without a
   configured credential: constant-time match on the `X-Admin-Token` header or
   `token` argument against `MANAGEMENT_TOKEN` (or `WEBHOOK_SECRET` when explicitly
   configured). No new mandatory variable: the process still boots without it, but
   those routes answer 503 until it is set. `admin_id` alone is no longer sufficient.
3. `/set_webhook` accepts POST only, never drops pending updates by default
   (`drop_pending_updates` only when explicitly requested), includes `channel_post`.
4. Startup webhook setup does not remove the webhook first and does not drop pending
   updates unless `WEBHOOK_DROP_PENDING=1`.
5. `/thumbnail/<file_id>` returns a redirect through the existing host route instead
   of a URL that contains the bot token.
6. Rate limiting no longer applies a global "50 per hour" to the Telegram webhook;
   only admin routes keep a limit, the webhook only has a per-IP guard.
7. `process_update` dispatches `channel_post` and keeps dispatch/state failures from
   turning into Telegram retries of already-handled updates.
8. `/cancel` is handled before state dispatch; a `/command` never enters a pending
   text state.
9. Secrets are never logged; missing `WEBHOOK_SECRET` is reported as a warning without
   the value.

`/live` and `/ready` probes are added (README already promised them).

## Implementation status (phase 2 — webhook hardening)

Done in `webhook_bot.py` (offline tests in `tests/test_webhook.py`):

1. `HTTPException` is re-raised in every route handler (webhook, `/set_webhook`,
   `/webhook_info`, `/thumbnail`, the file proxy and every `/admin/*` route), so
   `abort(400/403/404/429)` keeps its status; only genuine server errors become 500.
2. `management_route` decorator on `/set_webhook`, every `/admin/*` route and
   `/webhook_info`: 503 when no management credential is configured, 401 on a wrong
   or missing token, constant-time `hmac.compare_digest` against the `X-Admin-Token`
   header (or `?token=`). Credential order: `WEBHOOK_MANAGEMENT_TOKEN`, else a
   manually configured `WEBHOOK_SECRET` (the literal default value never counts).
   Startup/health/webhook routes are unaffected; nothing new is mandatory.
3. `/set_webhook` is POST-only; `drop_pending_updates` defaults to False and needs
   both `drop_pending_updates=1` and `confirm=1`; the old webhook is only removed
   first when `remove_first=1&confirm=1`; `allowed_updates` includes `channel_post`
   (and edited variants); the returned URL is redacted.
4. Boot-time setup (`_configure_webhook`) no longer calls `remove_webhook()` and
   sends `drop_pending_updates=False` unless `WEBHOOK_DROP_PENDING=1`.
5. `/thumbnail/<file_id>` redirects to the same-host `/telegram_file/<path>` proxy,
   which builds the tokenised Telegram URL server-side; the token never reaches the
   client, the redirect body, or the logs.
6. No global rate limit. Management routes: 120/hour, counted only for authorised
   callers (`deduct_when`), webhook: 600/minute per IP, health/`/live`/`/ready`
   exempt.
7. `process_update` dispatches `channel_post`, `edited_message` and
   `edited_channel_post` and never re-raises, so a handled update is not re-delivered.
8. `/cancel` is routed before state dispatch in `state_manager.handle_message`
   (phase 1), and `webhook()` passes the message through it before Telegram
   dispatch; invalid/empty JSON is rejected with 400 (never treated as success).
9. No secret logging; the management credential is only reported as set/unset and
   the webhook URL is logged/returned redacted.

`/live` always answers 200 (no DB); `/ready` reports 503 until `init_bot` finishes
and stays 503 when the schema check fails (the state is recorded, not re-probed).

## D. Tests

`tests/` is a standard `unittest` package. `tests/__init__.py` turns
`TELEGRAM_HTTP_BLOCKED=1` on, which forces any access to
`telebot.apihelper._make_request` to raise. Every test therefore runs offline even when
credentials exist in the environment; fakes stand in for the bot and for the database
cursor. Real behaviour (handlers, SQL strings, Flask routes) is exercised — no
source-text assertions. No PostgreSQL integration is claimed; the DB is a fake
connection recording executed statements.

Acceptance: `python -m unittest discover -s tests -v`,
`python -m compileall -q handlers db_manager.py state_manager.py webhook_bot.py utils.py tests`,
`git diff --check`.

## Implementation status (phase 1 — threads + display titles)

Done and covered by `tests/` (offline, fakes for bot and DB; Telegram HTTP and
`psycopg2.connect` are blocked before production modules are imported):

* Schema bootstrap is atomic and ordered: parent tables (incl. `video_comments`)
  first, then additive columns, then the child table `video_comment_messages`
  (FK), index, unique legacy index and the legacy import — all in one
  transaction; any failure rolls back and re-raises, so nothing half-applied stays.
* Legacy import is guarded twice (`NOT EXISTS` per `(comment_id, origin)` +
  `ON CONFLICT DO NOTHING`), keeps original text/author/timestamps and never
  updates/deletes legacy rows.
* Member/admin threads: ownership and admin checks on every callback, private
  chat only, HTML-escaped output, full bodies via page/chunk splitting (no silent
  truncation), ordered oldest-first.
* Reply/follow-up input is bound to the bot prompt message
  (`prompt_message_id` + thread fingerprint); a stale or unrelated message is
  refused instead of being routed to another thread.
* Persisted reply vs notification are separate; a failed notification is
  reported as "saved but not notified" and never re-saves the reply.
* Mark-read keeps the reply button; an unread member message still shows as
  awaiting reply.
* `state_manager` routes `/cancel` before dispatch, never clears
  `editing_display_title` before the user handler, and never lets a `/command`
  become a title/reply text.
* Display titles: additive `display_title` with top precedence over series
  metadata, used by lists/search/favorites/history/detail/inline/comments;
  validation (empty/overlong) at input and again at save, rights re-checked at
  save, cache invalidated.

Not done yet: no live PostgreSQL or Telegram verification is claimed by this phase; the
Render smoke checklist in `README.md` is still to be executed by the operator.
