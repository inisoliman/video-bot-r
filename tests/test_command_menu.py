import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode (`python -m unittest discover -s tests` with tests/__init__.py)
    from . import support
except ImportError:  # top-level mode (`python -m unittest discover -s tests`)
    import support

import importlib
import logging
import unittest

from telebot import types

from handlers import commands as command_menu
from handlers import admin_handlers, group_handlers, user_handlers

BOT_TOKEN = "123456:SECRET-BOT-TOKEN"
APP_URL = "https://example.onrender.com"
ADMIN_TOKEN = "management-token-value"
BASE_ENV = {
    "BOT_TOKEN": BOT_TOKEN,
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
    "CHANNEL_ID": "-1001234567890",
    "ADMIN_IDS": f"{support.ADMIN_ID},{support.OTHER_ADMIN_ID}",
    "APP_URL": APP_URL,
}
COMMAND_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")


def fresh_module(extra_env=None):
    """Import a fresh ``webhook_bot`` with an isolated environment.

    Module-level defaults (``MANAGEMENT_TOKEN`` etc.) are read from the environment
    while ``support.env`` is applied, so they stay correct after it restores.
    """
    sys.modules.pop("webhook_bot", None)
    values = dict(BASE_ENV)
    values.update(extra_env or {})
    with support.env(**values):
        return importlib.import_module("webhook_bot")


class RecordingBot:
    """Fake Bot that records @message_handler(commands=[...]) registrations."""

    def __init__(self):
        self.registered = []
        self.calls = []

    def message_handler(self, *args, **kwargs):
        self.registered.extend(kwargs.get("commands") or [])

        def decorator(func):
            return func

        return decorator

    # Recorders for publishing calls ------------------------------------
    def set_my_commands(self, commands, scope=None, language_code=None):
        self.calls.append(("set_my_commands", commands, scope, language_code))
        return True

    def set_chat_menu_button(self, chat_id=None, menu_button=None):
        self.calls.append(("set_chat_menu_button", chat_id, menu_button))
        return True


def registered_commands():
    """Every command name a handler is registered for, across all handler modules."""
    bot = RecordingBot()
    user_handlers.register(bot, "-1001234567890", [support.ADMIN_ID, support.OTHER_ADMIN_ID])
    group_handlers.register(bot, [support.ADMIN_ID])
    # admin_handlers wraps its registrations with a check_admin decorator that calls
    # ``message.from_user`` at import time, so its decorators are recorded directly.
    admin_handlers.message_handler = bot.message_handler
    admin_handlers.register(bot, [support.ADMIN_ID, support.OTHER_ADMIN_ID])
    return {name for name in bot.registered
            if isinstance(name, str) and not isinstance(name, tuple)}


