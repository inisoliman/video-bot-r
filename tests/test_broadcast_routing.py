"""انحدار توجيه البث: رسالة الأدمن في waiting_broadcast_message تصل لمعالج البث الحقيقي
وتُجدول مهمة واحدة، ولا تصل أبداً إلى مسار البحث العام.

أوفلاين بالكامل: بوت TeleBot حقيقي (support يحجب HTTP وpsycopg2)، والتسجيل عبر
handlers.register_all_handlers نفسه، ثم process_new_messages مع threaded=False
(نفس هيئة webhook_bot.process_update). المُزيَّف هو الآثار الخارجية فقط:
حالة مخزّنة، جمهور البث، وحجز العامل (يُسجَّل ولا يبدأ خيطاً). لا إرسال جماعي.
"""

import sys
import unittest
from unittest import mock

import support

from telebot import types

import db_manager
import handlers as handlers_package
import handlers.admin_broadcast as broadcast_module
import handlers.user_handlers as user_handlers
from state_manager import state_manager

ADMINS = [support.ADMIN_ID]
CHANNEL_ID = '-1001234567890'
OWNED_STATE = {'state': broadcast_module.STATE_BROADCAST,
               'context': {'admin_id': support.ADMIN_ID, 'prompt_message_id': 1}}


class _ProbeBot:
    """بوت TeleBot حقيقي: تُسجَّل عليه المعالجات فعلياً، ويُسجَّل الإرسال بلا شبكة.

    الجسر إلى البوت الداخلي عبر __getattr__ وحده (لا نسخ سمة في __dict__).
    """

    def __init__(self):
        import telebot
        self.__dict__['sent'] = []
        self.__dict__['copies'] = []
        self.__dict__['_inner'] = telebot.TeleBot(
            '123456:TEST-TOKEN-NOT-REAL', threaded=False, validate_token=False,
            parse_mode='HTML')

    def send_message(self, chat_id, text=None, **kwargs):
        self.sent.append((chat_id, text))
        return types.Message.de_json({
            'message_id': 9000 + len(self.sent), 'date': 1,
            'chat': {'id': chat_id, 'type': 'private'},
            'from': {'id': 1, 'is_bot': True, 'first_name': 'bot'}, 'text': text or 'x'})

    def copy_message(self, user_id, from_chat_id, message_id):
        self.copies.append((user_id, from_chat_id, message_id))
        return object()

    def get_me(self):
        return types.User.de_json({'id': 1, 'is_bot': True, 'first_name': 'b',
                                   'username': 'probe_bot'})

    def __getattr__(self, name):
        return getattr(self.__dict__['_inner'], name)


