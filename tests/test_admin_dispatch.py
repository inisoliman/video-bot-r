"""تصحيح موجة A: توجيه الرسائل (ContinueHandling)، صدق الحالة، وربط أزرار الـ callback.

أوفلاين بالكامل: بوت TeleBot حقيقي بلا token صالح وبلا شبكة (support يحجب HTTP)،
ودوال القاعدة/الحالة محقونة صراحةً بالقيم كما تفعل القاعدة الفعلية.
"""

import support  # noqa: F401  (يحجب telebot HTTP وpsycopg2 قبل استيراد الإنتاج)

import importlib
import unittest

import telebot
from telebot import types
from telebot.handler_backends import ContinueHandling

import db_manager
import handlers as handlers_package
from handlers import admin_flows
from handlers import admin_handlers
from handlers import callback_handlers
from handlers import comment_handlers
from handlers import helpers
from handlers import thumbnails as thumbnail_flows
from handlers import user_handlers


ADMINS = [support.ADMIN_ID, support.OTHER_ADMIN_ID]
PROMPT_ID = 7001
SETTINGS = []


def _restore(module, name, value):
    setattr(module, name, value)


class AutoStop(Exception):
    """يوقف قبول التحديثات بعد أحدث مشاهَد (بلا شبكة ولا حلقات)."""


def handler_funcs(bot):
    return [entry['function'] for entry in bot.message_handlers]


def handler_index(bot, name):
    for index, func in enumerate(handler_funcs(bot)):
        if getattr(func, '__name__', '') == name:
            return index
    raise AssertionError(f'handler {name!r} was not registered')


def make_message(text, user_id=support.ADMIN_ID, chat_type='private', message_id=11,
                 content_type='text', reply_to=None):
    return support.make_message(text, user_id=user_id, chat_type=chat_type,
                                message_id=message_id, content_type=content_type,
                                reply_to_message=reply_to)


def reply_message(message_id, text='بث'):
    message = make_message(text, message_id=message_id)
    message.reply_to_message = types.Message.de_json({
        'message_id': PROMPT_ID + 100, 'date': 1, 'chat': {'id': 1, 'type': 'private'},
        'from': {'id': support.ADMIN_ID, 'is_bot': True, 'first_name': 'bot'},
        'text': 'prompt'})
    return message


class RealDispatchTestCase(support.OfflineTestCase):
    """يستخدم توجيه TeleBot الحقيقي (دوال المعالجات المسجّلة فقط، بلا شبكة)."""

    def setUp(self):
        super().setUp()
        self.state = None
        self.cleared = []
        self.original_state = (admin_flows.read_state_row, admin_flows.set_user_state,
                               admin_flows.clear_user_waiting_state)
        admin_flows.read_state_row = lambda user_id: self.state
        admin_flows.set_user_state = lambda user_id, name, context=None: True
        admin_flows.clear_user_waiting_state = lambda user_id: (self.cleared.append(user_id), True)[1]
        self.addCleanup(lambda: _assign_flow_state(self.original_state))
        self.original_check = helpers.check_subscription
        helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(_restore, helpers, 'check_subscription', self.original_check)

    def build_bot(self):
        bot = telebot.TeleBot('123456:TEST-TOKEN-NOT-REAL', threaded=False,
                              validate_token=False, parse_mode='HTML')
        bot.stop_polling = lambda: (_ for _ in ()).throw(AutoStop())
        bot.send_message = lambda *a, **k: types.Message.de_json({
            'message_id': 8001, 'date': 1, 'chat': {'id': 1, 'type': 'private'},
            'from': {'id': 1, 'is_bot': True, 'first_name': 'bot'}, 'text': 'ok'})
        bot.answer_callback_query = lambda *a, **k: True
        bot.copy_message = lambda *a, **k: types.Message.de_json({
            'message_id': 8002, 'date': 1, 'chat': {'id': 1, 'type': 'private'},
            'from': {'id': 1, 'is_bot': True, 'first_name': 'bot'}, 'text': 'copy'})
        handlers_package.register_all_handlers(bot, '-1001234567890', ADMINS)
        return bot


