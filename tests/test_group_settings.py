"""إعداد حذف رسائل الجروب: قراءة/كتابة آمنة في ``bot_settings``.

كل شيء أوفلاين: ``support`` يحجب HTTP وقاعدة البيانات، والدوال محقونة صراحةً.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode
    from . import support
except ImportError:  # top-level mode
    import support

from handlers import group_settings  # noqa: E402


class GroupSettingsTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self._get = group_settings.get_bot_setting
        self._set = group_settings.set_bot_setting
        self.addCleanup(setattr, group_settings, "get_bot_setting", self._get)
        self.addCleanup(setattr, group_settings, "set_bot_setting", self._set)

    def test_default_is_enabled(self):
        found = {}
        group_settings.get_bot_setting = lambda key, default=None: found.setdefault(key, default)
        self.assertTrue(group_settings.group_menu_autodelete_enabled())
        self.assertEqual(found[group_settings.GROUP_MENU_AUTODELETE_KEY], "true")

    def test_stored_values_are_parsed(self):
        for stored, expected in (("true", True), ("1", True), ("ON", True),
                                 ("false", False), ("0", False), ("off", False)):
            with self.subTest(stored=stored):
                group_settings.get_bot_setting = lambda key, default=None, value=stored: value
                self.assertIs(group_settings.group_menu_autodelete_enabled(), expected)

    def test_unknown_or_unavailable_value_falls_back_to_default(self):
        group_settings.get_bot_setting = lambda key, default=None: "maybe"
        self.assertTrue(group_settings.group_menu_autodelete_enabled())

        def boom(*_args, **_kwargs):
            raise RuntimeError("db down")

        group_settings.get_bot_setting = boom
        self.assertTrue(group_settings.group_menu_autodelete_enabled())

    def test_save_writes_normalized_flag_and_reports_failures(self):
        writes = []
        group_settings.set_bot_setting = lambda key, value: (
            writes.append((key, value)), True)[1]

        self.assertTrue(group_settings.set_group_menu_autodelete(False))
        self.assertTrue(group_settings.set_group_menu_autodelete(True))
        self.assertEqual(writes, [
            (group_settings.GROUP_MENU_AUTODELETE_KEY, "false"),
            (group_settings.GROUP_MENU_AUTODELETE_KEY, "true"),
        ])

        def boom(*_args, **_kwargs):
            raise RuntimeError("db down")

        group_settings.set_bot_setting = boom
        self.assertFalse(group_settings.set_group_menu_autodelete(True))
