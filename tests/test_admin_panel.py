"""بث لوحة الأدمن: زر -> حالة مخزّنة -> رسالة (نص/محتوى غني) -> عامل خلفي.

اختبارات أوفلاين بالكامل: لا Telegram ولا PostgreSQL حقيقيان (fakes من support).
"""

import support  # noqa: F401  (يحجب telebot HTTP وpsycopg2 قبل استيراد الإنتاج)

import importlib

import unittest
from types import SimpleNamespace

import db_manager
import state_manager
from handlers import admin_broadcast as ab
from handlers import callback_handlers
from handlers import helpers


ADMINS = [support.ADMIN_ID, support.OTHER_ADMIN_ID]
MEMBERS = [support.MEMBER_ID, support.OTHER_MEMBER_ID]
PROMPT_ID = 4242


def api_error(description, code=403):
    from telebot.apihelper import ApiTelegramException
    return ApiTelegramException('copyMessage', {'ok': False},
                                {'error_code': code, 'description': description})


class BroadcastBot:
    """بوت وهمي مستقل تماماً (لا يرث FakeBot كي لا تتأثر سجلاته بحقن اختبارات أخرى).

    يسجّل كل استدعاء في ``calls`` باسم (الاسم، args، kwargs)، ويفشل نسخ المحتوى
    لأرقام محددة برسائل تيليجرام حقيقية الشكل.
    """

    def __init__(self, copy_errors=None):
        self.calls = []
        self.registered = []
        self.copy_errors = dict(copy_errors or {})
        self.copies = []
        self.next_message_id = 1000

    # --- تسجيل المعالجات (حتى نستدعي callback_handler مباشرة) ---
    def message_handler(self, *args, **kwargs):
        def decorator(func):
            self.registered.append((kwargs.get('content_types'), func, kwargs))
            return func
        return decorator

    def callback_query_handler(self, *args, **kwargs):
        def decorator(func):
            self.registered.append((None, func, kwargs))
            return func
        return decorator

    def callback_handler(self):
        for content_types, func, kwargs in self.registered:
            if content_types is None and 'func' in kwargs:
                return func
        raise AssertionError('callback handler was not registered')

    # --- استدعاءات تيليجرام المستخدمة ---
    def _record(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))
        return True

    def send_message(self, chat_id, text, **kwargs):
        self._record('send_message', (chat_id, text), kwargs)
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id,
                               chat=SimpleNamespace(id=chat_id, type='private'))

    def reply_to(self, message, text, **kwargs):
        return self.send_message(message.chat.id, text, **kwargs)

    def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        return self._record('edit_message_text', (text, chat_id, message_id), kwargs)

    def answer_callback_query(self, call_id, text=None, **kwargs):
        return self._record('answer_callback_query', (call_id, text), kwargs)

    def copy_message(self, chat_id, from_chat_id=None, message_id=None, **kwargs):
        self._record('copy_message', (chat_id, from_chat_id, message_id), kwargs)
        error = self.copy_errors.get(chat_id)
        if error is not None:
            raise error
        self.copies.append((chat_id, from_chat_id, message_id))
        return SimpleNamespace(message_id=self.next_message_id)


    def register_next_step_handler(self, *args, **kwargs):
        """يُسجَّل فقط ليثبت أن التدفق لا يستخدمه."""
        return self._record('register_next_step_handler', args, kwargs)

    # --- استعلامات للمصادر المكتوبة صراحةً ---
    def sent(self):
        return [args[1] for name, args, _k in self.calls if name == 'send_message']

    def sent_report(self):
        texts = [t for t in self.sent() if t and 'اكتمل البث' in t]
        return texts[-1] if texts else None

    def last_text(self):
        texts = [t for t in self.sent() if t]
        return texts[-1] if texts else None

    def copies_to(self):
        return [args[0] for name, args, _k in self.calls if name == 'copy_message']


def sent_report(bot):
    """نص تقرير نهاية البث الفعلي (واحد فقط لكل عملية)."""
    texts = [t for t in bot.sent() if t and 'نجح:' in t and 'فشل:' in t]
    return texts[-1] if texts else None


