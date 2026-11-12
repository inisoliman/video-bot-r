"""فتح المكتبة المصغّرة في الجروب/السوبرجروب عبر رابط تيليجرام المباشر.

قيود تيليجرام (الموثقة): زر ``web_app`` يفتح التطبيق المصغّر من المحادثة الخاصة
فقط، ولا يمكن ضبط زر قائمة للجروب؛ الطريقة الصحيحة في الجروبات هي رابط التطبيق
المصغّر الرئيسي ``https://t.me/<bot>?startapp=library`` كزر URL عادي.

تُختبَر هنا مسارات المنتج الحقيقية: بوت حقيقي (TeleBot بلا شبكة) تُسجَّل عليه
معالجات ``handlers`` فعلياً، و``types.Update.de_json`` كما في ``webhook_bot``.
الشبكة وقاعدة البيانات محجوبتان في ``support``، والخيوط المؤجَّلة (حذف رسالة
الترحيب بعد 30 دقيقة) مستبدلة صراحةً فلا ينام الاختبار ولا يحذف شيئاً.

الصفحة نفسها تُشغَّل في Chromium المحلي (Playwright) مع بديل SDK يسجّل الاستدعاءات:
يُحاكى فتحها من رابط مباشر داخل جروب (``tgWebAppData`` + ``startapp`` + chat_type
group) فلا تنكسر، وتبقى التصفح والمفضلة كما هي، والفتح لا يدّعي وصولاً للرسائل.
"""

import json
import os
import sys
import threading
import unittest
from html import escape
from types import SimpleNamespace

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode
    from . import support
except ImportError:  # top-level mode
    import support

import db_manager  # noqa: E402
import handlers as handlers_package  # noqa: E402
from handlers import group_handlers, user_handlers  # noqa: E402
from miniapp import MINIAPP_KEYBOARD_TEXT  # noqa: E402
from state_manager import state_manager  # noqa: E402
from telebot import types  # noqa: E402

ADMINS = [support.ADMIN_ID]
CHANNEL_ID = '-1001234567890'
GROUP_CHAT_ID = -1009876543210
GROUP_TYPE = 'group'
SUPERGROUP_TYPE = 'supergroup'
APP_URL = 'https://library.example.com'
BOT_USERNAME = 'video_library_bot'
BOT_ID = 424242
GROUP_LINK = f'https://t.me/{BOT_USERNAME}?startapp=library'
BUTTON_TEXT = '📱 فتح مكتبة الفيديوهات'
HINT_TEXT = 'مكتبة الفيديوهات متاحة في الخاص فقط'
WELCOME_LINES = (
    '👋 <b>مرحباً! تم إضافتي للجروب</b>',
    '🔍 <b>للبحث السريع عن الفيديوهات:</b>',
    '📝 <b>مثال:</b>',
    '💡 <b>نصيحة:</b>',
    '⚡ هذه الرسالة ستُحذف تلقائياً بعد 30 دقيقة',
)
SECRETS = ('123456:TEST-TOKEN-NOT-REAL', 'BROWSER-CHECK', 'MANAGEMENT_TOKEN',
           'initData=', 'tgWebAppData', 'hash=', 'signature=', 'SECRET_TWA')
TELEGRAM_SDK_URL = 'https://telegram.org/js/telegram-web-app.js'
PAGE_PATH = '/app'


# ---------------------------------------------------------------------------
# بيئة الاختبار: عنوان التطبيق واسم البوت المستخدمان مؤقتاً فقط
# ---------------------------------------------------------------------------

class ScopedEnv:
    """متغيّرات البيئة الخاصة بالمكتبة (MINIAPP_BOT_USERNAME ليست في support.env)."""

    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *_exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


class _FakeThread:
    """بديل threading.Thread: يُسجَّل الحذف المؤجّل ولا يشغّله."""

    started = []

    def __init__(self, target=None, daemon=None, args=(), kwargs=None):
        self.target = target
        self.daemon = daemon
        self.args = args
        self.kwargs = kwargs or {}
        _FakeThread.started.append(self)

    def start(self):
        return None


