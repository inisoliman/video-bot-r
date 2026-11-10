import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode (`python -m unittest discover -s tests` with tests/__init__.py)
    from . import support
except ImportError:  # top-level mode (`python -m unittest discover -s tests`)
    import support

import importlib
import json
import logging
import unittest
from types import SimpleNamespace

BOT_TOKEN = "123456:SECRET-BOT-TOKEN"
APP_URL = "https://example.onrender.com"
ADMIN_TOKEN = "management-token-value"
BASE_ENV = {
    "BOT_TOKEN": BOT_TOKEN,
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
    "CHANNEL_ID": "-1001234567890",
    "ADMIN_IDS": str(support.ADMIN_ID),
    "APP_URL": APP_URL,
}
EXTRA_ENV_KEYS = ("WEBHOOK_MANAGEMENT_TOKEN",)
MANAGEMENT_PATHS = (
    "/set_webhook",
    "/admin/update_thumbnails",
    "/admin/optimize_db",
    "/admin/migrate_database",
    "/admin/db_stats",
    "/webhook_info",
)


def fresh_module(**env_values):
    """Re-import ``webhook_bot`` with an isolated environment.

    Only the keys in ``ENV_KEYS`` are isolated by ``support.env``; every
    ``WEBHOOK_*`` variable the module reads is scrubbed here as well.
    """
    env_values = dict(env_values)
    if "BASE" in env_values:
        base = env_values.pop("BASE")
        env_values |= BASE_ENV if base else {}
    else:
        env_values |= BASE_ENV
    saved = {key: os.environ.get(key) for key in EXTRA_ENV_KEYS}
    for key in EXTRA_ENV_KEYS:
        os.environ.pop(key, None)
    sys.modules.pop("webhook_bot", None)
    try:
        with support.env(**env_values):
            return importlib.import_module("webhook_bot")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class CapturedLogs:
    """Collects log output of the module logger so tests can prove no secret leaks."""

    def __init__(self):
        self.records = []

    def __enter__(self):
        self._handler = logging.Handler()
        self._handler.emit = self.records.append
        root = logging.getLogger()
        self._root_level = root.level
        root.addHandler(self._handler)
        root.setLevel(logging.DEBUG)
        return self

    def __exit__(self, exc_type, exc, tb):
        root = logging.getLogger()
        root.removeHandler(self._handler)
        root.setLevel(self._root_level)
        return False

    def text(self):
        return "\n".join(str(r.getMessage()) for r in self.records)


class WebhookTestBase(support.OfflineTestCase):
    env_values = None

    def setUp(self):
        super().setUp()
        self.captured = CapturedLogs()
        self.captured.__enter__()
        self.module = fresh_module(**(self.env_values or {}))
        self.bot = support.FakeBot()
        self.module.bot = self.bot
        self.client = self.module.app.test_client()
        self.app_token = "app-token-123"
        self.module.app.config["TESTING"] = True

    def tearDown(self):
        self.captured.__exit__(None, None, None)
        sys.modules.pop("webhook_bot", None)
        super().tearDown()

    # helpers -------------------------------------------------------------
    def webhook_path(self):
        return f"/bot{BOT_TOKEN}"

    def post_update(self, payload, **kwargs):
        return self.client.post(
            self.webhook_path(),
            data=json.dumps(payload),
            content_type="application/json",
            **kwargs,
        )

    def message_update(self, text="/start", user_id=support.MEMBER_ID):
        return {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "date": 1700000000,
                "text": text,
                "from": {"id": user_id, "is_bot": False, "first_name": "M"},
                "chat": {"id": user_id, "type": "private"},
            },
        }

    def channel_post_update(self):
        return {
            "update_id": 2,
            "channel_post": {
                "message_id": 11,
                "date": 1700000000,
                "caption": "post",
                "chat": {"id": -1001234567890, "type": "channel", "title": "C"},
            },
        }