class BroadcastTestCase(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        # الحالة المخزّنة لكل مستخدم كانت تُقرأ عبر ربط الاستيراد؛ حالة db_manager
        # غير محاكاة في هذا الملف، لذا نعزل مصدر الحالة صراحةً هنا
        import handlers.admin_broadcast as broadcast_module
        self._state = None
        self._states = []
        self._cleared = []
        self._reader = lambda user_id: self._state
        # عمليات الحالة تعيد قيماً منطقية صريحة (كما تفعل القاعدة عند النجاح)
        self._writer = lambda user_id, state, context=None: (
            self._states.append((user_id, state, context)), True)[1]
        self._clearer = lambda user_id: (self._cleared.append(user_id), True)[1]
        self.original_broadcast_state = (broadcast_module.read_state_row,
                                         broadcast_module.set_user_state,
                                         broadcast_module.clear_user_waiting_state)
        broadcast_module.read_state_row = self._reader
        broadcast_module.set_user_state = self._writer
        broadcast_module.clear_user_waiting_state = self._clearer
        self.addCleanup(self._restore_broadcast_state)

        self.state = None
        self.audience = ([7, 8, 9], None)
        self.original_fetch = db_manager.fetch_all_user_ids
        db_manager.fetch_all_user_ids = lambda: self.audience
        self.addCleanup(_restore, db_manager, 'fetch_all_user_ids', self.original_fetch)
        self.delete_ok = True
        self.deleted = []
        self.original_delete = db_manager.delete_bot_user
        db_manager.delete_bot_user = lambda uid: (self.deleted.append(uid), self.delete_ok)[1]
        self.addCleanup(_restore, db_manager, 'delete_bot_user', self.original_delete)
        self.bot = BroadcastBot()
        self.sleeps = []

    def _restore_broadcast_state(self):
        """يُعاد الربط الأصلي صراحةً (لا أسماء مختلطة بعد اختبارات أخرى)."""
        import handlers.admin_broadcast as broadcast_module
        (broadcast_module.read_state_row, broadcast_module.set_user_state,
         broadcast_module.clear_user_waiting_state) = self.original_broadcast_state
        ab._active_jobs.clear()
        ab._JOBS.clear()

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = value

    def open_state(self, prompt_message_id=PROMPT_ID):
        self.state = {'state': ab.STATE_BROADCAST,
                      'context': {'admin_id': support.ADMIN_ID,
                                  'prompt_message_id': prompt_message_id}}
        ab._active_jobs.clear()
        self._states.clear()
        self._cleared.clear()

    def reply(self, text=None, content_type='text', reply_to=PROMPT_ID, user_id=support.ADMIN_ID):
        reply_to_message = SimpleNamespace(message_id=reply_to) if reply_to else None
        return support.make_message(text, user_id=user_id, content_type=content_type,
                                    message_id=91, reply_to_message=reply_to_message)

    def run_worker(self, **kwargs):
        """العامل مباشرة (متزامن) مع رسالة تستأنف رسالة التلميح."""
        return ab.run_broadcast(self.bot, support.ADMIN_ID, 900, 91, [7, 8, 9],
                                sleeper=self.sleeps.append, **kwargs)

    def _run_inline_job(self, message, capture=None):
        """يعالج الرسالة مع تشغيل العامل في نفس الخيط (بلا خيوط حقيقية)."""
        original_start = ab._reserve_job

        def run_inline(admin_id, runner, bot=None):
            if capture is not None:
                capture['bot'] = bot
            capture and capture.setdefault('admins', []).append(admin_id)
            runner()
            return True

        ab._reserve_job = run_inline
        try:
            self.assertTrue(ab.handle_broadcast_message(self.bot, ADMINS, message))
        finally:
            ab._reserve_job = original_start
        return self.bot, capture or {}

    def execute(self, message):
        """يمرّ عبر handle_broadcast_message مع تشغيل العامل المتزامن."""
        return self._run_inline_job(message)

    def start_job(self, message):
        """يشغّل العامل فعلياً بشكل متزامن (بلا خيوط): نتائج حتمية مهما كان ترتيب الاختبار."""
        driver, captured = self._run_inline_job(message, capture={})
        self.assertEqual(captured.get('admins'), [support.ADMIN_ID])
        return driver

    def deliver(self, message):
        """مثل start_job لكن يتوقع خروجا مبكرا (لا عامل): يعيد ما سُجّل من جدولة."""
        driver = self.bot
        original_start = ab._reserve_job
        called = []

        def run_inline(admin_id, runner, bot=None):
            called.append(admin_id)
            runner()
            return True

        ab._reserve_job = run_inline
        try:
            self.assertTrue(ab.handle_broadcast_message(driver, ADMINS, message))
        finally:
            ab._reserve_job = original_start
        return driver, called


class BroadcastPromptTest(BroadcastTestCase):
    def test_panel_button_starts_persisted_state_and_asks_for_message(self):
        original_check = helpers.check_subscription
        helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(_restore, helpers, 'check_subscription', original_check)
        states = self._states
        bot = self.bot
        callback_handlers.register(bot, ADMINS)
        handler = bot.callback_handler()
        handler(support.make_call('admin::broadcast'))

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0][1], ab.STATE_BROADCAST)
        self.assertEqual(states[0][2]['admin_id'], support.ADMIN_ID)
        sent = [c for c in bot.calls if c[0] == 'send_message']
        self.assertEqual(len(sent), 1)
        self.assertTrue(any('أرسل الرسالة' in (t or '') for t in bot.sent()))
        self.assertTrue([c for c in bot.calls if c[0]=='answer_callback_query'])
        # الحالة تُربط برسالة التلميح التي أُرسلت فعلاً
        markup = sent[0][2].get('reply_markup')
        self.assertIn(ab.CALLBACK_CANCEL_BROADCAST, support.button_callbacks(markup))
        # لا next_step إطلاقاً: التدفق يعتمد الحالة المخزّنة فقط
        self.assertFalse([c for c in bot.calls if c[0] == 'register_next_step_handler'])

    def test_prompt_requires_admin(self):
        self.assertFalse(ab.prompt_broadcast(self.bot, support.MEMBER_ID, ADMINS))
        self.assertEqual(self.bot.calls, [])

    def test_prompt_refused_while_job_active(self):
        ab._active_jobs[support.ADMIN_ID] = True
        self.assertFalse(ab.prompt_broadcast(self.bot, support.ADMIN_ID, ADMINS))
        self.assertIn('قيد التنفيذ', self.bot.last_text())


