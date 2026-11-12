"""Phase 2: expand the configured webhook update types, offline only.

Scope under test: ``WEBHOOK_ALLOWED_UPDATES`` and ``_webhook_allowed_updates``
in ``webhook_bot.py``, which feed both ``/set_webhook`` and boot initialization.
Only ``my_chat_member`` and ``chosen_inline_result`` are added; no handler,
dispatch or ordering behavior is exercised here.
"""

import os
import sys

try:  # package mode (`python -m unittest discover` with tests/__init__.py)
    from . import support
except ImportError:  # top-level mode (`python -m unittest discover -s tests`)
    import support

import importlib
import json
import sys as _sys
import unittest

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

# The exact useful update set: every pre-existing type preserved (in order),
# plus exactly the two phase-2 additions. `chat_member` is deliberately absent.
EXPECTED_WITH_CHANNEL_POST = [
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "callback_query",
    "inline_query",
    "my_chat_member",
    "chosen_inline_result",
]
EXPECTED_WITHOUT_CHANNEL_POST = [
    "message",
    "edited_message",
    "edited_channel_post",
    "callback_query",
    "inline_query",
    "my_chat_member",
    "chosen_inline_result",
]
PHASE2_ADDITIONS = ("my_chat_member", "chosen_inline_result")
FORBIDDEN_UPDATES = (
    "chat_member",
    "chat_join_request",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
    "message_reaction",
    "message_reaction_count",
    "poll",
    "poll_answer",
    "pre_checkout_query",
    "shipping_query",
    "my_chat_member_reaction",
    "purchased_paid_media",
)


def fresh_module(**env_values):
    """Re-import ``webhook_bot`` with an isolated environment (see test_webhook)."""
    env_values = dict(env_values)
    if "BASE" in env_values:
        base = env_values.pop("BASE")
        env_values |= BASE_ENV if base else {}
    else:
        env_values |= BASE_ENV
    saved = {key: os.environ.get(key) for key in EXTRA_ENV_KEYS}
    for key in EXTRA_ENV_KEYS:
        os.environ.pop(key, None)
    _sys.modules.pop("webhook_bot", None)
    try:
        with support.env(**env_values):
            return importlib.import_module("webhook_bot")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class AllowedUpdatesBase(support.OfflineTestCase):
    env_values = None

    def setUp(self):
        super().setUp()
        self.module = fresh_module(**(self.env_values or {}))
        self.bot = support.FakeBot()
        self.module.bot = self.bot
        self.client = self.module.app.test_client()
        self.module.app.config["TESTING"] = True

    def tearDown(self):
        _sys.modules.pop("webhook_bot", None)
        super().tearDown()


class ConfiguredUpdateSetTest(AllowedUpdatesBase):
    """The declared useful update set, independent of the include flag."""

    def test_declared_constant_is_exactly_the_useful_set(self):
        self.assertEqual(
            list(self.module.WEBHOOK_ALLOWED_UPDATES), EXPECTED_WITH_CHANNEL_POST)

    def test_declared_constant_has_no_duplicates(self):
        declared = list(self.module.WEBHOOK_ALLOWED_UPDATES)
        self.assertEqual(len(declared), len(set(declared)))

    def test_phase2_additions_present_and_chat_member_absent(self):
        declared = list(self.module.WEBHOOK_ALLOWED_UPDATES)
        for update_type in PHASE2_ADDITIONS:
            with self.subTest(update_type=update_type):
                self.assertIn(update_type, declared)
        for update_type in FORBIDDEN_UPDATES:
            with self.subTest(update_type=update_type):
                self.assertNotIn(update_type, declared)

    def test_helper_include_channel_post_true(self):
        self.assertEqual(
            self.module._webhook_allowed_updates(include_channel_post=True),
            EXPECTED_WITH_CHANNEL_POST)

    def test_helper_include_channel_post_false(self):
        self.assertEqual(
            self.module._webhook_allowed_updates(include_channel_post=False),
            EXPECTED_WITHOUT_CHANNEL_POST)

    def test_helper_both_branches_have_no_duplicates_and_no_chat_member(self):
        for include_channel_post in (True, False):
            with self.subTest(include_channel_post=include_channel_post):
                allowed = self.module._webhook_allowed_updates(
                    include_channel_post=include_channel_post)
                self.assertEqual(len(allowed), len(set(allowed)))
                self.assertNotIn("chat_member", allowed)
                for update_type in PHASE2_ADDITIONS:
                    self.assertIn(update_type, allowed)

    def test_helper_returns_a_fresh_list_each_call(self):
        first = self.module._webhook_allowed_updates(include_channel_post=True)
        first.append("chat_member")
        second = self.module._webhook_allowed_updates(include_channel_post=True)
        self.assertNotIn("chat_member", second)
        self.assertNotIn("chat_member", self.module.WEBHOOK_ALLOWED_UPDATES)

    def test_channel_post_toggle_only_affects_channel_post(self):
        with_channel = self.module._webhook_allowed_updates(include_channel_post=True)
        without_channel = self.module._webhook_allowed_updates(include_channel_post=False)
        self.assertEqual(with_channel.count("channel_post"), 1)
        self.assertEqual(without_channel.count("channel_post"), 0)
        removed = list(with_channel)
        removed.remove("channel_post")
        self.assertEqual(removed, without_channel)


class SetWebhookRouteTest(AllowedUpdatesBase):
    env_values = {"BASE": True, "WEBHOOK_MANAGEMENT_TOKEN": ADMIN_TOKEN}

    def test_set_webhook_kwargs_and_json_carry_expanded_set(self):
        response = self.client.post("/set_webhook", headers={"X-Admin-Token": ADMIN_TOKEN})
        self.assertEqual(response.status_code, 200)
        params = self.bot.calls_of("set_webhook")[0][2]
        self.assertEqual(list(params["allowed_updates"]), EXPECTED_WITH_CHANNEL_POST)
        self.assertEqual(len(params["allowed_updates"]), len(set(params["allowed_updates"])))
        self.assertNotIn("chat_member", params["allowed_updates"])

        body = response.get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["allowed_updates"], EXPECTED_WITH_CHANNEL_POST)
        self.assertNotIn("chat_member", body["allowed_updates"])
        self.assertNotIn(BOT_TOKEN, response.get_data(as_text=True))

    def test_set_webhook_route_does_not_add_handlers_or_extra_calls(self):
        self.client.post("/set_webhook", headers={"X-Admin-Token": ADMIN_TOKEN})
        self.assertEqual([c[0] for c in self.bot.calls], ["set_webhook"])


class BootInitializationTest(AllowedUpdatesBase):
    def test_boot_set_webhook_kwargs_carry_expanded_set(self):
        self.module.verify_and_repair_schema = lambda: None
        self.module.register_all_handlers = lambda *a, **k: None
        self.module.start_history_cleanup = lambda: None
        self.assertTrue(self.module.init_bot())
        params = self.bot.calls_of("set_webhook")[0][2]
        self.assertEqual(list(params["allowed_updates"]), EXPECTED_WITH_CHANNEL_POST)
        self.assertNotIn("chat_member", params["allowed_updates"])
        self.assertEqual(self.bot.calls_of("remove_webhook"), [])
        self.module.app.config["READY_STATE"] = "ready"


if __name__ == "__main__":
    unittest.main()