def _assign_flow_state(values):
    (admin_flows.read_state_row, admin_flows.set_user_state,
     admin_flows.clear_user_waiting_state) = values


class AdminDispatchTest(RealDispatchTestCase):
    """admin_state_input يجب أن يمرّر الرسائل غير التابعة لتدفق جديد للبقية."""

    def test_unrelated_admin_text_continues_to_user_handlers(self):
        bot = self.build_bot()
        admin_entry = handler_index(bot, 'admin_state_input')
        comment_entry = handler_index(bot, 'handle_comment_text_states')
        # الترتيب الفعلي: إدخال الأدمن يُسجَّل قبل النص العام، وTeleBot يمرّ بالترتيب نفسه
        self.assertLess(admin_entry, comment_entry)
        message = make_message('كلمة بحث عادية')
        self.assertIsInstance(handler_funcs(bot)[admin_entry](message), ContinueHandling)

        # بنفس منطق توجيه TeleBot (بلا شبكة): المُعالج التالي هو نص user_handlers نفسه
        seen = []

        class StopRouting(Exception):
            pass

        def dispatch_like_telebot():
            # TeleBot يمرّ على message_handlers بترتيب الإضافة نفسه (routes في webhook_bot)
            for entry in bot.message_handlers:
                func = entry['function']
                if not bot._test_message_handler(entry, message):
                    continue
                seen.append(func.__name__)
                if not isinstance(func(message), ContinueHandling):
                    raise StopRouting()

        with self.assertRaises(StopRouting):
            dispatch_like_telebot()
        self.assertIn('admin_state_input', seen)
        self.assertIn('handle_comment_text_states', seen)
        self.assertGreater(seen.index('handle_comment_text_states'),
                           seen.index('admin_state_input'))

    def test_delegated_title_state_continues_while_handled_broadcast_stops(self):
        bot = self.build_bot()
        funcs = handler_funcs(bot)
        admin_entry = handler_index(bot, 'admin_state_input')

        self.state = {'state': 'title_rename_search', 'context': {'admin_id': support.ADMIN_ID}}
        self.assertIsInstance(funcs[admin_entry](make_message('اسم قديم')), ContinueHandling)

        self.state = {'state': 'replying_comment', 'context': {'comment_id': 7}}
        self.assertIsInstance(funcs[admin_entry](make_message('رد')), ContinueHandling)

        self.state = None
        self.assertIsInstance(funcs[admin_entry](make_message('نص')), ContinueHandling)

    def test_handled_broadcast_does_not_continue(self):
        bot = self.build_bot()
        funcs = handler_funcs(bot)
        admin_entry = handler_index(bot, 'admin_state_input')
        scheduled = []
        original_start = admin_handlers.handle_broadcast_message
        admin_handlers.handle_broadcast_message = lambda b, ids, m: scheduled.append(m) or True
        self.addCleanup(_restore, admin_handlers, 'handle_broadcast_message', original_start)
        self.assertIsNone(funcs[admin_entry](make_message('رسالة بث')))
        self.assertEqual(len(scheduled), 1)

    def test_state_input_current_nonadmin_message_is_refused(self):
        bot = self.build_bot()
        funcs = handler_funcs(bot)
        admin_entry = handler_index(bot, 'admin_state_input')
        self.state = {'state': 'waiting_category_name', 'context': {'admin_id': support.MEMBER_ID}}
        # غير الأدمن يُرفض قبل أي معالجة (لا استهلاك لحالة أحد)
        self.assertIsNone(funcs[admin_entry](make_message('اسم', user_id=support.MEMBER_ID)))
        self.assertEqual(self.cleared, [])


class BroadcastOwnershipTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        import handlers.admin_broadcast as broadcast_module
        self.ab = broadcast_module
        self.holder = {'state': {'state': broadcast_module.STATE_BROADCAST,
                                 'context': {'admin_id': support.ADMIN_ID,
                                             'prompt_message_id': PROMPT_ID}}}
        original = (broadcast_module.read_state_row, broadcast_module.set_user_state,
                    broadcast_module.clear_user_waiting_state)
        broadcast_module.read_state_row = lambda user_id: self.holder['state']
        broadcast_module.set_user_state = lambda user_id, name, context=None: True
        broadcast_module.clear_user_waiting_state = lambda user_id: True
        self.addCleanup(self._restore_broadcast, original)
        self.original_fetch = db_manager.fetch_all_user_ids
        db_manager.fetch_all_user_ids = lambda: ([7, 8], None)
        self.addCleanup(_restore, db_manager, 'fetch_all_user_ids', self.original_fetch)
        self.bot = _CopyBot()
        self.original_reserve = self.ab._reserve_job
        self.ab._reserve_job = lambda admin_id, runner, bot=None: (runner(), True)[1]
        self.addCleanup(_restore, self.ab, '_reserve_job', self.original_reserve)

    def _restore_broadcast(self, original):
        (self.ab.read_state_row, self.ab.set_user_state,
         self.ab.clear_user_waiting_state) = original

    def _plain(self, user_id=support.ADMIN_ID, reply_to=None):
        message = support.make_message('مرحبا', user_id=user_id, message_id=91,
                                       reply_to_message=reply_to)
        return message

    def test_owned_plain_message_runs_job_and_counts_success(self):
        msg = self._plain()
        self.assertTrue(self.ab.handle_broadcast_message(self.bot, ADMINS, msg))
        self.assertEqual(self.bot.copies, [(7, support.ADMIN_ID, 91), (8, support.ADMIN_ID, 91)])

    def test_owner_must_match_explicitly(self):
        # الحالة بلا admin_id أو لمالك آخر: لا تُقبل حتى لو كان المُرسِل أدمن
        self.holder['state'] = {'state': self.ab.STATE_BROADCAST, 'context': {'prompt_message_id': PROMPT_ID}}
        self.assertTrue(self.ab.handle_broadcast_message(self.bot, ADMINS, self._plain()))  # مُستهلَكة: تُبلَّغ بلا بث
        self.holder['state'] = {'state': self.ab.STATE_BROADCAST,
                               'context': {'admin_id': support.OTHER_ADMIN_ID,
                                           'prompt_message_id': PROMPT_ID}}
        self.assertTrue(self.ab.handle_broadcast_message(self.bot, ADMINS, self._plain()))  # مُستهلَكة: تُبلَّغ بلا بث
        self.assertEqual(self.bot.copies, [])

    def test_wrong_reply_is_rejected_without_job(self):
        message = self._plain()
        message.reply_to_message = types.Message.de_json({
            'message_id': PROMPT_ID + 5, 'date': 1, 'chat': {'id': 1, 'type': 'private'},
            'from': {'id': support.ADMIN_ID, 'is_bot': True, 'first_name': 'bot'}, 'text': 'x'})
        self.ab.handle_broadcast_message(self.bot, ADMINS, message)
        self.assertEqual(self.bot.copies, [])
        self.assertIn('أرسل رسالة البث', self.bot.last())

    def test_copy_none_is_not_counted_as_success(self):
        self.bot.return_none = True
        self.ab.handle_broadcast_message(self.bot, ADMINS, self._plain())
        report = [t for t in self.bot.sent() if 'نجح:' in (t or '')][-1]
        self.assertIn('نجح: 0', report)
        self.assertIn('فشل: 2', report)

    def test_rate_is_twenty_per_second_and_no_sleep_after_partial_batch(self):
        sleeps = []
        self.bot.copy_errors = {}
        self.ab.run_broadcast(self.bot, support.ADMIN_ID, 1, 91, list(range(1, 41)),
                              sleeper=sleeps.append)
        self.assertEqual(sleeps, [1.0])  # نوم واحد بعد الدفعة الكاملة الأولى فقط
        sleeps.clear()
        self.ab.run_broadcast(self.bot, support.ADMIN_ID, 1, 91, list(range(1, 21)),
                              sleeper=sleeps.append)
        self.assertEqual(sleeps, [])  # 20 بالضبط: لا نوم بعد دفعة أخيرة مكتملة تماماً
        sleeps.clear()
        self.ab.run_broadcast(self.bot, support.ADMIN_ID, 1, 91, list(range(1, 22)),
                              sleeper=sleeps.append)
        self.assertEqual(sleeps, [1.0])

    def test_worker_exception_reports_on_captured_bot_and_clears_job(self):
        # العامل التُقط ببوت مختلف عن البوت الحالي: التقارير تصل للبوت الملتقط فقط
        captured_bot = _ReportBot()
        scheduled = []

        def runner():
            scheduled.append('ran')
            raise RuntimeError('worker exploded')

        self.ab._JOBS[support.ADMIN_ID] = captured_bot
        self.ab._active_jobs[support.ADMIN_ID] = True
        self.addCleanup(self.ab._active_jobs.clear)
        self.addCleanup(self.ab._JOBS.clear)
        self.ab._job_wrapper(support.ADMIN_ID, runner)
        self.assertEqual(scheduled, ['ran'])
        self.assertIn('توقف البث', captured_bot.last() or '')
        self.assertEqual(self.bot.sent(), [])          # البوت الحالي لم يُستخدم للتقرير
        self.assertNotIn(support.ADMIN_ID, self.ab._active_jobs)   # تنظيف القفل
        self.assertNotIn(support.ADMIN_ID, self.ab._JOBS)          # تنظيف سجل المهمة
        self.assertFalse(self.ab.has_active_job(support.ADMIN_ID))