class BroadcastMessageTest(BroadcastTestCase):
    # FakeBot المحقّق يعالج send_message عبر _record فيسجّل كل وسائطه كما مرّرت؛
    # البوت المستقل هنا يسجّل (send_message, args, kwargs) بنفس الشكل، لذا استعلامات
    # التقرير تقرأ args[1] مباشرة بلا اعتماد على طبقة المحاكاة.
    def check_report(self, bot, *fragments):
        texts = [args[1] for name, args, _k in bot.calls
                 if name == 'send_message' and len(args) > 1 and args[1]]
        report = [t for t in texts if 'نجح:' in t]
        self.assertTrue(report, texts)
        for fragment in fragments:
            self.assertIn(fragment, report[-1])
        return report[-1]

    def test_text_message_starts_copy_to_every_recipient(self):
        self.open_state()
        bot = self.start_job(self.reply('مرحبا بالجميع'))
        self.assertEqual(bot.copies, [(uid, 900, 91) for uid in (7, 8, 9)])
        self.check_report(bot, 'نجح: 3', 'فشل: 0')

    def test_rich_photo_message_is_copied_not_treated_as_text_only(self):
        self.open_state()
        bot = self.start_job(self.reply(None, content_type='photo'))
        self.assertEqual(bot.copies, [(uid, 900, 91) for uid in (7, 8, 9)])
        self.assertTrue(all(c[2] == 91 for c in bot.copies))

    def test_partial_failure_and_blocked_user_cleanup(self):
        self.open_state()
        self.bot = BroadcastBot({8: api_error('Forbidden: bot was blocked by the user'),
                                 9: api_error('Forbidden: user is deactivated')})
        self.assertTrue(ab.handle_broadcast_message(self.bot, ADMINS, self.reply('x')))
        self.assertEqual(self.bot.copies, [(7, 900, 91)])
        self.assertEqual(sorted(self.deleted), [8, 9])
        self.check_report(self.bot, 'نجح: 1', 'فشل: 0', 'معطّل): 2')

    def test_generic_failure_is_not_counted_as_removed(self):
        self.open_state()
        self.bot = BroadcastBot({8: api_error('Bad Request: chat not found', 400),
                                 9: api_error('Too Many Requests: retry after 5', 429)})
        self.execute(self.reply('x'))
        self.assertEqual(self.deleted, [])  # خطأ غير حظر/تعطيل: لا حذف مشتركين
        # لا محذوفين: الحذف يتم فقط بعد نحو تصنيف الأخطاء أعلاه، وكلاهما فشل عادي
        self.check_report(self.bot, 'نجح: 1', 'فشل: 2')

    def test_delete_failure_keeps_user_in_failed_count(self):
        self.open_state()
        self.delete_ok = False
        self.bot = BroadcastBot({8: api_error('Forbidden: bot was blocked by the user')})
        self.execute(self.reply('x'))
        self.assertEqual(self.deleted, [8])  # حاول الحذف لكن القاعدة رفضت
        # لا يُبلَّغ عن حذف مؤكد عندما يفشل delete_bot_user
        self.check_report(self.bot, 'فشل: 1')

    def test_zero_recipients_is_a_real_empty_audience(self):
        self.open_state()
        self.audience = ([], None)
        bot = self.start_job(self.reply('x'))
        self.assertEqual(bot.copies, [])
        self.check_report(bot, 'نجح: 0')

    def test_db_lookup_failure_never_reports_success(self):
        self.open_state()
        self.audience = (None, RuntimeError('db down'))
        bot, jobs = self.deliver(self.reply('x'))
        self.assertEqual(jobs, [])  # لم يبدأ أي عامل
        self.assertEqual(bot.copies, [])
        self.assertIn('قاعدة البيانات', bot.last_text())
        self.assertFalse([t for t in bot.sent() if t and 'نجح:' in t])  # لا تقرير دون إرسال

    def test_fetch_all_user_ids_distinguishes_error_from_empty(self):
        """execute_query(fetch='all') يحوّل الخطأ إلى []: المسار الصارم يرفع بدلاً منه."""
        original = db_manager.get_db_connection

        class Broken:
            def __enter__(self):
                import psycopg2
                raise psycopg2.OperationalError('SSL connection has been closed unexpectedly')

            def __exit__(self, *args):
                return False

        # نستعيد الاستبدال المحلي أولاً حتى يُرى استبدالنا في الاتصال
        db_manager.fetch_all_user_ids = self.original_fetch
        db_manager.get_db_connection = lambda: Broken()
        self.addCleanup(_restore, db_manager, 'get_db_connection', original)
        ids, error = db_manager.fetch_all_user_ids()
        self.assertIsNone(ids)
        self.assertIsNotNone(error)

    def test_cancel_by_button_clears_state_without_sending(self):
        self.open_state()
        cleared = self._cleared
        bot = self.bot
        callback_handlers.register(bot, ADMINS)
        checker = helpers.check_subscription
        helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(_restore, helpers, 'check_subscription', checker)
        bot.callback_handler()(support.make_call(ab.CALLBACK_CANCEL_BROADCAST))
        self.assertEqual(cleared, [support.ADMIN_ID])
        self.assertEqual(bot.copies_to(), [])
        self.assertFalse([t for t in bot.sent() if t and 'نجح:' in t])  # لا تقرير دون إرسال

    def test_non_admin_and_foreign_state_are_refused(self):
        self.open_state()
        self.assertFalse(ab.handle_broadcast_message(self.bot, ADMINS, self.reply('x', user_id=support.MEMBER_ID)))
        self.state = {'state': ab.STATE_BROADCAST, 'context': {'admin_id': support.OTHER_ADMIN_ID}}
        self.bot = BroadcastBot()
        self.execute(self.reply('x'))
        self.assertEqual(self.bot.copies_to(), [])

    def test_unbound_or_non_private_message_does_not_broadcast(self):
        # رسالة عادية (بلا رد) في حالة مملوكة تُقبل — هذا ما يطلبه التلميح صراحةً
        self.open_state()
        self.bot = BroadcastBot()
        self.execute(self.reply('x', reply_to=None))
        self.assertEqual(self.bot.copies_to(), [7, 8, 9])
        # الرد على رسالة أخرى غير التلميح يُرفض بلا إرسال
        self.open_state()
        self.bot = BroadcastBot()
        ab.handle_broadcast_message(self.bot, ADMINS, self.reply('x', reply_to=PROMPT_ID + 1))
        self.assertEqual(self.bot.copies_to(), [])
        self.assertIn('أرسل رسالة البث', self.bot.last_text())
        group = support.make_message('x', chat_type='group', chat_id=-1001)
        self.assertFalse(ab.handle_broadcast_message(self.bot, ADMINS, group))

    def test_duplicate_job_does_not_start_second_broadcast(self):
        self.open_state()
        ab._active_jobs[support.ADMIN_ID] = True
        bot = BroadcastBot()
        ab.handle_broadcast_message(bot, ADMINS, self.reply('x'))
        self.assertEqual(bot.copies_to(), [])
        self.assertIn('قيد التنفيذ', bot.last_text())

    def test_worker_failure_reports_failure_not_success(self):
        import threading
        original_thread = threading.Thread

        class BoomThread:
            def __init__(self, *args, **kwargs):
                raise RuntimeError('cannot start thread')

        threading.Thread = BoomThread
        self.addCleanup(_restore, threading, 'Thread', original_thread)
        self.open_state()
        bot = BroadcastBot()
        ab.handle_broadcast_message(bot, ADMINS, self.reply('x'))
        self.assertEqual(bot.copies_to(), [])
        self.assertIn('لم يبدأ البث', bot.last_text())
        self.assertEqual(ab._active_jobs, {})