class ManagementAuthTest(WebhookTestBase):
    """No credential configured: process runs, health works, management answers 503."""

    def test_health_live_ready_work_without_management_credential(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        live = self.client.get("/live")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.get_json()["status"], "alive")
        ready = self.client.get("/ready")
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.get_json()["state"], "initializing")

    def test_management_routes_answer_503_without_configured_credential(self):
        for path in MANAGEMENT_PATHS:
            for method in ("get", "post"):
                with self.subTest(path=path, method=method):
                    response = getattr(self.client, method)(
                        path, headers={"X-Admin-Token": ADMIN_TOKEN})
                    if response.status_code == 405:
                        continue  # route intentionally accepts another method only
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn(ADMIN_TOKEN, response.get_data(as_text=True))
        self.assertEqual(self.bot.calls, [])

    def test_default_webhook_secret_is_not_a_credential(self):
        module = self.module
        self.assertEqual(module.WEBHOOK_SECRET, "default_secret")
        self.assertEqual(module.MANAGEMENT_TOKEN, "")
        response = self.client.post("/set_webhook", headers={"X-Admin-Token": "default_secret"})
        self.assertEqual(response.status_code, 503)

    def test_admin_id_alone_is_not_enough(self):
        response = self.client.get(f"/admin/db_stats?admin_id={support.ADMIN_ID}")
        self.assertEqual(response.status_code, 503)


class ManagementCredentialsTest(WebhookTestBase):
    env_values = {"BASE": True, "WEBHOOK_MANAGEMENT_TOKEN": ADMIN_TOKEN}

    def test_wrong_or_missing_token_is_401_and_nothing_runs(self):
        for path in MANAGEMENT_PATHS:
            for method in ("get", "post"):
                with self.subTest(path=path, method=method):
                    call = getattr(self.client, method)
                    missing = call(path)
                    if missing.status_code == 405:
                        continue
                    self.assertEqual(missing.status_code, 401)
                    self.assertEqual(
                        call(path, headers={"X-Admin-Token": "wrong"}).status_code, 401)
        self.assertEqual(self.bot.calls, [])

    def test_query_token_and_header_are_both_accepted(self):
        self.assertEqual(self.client.post("/set_webhook").status_code, 401)
        ok = self.client.post(f"/set_webhook?token={ADMIN_TOKEN}")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(len(self.bot.calls_of("set_webhook")), 1)

    def test_set_webhook_get_is_rejected(self):
        response = self.client.get(f"/set_webhook?token={ADMIN_TOKEN}")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.bot.calls, [])

    def test_set_webhook_defaults(self):
        response = self.client.post("/set_webhook", headers={"X-Admin-Token": ADMIN_TOKEN})
        self.assertEqual(response.status_code, 200)
        params = self.bot.calls_of("set_webhook")[0][2]
        self.assertFalse(params["drop_pending_updates"])
        self.assertIn("channel_post", params["allowed_updates"])
        self.assertIn("message", params["allowed_updates"])
        self.assertNotIn("secret_token", params)  # default WEBHOOK_SECRET is never sent
        self.assertEqual(self.bot.calls_of("remove_webhook"), [])
        body = response.get_data(as_text=True)
        self.assertNotIn(BOT_TOKEN, body)
        self.assertNotIn("SECRET-BOT-TOKEN", self.captured.text())

    def test_drop_pending_requires_explicit_request_and_confirmation(self):
        headers = {"X-Admin-Token": ADMIN_TOKEN}
        self.client.post("/set_webhook?drop_pending_updates=1", headers=headers)
        self.assertFalse(self.bot.calls_of("set_webhook")[0][2]["drop_pending_updates"])
        self.client.post("/set_webhook?drop_pending_updates=1&confirm=1", headers=headers)
        self.assertTrue(self.bot.calls_of("set_webhook")[1][2]["drop_pending_updates"])
        self.assertEqual(self.bot.calls_of("remove_webhook"), [])

    def test_remove_first_requires_confirmation(self):
        headers = {"X-Admin-Token": ADMIN_TOKEN}
        self.client.post("/set_webhook?remove_first=1", headers=headers)
        self.assertEqual(self.bot.calls_of("remove_webhook"), [])
        self.client.post("/set_webhook?remove_first=1&confirm=1", headers=headers)
        self.assertEqual(len(self.bot.calls_of("remove_webhook")), 1)

    def test_webhook_info_redacts_url(self):
        self.bot.get_webhook_info = lambda: SimpleNamespace(
            url=f"{APP_URL}/bot{BOT_TOKEN}",
            pending_update_count=0,
            last_error_message=None,
            max_connections=40,
            allowed_updates=["message"],
        )
        response = self.client.get("/webhook_info", headers={"X-Admin-Token": ADMIN_TOKEN})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("SECRET-BOT-TOKEN", response.get_data(as_text=True))


