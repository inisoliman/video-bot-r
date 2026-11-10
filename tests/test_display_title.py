import support  # noqa: F401

import unittest

import db_manager
import utils
from handlers import comment_handlers as ch
from handlers import helpers


class DisplayTitlePrecedenceTest(support.OfflineTestCase):
    def test_override_wins_over_series_metadata(self):
        video = {"id": 5, "caption": "مسلسل X الحلقة 1", "file_name": "x.mp4",
                 "display_title": "العنوان الجديد", "view_count": 3,
                 "metadata": {"series_name": "X", "season_number": 1, "episode_number": 2}}
        rendered = helpers.format_video_display_info(video)
        self.assertIn("العنوان الجديد", rendered)
        self.assertNotIn("X - ", rendered)

    def test_fallback_order_without_override(self):
        video = {"id": 5, "caption": "كابشن\nسطر ثانٍ", "file_name": "x.mp4", "metadata": {}}
        self.assertEqual(utils.video_title(video), "كابشن")
        video2 = {"id": 5, "caption": None, "file_name": "file.mp4", "metadata": {}}
        self.assertEqual(utils.video_title(video2), "file.mp4")

    def test_validation_empty_and_overlong(self):
        ok, _ = utils.validate_display_title("   ")
        self.assertFalse(ok)
        ok, _ = utils.validate_display_title(None)
        self.assertFalse(ok)
        ok, _ = utils.validate_display_title("x" * (utils.MAX_DISPLAY_TITLE_LENGTH + 1))
        self.assertFalse(ok)
        ok, value = utils.validate_display_title("  عنوان  ")
        self.assertTrue(ok)
        self.assertEqual(value, "عنوان")


class TitleFlowTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.db = support.ScriptedExecuteQuery(db_manager).install()
        self.admins = [support.ADMIN_ID]
        self.bot = support.FakeBot()
        self.state = None
        self.original_get = getattr(db_manager, 'get_user_state', None)
        db_manager.get_user_state = lambda uid: self.state
        self.addCleanup(lambda: setattr(db_manager, 'get_user_state', self.original_get)
                        if self.original_get is not None else delattr(db_manager, 'get_user_state'))

    def tearDown(self):
        self.db.restore()

    def video_row(self, title=None):
        return {"id": 3, "caption": "قديم", "file_name": "a.mp4", "display_title": title}

    def test_start_requires_admin(self):
        self.assertFalse(ch.start_title_edit(self.bot, support.MEMBER_ID, 3, self.admins))
        self.assertNotIn("تعديل عنوان", self.bot.last_text() or "")

    def test_preview_then_save_persists_and_invalidates_cache(self):
        self.db.script = {0: [self.video_row()]}
        self.assertTrue(ch.start_title_edit(self.bot, support.ADMIN_ID, 3, self.admins))
        self.assertIn("تعديل عنوان", self.bot.last_text())
        # الأدمن يرسل النص → معاينة
        self.state = {"state": "editing_display_title", "context": {"video_id": 3}}
        self.db.calls.clear()
        self.db.script = {0: [self.video_row()]}
        self.assertTrue(ch.process_title_text(self.bot, support.make_message("عنوان نهائي"), self.admins))
        self.assertIn("معاينة", self.bot.last_text())
        context = [c[1][2] for c in self.db.calls if "INSERT INTO user_states" in c[0]][-1]
        import json as _json
        self.assertEqual(_json.loads(context).get("title"), "عنوان نهائي")
        # الحفظ يعيد التحقق ويعمل update لعمود display_title فقط
        self.state = {"state": "editing_display_title", "context": {"video_id": 3, "title": "عنوان نهائي"}}
        self.db.calls.clear()
        self.db.script = {0: [self.video_row()], 1: [{"id": 3}]}
        ch.save_display_title(self.bot, support.make_call("admin_title::save::3"), support.ADMIN_ID, 3, self.admins)
        update = [c for c in self.db.calls if "UPDATE video_archive SET display_title" in c[0]]
        self.assertEqual(len(update), 1)
        self.assertEqual(update[0][1], ("عنوان نهائي", 3))
        self.assertIn("تم تحديث العنوان", self.bot.last_text())

    def test_save_rejects_empty_and_overlong(self):
        for bad in ("   ", "y" * (utils.MAX_DISPLAY_TITLE_LENGTH + 5)):
            self.state = {"state": "editing_display_title", "context": {"video_id": 3, "title": bad}}
            self.db.script = {0: [self.video_row()]}
            ch.save_display_title(self.bot, support.make_call("admin_title::save::3"),
                                  support.ADMIN_ID, 3, self.admins)
            self.assertFalse([c for c in self.db.calls if "UPDATE video_archive SET display_title" in c[0]])

    def test_save_rechecks_rights(self):
        self.state = {"state": "editing_display_title", "context": {"video_id": 3, "title": "ok"}}
        self.db.script = {0: [self.video_row()]}
        ch.save_display_title(self.bot, support.make_call("admin_title::save::3", user_id=support.MEMBER_ID),
                              support.MEMBER_ID, 3, self.admins)
        self.assertFalse([c for c in self.db.calls if "UPDATE video_archive" in c[0]])
        self.assertIn("صلاحية", self.bot.last_text())

    def test_command_with_id_does_not_save_id_as_title(self):
        value = ch._strip_title_command("/title 3 عنوان صحيح", 3)
        self.assertEqual(value, "عنوان صحيح")
        value = ch._strip_title_command("/title عنوان بلا رقم", 3)
        self.assertEqual(value, "عنوان بلا رقم")

    def test_reset_restores_original(self):
        self.state = {"state": "editing_display_title", "context": {"video_id": 3, "title": "x"}}
        self.db.script = {0: [self.video_row("x")], 1: [{"id": 3}]}
        ch.dispatch_title_action(self.bot, support.make_call("admin_title::reset::3"),
                                 self.admins, "reset", ["3"])
        update = [c for c in self.db.calls if "UPDATE video_archive SET display_title" in c[0]]
        self.assertEqual(update[0][1], (None, 3))

    def test_cancel_clears_state_without_update(self):
        self.state = {"state": "editing_display_title", "context": {"video_id": 3, "title": "x"}}
        self.db.script = {}
        ch.dispatch_title_action(self.bot, support.make_call("admin_title::cancel::3"),
                                 self.admins, "cancel", ["3"])
        self.assertTrue([c for c in self.db.calls if c[0].startswith("DELETE FROM user_states")])
        self.assertFalse([c for c in self.db.calls if "UPDATE video_archive" in c[0]])


class InlineDisplayTest(support.OfflineTestCase):
    def test_inline_result_uses_edited_title(self):
        from handlers import inline_handlers
        video = {"id": 3, "file_id": "A" * 25, "caption": "cap", "display_title": "Edited <t>",
                 "view_count": 0, "avg_rating": 0, "category_name": None}
        result = inline_handlers.create_inline_result(video)
        self.assertIn("Edited", result.title)


if __name__ == "__main__":
    unittest.main()
