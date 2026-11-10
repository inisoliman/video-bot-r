import support  # noqa: F401

import json as _json
import unittest

import db_manager
from handlers import callback_handlers
from handlers import comment_handlers as ch
from handlers import helpers
from handlers import user_handlers
from utils import MAX_DISPLAY_TITLE_LENGTH, video_title


CAPTION_75 = "75 - موت الجبار"
NEW_NAME = "موت الجبار - نسخة مدبلجة"


def video_row(video_id=75, caption=CAPTION_75, display_title=None, file_name=None):
    return {
        "id": video_id,
        "caption": caption,
        "file_name": file_name or f"{video_id}.mp4",
        "display_title": display_title,
    }


class _CapturingBot(support.FakeBot):
    """FakeBot يسجّل المعالجات المسجّلة حتى نستدعيها مباشرة (أوفلاين)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.registered = []
        self.registered_next_step = []

    def message_handler(self, *args, **kwargs):
        def decorator(func):
            self.registered.append((kwargs.get("content_types"), func, kwargs))
            return func

        return decorator

    def callback_query_handler(self, *args, **kwargs):
        def decorator(func):
            self.registered.append((None, func, kwargs))
            return func

        return decorator

    def register_next_step_handler(self, *args, **kwargs):
        self.registered_next_step.append(args)
        return True

    def callback_handler(self):
        for content_types, func, kwargs in self.registered:
            if content_types is None and "func" in kwargs:
                return func
        raise AssertionError("callback handler was not registered")

    def text_state_handler(self):
        for content_types, func, _kwargs in self.registered:
            if content_types and "text" in content_types:
                return func
        raise AssertionError("private text state handler was not registered")


class TitleRenameByNameTest(support.OfflineTestCase):
    """تدفق «تعديل اسم الفيديو» بالبحث عن الاسم الحالي أو «قديم => جديد»."""

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
        # مسار الاختبار: الحالة تُغذّى كدالة تُقرأ عند الطلب لا كقيمة مجمّدة، حتى
        # تُرى الحالة الجديدة بعد أي set_user_state وسط نفس الاختبار.
        self.db.original_get_user_state = (getattr(db_manager, "get_user_state", None),
                                           getattr(user_handlers, "get_user_state", None))
        holder = self

        def fake_get_user_state(user_id, **_kwargs):
            return holder.state

        self._fake_get_user_state = fake_get_user_state
        db_manager.get_user_state = fake_get_user_state
        user_handlers.get_user_state = fake_get_user_state
        # التوجيه في user_handlers يُقرأ بالاسم من db_manager وقت الاستيراد، لذا
        # نستبدل النسختين المستوردتين معاً (الحالة الحقيقية واحدة في DB).
        self.addCleanup(setattr, db_manager, "get_user_state",
                        self.db.original_get_user_state[0])
        self.addCleanup(setattr, user_handlers, "get_user_state",
                        self.db.original_get_user_state[1])
        original_check = helpers.check_subscription
        helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(setattr, helpers, "check_subscription", original_check)
        self.videos = {75: video_row(75)}
        original_source = getattr(db_manager, "get_video_title_source", None)
        db_manager.get_video_title_source = lambda vid: self.videos.get(int(vid))
        self.addCleanup(_restore_attr, db_manager, "get_video_title_source", original_source)

    # helpers -------------------------------------------------------------
    def _reprobe_state_reader(self):
        """
        القيم الافتراضية في وسائط المعالجات تُقرأ وقت التسجيل (binding)، ومع الاختبارات
        السابقة يُعاد ربط النسخة المستوردة: نعيد ربطها بدالة الحالة الجديدة قبل التسجيل.
        """
        user_handlers.get_user_state = self._fake_get_user_state
        self.addCleanup(setattr, user_handlers, "get_user_state",
                        self.db.original_get_user_state[1])

    def _track_bot(self, bot):
        """المعالجات المسجّلة قد تستخدم بوتاً مسجّراً بدلاً من self.bot: نقرأ من الاثنين."""
        self.other_bot = bot
        return bot

    def texts(self):
        out = [t for t in self.bot.sent_texts() if t]
        other = getattr(self, "other_bot", None)
        if other is not None:
            out += [t for t in other.sent_texts() if t]
        return out

    def answers(self):
        out = list(self.bot.answers())
        other = getattr(self, "other_bot", None)
        if other is not None:
            out += other.answers()
        return out

    def markups(self):
        out = list(self.bot.markup_for())
        other = getattr(self, "other_bot", None)
        if other is not None:
            out += other.markup_for()
        return out

    def states_set(self):
        return [c for c in self.db.calls if c[0].startswith("INSERT INTO user_states")]

    def states_cleared(self):
        return [c for c in self.db.calls if c[0].startswith("DELETE FROM user_states")]

    def titles_updated(self):
        return [c for c in self.db.calls if "UPDATE video_archive SET display_title" in c[0]]

    def searches(self):
        return [c for c in self.db.calls if c[0].startswith("SELECT id, caption, file_name")]

    def last_stored_state(self):
        inserts = self.states_set()
        self.assertTrue(inserts, self.db.normalized())
        return inserts[-1][1][1], _json.loads(inserts[-1][1][2])

    def search_state(self, query=None):
        context = {"admin_id": support.ADMIN_ID}
        if query:
            context["query"] = query
        return {"state": ch.STATE_TITLE_NAME, "context": context}

    def non_admin_search_state(self, query=None):
        context = {"admin_id": support.MEMBER_ID}
        if query:
            context["query"] = query
        return {"state": ch.STATE_TITLE_NAME, "context": context}

    def new_name_state(self, video_id=75, old_query=CAPTION_75):
        return {"state": ch.STATE_TITLE_NEW_NAME,
                "context": {"video_id": video_id, "old_query": old_query,
                            "admin_id": support.ADMIN_ID}}

    def enter_search_as_admin(self):
        self.db.script = {}
        self.assertTrue(ch.prompt_title_name(self.bot, support.ADMIN_ID))
        state_name, context = self.last_stored_state()
        self.state = {"state": state_name, "context": context}

    # (a) الزر → طلب الاسم الحالي -----------------------------------------
    def test_rename_button_prompts_for_current_name(self):
        bot = self._track_bot(_CapturingBot())
        callback_handlers.register(bot, self.admins)
        bot.callback_handler()(support.make_call("admin::rename_title"))
        state_name, context = self.last_stored_state()
        self.assertEqual(state_name, ch.STATE_TITLE_NAME)
        self.assertEqual(context.get("admin_id"), support.ADMIN_ID)
        prompt = self.texts()[-1]
        self.assertIn("الاسم الحالي", prompt)
        self.assertIn(CAPTION_75, prompt)
        self.assertIn("/cancel", prompt)
        self.assertIn(ch.CALLBACK_CANCEL_TITLE_ID,
                      support.button_callbacks(self.markups()[-1]))

    def test_fresh_state_proceeds_through_real_text_handler(self):
        """النص يمر عبر معالج نصوص user_handlers المسجّل فعلاً (لا معالج البحث العام)."""
        bot = self._track_bot(_CapturingBot())
        user_handlers.register(bot, "-100123", self.admins)
        self._reprobe_state_reader()
        self.db.script = {}
        ch.prompt_title_name(bot, support.ADMIN_ID)
        state_name, context = self.last_stored_state()
        self.state = {"state": state_name, "context": context}
        self.db.script = {0: [video_row(75)], 1: [video_row(75)]}
        bot.text_state_handler()(support.make_message(CAPTION_75))
        self.assertIn("وجدت هذا الفيديو", self.texts()[-1])
        self.assertFalse(any("اختر نوع البحث" in t for t in self.texts()))

    def test_new_name_typed_through_real_text_handler(self):
        bot = self._track_bot(_CapturingBot())
        user_handlers.register(bot, "-100123", self.admins)
        self._reprobe_state_reader()
        self.state = self.new_name_state()
        self.db.script = {0: [video_row(75)], 1: [{"id": 75}]}
        bot.text_state_handler()(support.make_message(NEW_NAME))
        update = self.titles_updated()
        self.assertEqual(len(update), 1, self.db.normalized())
        self.assertEqual(update[0][1], (NEW_NAME, 75))

    # (b) الاسم المطابق الوحيد → تأكيد → الاسم الجديد → الحفظ --------------
    def test_single_exact_match_asks_confirmation_then_saves_new_name(self):
        self.state = self.search_state()
        self.db.script = {0: [video_row(75)]}
        self.assertTrue(ch.process_title_name_text(
            self.bot, support.make_message(CAPTION_75), self.admins))
        confirmation = self.texts()[-1]
        self.assertIn("وجدت هذا الفيديو", confirmation)
        self.assertIn("#75", confirmation)
        callbacks = support.button_callbacks(self.markups()[-1])
        self.assertIn("title_name::pick::75", callbacks)
        self.assertIn(ch.CALLBACK_CANCEL_TITLE_ID, callbacks)
        self.assertEqual(self.titles_updated(), [])
        state_name, context = self.last_stored_state()
        self.assertEqual(state_name, ch.STATE_TITLE_NAME)
        self.assertEqual(context.get("query"), CAPTION_75)

        self.state = self.new_name_state()
        self.db.calls.clear()
        self.db.script = {0: [video_row(75)], 1: [{"id": 75}]}
        self.assertTrue(ch.process_title_new_name(
            self.bot, support.make_message(NEW_NAME), self.admins))
        update = self.titles_updated()
        self.assertEqual(len(update), 1, self.db.normalized())
        self.assertEqual(update[0][1], (NEW_NAME, 75))
        self.assertIs(update[0][3], True)  # يُنفَّذ بـ commit
        self.assertTrue(self.states_cleared())
        success = self.texts()[-1]
        self.assertIn("تم تعديل اسم الفيديو #75", success)
        self.assertIn(NEW_NAME, success)
        self.assertIn("للتراجع", success)

    # (c) «قديم => جديد» بخطوة واحدة --------------------------------------
    def test_direct_rename_in_one_message(self):
        self.state = self.search_state()
        self.db.script = {0: [video_row(75)], 1: [{"id": 75}]}
        message = support.make_message(f"{CAPTION_75} => {NEW_NAME}")
        self.assertTrue(ch.process_title_name_text(self.bot, message, self.admins))
        update = self.titles_updated()
        self.assertEqual(len(update), 1, self.db.normalized())
        self.assertEqual(update[0][1], (NEW_NAME, 75))
        self.assertIn("تم تعديل اسم الفيديو #75", self.texts()[-1])
        self.assertTrue(self.states_cleared())

    def test_direct_rename_separators(self):
        for separator in ("=>", "==>", "->", "—>", "←"):
            with self.subTest(separator=separator):
                parsed = ch.parse_title_name_command(f"{CAPTION_75} {separator} {NEW_NAME}")
                self.assertEqual(parsed, (CAPTION_75, NEW_NAME))
                self.state = self.search_state()
                self.db.calls.clear()
                self.db.script = {0: [video_row(75)], 1: [{"id": 75}]}
                self.assertTrue(ch.process_title_name_text(
                    self.bot, support.make_message(f"{CAPTION_75} {separator} {NEW_NAME}"),
                    self.admins))
                update = self.titles_updated()
                self.assertEqual(len(update), 1, self.db.normalized())
                self.assertEqual(update[0][1], (NEW_NAME, 75))

    # (d) اسم غامض → أزرار الاختيار → اختيار → طلب الاسم الجديد ------------
    def test_ambiguous_name_shows_candidates_and_pick_asks_new_name(self):
        twins = [video_row(75), video_row(76)]
        self.state = self.search_state()
        self.db.script = {0: twins}
        self.assertTrue(ch.process_title_name_text(
            self.bot, support.make_message(CAPTION_75), self.admins))
        self.assertIn("فيديو يطابق", self.texts()[-1])
        callbacks = support.button_callbacks(self.markups()[-1])
        self.assertIn("title_name::pick::75", callbacks)
        self.assertIn("title_name::pick::76", callbacks)
        self.assertEqual(self.titles_updated(), [])

        # الاختيار عبر معالج callback الحقيقي في callback_handlers
        bot = self._track_bot(_CapturingBot())
        callback_handlers.register(bot, self.admins)
        self.videos[76] = twins[1]
        self.db.calls.clear()
        self.db.script = {0: [twins[1]]}
        bot.callback_handler()(support.make_call("title_name::pick::76"))
        self.assertTrue(self.states_set(), (self.db.normalized(), bot.calls, self.state))
        state_name, context = self.last_stored_state()
        self.assertEqual(state_name, ch.STATE_TITLE_NEW_NAME)
        self.assertEqual(context.get("video_id"), 76)
        self.assertIn("أرسل الاسم الجديد", self.texts()[-1])
        self.assertEqual(self.titles_updated(), [])

        # ثم الاسم الجديد عبر معالج النصوص الحقيقي
        text_bot = self._track_bot(_CapturingBot())
        user_handlers.register(text_bot, "-100123", self.admins)
        self.state = {"state": state_name, "context": context}
        self.db.calls.clear()
        self.db.script = {0: [twins[1]], 1: [{"id": 76}]}
        text_bot.text_state_handler()(support.make_message(NEW_NAME))
        update = self.titles_updated()
        self.assertEqual(len(update), 1, self.db.normalized())
        self.assertEqual(update[0][1], (NEW_NAME, 76))

        # ثم الاسم الجديد عبر معالج النصوص الحقيقي
        text_bot = self._track_bot(_CapturingBot())
        user_handlers.register(text_bot, "-100123", self.admins)
        self.state = {"state": state_name, "context": context}
        self.db.calls.clear()
        self.db.script = {0: [twins[1]], 1: [{"id": 76}]}
        text_bot.text_state_handler()(support.make_message(NEW_NAME))
        update = self.titles_updated()
        self.assertEqual(len(update), 1, self.db.normalized())
        self.assertEqual(update[0][1], (NEW_NAME, 76))

    # (e) اسم غير معروف → رسالة واضحة مع بقاء التدفق قابلاً للاستخدام ------
    def test_unknown_name_is_clear_and_flow_stays_usable(self):
        self.state = self.search_state()
        self.db.script = {0: []}
        self.assertTrue(ch.process_title_name_text(
            self.bot, support.make_message("اسم غير موجود إطلاقاً"), self.admins))
        self.assertIn("لم أجد", self.texts()[-1])
        self.assertEqual(self.states_cleared(), [])
        self.assertEqual(self.titles_updated(), [])

        # بعد الفشل يمكنه المحاولة بالاسم الصحيح
        self.db.calls.clear()
        self.db.script = {0: [video_row(75)], 1: [video_row(75)]}
        self.assertTrue(ch.process_title_name_text(
            self.bot, support.make_message(CAPTION_75), self.admins))
        self.assertIn("وجدت هذا الفيديو", self.texts()[-1])

    def test_near_results_list_when_no_exact_match(self):
        self.state = self.search_state()
        self.db.script = {0: [video_row(75, caption="75 - موت الجبار الجزء الثاني")]}
        self.assertTrue(ch.process_title_name_text(
            self.bot, support.make_message("موت الجبار"), self.admins))
        text = self.texts()[-1]
        self.assertIn("نتائج قريبة", text)
        self.assertIn("#75", text)
        self.assertEqual(self.titles_updated(), [])

    # (f) غير الأدمن لا يستطيع التعديل أبداً -------------------------------
    def test_non_admin_never_renames(self):
        self.assertFalse(ch.prompt_title_name(self.bot, support.MEMBER_ID))
        self.assertIn("للإدارة فقط", self.texts()[-1])
        self.assertEqual(self.states_set(), [])

        # حتى مع حالة مزروعة له: يرفض ويُمسح دون أي بحث أو كتابة
        self.state = self.non_admin_search_state()
        self.db.calls.clear()
        self.assertTrue(ch.process_title_name_text(
            self.bot, support.make_message(f"{CAPTION_75} => {NEW_NAME}",
                                           user_id=support.MEMBER_ID),
            self.admins))
        self.assertIn("صلاحية", self.texts()[-1])
        self.assertEqual(self.searches(), [])
        self.assertEqual(self.titles_updated(), [])
        self.assertTrue(self.states_cleared())

        self.state = self.new_name_state()
        self.db.calls.clear()
        self.assertTrue(ch.process_title_new_name(
            self.bot, support.make_message(NEW_NAME, user_id=support.MEMBER_ID),
            self.admins))
        self.assertIn("صلاحية", self.texts()[-1])
        self.assertEqual(self.titles_updated(), [])
        self.assertTrue(self.states_cleared())

    def test_callback_paths_refuse_non_admin(self):
        bot = self._track_bot(_CapturingBot())
        callback_handlers.register(bot, self.admins)
        handler = bot.callback_handler()
        targets = ("admin::rename_title", "admin::edit_title", "admin::cancel_title_id",
                   "admin_title::cancel::75", "admin_title::save::75",
                   "title_name::pick::75")
        for data in targets:
            with self.subTest(data=data):
                self.state = self.search_state()
                self.db.calls.clear()
                self.db.script = {}
                handler(support.make_call(data, user_id=support.MEMBER_ID))
                self.assertEqual(self.titles_updated(), [], self.db.normalized())
                self.assertEqual(self.states_set(), [])
                self.assertEqual(self.states_cleared(), [])
                answers = [a for a in self.answers() if a[0]]
                self.assertTrue(answers, data)
                self.assertIn("للإدارة فقط", answers[-1][0])
                self.assertFalse([t for t in self.texts() if NEW_NAME in t])

    def test_non_private_chat_is_refused(self):
        self.state = self.new_name_state()
        self.db.calls.clear()
        message = support.make_message(NEW_NAME, chat_type="group", chat_id=-1001)
        self.assertFalse(ch.process_title_new_name(self.bot, message, self.admins))
        self.assertEqual(self.titles_updated(), [])
        self.assertEqual(self.texts(), [])

    # (g) اسم فارغ أو طويل → رفض بدون كتابة في DB -------------------------
    def test_empty_and_overlong_new_name_are_rejected_without_db_write(self):
        for bad in ("   ", "\n", "ط" * (MAX_DISPLAY_TITLE_LENGTH + 1)):
            with self.subTest(length=len(bad)):
                self.state = self.new_name_state()
                self.db.calls.clear()
                self.db.script = {0: [video_row(75)], 1: [{"id": 75}]}
                self.assertTrue(ch.process_title_new_name(
                    self.bot, support.make_message(bad), self.admins))
                self.assertEqual(self.titles_updated(), [], self.db.normalized())
                self.assertEqual(self.states_cleared(), [])
                self.assertTrue(any("فارغ" in t or "طويل" in t for t in self.texts()),
                                self.texts())
                self.assertFalse(any("تم تعديل اسم الفيديو" in t for t in self.texts()))

    # (h) رقم الفيديو في نهاية النص لا يمنع التطابق -----------------------
    def test_trailing_video_id_in_search_text_still_resolves(self):
        """«الاسم 75» يُطابق حتى لو أضاف الأدمن رقم الفيديو في نهاية النص."""
        self.state = self.search_state()
        # البحث الأول (بالنص كاملاً) لا يجد، والثاني (بعد حذف الرقم) يجد الفيديو
        self.db.script = {0: [], 1: [video_row(75)]}
        self.assertTrue(ch.process_title_name_text(
            self.bot, support.make_message("75 - موت الجبار 75"), self.admins))
        self.assertIn("وجدت هذا الفيديو", self.texts()[-1])
        self.assertIn("#75", self.texts()[-1])
        self.assertEqual(len(self.searches()), 2, self.db.normalized())
        self.assertEqual(self.titles_updated(), [])
        # حالة عدم وجود النتائج نفسها تبقي الأوضاع قابلة للتكرار
        self.state = self.search_state()
        self.db.calls.clear()
        self.db.script = {0: []}
        ch.process_title_name_text(self.bot, support.make_message("اسم مجهول تماماً"), self.admins)
        self.assertIn("لم أجد", self.texts()[-1])

    def test_trailing_id_only_text_is_not_used_as_name(self):
        """رقم وحده لا يصبح اسماً يساوي عنوان فيديو."""
        row = video_row(75)
        self.assertFalse(ch._title_matches("75", row))
        self.assertEqual(ch._strip_trailing_video_id("موت الجبار 75"), "موت الجبار")

    def test_title_matches_by_display_title_and_casefold(self):
        row = video_row(75, caption=None, display_title="  Mot جبار ")
        self.assertTrue(ch._title_matches("mot جبار", row))
        self.assertTrue(ch._title_matches(" Mot جبار ", row))
        self.assertFalse(ch._title_matches("كلمة أخرى", row))
        self.assertFalse(ch._title_matches("", row))
        # قبل التعديل: أول سطر من الكابشن هو الاسم المطابق
        plain = video_row(75, caption="Mot line\nsecond")
        self.assertTrue(ch._title_matches("mot line", plain))
        self.assertFalse(ch._title_matches("second", plain))

    # (i) الأرقام العربية-الهندية في تدفق الرقم ---------------------------
    def test_arabic_indic_digits_resolve_in_id_flow(self):
        self.state = {"state": ch.STATE_TITLE_VIDEO_ID,
                      "context": {"admin_id": support.ADMIN_ID}}
        self.db.calls.clear()
        self.db.script = {0: [video_row(75)]}
        self.assertTrue(ch.process_title_video_id(
            self.bot, support.make_message("٧٥"), self.admins))
        self.assertIn("تعديل عنوان", self.texts()[-1])
        stored = [c[1][2] for c in self.states_set() if '"video_id": 75' in str(c[1][2])]
        self.assertTrue(stored, self.db.normalized())
        self.assertEqual(ch.parse_title_video_id(" ٧٥ "), 75)

    # (j) بعد التعديل: الاسم المعروض والبحث يستخدمان display_title --------
    def test_after_rename_display_and_search_use_display_title(self):
        self.state = self.search_state()
        self.db.script = {0: [video_row(75)], 1: [{"id": 75}]}
        self.assertTrue(ch.process_title_name_text(
            self.bot, support.make_message(f"{CAPTION_75} => {NEW_NAME}"), self.admins))
        self.videos[75] = video_row(75, display_title=NEW_NAME)
        self.assertEqual(video_title(self.videos[75]), NEW_NAME)

        helpers_check = helpers.format_video_display_info(self.videos[75])
        self.assertIn(NEW_NAME, helpers_check)

        db_manager.clear_search_cache()
        self.db.calls.clear()
        self.db.script = {0: [self.videos[75]], 1: [{"count": 1}]}
        videos, total = db_manager.search_videos(NEW_NAME)
        self.assertEqual(total, 1)
        self.assertEqual(video_title(videos[0]), NEW_NAME)
        search_sql = [c[0] for c in self.db.calls
                      if c[0].startswith("SELECT * FROM video_archive")][0]
        self.assertIn("display_title ILIKE %s", search_sql)
        search_params = [c[1] for c in self.db.calls
                         if c[0].startswith("SELECT * FROM video_archive")][0]
        self.assertIn(f"%{NEW_NAME}%", search_params)

    def test_set_display_title_uses_defined_query(self):
        """كان NameError على query غير معرّف في set_display_title."""
        self.db.calls.clear()
        self.db.script = {0: [{"id": 75}]}
        self.assertTrue(db_manager.set_display_title(75, NEW_NAME))
        update = self.titles_updated()
        self.assertEqual(len(update), 1, self.db.normalized())
        self.assertEqual(update[0][1], (NEW_NAME, 75))
        self.assertIs(update[0][3], True)

    def test_bulk_set_display_title_updates_rows(self):
        self.db.calls.clear()
        self.db.script = {0: [{"id": 75}, {"id": 76}]}
        count = db_manager.bulk_set_display_title([75, "76", "x", None], NEW_NAME)
        self.assertEqual(count, 2)
        update = self.titles_updated()
        self.assertEqual(len(update), 1, self.db.normalized())
        self.assertEqual(update[0][1], (NEW_NAME, [75, 76]))
        self.assertEqual(db_manager.bulk_set_display_title([], NEW_NAME), 0)

    # (k) أزرار admin_title/cancel وpick: تحقق الصلاحية -------------------
    def test_admin_title_cancel_requires_admin_and_clears_for_admin(self):
        self.state = {"state": ch.STATE_EDITING_TITLE, "context": {"video_id": 75}}
        ch.dispatch_title_action(self.bot, support.make_call(
            "admin_title::cancel::75", user_id=support.MEMBER_ID),
            self.admins, "cancel", ["75"])
        self.assertEqual(self.states_cleared(), [])
        self.assertIn("للإدارة فقط", self.answers()[-1][0])

        ch.dispatch_title_action(self.bot, support.make_call("admin_title::cancel::75"),
                                 self.admins, "cancel", ["75"])
        self.assertTrue(self.states_cleared())

    def test_pick_callback_requires_admin_and_private_chat(self):
        self.state = self.search_state()
        self.db.calls.clear()
        self.db.script = {0: [video_row(75)]}
        ch._pick_candidate(self.bot, support.make_call(
            "title_name::pick::75", user_id=support.MEMBER_ID), self.admins, "75")
        self.assertEqual(self.states_set(), [])
        self.assertIn("للإدارة فقط", self.answers()[-1][0])

        call = support.make_call("title_name::pick::75", chat_type="group", chat_id=-1001)
        ch._pick_candidate(self.bot, call, self.admins, "75")
        self.assertEqual(self.states_set(), [])
        self.assertIn("خاصة", self.answers()[-1][0])


def _restore_attr(module, name, value):
    if value is None:
        if name in vars(module):
            delattr(module, name)
    else:
        setattr(module, name, value)


if __name__ == "__main__":
    unittest.main()