class SecretFallbackTest(WebhookTestBase):
    env_values = {"BASE": False, "BOT_TOKEN": BOT_TOKEN,
                  "DATABASE_URL": BASE_ENV["DATABASE_URL"],
                  "CHANNEL_ID": BASE_ENV["CHANNEL_ID"],
                  "ADMIN_IDS": BASE_ENV["ADMIN_IDS"],
                  "APP_URL": APP_URL, "WEBHOOK_SECRET": "explicit-webhook-secret"}

    def test_explicit_webhook_secret_authenticates_and_is_sent_to_telegram(self):
        self.assertEqual(self.module.MANAGEMENT_TOKEN, "explicit-webhook-secret")
        response = self.client.post("/set_webhook",
                                    headers={"X-Admin-Token": "explicit-webhook-secret"})
        self.assertEqual(response.status_code, 200)
        params = self.bot.calls_of("set_webhook")[0][2]
        self.assertEqual(params["secret_token"], "explicit-webhook-secret")
        self.assertNotIn("explicit-webhook-secret", response.get_data(as_text=True))
        self.assertNotIn("explicit-webhook-secret", self.captured.text())

    def test_webhook_rejects_wrong_telegram_secret_with_403(self):
        payload = json.dumps(self.message_update())
        missing = self.post_update(self.message_update())
        self.assertEqual(missing.status_code, 403)
        wrong = self.client.post(self.webhook_path(), data=payload,
                                 content_type="application/json",
                                 headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(self.bot.calls, [])


class WebhookRouteTest(WebhookTestBase):
    def test_valid_update_is_processed(self):
        response = self.post_update(self.message_update())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})
        self.assertEqual(len(self.bot.calls_of("process_new_messages")), 1)

    def test_http_exception_status_is_preserved(self):
        self.assertEqual(self.client.post(self.webhook_path(), data="not json",
                                          content_type="text/plain").status_code, 400)
        self.assertEqual(self.client.post(self.webhook_path(), data="{broken",
                                          content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(self.webhook_path(), data="{}",
                                          content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(self.webhook_path(), data=json.dumps([1, 2]),
                                          content_type="application/json").status_code, 400)
        self.assertEqual(self.bot.calls, [])

    def test_channel_post_is_dispatched(self):
        response = self.post_update(self.channel_post_update())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.bot.calls_of("process_new_channel_posts")), 1)

    def test_failed_processing_still_acks_update(self):
        self.bot.raise_on = {"process_new_messages"}
        response = self.post_update(self.message_update())
        self.assertEqual(response.status_code, 200)

    def test_cancel_is_routed_before_telegram_dispatch(self):
        cleared = []
        state = {"state": "editing_display_title", "context": {"video_id": 3}}
        original_get = self.module.state_manager.get_state
        original_clear = self.module.state_manager.clear_state
        self.module.state_manager.get_state = lambda user_id: state
        self.module.state_manager.clear_state = lambda user_id: cleared.append(user_id)
        try:
            response = self.post_update(self.message_update(text="/cancel"))
        finally:
            self.module.state_manager.get_state = original_get
            self.module.state_manager.clear_state = original_clear
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cleared, [support.MEMBER_ID])
        self.assertEqual(self.bot.calls_of("process_new_messages"), [])
        self.assertEqual(len(self.bot.calls_of("reply_to")), 1)

    def test_regular_message_reaches_telegram_dispatch(self):
        self.module.state_manager.get_state = lambda user_id: None
        self.post_update(self.message_update(text="hello"))
        self.assertEqual(len(self.bot.calls_of("process_new_messages")), 1)