class HandlerBot:
    """بوت وهمي يسجّل المعالجات والاستدعاءات صراحةً (بلا شبكة، بلا __getattr__)."""

    def __init__(self, me):
        self.me = me
        self.registrations = []
        self.calls = []

    # التسجيل ---------------------------------------------------------------
    def message_handler(self, *args, **kwargs):
        def decorator(func):
            self.registrations.append((dict(kwargs), func))
            return func
        return decorator

    def callback_query_handler(self, *args, **kwargs):
        def decorator(func):
            self.registrations.append((dict(kwargs, callback=True), func))
            return func
        return decorator

    def inline_handler(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def next_step_backend(self, *args, **kwargs):
        return SimpleNamespace(register_next_step_handler=lambda *a, **k: True)

    def handler_for(self, command):
        for kwargs, func in self.registrations:
            if command in (kwargs.get('commands') or []):
                return func
        raise AssertionError(f'no handler registered for /{command}')

    def welcome_handler(self):
        for kwargs, func in self.registrations:
            if 'new_chat_members' in (kwargs.get('content_types') or []):
                return func
        raise AssertionError('no new_chat_members handler registered')

    # الاستدعاءات -----------------------------------------------------------
    def get_me(self):
        self.calls.append(('get_me', None, {}))
        if isinstance(self.me, Exception):
            raise self.me
        return self.me

    def get_sticker_set(self, *_args, **_kwargs):
        raise AssertionError('no sticker set expected in these flows')

    def _record(self, name, text=None, **kwargs):
        self.calls.append((name, text, kwargs))
        chat = kwargs.get('chat_id', GROUP_CHAT_ID)
        return SimpleNamespace(message_id=len(self.calls) + 10,
                               chat=SimpleNamespace(id=chat, type=GROUP_TYPE))

    def send_message(self, chat_id, text=None, **kwargs):
        return self._record('send_message', text, chat_id=chat_id, **kwargs)

    def reply_to(self, message, text=None, **kwargs):
        return self._record('reply_to', text, chat_id=message.chat.id, **kwargs)

    def delete_message(self, *args, **kwargs):
        return self._record('delete_message', None, **kwargs)

    # استعلامات ------------------------------------------------------------
    def of(self, name):
        return [(text, kwargs) for called, text, kwargs in self.calls if called == name]

    def markup_of(self, name):
        payloads = [kwargs.get('reply_markup') for _text, kwargs in self.of(name)]
        return payloads[-1] if payloads else None


def serialized(markup):
    return [button for row in markup.to_dict()['inline_keyboard'] for button in row]


def bot_user(username=BOT_USERNAME):
    data = {'id': BOT_ID, 'is_bot': True, 'first_name': 'Library'}
    if username is not None:
        data['username'] = username
    return types.User.de_json(data)


def group_update(chat_type=GROUP_TYPE, chat_id=GROUP_CHAT_ID):
    return types.Update.de_json({
        'update_id': 700,
        'message': {
            'message_id': 55, 'date': 1700000000, 'text': '/library',
            'from': {'id': support.MEMBER_ID, 'is_bot': False, 'first_name': 'M'},
            'chat': {'id': chat_id, 'type': chat_type, 'title': 'G'},
        },
    }).message


class LibraryLaunchBase(support.OfflineTestCase):
    """تسجيل معالجات المنتج الحقيقية على بوت صريح، مع تعهد الآثار الخارجية."""

    def setUp(self):
        super().setUp()
        self.bot = HandlerBot(bot_user())
        self.jobs = []
        self.patches = []
        _FakeThread.started = []
        self.patch(group_handlers, threading=SimpleNamespace(Thread=_FakeThread))
        # بوت المنتج الحقيقي (المسجَّل في webhook_bot) يُعرض هنا عبر نفس المفتاح
        if hasattr(user_handlers, '_valid_bot_server'):
            self.patch(user_handlers, _valid_bot_server=lambda: self.bot)
        with ScopedEnv(MINIAPP_BOT_USERNAME=None):
            # ذاكرة get_me خاصة بالتنفيذ: تُفرَّغ إن وُجدت (غيابها ليس خطأ)
            cache = getattr(user_handlers, '_MINUSERS_CACHE', None)
            if isinstance(cache, dict):
                cache.clear()
                self.addCleanup(cache.clear)
        self.install()

    def tearDown(self):
        while self.patches:
            self.patches.pop()()
        super().tearDown()

    def patch(self, owner, **values):
        for name, value in values.items():
            had = name in owner.__dict__
            original = owner.__dict__.get(name)
            owner.__dict__[name] = value
            self.patches.append(
                lambda o=owner, n=name, h=had, old=original: (
                    o.__dict__.__setitem__(n, old) if h else o.__dict__.pop(n, None)))

    def install(self):
        self.patch(handlers_package.admin_broadcast,
                   read_state_row=lambda _uid: None,
                   clear_user_waiting_state=lambda _uid: True,
                   set_user_state=lambda *a, **k: True,
                   _fetch_audience=lambda: ([], None),
                   _reserve_job=lambda admin_id, runner, bot=None: (
                       self.jobs.append((admin_id, runner)) or True))
        self.patch(handlers_package.admin_handlers, read_state_row=lambda _uid: None,
                   clear_user_waiting_state=lambda _uid: True)
        self.patch(state_manager, read_state_row=lambda _uid: None,
                   get_user_waiting_context=lambda _uid: None,
                   clear_user_waiting_state=lambda _uid: True)
        self.patch(db_manager, add_bot_user=lambda *a, **k: True)
        self.patch(user_handlers, check_subscription=lambda bot, user_id: (True, []),
                   add_bot_user=lambda *a, **k: True)
        handlers_package.register_all_handlers(self.bot, CHANNEL_ID, ADMINS)

    # الأحداث ---------------------------------------------------------------
    def welcome(self, chat_type=GROUP_TYPE, new_members=None):
        message = types.Message.de_json({
            'message_id': 54, 'date': 1700000000,
            'from': {'id': support.MEMBER_ID, 'is_bot': False, 'first_name': 'M'},
            'chat': {'id': GROUP_CHAT_ID, 'type': chat_type, 'title': 'G'},
            'new_chat_members': new_members or [
                {'id': BOT_ID, 'is_bot': True, 'first_name': 'Library'}],
        })
        self.bot.welcome_handler()(message)

    def welcome_text(self):
        sends = self.bot.of('send_message')
        self.assertEqual(len(sends), 1, sends)
        return sends[0][0], sends[0][1]

    def assert_no_secrets(self):
        blob = json.dumps(
            [(name, text, {k: str(v) for k, v in kwargs.items()})
             for name, text, kwargs in self.bot.calls], ensure_ascii=False)
        for secret in SECRETS:
            self.assertNotIn(secret, blob)


# ---------------------------------------------------------------------------
# 1) بناء الرابط: التحقق والرفض وعدم تسريب أي شيء
# ---------------------------------------------------------------------------

class GroupLinkBuilderTest(LibraryLaunchBase):
    def link(self):
        """دالة الرابط العامة الوحيدة المطلوبة من الوحدة (غيابها فشل صريح هنا)."""
        builder = getattr(user_handlers, 'library_group_link', None)
        self.assertTrue(callable(builder),
                        'handlers.user_handlers must expose library_group_link()')
        return builder()

    def test_env_username_builds_the_main_mini_app_link(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            self.assertEqual(self.link(), GROUP_LINK)
        # الاسم وحده لا يكفي: الرابط نفسه يُنشر من APP_URL بـ https فقط
        with support.env(APP_URL='https://library2.example.com'), \
                ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            self.assertEqual(self.link(),
                             f'https://t.me/{BOT_USERNAME}?startapp=library')

    def test_insecure_or_missing_app_url_builds_no_link(self):
        for app_url in ('http://insecure.example.com', None, '', 'ftp://x'):
            with self.subTest(app_url=app_url):
                with support.env(APP_URL=app_url), \
                        ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
                    self.assertIsNone(self.link())
                    markup = getattr(user_handlers, 'build_group_library_markup', None)
                    self.assertFalse(callable(markup) and markup())

    def test_get_me_username_is_the_fallback(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=None):
            self.assertEqual(self.link(), GROUP_LINK)

    def test_env_wins_over_get_me(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME='env_username_bot'):
            self.assertEqual(self.link(), 'https://t.me/env_username_bot?startapp=library')

    def test_invalid_usernames_are_rejected(self):
        invalid = ('', 'abc', 'a' * 33, 'bad-name', '../../../etc/passwd',
                   'bot?startapp=evil', 'bot#frag', 'bot%20x', 'bot name',
                   'bot\r\nX-Injected: 1', '<script>', 'اسم_عربي', 'bot\ttab', None)
        for candidate in invalid:
            with self.subTest(username=candidate):
                with support.env(APP_URL=APP_URL), \
                        ScopedEnv(MINIAPP_BOT_USERNAME=candidate):
                    self.bot.me = bot_user(candidate)
                    self.assertIsNone(self.link())
        # أسماء حدّية صالحة تُقبل كما هي (5 و32 حرفاً)
        for candidate in ('abcde', 'a' * 32):
            with self.subTest(valid=candidate):
                with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=candidate):
                    self.assertEqual(self.link(),
                                     f'https://t.me/{candidate}?startapp=library')

    def test_get_me_failure_or_missing_username_yields_none(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=None):
            self.bot.me = bot_user(None)
            self.assertIsNone(self.link())
            self.bot.me = RuntimeError('telegram unreachable')
            self.assertIsNone(self.link())

    def test_username_is_stripped_of_at_sign_and_spaces(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=f' @{BOT_USERNAME} '):
            self.assertEqual(self.link(), GROUP_LINK)

    def test_link_contains_no_secrets_or_query_injection(self):
        for candidate in (f'{BOT_USERNAME}?startapp=x&initData=SECRET_TWA',
                          f'{BOT_USERNAME}&hash=SECRET_TWA',
                          f'@{BOT_USERNAME}/?start=SECRET_TWA'):
            with self.subTest(username=candidate):
                with support.env(APP_URL=APP_URL, MANAGEMENT_TOKEN='SECRET_TWA',
                                 BOT_TOKEN='SECRET_TWA'), \
                        ScopedEnv(MINIAPP_BOT_USERNAME=candidate):
                    self.bot.me = bot_user(candidate)
                    self.assertIsNone(self.link())
        with support.env(APP_URL=APP_URL, MANAGEMENT_TOKEN='SECRET_TWA'), \
                ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            link = self.link()
        self.assertEqual(link.count('?'), 1)
        self.assertEqual(link.split('?')[1], 'startapp=library')
        for secret in SECRETS:
            self.assertNotIn(secret, link)


# ---------------------------------------------------------------------------
# 2) رسالة الترحيب في الجروب عند إضافة البوت
# ---------------------------------------------------------------------------

class GroupWelcomeButtonTest(LibraryLaunchBase):
    def test_group_welcome_has_one_url_button_and_no_web_app(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            self.welcome()
        text, kwargs = self.welcome_text()
        self.assertEqual(kwargs.get('parse_mode'), 'HTML')
        for line in WELCOME_LINES:
            self.assertIn(line, text)
        self.assertEqual(text.count('📱'), 1, text)
        buttons = serialized(kwargs['reply_markup'])
        self.assertEqual(len(buttons), 1, buttons)
        self.assertEqual(buttons[0]['text'], BUTTON_TEXT)
        self.assertEqual(buttons[0]['url'], GROUP_LINK)
        self.assertNotIn('web_app', buttons[0])
        self.assertNotIn('callback_data', buttons[0])
        # خيط الحذف بعد 30 دقيقة يبقى مجدولاً كما هو
        self.assertEqual(len(_FakeThread.started), 1)
        self.assertTrue(_FakeThread.started[0].daemon)
        self.assert_no_secrets()

    def test_supergroup_welcome_matches_group(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            self.welcome(chat_type=SUPERGROUP_TYPE)
        text, kwargs = self.welcome_text()
        for line in WELCOME_LINES:
            self.assertIn(line, text)
        buttons = serialized(kwargs['reply_markup'])
        self.assertEqual([b.get('url') for b in buttons], [GROUP_LINK])
        self.assertNotIn('web_app', buttons[0])

    def test_welcome_combines_with_the_private_web_app_flow(self):
        """زر الجروب رابط عادي حتى حين يتوفر مسار web_app للخاص."""
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            self.assertIsNotNone(user_handlers.build_library_markup())
            self.welcome()
        _text, kwargs = self.welcome_text()
        buttons = serialized(kwargs['reply_markup'])
        self.assertEqual(len(buttons), 1)
        self.assertNotIn('web_app', buttons[0])
        self.assertNotIn('tgWebAppData', buttons[0]['url'])

    def test_no_username_keeps_the_old_welcome_without_a_button(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=None):
            self.bot.me = bot_user(None)
            self.welcome()
        text, kwargs = self.welcome_text()
        for line in WELCOME_LINES:
            self.assertIn(line, text)
        self.assertIsNone(kwargs.get('reply_markup'))
        self.assertEqual(len(_FakeThread.started), 1)

    def test_other_new_members_get_no_welcome(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            self.welcome(new_members=[{'id': support.OTHER_MEMBER_ID, 'is_bot': False,
                                       'first_name': 'O'}])
        self.assertEqual(self.bot.of('send_message'), [])

    def test_welcome_escape_of_username_is_preserved(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=None):
            self.bot.me = bot_user('group_bot<&>')
            self.welcome()
        text, kwargs = self.welcome_text()
        self.assertIn('<code>@group_bot&lt;&amp;&gt; أكشن</code>', text)
        # اسم غير صالح لرابط startapp لا يُصنع له زر مكسور
        self.assertIsNone(kwargs.get('reply_markup'))


# ---------------------------------------------------------------------------
# 3) /library في الجروب والسوبرجروب: تلميح عربي + رابط عادي
# ---------------------------------------------------------------------------

class GroupLibraryCommandTest(LibraryLaunchBase):
    def test_group_library_replies_hint_and_url_button(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            self.bot.handler_for('library')(group_update())
        replies = self.bot.of('reply_to')
        self.assertEqual(len(replies), 1, replies)
        text, kwargs = replies[0]
        self.assertIn(HINT_TEXT, text)
        self.assertEqual(kwargs.get('parse_mode'), 'HTML')
        buttons = serialized(kwargs['reply_markup'])
        self.assertEqual(len(buttons), 1, buttons)
        self.assertEqual(buttons[0]['url'], GROUP_LINK)
        self.assertNotIn('web_app', buttons[0])
        self.assert_no_secrets()

    def test_supergroup_library_uses_the_same_url_button(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            self.bot.handler_for('library')(group_update(chat_type=SUPERGROUP_TYPE))
        text, kwargs = self.bot.of('reply_to')[0]
        self.assertIn(HINT_TEXT, text)
        self.assertEqual([b['url'] for b in serialized(kwargs['reply_markup'])],
                         [GROUP_LINK])
        self.assertNotIn('web_app', serialized(kwargs['reply_markup'])[0])

    def test_group_library_without_username_has_no_broken_button(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=None):
            self.bot.me = bot_user(None)
            self.bot.handler_for('library')(group_update())
        text, kwargs = self.bot.of('reply_to')[0]
        self.assertIn(HINT_TEXT, text)
        self.assertNotIn('https://t.me/', text)
        self.assertIsNone(kwargs.get('reply_markup'))

    def test_private_library_still_builds_the_web_app_markup(self):
        with support.env(APP_URL=APP_URL), ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            private = types.Message.de_json({
                'message_id': 9, 'date': 1700000000, 'text': '/library',
                'from': {'id': support.MEMBER_ID, 'is_bot': False, 'first_name': 'M'},
                'chat': {'id': support.MEMBER_ID, 'type': 'private'},
            })
            self.bot.handler_for('library')(private)
            _, kwargs = self.bot.of('reply_to')[0]
            expected = serialized(user_handlers.build_library_markup())
        buttons = serialized(kwargs['reply_markup'])
        self.assertEqual(buttons, expected)
        self.assertEqual(buttons[0]['text'], BUTTON_TEXT)
        self.assertEqual(buttons[0]['web_app']['url'],
                         APP_URL + PAGE_PATH + '?tgWebAppData=%7B%7D')
        self.assertNotIn('url', {k for k in buttons[0] if k != 'web_app'})


# ---------------------------------------------------------------------------
# 4) الصفحة: تُفتح من رابط مباشر داخل جروب دون أن تنكسر
# ---------------------------------------------------------------------------

MODERN_STUB = r"""
(function () {
  var rec = window.__rec;
  var sdk = {
    initData: "chat_type=group" + String.fromCharCode(38) +
              "chat_instance=TI" + String.fromCharCode(38) +
              "start_param=library" + String.fromCharCode(38) +
              "header=SECRET_TWA",
    initDataUnsafe: { user: { id: 500 }, start_param: "library" },
    colorScheme: "dark",
    ready: function () { rec.ready += 1; },
    expand: function () { rec.expand += 1; },
    close: function () { rec.close += 1; },
    requestFullscreen: function () { rec.isFullscreen = true; },
    exitFullscreen: function () { rec.isFullscreen = false; },
    isFullscreen: false,
    onEvent: function (name, callback) { rec.events.push(name); },
    offEvent: function () {},
    HapticFeedback: {
      impactOccurred: function (style) { rec.haptics.push("impact:" + style); },
      notificationOccurred: function (type) { rec.haptics.push("notification:" + type); },
      selectionChanged: function () {}
    },
    BackButton: { isVisible: false, show: function () {}, hide: function () {},
                  onClick: function () {}, offClick: function () {} }
  };
  window.Telegram = { WebApp: sdk };
})();
"""

STUB_SHIM = """
window.__rec = { ready: 0, expand: 0, close: 0, isFullscreen: false,
                 events: [], haptics: [], log: [] };
"""

SDK_ONLY_STUB = """
window.Telegram = { WebApp: {
  initData: "", initDataUnsafe: {}, colorScheme: "dark",
  ready: function () {}, expand: function () {}, close: function () {}
} };
"""


def _load_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    return sync_playwright


SYNC_PLAYWRIGHT = _load_sync_playwright()


class GroupLaunchPageContract(unittest.TestCase):
    """فحص ثابت (بلا متصفح): الصفحة المقدَّمة لا تُقلّد تفاعل الجروب."""

    @classmethod
    def setUpClass(cls):
        import miniapp
        with ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            cls.rendered = miniapp.render_page()
        cls.js = cls.rendered.split('<script>', 1)[1]

    def test_page_has_no_group_posting_hook(self):
        for token in ('sendMessage', 'sendVideo', 'chat_id', 'chat_instance',
                      'web_app', '/api/group', 'group-only'):
            self.assertNotIn(token, self.js, token)

    def test_page_claims_no_chat_access(self):
        self.assertNotIn('الوصول إلى الرسائل', self.rendered)
        self.assertNotIn('رسائل الجروب', self.rendered)

    def test_open_keeps_its_telegram_only_message(self):
        self.assertIn('افتح المكتبة من داخل تيليجرام حتى يصل الفيديو إلى محادثتك',
                      self.rendered)

    def test_bot_link_is_the_plain_bot_deep_link(self):
        self.assertIn(json.dumps(f'https://t.me/{BOT_USERNAME}'), self.rendered)


@unittest.skipUnless(SYNC_PLAYWRIGHT is not None,
                     'Playwright is not installed (pip install playwright && '
                     'python -m playwright install chromium)')
class GroupLaunchBrowserTest(unittest.TestCase):
    """الصفحة تعمل فعلياً عند فتحها من رابط مباشر داخل جروب (Chromium محلي)."""

    @classmethod
    def setUpClass(cls):
        import miniapp
        cls.path = miniapp.URL_PREFIX
        with ScopedEnv(MINIAPP_BOT_USERNAME=BOT_USERNAME):
            cls.rendered = miniapp.render_page()
        cls._playwright = SYNC_PLAYWRIGHT().start()
        try:
            cls.browser = cls._playwright.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - Chromium may be missing
            cls._playwright.stop()
            raise unittest.SkipTest(f'local Chromium cannot start: {exc}')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()

    def open_page(self, stub, shim, query):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        errors = []
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        posts = []
        gets = []
        document_url = 'https://example.invalid' + self.path

        def respond(route, request):
            if request.url == TELEGRAM_SDK_URL:
                return route.fulfill(status=200, content_type='application/javascript',
                                     body=stub)
            if request.url.split('?', 1)[0] == document_url:
                body = self.rendered.replace(
                    '<head>', '<head>' + '<script>' + shim + '</script>'
                    + '<script>' + stub + '</script>', 1)
                return route.fulfill(status=200, content_type='text/html; charset=utf-8',
                                     body=body)
            target = request.url.split('?', 1)[0]
            if target == document_url + '/api/library':
                gets.append(request.url)
                import urllib.parse
                params = urllib.parse.parse_qs(request.url.split('?', 1)[1])
                page_index = int((params.get('page') or ['0'])[0])
                body = {'items': [{'id': 41, 'title': 'فيلم', 'category': 'أفلام',
                                   'thumbnail_url': '', 'views': 3, 'duration': '1:00'}],
                        'total': 1, 'page': page_index, 'page_size': 10,
                        'has_more': False}
                return route.fulfill(status=200, content_type='application/json',
                                     body=json.dumps(body).replace('</', '<\\/'))
            if target == document_url + '/api/categories':
                return route.fulfill(status=200, content_type='application/json',
                                     body='{"items":[{"id":1,"name":"أفلام","count":1}]}')
            if target == document_url + '/api/favorite':
                posts.append(('favorite', json.loads(request.post_data or '{}')))
                return route.fulfill(status=200, content_type='application/json',
                                     body='{"ok": true}')
            if target == document_url + '/api/open-video':
                posts.append(('open', json.loads(request.post_data or '{}')))
                return route.fulfill(status=200, content_type='application/json',
                                     body='{"ok": true, "video_id": 41}')
            return route.abort()

        page.route('**/*', lambda route: respond(route, route.request))
        page.goto(document_url + query)
        page.wait_for_selector('.card', timeout=10000)
        page.wait_for_timeout(100)
        return page, errors, gets, posts

    def rec(self, page):
        return page.evaluate('() => window.__rec')

    def test_group_direct_link_page_loads_with_library_and_favorites(self):
        page, errors, gets, posts = self.open_page(
            MODERN_STUB, STUB_SHIM, '?tgWebAppData=%7B%7D&startapp=library')
        self.assertEqual(errors, [])
        self.assertEqual(page.locator('.card').count(), 1)
        self.assertTrue(gets, 'library listing must load without chat access')
        self.assertIn('startapp=library', page.url)
        page.locator('.btn.fav').first.click()
        page.wait_for_timeout(200)
        self.assertEqual(page.locator('.btn.fav').first.get_attribute('aria-pressed'), 'true')
        self.assertEqual([kind for kind, _body in posts], ['favorite'])
        favorite = dict(posts[0][1])
        favorite.pop('video_id', None)
        favorite.pop('action', None)
        self.assertEqual(set(favorite), {'initData'},
                         'favorites send only the WebApp initData')
        self.assertIn('chat_type=group', favorite['initData'])
        self.assertEqual(errors, [])

    def test_group_open_keeps_existing_behavior_and_claims_no_chat_access(self):
        page, errors, _gets, posts = self.open_page(
            MODERN_STUB, STUB_SHIM, '?tgWebAppData=%7B%7D&startapp=library')
        page.locator('.btn.open').first.click()
        page.wait_for_function('() => window.__rec.close >= 1', timeout=5000)
        self.assertEqual([kind for kind, _body in posts], ['open'])
        body = posts[0][1]
        self.assertEqual(set(body), {'video_id', 'initData', 'request_id'})
        self.assertIn('chat_type=group', body['initData'])
        for banned in ('chat_id', 'message_id', 'chat_instance', 'send_to_chat'):
            self.assertNotIn(banned, body)
        self.assertEqual(errors, [])

    def test_group_link_without_sdk_keeps_the_telegram_only_message(self):
        page, errors, gets, posts = self.open_page(
            SDK_ONLY_STUB, '', '?tgWebAppData=%7B%7D&startapp=library')
        self.assertEqual(errors, [])
        self.assertTrue(gets)
        page.locator('.btn.open').first.click()
        page.wait_for_timeout(200)
        toast = page.evaluate("() => document.getElementById('toast').textContent")
        self.assertIn('افتح المكتبة من داخل تيليجرام', toast)
        self.assertEqual(posts, [])


if __name__ == '__main__':
    unittest.main()