class AdminFlowsTest(BroadcastTestCase):
    """تدفقات لوحة الأدمن بالحالة المخزّنة (نفس بوت FakeBot المستقل)."""

    def setUp(self):
        super().setUp()
        self.flows = __import__('handlers.admin_flows', fromlist=['x'])
        flows = importlib.import_module('handlers.admin_flows')
        self.flows = flows
        self.original_flows_state = (flows.read_state_row, flows.set_user_state,
                                     flows.clear_user_waiting_state)
        self.add_patches()
        self.calls = []
        self.channel_added = []
        self.remove_result = True

    def _record_channel(self, channel_id, name):
        self.channel_added.append((channel_id, name))
        return True

    def add_patches(self):
        """عزل الحالة والكتابات داخل admin_flows (مع تأخير الاستيراد كي لا نطمس مودوَل
        test_admin_panel نفسه)، مع تتبع كل استدعاء (بلا قاعدة/تيليجرام)."""
        flows = self.flows
        original = self.original_flows_state
        flows.read_state_row = original[0]
        flows.set_user_state = original[1]
        flows.clear_user_waiting_state = original[2]
        flows.read_state_row = self._reader
        flows.set_user_state = self._writer
        flows.clear_user_waiting_state = self._clearer
        self.addCleanup(_restore_flow_state, flows, original)
        self.named = []
        self.move_calls = []
        self.delete_calls = []
        self.patches = (
            ('add_category',
             lambda name, parent_id=None: (self.named.append((name, parent_id)), (True, 5))[1]),
            ('count_existing_video_ids', lambda ids: len(ids)),
            ('fetch_admin_categories', lambda: [{'id': 5, 'name': 'أفلام', 'parent_id': None}]),
            ('find_required_channel', lambda channel_id=None, name=None: None),
            ('fetch_admin_required_channels', lambda: []),
            ('move_videos_bulk_reported',
             lambda ids, target: (self.move_calls.append((ids, target)), (1, None))[1]),
            ('delete_videos_by_ids_reported',
             lambda ids: (self.delete_calls.append(ids), (len(ids), None))[1]),
            ('add_required_channel',
             lambda channel_id, name: (self.channel_added.append((channel_id, name)), True)[1]),
            ('remove_required_channel',
             lambda cid: (self.calls.append(('remove_channel', cid)), self.remove_result)[1]),
            ('set_bot_setting',
             lambda k, v: (self.calls.append(('setting', k, v)), True)[1]),
        )
        self.apply_patches()

    def apply_patches(self):
        """حقن دوال القاعدة في admin_flows بالاسم (يُعاد تطبيقه قبل كل استدعاء)."""
        for name, replacement in self.patches:
            setattr(self.flows, name, replacement)

    def open_flow(self, state, **context):
        self.apply_patches()
        context.setdefault('admin_id', support.ADMIN_ID)
        self._states.clear()
        self._cleared.clear()
        self.state = {'state': state, 'context': context}

    def text(self, value, user_id=support.ADMIN_ID):
        return support.make_message(value, user_id=user_id, message_id=77)

    def keep_flow_state(self, context, keys=()):
        """يبقي الحالة المخزّنة نفسها بين خطوتين (القراءة عند الطلب لا قبلها)."""
        self.apply_patches()
        state = dict(context)
        for key in keys:
            state.pop(key, None)
        self._states.clear()
        self._cleared.clear()
        self.state = {'state': context.pop('_state'), 'context': state}

    def setting_values(self):
        return [c for c in self.calls if c[0] == 'setting']

    # --- Mini App limit: رقم حر لا يغيّر الإعداد بلا طلب -------------------
    def test_miniapp_begin_persists_state_and_binds_prompt(self):
        self.assertTrue(self.flows.begin_miniapp_limit(self.bot, support.ADMIN_ID, ADMINS))
        self.assertEqual(self._states[-1][1], self.flows.ST_MINIAPP_LIMIT)
        self.assertTrue(self._states[-1][2].get('prompt_message_id'))
        self.assertIn('الأقصى', self.bot.last_text())

    def test_miniapp_numeric_accepted_range_and_arabic_digits(self):
        for raw, expected in (('250', 250), ('٠', 0)):
            self.calls.clear()
            self.open_flow(self.flows.ST_MINIAPP_LIMIT)
            self.assertTrue(self.flows.receive_miniapp_limit(
                self.bot, ADMINS, self.text(raw)))
            self.assertEqual(self.setting_values(), [('setting', 'miniapp_hourly_limit_per_ip',
                                                      str(expected))])
            self.assertEqual(self._cleared, [support.ADMIN_ID])
            self.assertIn('أصبح', self.bot.last_text())

    def test_miniapp_bad_values_keep_state_without_saving(self):
        for raw in ('-5', '100001', 'abc', ''):
            self.calls.clear()
            self._cleared.clear()
            self.open_flow(self.flows.ST_MINIAPP_LIMIT)
            self.assertTrue(self.flows.receive_miniapp_limit(self.bot, ADMINS, self.text(raw)))
            self.assertEqual(self.setting_values(), [], raw)
            self.assertEqual(self._cleared, [], raw)
            self.assertIn('غير صالحة', self.bot.last_text(), raw)

    def test_arbitrary_number_is_not_a_miniapp_setting(self):
        """بلا حالة طلب: رقم حر من الأدمن لا يصل إلى الإعداد ولا يستهلكه أي تدفق."""
        self.state = None
        self.assertFalse(self.flows.handle_flow_message(self.bot, ADMINS, self.text('500')))
        self.assertFalse(ab.handle_broadcast_message(self.bot, ADMINS, self.text('500')))
        self.assertEqual(self.calls, [])

    # --- التصنيفات ---------------------------------------------------------
    def test_category_add_main_success_and_invalid_inputs(self):
        self.open_flow(self.flows.ST_CATEGORY, parent_id=None)
        for bad in ('   ', '/admin', 'x' * 120):
            self._cleared.clear()
            self.assertTrue(self.flows.handle_flow_message(self.bot, ADMINS, self.text(bad)))
            self.assertEqual(self._cleared, [])
        self.assertTrue(self.flows.handle_flow_message(self.bot, ADMINS, self.text('  مسلسلات  ')))
        self.assertEqual(self.named, [('مسلسلات', None)])
        self.assertEqual(self._cleared, [support.ADMIN_ID])
        self.assertIn('تم إنشاء التصنيف', self.bot.last_text())

    # --- القنوات -----------------------------------------------------------
    def test_add_channel_two_steps_numeric_only(self):
        self.open_flow(self.flows.ST_CHANNEL_ID)
        self.assertEqual(self.state['state'], self.flows.ST_CHANNEL_ID)
        self.apply_patches()
        self.assertTrue(self.flows.receive_channel_id(self.bot, ADMINS, self.text('@my_channel')))
        self.assertIn('غير صالح', self.bot.last_text())
        self.assertEqual(self.channel_added, [])
        self.apply_patches()
        self.assertTrue(self.flows.receive_channel_id(
            self.bot, ADMINS, self.text('-1001234567890')))
        self.assertEqual(self._states[-1][1], self.flows.ST_CHANNEL_NAME)
        self.apply_patches()
        self.state = {'state': self.flows.ST_CHANNEL_NAME, 'context': self._states[-1][2]}
        self.assertTrue(self.flows.receive_channel_name(
            self.bot, ADMINS, self.text('قناة الأفلام')))
        self.assertEqual(self.channel_added, [('-1001234567890', 'قناة الأفلام')])
        self.assertIn('تمت إضافة القناة', self.bot.last_text())

    def test_remove_channel_is_truthful(self):
        self.remove_result = False
        self.assertTrue(self.flows.remove_channel_now(self.bot, support.ADMIN_ID, ADMINS,
                                                      '-1001234567890'))
        self.assertIn('لم تُزل', self.bot.last_text())
        self.remove_result = True
        self.assertTrue(self.flows.remove_channel_now(self.bot, support.ADMIN_ID, ADMINS,
                                                      '-1001234567890'))
        self.assertIn('تمت إزالة القناة', self.bot.last_text())

    # --- نقل/حذف -----------------------------------------------------------
    def test_move_invalid_ids_keep_state_and_valid_ids_show_targets(self):
        self.open_flow(self.flows.ST_MOVE_IDS)
        self.assertTrue(self.flows.receive_move_ids(self.bot, ADMINS, self.text('abc, 12')))
        self.assertIn('أرقام غير صالحة', self.bot.last_text())
        self.assertEqual(self.move_calls, [])
        self.assertTrue(self.flows.receive_move_ids(self.bot, ADMINS, self.text('12, ١٣')))
        self.assertEqual(self._states[-1][1], self.flows.ST_MOVE_TARGET)
        self.assertEqual(self._states[-1][2]['video_ids'], [12, 13])
        self.assertIn('اختر التصنيف', self.bot.last_text())

    def test_move_confirm_uses_stored_state_and_reports_partial(self):
        self.open_flow(self.flows.ST_MOVE_TARGET, video_ids=[12, 13])
        self.assertTrue(self.flows.move_now(self.bot, support.ADMIN_ID, ADMINS, 'none'))
        self.assertEqual(self.move_calls, [([12, 13], None)])
        self.assertIn('تم نقل 1', self.bot.last_text())
        self.assertIn('لم يُنقل', self.bot.last_text())

    def test_delete_requires_explicit_confirmation(self):
        self.open_flow(self.flows.ST_DELETE_IDS)
        self.assertTrue(self.flows.receive_delete_ids(self.bot, ADMINS, self.text('21 22')))
        self.assertEqual(self._states[-1][1], self.flows.ST_DELETE_CONFIRM)
        self.assertEqual(self.delete_calls, [])
        self.assertIn('تأكيد الحذف', self.bot.last_text())
        self.apply_patches()
        self.state = {'state': self.flows.ST_DELETE_CONFIRM, 'context': self._states[-1][2]}
        self.assertTrue(self.flows.delete_now(self.bot, support.ADMIN_ID, ADMINS))
        self.assertEqual(self.delete_calls, [[21, 22]])
        self.assertIn('تم حذف', self.bot.last_text())

    def test_flow_input_from_non_admin_or_group_never_mutates(self):
        self.open_flow(self.flows.ST_CATEGORY, parent_id=None)
        self.assertFalse(self.flows.handle_flow_message(
            self.bot, ADMINS, self.text('اسم', user_id=support.MEMBER_ID)))
        group = support.make_message('اسم', chat_type='group', chat_id=-1001)
        self.assertFalse(self.flows.receive_category_name(self.bot, ADMINS, group))
        self.assertEqual(self.named, [])

    def test_cancel_clears_without_mutation(self):
        self.open_flow(self.flows.ST_CATEGORY, parent_id=None)
        call = support.make_call('admin::cancel_flow')
        self.assertTrue(self.flows.cancel_flow(self.bot, support.ADMIN_ID, ADMINS, call=call))
        self.assertEqual(self._cleared, [support.ADMIN_ID])
        self.assertEqual(self.named, [])


def _restore_flow_state(flows, values):
    (flows.read_state_row, flows.set_user_state,
     flows.clear_user_waiting_state) = values


def _restore(module, name, value):
    setattr(module, name, value)


if __name__ == '__main__':
    unittest.main()
