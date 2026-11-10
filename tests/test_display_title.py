import support  # noqa: F401

import json as _json
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


class TitleIdStateFlowTest(support.OfflineTestCase):
    """تدفق تعديل العنوان عبر حالة DB (بدون register_next_step_handler)."""

    def setUp(self):
        super().setUp()
        self.db = support.ScriptedExecuteQuery(db_manager).install()
        self.addCleanup(self.db.restore)
        self.admins = [support.ADMIN_ID]
        self.original_admins = list(ch.ADMIN_IDS)
        ch.ADMIN_IDS[:] = self.admins
        self.addCleanup(ch.ADMIN_IDS.__setitem__, slice(None), self.original_admins)
        self.bot = support.FakeBot()
        self.state = None
        self.original_get_state = getattr(db_manager, 'get_user_state', None)
        db_manager.get_user_state = lambda uid: self.state
        self.addCleanup(_restore_attr, db_manager, 'get_user_state', self.original_get_state)

    # helpers -------------------------------------------------------------
    def states_set(self):
        return [c for c in self.db.calls if c[0].startswith("INSERT INTO user_states")]

    def state_cleared(self):
        return [c for c in self.db.calls if c[0].startswith("DELETE FROM user_states")]

    def titles_updated(self):
        return [c for c in self.db.calls if "UPDATE video_archive SET display_title" in c[0]]

    def video_row(self, title=None):
        return {"id": 3, "caption": "قديم", "file_name": "a.mp4", "display_title": title}

    def enter_id_state(self):
        self.state = {"state": ch.STATE_TITLE_VIDEO_ID,
                      "context": {"admin_id": support.ADMIN_ID}}

    # (a) لوحة الأدمن → حالة DB + طلب الرقم --------------------------------
    def test_admin_panel_edit_title_sets_state_and_prompts_for_id(self):
        self.db.script = {}
        self.assertTrue(ch.prompt_title_video_id(self.bot, support.ADMIN_ID))
        inserts = self.states_set()
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0][1][0], support.ADMIN_ID)
        self.assertEqual(inserts[0][1][1], ch.STATE_TITLE_VIDEO_ID)
        self.assertIn('"admin_id"', inserts[0][1][2])
        prompt = self.bot.last_text()
        self.assertTrue(prompt, self.bot.calls)
        self.assertIn("رقم الفيديو", prompt)
        self.assertIn("/cancel", prompt)

    def test_admin_panel_edit_title_requires_admin(self):
        self.assertFalse(ch.prompt_title_video_id(self.bot, support.MEMBER_ID))
        self.assertEqual(self.states_set(), [])

    def test_admin_panel_edit_title_db_error_is_visible(self):
        self.db.fail = lambda sql, params: RuntimeError("db down") if "user_states" in sql else None
        self.assertFalse(ch.prompt_title_video_id(self.bot, support.ADMIN_ID))
        self.assertIn("قاعدة البيانات", self.bot.last_text())

    def test_callback_admin_edit_title_reaches_state_flow(self):
        """الزر «✏️ تعديل عنوان فيلم» يمر عبر callback_handlers إلى تدفق الحالة."""
        from handlers import callback_handlers, helpers
        original_check = helpers.check_subscription
        helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(setattr, helpers, 'check_subscription', original_check)
        bot = _CapturingBot()
        callback_handlers.register(bot, self.admins)
        handler = None
        for _content_types, func, kwargs in bot.registered:
            if "func" in kwargs:
                handler = func
        self.assertIsNotNone(handler, "callback handler not registered")
        self.db.script = {}
        handler(support.make_call("admin::edit_title"))
        self.assertEqual(bot.registered_next_step, [])
        self.assertEqual(len(self.states_set()), 1)
        self.assertEqual(self.states_set()[0][1][1], ch.STATE_TITLE_VIDEO_ID)
        self.assertTrue(any("رقم الفيديو" in t for t in bot.sent_texts() if t))

    # (b) رقم صحيح → بدء تعديل العنوان وطلب العنوان الجديد -----------------
    def test_valid_id_starts_title_edit_and_asks_for_new_name(self):
        self.enter_id_state()
        original_source = db_manager.get_video_title_source
        self.db.script = {0: [self.video_row()]}
        db_manager.get_video_title_source = lambda vid: self.video_row()
        self.addCleanup(setattr, db_manager, 'get_video_title_source', original_source)
        message = support.make_message("3")
        self.assertTrue(ch.process_title_video_id(self.bot, message, self.admins))
        texts = [t for t in self.bot.sent_texts() if t]
        self.assertTrue(any("تعديل عنوان" in t for t in texts))
        self.assertTrue(any("أرسل العنوان الجديد" in t for t in texts))
        self.assertIn("تعديل عنوان", self.bot.last_text())
        stored = [c[1][2] for c in self.states_set() if '"video_id": 3' in str(c[1][2])]
        self.assertTrue(stored)
        self.assertIn("prompt_message_id", stored[-1])

    def test_valid_id_accepts_prefix_hash_arabic_digits_and_spaces(self):
        original_source = db_manager.get_video_title_source
        db_manager.get_video_title_source = lambda vid: {"id": vid, "caption": "c"}
        self.addCleanup(setattr, db_manager, 'get_video_title_source', original_source)
        for raw, expected in ((" #3 ", 3), ("٣", 3), ("۱۲", 12)):
            with self.subTest(raw=raw):
                self.enter_id_state()
                self.db.calls.clear()
                self.assertTrue(ch.process_title_video_id(
                    self.bot, support.make_message(raw), self.admins))
                stored = [c[1][2] for c in self.states_set()
                          if f'"video_id": {expected}' in str(c[1][2])]
                self.assertTrue(stored, self.db.normalized())

    # (c) نص غير رقمي → رسالة إعادة محاولة مع بقاء الحالة -------------------
    def test_non_numeric_text_keeps_state_and_asks_retry(self):
        self.enter_id_state()
        self.db.script = {}
        self.assertTrue(ch.process_title_video_id(
            self.bot, support.make_message("abc"), self.admins))
        self.assertEqual(self.state_cleared(), [])
        self.assertIn("غير صالح", self.bot.last_text())
        self.assertIn("رقم الفيديو", self.bot.last_text())

    # (d) رقم غير موجود → خطأ صريح بدل الصمت -------------------------------
    def test_unknown_id_shows_explicit_error_and_keeps_retry(self):
        self.enter_id_state()
        original_source = db_manager.get_video_title_source
        db_manager.get_video_title_source = lambda vid: None
        self.addCleanup(setattr, db_manager, 'get_video_title_source', original_source)
        self.assertTrue(ch.process_title_video_id(
            self.bot, support.make_message("999"), self.admins))
        sent = [t for t in self.bot.sent_texts() if t]
        self.assertEqual(len(sent), 1, sent)
        self.assertIn("لا يوجد فيديو", sent[0])
        self.assertIn("999", sent[0])
        self.assertIn("قائمة الفيديوهات", sent[0])
        self.assertIn("/cancel", sent[0])
        self.assertEqual(self.state_cleared(), [])

    def test_db_error_on_lookup_is_explicit(self):
        self.enter_id_state()
        self.db.fail = lambda sql, params: RuntimeError("db down") if "video_archive" in sql else None
        self.assertTrue(ch.process_title_video_id(
            self.bot, support.make_message("3"), self.admins))
        self.assertIn("قاعدة البيانات", self.bot.last_text())

    # (e) حالة مزروعة عبر ScriptedExecuteQuery في مسار النص -----------------
    def test_scripted_state_flows_through_text_handler(self):
        from handlers import user_handlers
        bot = _CapturingBot()
        self.original_set_waiting = user_handlers.set_user_waiting_for_input
        user_handlers.set_user_waiting_for_input = lambda uid, state, context=None: (
            self.db.set_user_state(uid, state, context))
        self.addCleanup(setattr, user_handlers, 'set_user_waiting_for_input',
                        self.original_set_waiting)
        user_handlers.register(bot, "-100123", self.admins)
        handler = _text_state_handler(bot)
        self.db.calls.clear()
        original_uh_get_state = user_handlers.get_user_state
        user_handlers.get_user_state = lambda uid: self.state
        self.addCleanup(setattr, user_handlers, 'get_user_state', original_uh_get_state)
        original_source = db_manager.get_video_title_source
        self.db.script = {0: [self.video_row()]}
        db_manager.get_video_title_source = lambda vid: self.video_row()
        self.addCleanup(setattr, db_manager, 'get_video_title_source', original_source)
        self.state = {"state": ch.STATE_TITLE_VIDEO_ID,
                      "context": {"admin_id": support.ADMIN_ID}}
        handler(support.make_message("3"))
        # المسار المعترض: لا نصوص بحث ولا طلب رقم جديد، بل بدء تعديل عنوان
        self.assertTrue(self.db.normalized(), self.db.calls)
        self.assertFalse(any("اختر نوع البحث" in t for t in bot.sent_texts() if t),
                         bot.sent_texts())
        self.assertTrue(any("تعديل عنوان" in t for t in bot.sent_texts() if t),
                        bot.sent_texts())

        self.db.script = {}
        self.state = {"state": ch.STATE_TITLE_VIDEO_ID,
                      "context": {"admin_id": support.ADMIN_ID}}
        handler(support.make_message("xyz"))
        self.assertTrue(any("غير صالح" in t for t in bot.sent_texts() if t))
        self.assertEqual(self.state_cleared(), [])

    def test_state_cleared_into_db_after_admin_press(self):
        """الحالة تُكتب فعلاً في DB ثم تُقرأ مرة أخرى عبر fetch_user_state."""
        self.db.script = {}
        ch.prompt_title_video_id(self.bot, support.ADMIN_ID)
        inserts = self.states_set()
        self.assertEqual(len(inserts), 1)
        self.state = {"state": inserts[0][1][1],
                      "context": _json.loads(inserts[0][1][2])}
        original_source = db_manager.get_video_title_source
        db_manager.get_video_title_source = lambda vid: self.video_row()
        self.addCleanup(setattr, db_manager, 'get_video_title_source', original_source)
        self.assertTrue(ch.process_title_video_id(
            self.bot, support.make_message("3"), self.admins))
        self.assertIn("تعديل عنوان", self.bot.last_text())

    # (f) نص حر بعد الرقم → معاينة ثم حفظ ----------------------------------
    def test_plain_title_text_shows_preview_then_save_persists(self):
        self.state = {"state": ch.STATE_EDITING_TITLE,
                      "context": {"video_id": 3, "admin_id": support.ADMIN_ID,
                                  "prompt_message_id": 5000}}
        self.db.script = {0: [self.video_row()]}
        self.assertTrue(ch.process_title_text(
            self.bot, support.make_message("عنوان حر"), self.admins))
        self.assertIn("معاينة", self.bot.last_text())
        stored = [c[1][2] for c in self.states_set() if "\\u0639" in str(c[1][2])]
        self.assertTrue(stored, [c[1] for c in self.states_set()])
        self.assertEqual(_json.loads(stored[-1]).get("title"), "عنوان حر")
        self.state = {"state": ch.STATE_EDITING_TITLE,
                      "context": {"video_id": 3, "title": "عنوان حر",
                                  "admin_id": support.ADMIN_ID, "prompt_message_id": 5000}}
        self.db.calls.clear()
        self.db.script = {0: [self.video_row()], 1: [{"id": 3}]}
        ch.save_display_title(self.bot, support.make_call("admin_title::save::3"),
                              support.ADMIN_ID, 3, self.admins)
        update = self.titles_updated()
        self.assertEqual(update[0][1], ("عنوان حر", 3))
        self.assertIn("تم تحديث العنوان", self.bot.last_text())

    def test_reply_to_prompt_also_accepted(self):
        self.state = {"state": ch.STATE_EDITING_TITLE,
                      "context": {"video_id": 3, "admin_id": support.ADMIN_ID,
                                  "prompt_message_id": 5000}}
        self.db.script = {0: [self.video_row()]}
        message = support.make_message(
            "عنوان بالرد", message_id=5001,
            reply_to_message=support.make_message("prompt", message_id=5000))
        self.assertTrue(ch.process_title_text(self.bot, message, self.admins))
        self.assertIn("معاينة", self.bot.last_text())

    def test_cancel_command_clears_state(self):
        self.enter_id_state()
        self.db.script = {}
        self.assertTrue(ch.process_title_video_id(
            self.bot, support.make_message("/cancel"), self.admins))
        self.assertTrue(self.state_cleared())
        self.assertTrue(any("تم الإلغاء" in t for t in self.bot.sent_texts() if t))
        self.assertEqual(self.bot.answers(), [])