class CommandListFormatTest(support.OfflineTestCase):
    def test_member_commands_are_telegram_valid(self):
        pairs = command_menu.MEMBER_COMMANDS
        self.assertTrue(pairs)
        names = [name for name, _description in pairs]
        self.assertEqual(len(names), len(set(names)), "duplicate member commands")
        for name, description in pairs:
            with self.subTest(command=name):
                self.assertRegex(name, COMMAND_PATTERN)
                self.assertFalse(name.startswith("/"))
                self.assertEqual(name, name.lower())
                self.assertTrue(description.strip())
                self.assertLessEqual(len(description), 64)

    def test_admin_commands_are_telegram_valid_and_superset(self):
        names = [name for name, _d in command_menu.ADMIN_COMMANDS]
        self.assertEqual(len(names), len(set(names)), "duplicate admin commands")
        for name, description in command_menu.ADMIN_COMMANDS:
            with self.subTest(command=name):
                self.assertRegex(name, COMMAND_PATTERN)
                self.assertLessEqual(len(description), 64)
        member_names = [name for name, _d in command_menu.MEMBER_COMMANDS]
        self.assertTrue(set(member_names).issubset(set(names)))
        self.assertTrue(set(names) - set(member_names), "admin list must add admin-only commands")

    def test_admin_extras_are_admin_only(self):
        member_names = {name for name, _d in command_menu.MEMBER_COMMANDS}
        extra_names = [name for name, _d in command_menu.ADMIN_ONLY_COMMANDS]
        self.assertTrue(extra_names)
        self.assertFalse(member_names.intersection(extra_names))

    def test_builders_return_bot_command_objects(self):
        for builder, pairs in ((command_menu.build_member_commands, command_menu.MEMBER_COMMANDS),
                               (command_menu.build_admin_commands, command_menu.ADMIN_COMMANDS)):
            built = builder()
            self.assertIsInstance(built, list)
            self.assertEqual([c.command for c in built], [name for name, _d in pairs])
            for item in built:
                self.assertIsInstance(item, types.BotCommand)
                self.assertIsInstance(item.description, str)

    def test_every_listed_command_has_a_registered_handler(self):
        registered = registered_commands()
        for name, _description in command_menu.ADMIN_COMMANDS:
            with self.subTest(command=name):
                self.assertIn(name, registered, f"no handler registered for /{name}")

    def test_unknown_and_absent_commands_are_rejected(self):
        registered = registered_commands()
        known_absent = {
            "favorites", "history", "popular", "help", "videos", "title_name",
            "suggest", "random", "settings", "stats", "profile", "list", "get",
            "menu", "support", "about", "feedback", "share", "inline", "broadcast",
        }
        listed = {name for name, _d in command_menu.ADMIN_COMMANDS}
        for name in known_absent:
            with self.subTest(command=name):
                self.assertNotIn(name, listed)
                self.assertNotIn(name, registered)
        # أي أمر غير مسجّل فعلياً (لا معالج له) لا يُقبل في قائمة منشورة
        for name in listed:
            self.assertIn(name, registered)


class PublishMenuTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.bot = RecordingBot()

    def publish(self, admin_ids=(support.ADMIN_ID, support.OTHER_ADMIN_ID)):
        return command_menu.publish_command_menu(self.bot, admin_ids)

    def test_publishes_default_scope_and_per_admin_chat_scopes(self):
        report = self.publish()
        self.assertTrue(report["default"])
        self.assertTrue(report["menu_button"])
        self.assertEqual(report["admins"],
                         {support.ADMIN_ID: True, support.OTHER_ADMIN_ID: True})

        scopes = [call for call in self.bot.calls if call[0] == "set_my_commands"]
        self.assertEqual(len(scopes), 3)
        default_scope = scopes[0][2]
        self.assertIsInstance(default_scope, types.BotCommandScopeDefault)
        self.assertEqual([c.command for c in scopes[0][1]],
                         [name for name, _d in command_menu.MEMBER_COMMANDS])
        chat_scopes = [call[2] for call in scopes[1:]]
        self.assertEqual({s.chat_id for s in chat_scopes},
                         {support.ADMIN_ID, support.OTHER_ADMIN_ID})
        for scope in chat_scopes:
            self.assertIsInstance(scope, types.BotCommandScopeChat)
        self.assertEqual([c.command for c in scopes[1][1]],
                         [name for name, _d in command_menu.ADMIN_COMMANDS])

    def test_menu_button_is_menu_button_commands(self):
        self.publish()
        buttons = [call for call in self.bot.calls if call[0] == "set_chat_menu_button"]
        self.assertEqual(len(buttons), 1)
        self.assertIsInstance(buttons[0][2], types.MenuButtonCommands)

    def test_per_admin_failure_is_swallowed_and_reported(self):
        failing = support.OTHER_ADMIN_ID

        def set_my_commands(commands, scope=None, language_code=None):
            if isinstance(scope, types.BotCommandScopeChat) and scope.chat_id == failing:
                raise RuntimeError("Forbidden: bot was blocked by the user")
            return True

        self.bot.set_my_commands = set_my_commands

        report = self.publish()
        self.assertTrue(report["default"])
        self.assertTrue(report["admins"][support.ADMIN_ID])
        self.assertFalse(report["admins"][failing])
        self.assertTrue(report["menu_button"])

    def test_total_failure_never_raises(self):
        class BrokenBot:
            def set_my_commands(self, *a, **k):
                raise RuntimeError("telegram down")

            def set_chat_menu_button(self, *a, **k):
                raise RuntimeError("telegram down")

        root = logging.getLogger()
        handler = logging.Handler()
        handler.emit = lambda record: None
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(setattr, root, "level", root.level)
        root.setLevel(logging.DEBUG)

        report = command_menu.publish_command_menu(BrokenBot(), [support.ADMIN_ID])
        self.assertEqual(report, {"default": False,
                                  "admins": {support.ADMIN_ID: False},
                                  "menu_button": False})

    def test_publish_logs_never_contain_secrets(self):
        records = []
        root = logging.getLogger()
        handler = logging.Handler()
        handler.emit = records.append
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)

        class LeakyBot(RecordingBot):
            def set_my_commands(self, commands, scope=None, language_code=None):
                raise RuntimeError(f"call failed for {BOT_TOKEN}")

        report = command_menu.publish_command_menu(LeakyBot(), [support.ADMIN_ID])
        self.assertFalse(report["default"])
        self.assertFalse(report["admins"][support.ADMIN_ID])
        logged = "\n".join(str(r.getMessage()) for r in records)
        self.assertNotIn(BOT_TOKEN, logged)
        self.assertNotIn(ADMIN_TOKEN, logged)