class _ReportBot:
    """بوت تقارير بسيط يسجّل ما يُرسل إليه."""

    def __init__(self):
        self.calls = []

    def send_message(self, chat_id, text, **kwargs):
        self.calls.append((chat_id, text))

    def sent(self):
        return [text for _chat, text in self.calls]

    def last(self):
        texts = self.sent()
        return texts[-1] if texts else None


class _CopyBot:
    def __init__(self):
        self.calls = []
        self.copies = []
        self.copy_errors = {}
        self.fail_all = False
        self.return_none = False

    def send_message(self, chat_id, text, **kwargs):
        self.calls.append((chat_id, text))

    def copy_message(self, user_id, from_chat_id, message_id):
        if self.fail_all:
            raise RuntimeError('boom')
        self.copies.append((user_id, from_chat_id, message_id))
        return None if self.return_none else object()

    def sent(self):
        return [text for _chat, text in self.calls]

    def last(self):
        texts = self.sent()
        return texts[-1] if texts else None


class StateTruthfulnessTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.manager = importlib.import_module('state_manager')
        self.wrote = []
        self.cleared = []
        self.write_result = True
        self.clear_result = True
        self.original = (self.manager.set_user_state, self.manager.clear_user_state,
                         self.manager.get_user_state)
        self.manager.set_user_state = lambda uid, name, ctx=None: (
            self.wrote.append((uid, name)), self.write_result)[1]
        self.manager.clear_user_state = lambda uid: (
            self.cleared.append(uid), self.clear_result)[1]
        self.manager.get_user_state = lambda uid: {'state': 'waiting_broadcast_message',
                                                   'context': {}}
        self.addCleanup(lambda: self._restore())

    def _restore(self):
        (self.manager.set_user_state, self.manager.clear_user_state,
         self.manager.get_user_state) = self.original

    def test_helpers_return_boolean_and_cancel_reports_failure(self):
        self.assertIs(self.manager.set_user_waiting_for_input(1, 'waiting_category_name'), True)
        self.assertIs(self.manager.clear_user_waiting_state(1), True)
        self.write_result = False
        self.clear_result = False
        self.assertIs(self.manager.set_user_waiting_for_input(1, 'waiting_category_name'), False)
        self.assertIs(self.manager.clear_user_waiting_state(1), False)
        bot = support.FakeBot()
        self.assertTrue(self.manager.state_manager.handle_message(
            support.make_message('/cancel'), bot))
        self.assertIn('تعذّر إلغاء', bot.last_text())
        self.assertNotIn('تم إلغاء العملية', bot.last_text())

    def test_prompt_start_requires_confirmed_store(self):
        flows = importlib.import_module('handlers.admin_flows')
        original = (flows.read_state_row, flows.set_user_state, flows.clear_user_waiting_state)
        flows.read_state_row = lambda uid: None
        flows.set_user_state = lambda uid, name, ctx=None: False
        self.addCleanup(lambda: _assign_flow_state(original))
        bot = support.FakeBot()
        self.assertFalse(flows.begin_category(bot, support.ADMIN_ID, ADMINS))
        self.assertIn('تعذّر بدء العملية', bot.last_text())


class CallbackWiringTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.original_check = helpers.check_subscription
        helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(_restore, helpers, 'check_subscription', self.original_check)
        self.original_settings = db_manager.set_bot_setting
        db_manager.set_bot_setting = lambda key, value: (SETTINGS.append((key, value)), True)[1]
        self.addCleanup(_restore, db_manager, 'set_bot_setting', self.original_settings)
        self.flow_state = None
        self.flow_writes = []
        self.flow_clears = []
        self.original_flow = (admin_flows.read_state_row, admin_flows.set_user_state,
                              admin_flows.clear_user_waiting_state)
        admin_flows.read_state_row = lambda uid: self.flow_state
        admin_flows.set_user_state = lambda uid, name, ctx=None: (
            self.flow_writes.append((uid, name, ctx)), True)[1]
        admin_flows.clear_user_waiting_state = lambda uid: (self.flow_clears.append(uid), True)[1]
        self.addCleanup(lambda: _assign_flow_state(self.original_flow))
        self.bot = _CallbackBot()

    def dispatch(self, data, chat_type='private'):
        callback_handlers.register(self.bot, ADMINS)
        handler = self.bot.callback_handler()
        call = support.make_call(data, chat_type=chat_type, chat_id=-1001 if chat_type == 'group' else None)
        call.message.chat.id = support.ADMIN_ID if chat_type == 'private' else -1001
        handler(call)
        return call

    def test_set_miniapp_limit_starts_persisted_flow(self):
        self.dispatch('admin::set_miniapp_limit')
        self.assertEqual(self.flow_writes[-1][1], admin_flows.ST_MINIAPP_LIMIT)
        self.assertIn('أرسل حد فتح المكتبة', self.bot.last())

    def test_thumbnails_start_and_cancel_through_callbacks(self):
        started = []
        clear_state = thumbnail_flows.clear_user_waiting_state
        thumbnail_flows.clear_user_waiting_state = lambda uid: (started.append(uid), True)[1]
        self.addCleanup(_restore, thumbnail_flows, 'clear_user_waiting_state', clear_state)
        self.dispatch('admin::set_default_thumb')
        self.assertIn('أرسل الصورة', self.bot.last())
        self.dispatch('admin::manual_thumb')
        self.assertIn('رقم الفيديو', self.bot.last())
        started.clear()
        self.dispatch('admin::cancel_thumb')
        self.assertEqual(started, [support.ADMIN_ID])   # إلغاء واحد يمسح الحالة مرة
        self.assertIn('تم الإلغاء', self.bot.answers[-1][0] or '')

    def test_thumbnail_begin_failure_is_reported_and_never_calls_none(self):
        original = thumbnail_flows.begin
        thumbnail_flows.begin = lambda bot, uid, ids, action: (False, 'خطأ في قاعدة البيانات')
        self.addCleanup(_restore, thumbnail_flows, 'begin', original)
        self.dispatch('admin::set_default_thumb')
        self.assertIn('تعذّر بدء', self.bot.answers[-1][0] or '')
        saved = callback_handlers.thumbnail_flows
        callback_handlers.thumbnail_flows = None
        self.addCleanup(_restore, callback_handlers, 'thumbnail_flows', saved)
        self.dispatch('admin::manual_thumb')
        self.assertIn('غير متاحة', self.bot.answers[-1][0] or '')

    def test_move_confirm_accepts_none_and_positive_only(self):
        original = admin_handlers.move_now
        seen = []
        admin_handlers.move_now = lambda bot, uid, ids, target, call=None: (
            seen.append(target), True)[1]
        self.addCleanup(_restore, admin_handlers, 'move_now', original)
        self.flow_state = {'state': admin_flows.ST_MOVE_TARGET,
                           'context': {'admin_id': support.ADMIN_ID, 'video_ids': [1]}}
        self.dispatch('admin::move_confirm::none')
        self.dispatch('admin::move_confirm::12')
        self.assertEqual(seen, ['none', '12'])
        self.dispatch('admin::move_confirm::abc')
        self.dispatch('admin::move_confirm::-4')
        self.assertEqual(seen, ['none', '12'])
        self.assertIn('غير مكتملة', self.bot.answers[-1][0] or '')

    def test_group_chat_callback_mutations_are_refused(self):
        cleared = self.flow_clears
        self.flow_state = {'state': admin_flows.ST_DELETE_CONFIRM,
                           'context': {'admin_id': support.ADMIN_ID, 'video_ids': [1]}}
        self.dispatch('admin::confirm_delete_ids', chat_type='group')
        self.assertIn('المحادثة الخاصة', self.bot.answers[-1][0] or '')
        self.assertEqual(cleared, [])

    def test_delete_category_confirm_escapes_html_and_sets_parse_mode(self):
        original = callback_handlers.get_category_by_id
        callback_handlers.get_category_by_id = lambda cid: {'id': cid, 'name': '<b>خطر</b>'}
        self.addCleanup(_restore, callback_handlers, 'get_category_by_id', original)
        self.dispatch('admin::delete_category_confirm::5')
        text, _chat, _mid = self.bot.edits[-1][0]
        self.assertIn('&lt;b&gt;خطر&lt;/b&gt;', text)
        self.assertEqual(self.bot.edits[-1][1].get('parse_mode'), 'HTML')


class _CallbackBot:
    def __init__(self):
        self.registered = []
        self.answers = []
        self.messages = []
        self.edits = []

    def callback_query_handler(self, *args, **kwargs):
        def decorator(func):
            self.registered.append(func)
            return func
        return decorator

    def message_handler(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def callback_handler(self):
        return self.registered[0]

    def answer_callback_query(self, call_id, text=None, **kwargs):
        self.answers.append((text, kwargs.get('show_alert', False)))
        return True

    def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        from types import SimpleNamespace
        return SimpleNamespace(message_id=9000 + len(self.messages),
                               chat=SimpleNamespace(id=chat_id, type='private'))

    def reply_to(self, message, text, **kwargs):
        return self.send_message(message.chat.id, text, **kwargs)

    def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        self.edits.append(((text, chat_id, message_id), kwargs))
        return True

    def edit_message_reply_markup(self, *args, **kwargs):
        return True

    def delete_message(self, *args, **kwargs):
        return True

    def sent(self):
        return [text for _chat, text, _k in self.messages]

    def last(self):
        texts = self.sent()
        return texts[-1] if texts else None


if __name__ == '__main__':
    unittest.main()
