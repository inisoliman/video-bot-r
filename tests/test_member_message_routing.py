"""توجيه رسائل الأعضاء: /start والنص العادي يصلان لمعالجات user_handlers الحقيقية،
وسلطة الأدمن تبقى كما هي.

الخلل المُثبَت: handlers.admin_handlers يسجّل admin_state_input (نص/صورة...) بفلتر
``message.chat.type == 'private'`` فقط، ومعالجات الأدمن تُسجَّل أولاً؛ فيُرد على
المستخدم العادي «ليس لديك صلاحية الوصول إلى هذا الأمر.» قبل أن يصل /start أو النص
العادي إلى user_handlers. الإصلاح المقصود محصور بفلتر هذا التسجيل.

أوفلاين بالكامل (support يحجب HTTP وpsycopg2): بوت TeleBot حقيقي (threaded=False,
validate_token=False) مع دوال إرسال وهمية صريحة، والتسجيل عبر
handlers.register_all_handlers، والتحديثات عبر types.Update.de_json ثم
process_new_messages (نفس هيئة webhook_bot). المُزيَّف هو الآثار الخارجية فقط:
حالة مخزّنة، اشتراك، إضافة مستخدم، جمهور البث، وحجز العامل (يُسجَّل ولا يبدأ خيطاً).
لا شبكة ولا قاعدة بيانات.
"""

import unittest

import support

from telebot import types

import db_manager
import handlers as handlers_package
from handlers import admin_broadcast
from state_manager import state_manager