class PublishCommandsRouteTest(support.OfflineTestCase):
    env_values = {"WEBHOOK_MANAGEMENT_TOKEN": ADMIN_TOKEN}

    def setUp(self):
        super().setUp()
        self.module = fresh_module({"WEBHOOK_MANAGEMENT_TOKEN": ADMIN_TOKEN})
        self.recorder = RecordingBot()
        self.module.bot = self.recorder
        self.client = self.module.app.test_client()
        self.module.app.config["TESTING"] = True

    def tearDown(self):
        sys.modules.pop("webhook_bot", None)
        super().tearDown()

    def test_route_requires_management_token(self):
        self.assertEqual(self.client.post("/admin/publish_commands").status_code, 401)
        self.assertEqual(
            self.client.post("/admin/publish_commands",
                             headers={"X-Admin-Token": "wrong"}).status_code, 401)
        self.assertEqual(self.recorder.calls, [])

    def test_route_answers_503_without_configured_credential(self):
        # webhook_bot can be imported only once per process (module-level env parsing),
        # so the "no credential configured" state is simulated on the live module.
        saved = self.module.MANAGEMENT_TOKEN
        self.module.MANAGEMENT_TOKEN = ""
        self.addCleanup(setattr, self.module, "MANAGEMENT_TOKEN", saved)
        response = self.client.post("/admin/publish_commands",
                                    headers={"X-Admin-Token": ADMIN_TOKEN})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.recorder.calls, [])

    def test_route_republishes_with_token(self):
        response = self.client.post("/admin/publish_commands",
                                    headers={"X-Admin-Token": ADMIN_TOKEN})
        self.assertEqual(response.status_code, 200)
        report = response.get_json()["report"]
        self.assertTrue(report["default"])
        self.assertTrue(report["menu_button"])
        self.assertEqual({int(k) for k in report["admins"]}, set(self.module.ADMIN_IDS))
        self.assertTrue(all(report["admins"].values()))
        self.assertTrue(self.recorder.calls)
        body = response.get_data(as_text=True)
        self.assertNotIn(BOT_TOKEN, body)
        self.assertNotIn(ADMIN_TOKEN, body)


if __name__ == "__main__":
    unittest.main()