class GenerationRouteTest(WebhookTestBase):
    def test_thumbnail_redirect_never_contains_bot_token(self):
        self.bot.get_file = lambda file_id: SimpleNamespace(file_path="photos/file_1.jpg")
        response = self.client.get("/thumbnail/AgACAgQAAxU", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertNotIn("SECRET-BOT-TOKEN", location)
        self.assertNotIn("api.telegram.org", location)
        self.assertIn("/telegram_file/", location)
        self.assertNotIn("SECRET-BOT-TOKEN", response.get_data(as_text=True))

    def test_telegram_file_proxy_builds_url_server_side(self):
        response = self.client.get("/telegram_file/photos/file_1.jpg", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertTrue(location.startswith("https://api.telegram.org/file/bot"))
        self.assertIn("photos/file_1.jpg", location)
        self.assertIn("private", response.headers["Cache-Control"])

    def test_thumbnail_failure_is_404_not_500(self):
        def boom(file_id):
            raise RuntimeError("telegram down")

        self.bot.get_file = boom
        self.assertEqual(self.client.get("/thumbnail/abc").status_code, 404)
        self.assertNotIn("SECRET-BOT-TOKEN", self.captured.text())


class ReadyAndBootTest(WebhookTestBase):
    def test_ready_becomes_ready_after_init_bot(self):
        calls = []
        self.module.verify_and_repair_schema = lambda: calls.append("schema")
        self.module.register_all_handlers = lambda *a, **k: calls.append("handlers")
        self.module.start_history_cleanup = lambda: calls.append("cleanup")
        try:
            self.assertTrue(self.module.init_bot())
        finally:
            pass
        self.assertEqual(self.client.get("/ready").status_code, 200)
        self.assertIn("cleanup", calls)

    def test_boot_setup_does_not_drop_pending_or_remove_first(self):
        self.module.verify_and_repair_schema = lambda: None
        self.module.register_all_handlers = lambda *a, **k: None
        self.module.start_history_cleanup = lambda: None
        self.assertTrue(self.module.init_bot())
        params = self.bot.calls_of("set_webhook")[0][2]
        self.assertFalse(params["drop_pending_updates"])
        self.assertIn("channel_post", params["allowed_updates"])
        self.assertEqual(self.bot.calls_of("remove_webhook"), [])
        self.assertNotIn("SECRET-BOT-TOKEN", self.captured.text())

    def test_boot_drop_pending_only_when_explicitly_enabled(self):
        module = fresh_module(BASE=True, WEBHOOK_DROP_PENDING="1")
        bot = support.FakeBot()
        module.bot = bot
        module.verify_and_repair_schema = lambda: None
        module.register_all_handlers = lambda *a, **k: None
        module.start_history_cleanup = lambda: None
        self.assertTrue(module.init_bot())
        self.assertTrue(bot.calls_of("set_webhook")[0][2]["drop_pending_updates"])
        module.app.config["READY_STATE"] = "ready"

    def test_ready_stays_503_when_schema_check_fails(self):
        def boom():
            raise RuntimeError("database unreachable")

        self.module.verify_and_repair_schema = boom
        self.assertFalse(self.module.init_bot())
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["state"], "db_error")

    def test_health_does_not_touch_secrets(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("SECRET-BOT-TOKEN", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