ADMINS = [support.ADMIN_ID]
CHANNEL_ID = '-1001234567890'
REFUSAL = 'ليس لديك صلاحية الوصول إلى هذا الأمر.'
WELCOME = 'أهلاً بك في بوت البحث عن الفيديوهات'
MEMBER_TEXT = 'فيلم'
OWNED_STATE = {'state': admin_broadcast.STATE_BROADCAST,
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

    def reply_to(self, message, text=None, **kwargs):
        return self.send_message(message.chat.id, text, **kwargs)

    def copy_message(self, user_id, from_chat_id, message_id):
        self.copies.append((user_id, from_chat_id, message_id))
        return object()

    def get_me(self):
        return types.User.de_json({'id': 1, 'is_bot': True, 'first_name': 'b',
                                   'username': 'probe_bot'})

    def get_chat_member(self, channel_id, user_id, **kwargs):
        raise AssertionError('subscription is stubbed: no get_chat_member expected')

    def __getattr__(self, name):
        return getattr(self.__dict__['_inner'], name)

    def texts(self, chat_id):
        return [text for target, text in self.__dict__['sent'] if target == chat_id]


class MemberMessageRoutingTest(support.OfflineTestCase):
    """التسجيل والتوجيه الفعليان؛ معالجات user_handlers / admin_handlers هي المنتج."""

    def setUp(self):
        super().setUp()
        self.bot = _ProbeBot()
        self.state = None
        self.state_clears = []
        self.jobs = []
        self.search_hits = []
        self.bot_users = []
        self._patches = []

    def tearDown(self):
        while self._patches:
            self._patches.pop()()
        super().tearDown()

    def stub(self, module, **values):
        """تعهيد مؤقت عبر __dict__ الموديول (نافذة المعالج)، مع استعادة في tearDown."""
        for name, value in values.items():
            had = name in module.__dict__
            original = module.__dict__.get(name)
            module.__dict__[name] = value
            self._patches.append(
                lambda n=name, h=had, o=original, m=module: (
                    m.__dict__.__setitem__(n, o) if h else m.__dict__.pop(n, None)))

    def store(self, user_id):
        return self.state

    def clear(self, user_id):
        self.state_clears.append(user_id)
        return True

    def install(self):
        handlers_package.register_all_handlers(self.bot, CHANNEL_ID, ADMINS)
        # نفس تعيينات اختبار توجيه البث للحالة المخزّنة والجمهور والجدولة
        self.stub(admin_broadcast,
                  read_state_row=self.store,
                  clear_user_waiting_state=self.clear,
                  set_user_state=lambda uid, name, ctx=None: True,
                  _fetch_audience=lambda: ([7, 8], None),
                  _reserve_job=lambda admin_id, runner, bot=None: (
                      self.jobs.append((admin_id, runner)), True)[1])
        admin_handlers = handlers_package.admin_handlers
        self.stub(admin_handlers,
                  read_state_row=self.store,
                  clear_user_waiting_state=self.clear)
        # حالة state_manager تُقرأ من globals state_manager (state_handler + التمرير)
        self.stub(state_manager,
                  read_state_row=self.store,
                  get_user_waiting_context=lambda user_id: None,
                  clear_user_waiting_state=self.clear)
        # دالة البحث الخارجية فقط: user_handlers يقرأ اسمها من globals الموديول
        user_handlers = handlers_package.user_handlers
        self.stub(user_handlers,
                  handle_private_text_search_direct=(
                      lambda message, bot: self.search_hits.append(
                          (message.from_user.id, message.text))),
                  check_subscription=lambda bot, user_id: (True, []),
                  add_bot_user=lambda user_id, username, first_name: (
                      self.bot_users.append(user_id), True)[1])
        self.stub(db_manager, add_bot_user=lambda *a, **k: True)

    def dispatch(self, text, user_id=support.MEMBER_ID):
        message = types.Update.de_json({
            'update_id': 500,
            'message': {
                'message_id': 91, 'date': 1700000000, 'text': text,
                'from': {'id': user_id, 'is_bot': False, 'first_name': 'U'},
                'chat': {'id': user_id, 'type': 'private'},
            },
        }).message
        self.bot.process_new_messages([message])

    def replies(self, user_id):
        return self.bot.texts(user_id)

    # --- رسائل الأعضاء ------------------------------------------------------
    def test_member_start_gets_normal_welcome(self):
        self.install()
        self.dispatch('/start')
        texts = self.replies(support.MEMBER_ID)
        self.assertNotIn(REFUSAL, texts)
        self.assertTrue(any(WELCOME in (text or '') for text in texts),
                        f'normal welcome missing: {texts!r}')
        self.assertEqual(self.bot_users, [support.MEMBER_ID])

    def test_member_plain_text_reaches_search_direct(self):
        self.install()
        self.dispatch(MEMBER_TEXT)
        texts = self.replies(support.MEMBER_ID)
        self.assertNotIn(REFUSAL, texts)
        # معالج النص الحقيقي وصل: يفتح خيارات البحث عن الكلمة نفسها (أثره المقصود)
        self.assertTrue(any(MEMBER_TEXT in (text or '') for text in texts),
                        f'replies={texts!r}')

    # --- سلطة الأدمن --------------------------------------------------------
    def test_admin_start_still_works(self):
        self.install()
        self.dispatch('/start', user_id=support.ADMIN_ID)
        texts = self.replies(support.ADMIN_ID)
        self.assertNotIn(REFUSAL, texts)
        self.assertTrue(any(WELCOME in (text or '') for text in texts),
                        f'admin welcome missing: {texts!r}')
        self.assertEqual(self.bot_users, [support.ADMIN_ID])

    def test_member_admin_command_is_still_refused(self):
        self.install()
        self.dispatch('/admin')
        self.assertIn(REFUSAL, self.replies(support.MEMBER_ID))
        self.assertEqual(self.search_hits, [])
        self.assertEqual(self.bot_users, [])

    # --- إصلاح توجيه البث محفوظ --------------------------------------------
    def test_owned_broadcast_handled_once_without_generic_search(self):
        self.install()
        self.state = dict(OWNED_STATE)
        self.dispatch('رسالة البث الحقيقية', user_id=support.ADMIN_ID)
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(self.jobs[0][0], support.ADMIN_ID)
        self.assertEqual(self.jobs[0][1].__name__, '<lambda>')  # runner المجدول
        self.assertEqual(self.search_hits, [])
        self.assertEqual(self.bot.copies, [])  # العامل يُسجَّل ولا يبدأ


if __name__ == '__main__':
    unittest.main()