class _CapturingBot(support.FakeBot):
    """FakeBot يسجّل المعالجات المسجّلة حتى نستدعيها مباشرة."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.registered = []
        self.registered_next_step = []

    def message_handler(self, *args, **kwargs):
        def decorator(func):
            self.registered.append((kwargs.get("content_types"), func, kwargs))
            return func
        if args and callable(args[0]) and not kwargs:
            self.registered.append((None, args[0], {}))
            return args[0]
        return decorator

    def callback_query_handler(self, *args, **kwargs):
        def decorator(func):
            self.registered.append((None, func, kwargs))
            return func
        if args and callable(args[0]) and not kwargs:
            self.registered.append((None, args[0], {}))
            return args[0]
        return decorator

    def register_next_step_handler(self, *args, **kwargs):
        self.registered_next_step.append(args)
        return True


def _restore_attr(module, name, value):
    """يعيد الدالة الأصلية إلى الوحدة (أو يحذف النسخة المؤقتة عند القراءة الأصلية)."""
    if value is None:
        if name in vars(module):
            delattr(module, name)
    else:
        setattr(module, name, value)


def _text_state_handler(bot):
    for content_types, func, _kwargs in bot.registered:
        if content_types and "text" in content_types:
            return func
    raise AssertionError("private text state handler was not registered")


class InlineDisplayTest(support.OfflineTestCase):
    def test_inline_result_uses_edited_title(self):
        from handlers import inline_handlers
        video = {"id": 3, "file_id": "A" * 25, "caption": "cap", "display_title": "Edited <t>",
                 "view_count": 0, "avg_rating": 0, "category_name": None}
        result = inline_handlers.create_inline_result(video)
        self.assertIn("Edited", result.title)


if __name__ == "__main__":
    unittest.main()
