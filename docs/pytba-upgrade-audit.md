# pyTelegramBotAPI upgrade audit: 4.33.0 → 4.37.1

Date: 2026-11-10 · Offline audit, no commit/push, no Telegram calls.

## Method

- Installed package (baseline): `C:\Users\helen\AppData\Roaming\Python\Python314\site-packages\telebot`,
  `telebot/version.py` → `__version__ = '4.33.0'`; dist-info metadata `pytelegrambotapi-4.33.0`.
- Candidate: extracted wheel `pytelegrambotapi-4.37.1-py3-none-any.whl` →
  `C:\Users\helen\AppData\Local\Temp\pytba\new\telebot`, `version.py` → `'4.37.1'`.
- Compared: full tree file list, then per-file unified diff, then an AST diff of the
  public surface in `__init__.py`, `apihelper.py`, `types.py`, `util.py`,
  `custom_filters.py`, `callback_data.py` (classes, methods, function/keyword params).
- Runtime evidence: `scripts/pytba_probe.py` executed under 4.33.0 before the bump and
  again under 4.37.1 after it (outputs below).
- Repo impact: ripgrep over all `*.py` for every removed/renamed name and for the
  feature names the task asked about.

Files changed between the two wheels: `__init__.py`, `apihelper.py`,
`async_telebot.py`, `asyncio_helper.py`, `types.py`, `util.py`, `version.py`,
`states/asyncio/middleware.py`. No files added or deleted.

## Bot API version each wheel targets

Neither wheel states its Bot API level in metadata; the level is derived from the
shipped surface:

| Wheel | Python | Dependencies | Bot API evidence |
|---|---|---|---|
| 4.33.0 | `>=3.10` | `requests` only | `style` on inline/reply buttons (Bot API 9.2), `copy_text`, `request_users`, `request_chat`, `request_managed_bot`, `show_caption_above_media`. No `disabled`, no ephemeral messages, no rich messages. → **Bot API 9.2+** |
| 4.37.1 | `>=3.11` (classifiers list 3.11–3.15, so 3.14 is supported) | `requests` + `aiohttp` (new hard dependency) | Adds `disabled` (DisabledButton), `EphemeralMessageParameters` + `edit/send/delete ephemeral*` methods, `InputRichMessage`/`RichText`/`RichBlock*`, `send_rich_message`, `BotCommand.is_ephemeral`, managed-bot methods, guest-query/subscription/stopped-generation handlers. The package's own docstring calls the previous field `"Bot API 10.2 ephemeral message arguments"` → **Bot API 10.2+ (10.3-era surface)** |

## New / changed public surface in 4.37.1

Feature names requested by the task:

- `InlineKeyboardButton(style=...)`: unchanged behaviour, still serialized. **New:**
  `disabled=DisabledButton` kwarg (`DisabledButton().to_dict() == {}`).
- `InlineKeyboardMarkup` / `ReplyKeyboardMarkup`: new `keyboard` alias and
  `force_reply` field in `to_dict` (only emitted when set). Existing positional/keyword
  usage unchanged.
- `BotCommand`: new `is_ephemeral` kwarg (omitted from `to_dict` unless set).
- `MenuButtonWebApp`: **signature change** from `(type, text, web_app, **kwargs)` to
  `(text, web_app, **kwargs)` — the same call the bot would make now works on both
  (`MenuButtonWebApp(text=..., web_app=...)` fails on 4.33.0, works on 4.37.1 with
  `{'type': 'web_app', ...}`). `MenuButtonCommands`/`MenuButtonDefault` unchanged.
- `WebAppInfo`: unchanged (`{'url': ...}`).
- New Bot methods (35 new defs): `send_rich_message`, `send_rich_message_draft`,
  `edit_ephemeral_message_{text,caption,media,reply_markup}`, `delete_ephemeral_message`,
  `send_live_photo`, `delete_message_reaction`, `delete_all_message_reactions`,
  `set_managed_bot_access_settings`, `get_managed_bot_access_settings`,
  `send_chat_join_request_web_app`, `answer_chat_join_request_query`, `answer_guest_query`,
  `get_user_personal_chat_messages`, `register_{guest_message,subscription,stopped_message_generation}_handler`, `async_antiflood`, `can_delete_outgoing_messages`, …
- New send kwargs (additive, defaults unchanged): `send_message`/`send_video`/
  `send_document`/`send_photo` gain `ephemeral_message_parameters`, `receiver_user_id`,
  `callback_query_id`; `edit_message_text` gains `rich_message`.
- ~120 new types: `DisabledButton`, `EphemeralMessageParameters`, all `InputRichBlock*` /
  `RichBlock*` / `RichText*` / `RichMessage*`, `InputMedia{Link,LivePhoto,Location,Sticker,Venue,VoiceNote}`,
  `InputPaidMediaLivePhoto`, `LivePhoto`, `MessageId`, `SentGuestMessage`, `PollMedia`,
  `InlineQueryResult` (new base class), `Community*`, `BotAccessSettings`,
  `BotSubscriptionUpdated`, `MessageGenerationStopped`, `Link`.
- **No `Widget*` types exist in either wheel** (`WidgetInfo` etc. absent, greps of both
  trees and the `dir()` probe agree). There is nothing "widget" to adopt here; the
  nearest equivalent in 4.37.1 is the rich-message family.

## Removed / renamed public API (the only breaking surface found)