class BroadcastRoutingRegressionTest(support.OfflineTestCase):
    """التسجيل والتوجيه الفعليان؛ معالج admin_broadcast الحقيقي هو المنتج."""

    def setUp(self):
        super().setUp()
        self.bot = _ProbeBot()
        self.state = None
        self.jobs = []
        self.search_hits = []
        self._patches = []

    def tearDown(self):
        while self._patches:
            self._patches.pop()()
        super().tearDown()

    def stub_globals(self, module_name, **values):
        """تعهيد مؤقت عبر __dict__ الموديول (نافذة المعالج)، مع استعادة في tearDown."""
        module = sys.modules[module_name]
        for name, value in values.items():
            had = name in module.__dict__
            original = module.__dict__.get(name)
            module.__dict__[name] = value
            self._patches.append(
                lambda n=name, h=had, o=original, m=module: (
                    m.__dict__.__setitem__(n, o) if h else m.__dict__.pop(n, None)))

    def install(self):
        handlers_package.register_all_handlers(self.bot, CHANNEL_ID, ADMINS)
        self.stub_globals(
            'handlers.admin_broadcast',
            read_state_row=lambda user_id: self.state,
            clear_user_waiting_state=lambda user_id: True,
            set_user_state=lambda uid, name, ctx=None: True,
            _fetch_audience=lambda: ([7, 8], None),
            _reserve_job=lambda admin_id, runner, bot=None: (
                self.jobs.append((admin_id, runner)), True)[1],
        )
        # نفس تعيينات state_manager لـ admin_handlers (نُقلت عبر الاستيراد)
        self.stub_globals(
            'handlers.admin_handlers',
            read_state_row=lambda user_id: self.state,
            clear_user_waiting_state=lambda user_id: True,
        )
        self.stub_globals(
            'handlers.user_handlers',
            handle_private_text_search_direct=(
                lambda message, bot: self.search_hits.append(message.text)),
            get_user_state=lambda user_id: self.state,
        )
        self.stub_globals(db_manager.__name__, add_bot_user=lambda *a, **k: True)

    def dispatch(self, text):
        message = types.Update.de_json({
            'update_id': 500,
            'message': {
                'message_id': 91, 'date': 1700000000, 'text': text,
                'from': {'id': support.ADMIN_ID, 'is_bot': False, 'first_name': 'A'},
                'chat': {'id': support.ADMIN_ID, 'type': 'private'},
            },
        }).message
        self.bot.process_new_messages([message])

    def test_owned_state_reaches_real_broadcast_handler_end_to_end(self):
        """الرسالة تمرّ عبر admin_state_input إلى المعالج الحقيقي: مهمة واحدة، لا بحث."""
        self.install()
        self.state = dict(OWNED_STATE)
        self.dispatch('رسالة البث الحقيقية')
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(self.jobs[0][0], support.ADMIN_ID)
        self.assertEqual(self.jobs[0][1].__name__, '<lambda>')  # runner المجدول
        self.assertEqual(self.search_hits, [])
        self.assertEqual(self.bot.copies, [])

    def test_real_handler_returns_true_for_owned_state_and_false_otherwise(self):
        """لو توقف المعالج عن إرجاع True لحالة مملوكة يسقط هذا الاختبار."""
        self.install()
        handler = sys.modules['handlers.admin_handlers'].handle_broadcast_message
        message = self.message('رسالة بث')
        self.state = dict(OWNED_STATE)
        self.assertIs(handler(self.bot, ADMINS, message), True)
        self.assertEqual([admin_id for admin_id, _runner in self.jobs], [support.ADMIN_ID])
        self.state = {'state': 'waiting_category_name',
                      'context': {'admin_id': support.ADMIN_ID}}
        self.assertIs(handler(self.bot, ADMINS, message), False)
        self.assertEqual(len(self.jobs), 1)

    def message(self, text, user_id=support.ADMIN_ID):
        return types.Update.de_json({
            'update_id': 500,
            'message': {'message_id': 91, 'date': 1700000000, 'text': text,
                        'from': {'id': user_id, 'is_bot': False, 'first_name': 'A'},
                        'chat': {'id': user_id, 'type': 'private'}},
        }).message

    def test_delegated_title_state_still_continues_to_its_user_handler(self):
        self.install()
        connected = []
        self.stub_globals(
            'handlers.comment_handlers',
            process_title_name_text=(
                lambda bot, message, admin_ids: (connected.append(message.text), True)[1]))
        self.state = {'state': 'title_rename_search', 'context': {'admin_id': support.ADMIN_ID}}
        self.dispatch('اسم قديم')
        self.assertEqual(connected, ['اسم قديم'])
        self.assertEqual(self.search_hits, [])
        self.assertEqual(self.jobs, [])

    def test_unknown_state_warns_but_admin_prompt_state_does_not(self):
        manager_module = sys.modules['state_manager']
        self.stub_globals('state_manager',
                          get_user_state=lambda user_id: self.state,
                          clear_user_state=lambda user_id: True)
        message = self.message('x')
        with mock.patch.object(manager_module.logger, 'warning') as warning:
            self.state = {'state': broadcast_module.STATE_BROADCAST, 'context': {}}
            self.assertFalse(state_manager.handle_message(message, self.bot))
            self.state = {'state': 'not_a_real_state', 'context': {}}
            self.assertFalse(state_manager.handle_message(message, self.bot))
        texts = [str(call.args[0]) for call in warning.call_args_list]
        self.assertFalse(any(broadcast_module.STATE_BROADCAST in text for text in texts))
        self.assertTrue(any('not_a_real_state' in text for text in texts))


if __name__ == '__main__':
    unittest.main()