| Removed | Where | Used by this bot? |
|---|---|---|
| keyword arg `thumb_url` | `InlineQueryResultArticle` | **no** |
| keywords `thumb_width`, `thumb_height` | `InlineQueryResultArticle` | **no** |
| methods/attrs `thumb_url`, `thumb_width`, `thumb_height` | `InlineQueryResult{Article,Contact,Document,Location,Venue}` (`thumbnail_*` fields remain) | **no** |
| methods/attrs `thumb_url`, `thumb_mime_type` | `InlineQueryResult{Gif,Mpeg4Gif,Photo,Video}` (`thumbnail_*` remain) | **no** |
| `InlineQueryResultBase` `__init__` / `to_dict` / `to_json` (re-parented to new `InlineQueryResult`) | inline result hierarchy | **no** |
| `InlineQueryResultCachedBase.__init__` / `to_json` (subclass now overrides) | inline result hierarchy | **no** |
| `Bot.__init__(..., can_delete_outgoing_messages=...)` | `Bot` | **no** (replaced by method `can_delete_outgoing_messages()`) |

Repo proof (`grep` over every `*.py`): no match for `can_delete_outgoing_messages`,
`InlineQueryResultBase`, `InlineQueryResultCachedBase`, `register_guest_message_handler`,
`send_rich_message`, `disabled=`. The only inline-result constructions in the bot are
`handlers/inline_handlers.py`: `InlineQueryResultArticle(id, title, description,
input_message_content)`, `InlineQueryResultCachedDocument(id, title, document_file_id,
description, caption, parse_mode)` and `InlineQueryResultCachedVideo(id, title,
video_file_id, description, caption, parse_mode)` — all of those signatures are
unchanged at 4.37.1 (AST diff: `init_old == init_new`), and both cached classes only
gained a `to_dict` override.

**Removals affecting the bot: none.**

## Runtime evidence that 4.33.0 already had the needed features

`scripts/pytba_probe.py` under 4.33.0:

```
library_version: 4.33.0
InlineKeyboardButton.has_style: True
InlineKeyboardButton.has_disabled: False
styled_button: {'text': 'x', 'callback_data': 'c', 'style': 'success'}
WebAppInfo: {'url': 'https://example.com/app'}
web_app_button: {'text': 'open', 'web_app': {'url': 'https://example.com/app'}}
MenuButtonDefault: True          MenuButtonCommands: True          MenuButtonWebApp: True
MenuButtonWebApp.sig: (self, type: 'str', text: 'str', web_app: 'WebAppInfo', **kwargs)
Bot.set_chat_menu_button: ['self', 'chat_id', 'menu_button']
Bot.set_my_commands: ['self', 'commands', 'scope', 'language_code']
Bot.answer_web_app_query: ['self', 'web_app_query_id', 'result']
BotCommand: {'command': 'start', 'description': 'بدء'}
BotCommandScopeDefault: {'type': 'default'}   BotCommandScopeChat: {'type': 'chat', 'chat_id': -100123}
```

`style=` is a real first-class parameter at 4.33.0 (not swallowed by `**kwargs`), and
`to_dict()` already emits it, so the "safe fallback" branch in
`handlers/button_styles.py` (the `except TypeError` around `super().__init__`) is
unreachable on both versions. Message/reply keyboards also support `style` at 4.33.0.

Same probe under 4.37.1 after the bump: `has_disabled: True`, `MenuButtonWebApp.sig:
(self, text, web_app, **kwargs)`, everything else identical; `has_DisabledButton: True`,
`has_EphemeralMessageParameters: True`, `has_WidgetInfo: False`, `has_InputRichMessage:
True`.

## Upgrade decision and gates

No bot-affecting removal → upgrade applied.

- `requirements.txt`: `pyTelegramBotAPI==4.37.1` (only that line changed).
- Installed: `pyTelegramBotAPI==4.37.1` (+ new hard dependency `aiohttp==3.14.5` and its
  transitive deps `aiohappyeyeballs`, `aiosignal`, `attrs`, `frozenlist`, `multidict`,
  `propcache`, `yarl`; 4.33.0 needed only `requests`). Sync `psycopg2` code paths are
  unaffected — `aiohttp` is only imported by the async helper module.
- `python -m unittest discover -s tests -v` → **139 tests, OK** (121 before, +18 new in
  `tests/test_button_styles.py`).
- `python -m compileall -q handlers db_manager.py state_manager.py webhook_bot.py utils.py tests scripts` → exit 0.
- `git diff --check` → exit 0 (only LF→CRLF notices, no whitespace errors).

## handlers/button_styles.py — no change

The file works unchanged on 4.37.1 (all styled-button tests pass). The `except
TypeError` fallback is provably dead on both 4.33.0 and 4.37.1, but it was left in place:
the task's rule is to remove fallbacks only when removal is clearly safe, and the bot
pins the library, so the safety margin the try/except buys for a future pin downgrade is
not worth the diff. The `to_dict()` re-assertion of `style` is harmless (same value as
the parent class writes) and is the behaviour the new tests lock in. Note: the file
currently shows as modified in `git status` due to a pre-existing line-ending change only
(`git diff` is empty for it); that was left untouched.

## Residual risks / limits

- Not exercised live against Telegram (offline by instruction); no Bot API 10.x-only
  behaviour was adopted, so the bot keeps working on the 9.2-era endpoints it already uses.
- Deployment installs `aiohttp` for the first time; if the Render build pins wheels, it
  needs an `aiohttp` wheel for the runtime Python (available for 3.11–3.14).
- `Handler`/dispatcher internals were only compared at the AST level; the 139-test suite
  (webhook dispatch, state routing, handlers) is the behavioural gate.
