"""Phase 8: 429 ``retry_after`` robustness and shared type/documentation contract.

Scope under test:

- ``handlers/telegram_rate_limit.py``: ``retry_after_seconds`` and ``sleep_after_429``
  against the exact pyTelegramBotAPI ``result_json`` shape
  (``{'error_code': 429, 'description': ..., 'parameters': {'retry_after': 17}}``),
  boundary values, typing and docstrings. The helper only delays *following* work and
  never replays the failed call.
- Real integrations that must wait the official ``retry_after`` instead of (or before)
  their fixed 35s / 10s fallbacks: ``handlers/admin_broadcast.run_broadcast``,
  ``scripts/heal_archive``, ``scripts/convert_docs_to_video``,
  ``handlers/maintenance_progress`` (draft and edit), and the bulk admin routes of
  ``webhook_bot.py`` (source contract plus real Flask request paths).

Fully offline: ``support`` blocks telebot HTTP and psycopg2; every bot, DB, clock and
thread is an explicit fake installed by name on the real module. No Telegram,
PostgreSQL, Render or network call is made.
"""

import os

try:  # package mode (`python -m unittest discover` with tests/__init__.py)
    from . import support
except ImportError:  # top-level mode (`python -m unittest discover -s tests`)
    import support

import ast
import importlib
import re
import sys as _sys
import time
import typing
import unittest
from types import SimpleNamespace

from telebot.apihelper import ApiTelegramException

import config
from handlers import admin_broadcast
from handlers import maintenance_progress
from handlers import telegram_rate_limit as rate_limit
from scripts import convert_docs_to_video
from scripts import heal_archive

# الأصل الحقيقي للمساعد: الاختبارات تثبّته قبل كل طلب كي لا يعتمد هذا الملف على
# ترتيب الحزمة (اختبارات أخرى تستعمل mock.patch على الوحدة الحقيقية).
_RATE_LIMIT_HELPER = rate_limit.sleep_after_429
_RATE_LIMIT_SLEEP = rate_limit.__dict__.get('sleep')


CHAT_ID = support.ADMIN_ID
MESSAGE_ID = 4242
ADMINS = [support.ADMIN_ID]
CHANNEL = -1001234567890

HELPER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'handlers', 'telegram_rate_limit.py')

# الأصل الحقيقي للمساعد (لإعادة الاسم إن حقنه اختبار آخر في العملية نفسها)
rate_limit_sleep_original = rate_limit.sleep_after_429


# ---------------------------------------------------------------------------
# أدوات مشتركة
# ---------------------------------------------------------------------------

def api_error(description, code=429, retry_after=None, include_parameters=True):
    """ApiTelegramException بشكل Telegram الحقيقي (retry_after داخل parameters)."""
    payload = {'ok': False, 'error_code': code, 'description': description}
    if include_parameters:
        payload['parameters'] = {} if retry_after is None else {'retry_after': retry_after}
    return ApiTelegramException('call', payload, payload)


def flood_error(retry_after=35):
    """خطأ 429 يحمل retry_after رسمياً، ووصفه يحوي كلمة 'flood' القديمة."""
    return api_error('Too Many Requests: retry after %s' % retry_after, 429, retry_after)


def _read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _source(module):
    return _read(module.__file__)


def _function_source(module, name, owner=None):
    """نص دالة واحدة عبر ast: الأولى بـ ``name`` أو المتداخلة داخل ``owner``."""
    text = _source(module)
    tree = ast.parse(text)
    candidates = [node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == name]
    if owner is not None:
        owners = [n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == owner]
        if owners:
            start, end = owners[0].lineno, owners[0].end_lineno or owners[0].lineno
            inner = [c for c in candidates if start <= c.lineno <= end]
            candidates = inner or candidates
    if not candidates:
        raise AssertionError('function %r not found in %s' % (name, module.__file__))
    return ast.get_source_segment(text, candidates[0])


def _install(module, **patches):
    """يبدّل أسماء داخل الوحدة الحقيقية ويعيدها في التنظيف (بلا mock.patch)."""
    originals = {}
    missing = []
    for name, value in patches.items():
        if name in vars(module):
            originals[name] = vars(module)[name]
        else:
            missing.append(name)
        setattr(module, name, value)
    return originals, missing


def _put_back(module, originals, missing=()):
    for name, value in originals.items():
        setattr(module, name, value)
    for name in missing:
        module.__dict__.pop(name, None)


class FakeClock:
    def __init__(self, step=0.0, start=1000.0):
        self.now = start
        self.step = step
        self.sleeps = []

    def time(self):
        self.now += self.step
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


# ---------------------------------------------------------------------------
# 1) المساعد المشترك: الشكل، القيم الحدية، الأنواع والتوثيق
# ---------------------------------------------------------------------------

class HelperContractTest(support.OfflineTestCase):
    def test_returns_official_integer_value_as_float(self):
        error = api_error('Too Many Requests: retry after 17', 429, 17)
        value = rate_limit.retry_after_seconds(error)
        self.assertIsInstance(value, float)
        self.assertEqual(value, 17.0)
        self.assertEqual(error.error_code, 429)
        self.assertEqual(error.result_json['parameters']['retry_after'], 17)

    def test_returns_official_float_value_unchanged(self):
        self.assertEqual(
            rate_limit.retry_after_seconds(api_error('flood', 429, 2.5)), 2.5)

    def test_accepts_numeric_string(self):
        self.assertEqual(
            rate_limit.retry_after_seconds(api_error('flood', 429, '17')), 17.0)
        self.assertEqual(
            rate_limit.retry_after_seconds(api_error('flood', 429, ' 1.75 ')), 1.75)
        self.assertEqual(
            rate_limit.retry_after_seconds(api_error('flood', 429, '1e2')), 100.0)

    def test_preserves_large_values_without_capping(self):
        for official in (31, 60, 3600, 86400, 10 ** 9):
            with self.subTest(official=official):
                self.assertEqual(
                    rate_limit.retry_after_seconds(api_error('flood', 429, official)),
                    float(official))

    def test_rejects_bool_values(self):
        for official in (True, False, 'True', 'false'):
            with self.subTest(official=official):
                self.assertIsNone(
                    rate_limit.retry_after_seconds(api_error('flood', 429, official)))

    def test_rejects_zero_negative_nan_and_infinity(self):
        for official in (0, 0.0, -1, -0.5, '-3', float('nan'), 'nan',
                         float('inf'), '-inf', 'inf', 'Infinity'):
            with self.subTest(official=official):
                self.assertIsNone(
                    rate_limit.retry_after_seconds(api_error('flood', 429, official)))

    def test_rejects_malformed_values(self):
        for official in ('', '   ', 'abc', '17s', '1e999', None):
            with self.subTest(official=official):
                self.assertIsNone(
                    rate_limit.retry_after_seconds(api_error('flood', 429, official)))
        for official in ([], {}, [17], {'value': 17}, object()):
            with self.subTest(official=type(official).__name__):
                self.assertIsNone(
                    rate_limit.retry_after_seconds(api_error('flood', 429, official)))

    def test_rejects_missing_or_broken_parameters(self):
        self.assertIsNone(rate_limit.retry_after_seconds(
            api_error('Too Many Requests', 429, include_parameters=False)))
        self.assertIsNone(rate_limit.retry_after_seconds(
            api_error('Too Many Requests', 429, None)))
        payload = {'ok': False, 'error_code': 429, 'description': 'flood',
                   'parameters': 'retry_after=17'}
        self.assertIsNone(rate_limit.retry_after_seconds(
            ApiTelegramException('call', payload, payload)))
        self.assertIsNone(rate_limit.retry_after_seconds(
            ApiTelegramException('call', payload, {
                'ok': False, 'error_code': 429, 'description': 'flood',
                'parameters': {'migrate_to_chat_id': 1}})))
        self.assertIsNone(rate_limit.retry_after_seconds(
            SimpleNamespace(error_code=429)))
        self.assertIsNone(rate_limit.retry_after_seconds(
            SimpleNamespace(error_code=429, result_json=['error_code'])))

    def test_requires_actual_error_code_429(self):
        for code in (400, 403, 500, None, '429', True, False):
            with self.subTest(code=code):
                error = api_error('Too Many Requests: retry after 17', code, 17)
                self.assertIsNone(rate_limit.retry_after_seconds(error))
        self.assertIsNone(rate_limit.retry_after_seconds(
            api_error('Too Many Requests: flood retry after 17', 400, 17)))

    def test_non_exception_inputs_are_ignored(self):
        for value in (None, 'error', 429, {'error_code': 429},
                      SimpleNamespace(error_code=429, result_json={
                          'parameters': {'retry_after': 17}})):
            with self.subTest(value=value):
                self.assertIsNone(rate_limit.retry_after_seconds(value))

    def test_only_result_json_is_the_source_of_truth(self):
        """لا خاصية exc.parameters في pyTelegramBotAPI: نقرأ result_json فقط."""
        source = _read(HELPER_PATH)
        self.assertIn("getattr(error, 'result_json', None)", source)
        self.assertIn("payload.get('parameters')", source)
        self.assertIn("parameters.get('retry_after')", source)
        value = rate_limit.retry_after_seconds(
            api_error('Too Many Requests: retry after 17', 429, 17))
        self.assertEqual(value, 17.0)
        # description نص بشري لا يُستخرج منه رقم (تعليمات النص لا الكود)
        code = source.replace('"""', '\x00').split('\x00')[2::2]
        self.assertNotIn('description', ''.join(code))

    def test_sleep_after_429_calls_sleeper_exactly_once(self):
        calls = []
        self.assertIs(rate_limit.sleep_after_429(
            api_error('flood', 429, 17), calls.append), True)
        self.assertEqual(calls, [17.0])
        self.assertEqual(len(calls), 1)

    def test_sleep_after_429_does_not_sleep_without_official_value(self):
        for error in (None, SimpleNamespace(error_code=400), object(),
                      api_error('Too Many Requests: flood retry after 35', 400),
                      api_error('flood', 429, 0), api_error('flood', 429, None),
                      api_error('flood', 429, True),
                      api_error('Too Many Requests', 429, include_parameters=False)):
            with self.subTest(error=repr(error)):
                calls = []
                self.assertIs(rate_limit.sleep_after_429(error, calls.append), False)
                self.assertEqual(calls, [])

    def test_sleep_after_429_does_not_swallow_sleeper_errors(self):
        def boom(_seconds):
            raise RuntimeError('stop signal')

        with self.assertRaises(RuntimeError):
            rate_limit.sleep_after_429(api_error('flood', 429, 3), boom)

    def test_typing_contract(self):
        self.assertEqual(dict(typing.get_type_hints(rate_limit.retry_after_seconds)), {
            'error': BaseException, 'return': typing.Optional[float]})
        hints = typing.get_type_hints(rate_limit.sleep_after_429)
        self.assertEqual(hints['error'], BaseException)
        self.assertEqual(hints['sleeper'], typing.Callable[[float], object])
        self.assertEqual(hints['return'], bool)
        self.assertIn('from typing import Callable, Optional', _read(HELPER_PATH))
        for name in ('retry_after_seconds', 'sleep_after_429'):
            self.assertTrue(getattr(rate_limit, name).__doc__.strip(), name)
        self.assertTrue((rate_limit.__doc__ or '').strip())

    def test_docstrings_document_result_json_shape(self):
        module_doc = (rate_limit.__doc__ or '').replace("'", '"')
        retry_doc = (rate_limit.retry_after_seconds.__doc__ or '').replace("'", '"')
        for doc, label in ((module_doc, 'module'), (retry_doc, 'retry_after_seconds')):
            with self.subTest(doc=label):
                self.assertIn('result_json', doc)
                self.assertIn('error_code', doc)
                self.assertIn('429', doc)
                self.assertIn('parameters', doc)
                self.assertIn('retry_after', doc)
                self.assertIn('{"retry_after": 17}', doc)
        self.assertIn('None', retry_doc)

    def test_sleep_docstring_documents_delay_without_replay(self):
        sleep_doc = rate_limit.sleep_after_429.__doc__ or ''
        self.assertIn('sleeper', sleep_doc)
        self.assertIn('True', sleep_doc)
        self.assertIn('False', sleep_doc)
        self.assertIn('مرة واحدة', sleep_doc)          # استدعاء واحد بالضبط
        self.assertIn('لا يعيد تشغيل', sleep_doc)      # مهلة لا إعادة محاولة
        self.assertIn('لا يُسجَّل', sleep_doc)         # لا تسجيل حمولة/أسرار

    def test_helper_never_logs_payload_or_secrets(self):
        source = _read(HELPER_PATH)
        self.assertNotIn('print(', source)
        for level in ('logger.debug', 'logger.info', 'logger.warning', 'logger.error'):
            self.assertNotIn(level, source)
        calls = []
        hidden = api_error('Too Many Requests: token 123456:SECRET retry after 9',
                           429, 9)
        with self.assertNoLogs(level='DEBUG'):
            self.assertIs(rate_limit.sleep_after_429(hidden, calls.append), True)
        self.assertEqual(calls, [9.0])


# ---------------------------------------------------------------------------
# 2) البث: 429 لمستلم واحد + نجاح لآخر => نوم رسمي وأرقام/تنظيف كما هي
# ---------------------------------------------------------------------------

class BroadcastRateLimitBot:
    """بوت بث مستقل يسجّل كل استدعاء ويرمي الأخطاء المجدولة مرة واحدة لكل مستلم."""

    def __init__(self, errors=None):
        self.calls = []
        self.errors = dict(errors or {})
        self.next_message_id = 5000

    def send_message(self, chat_id, text, **kwargs):
        self.calls.append(('send_message', (chat_id, text), kwargs))
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id,
                               chat=SimpleNamespace(id=chat_id, type='private'))

    def copy_message(self, chat_id, from_chat_id=None, message_id=None, **kwargs):
        self.calls.append(('copy_message', (chat_id, from_chat_id, message_id), kwargs))
        error = self.errors.pop(chat_id, None)
        if error is not None:
            raise error
        return SimpleNamespace(message_id=self.next_message_id)

    def delete_message(self, *args, **kwargs):
        self.calls.append(('delete_message', args, kwargs))
        return True

    def copies_to(self):
        return [c[1][0] for c in self.calls if c[0] == 'copy_message']

    def report(self):
        texts = [c[1][1] for c in self.calls
                 if c[0] == 'send_message' and len(c[1]) > 1 and c[1][1]]
        matches = [t for t in texts if 'نجح:' in t]
        return matches[-1] if matches else None


class BroadcastRateLimitTest(support.OfflineTestCase):
    def run_broadcast(self, bot, user_ids):
        self.sleeps = []
        return admin_broadcast.run_broadcast(
            bot, support.ADMIN_ID, 900, 91, user_ids, sleeper=self.sleeps.append)

    def patch_delete(self, module):
        deleted = []
        originals, missing = _install(
            module, delete_bot_user=lambda user_id: deleted.append(user_id) or True,
            db_manager=SimpleNamespace(delete_bot_user=lambda user_id: deleted.append(
                user_id) or True))
        self.addCleanup(_put_back, module, originals, missing)
        return deleted

    def test_429_sleeps_official_retry_after_and_keeps_counts(self):
        bot = BroadcastRateLimitBot({8: flood_error(17)})
        report = self.run_broadcast(bot, [7, 8, 9])
        self.assertEqual(report, {'sent': 2, 'failed': 1, 'removed': 0})
        self.assertEqual(self.sleeps, [17.0])
        self.assertEqual(bot.copies_to(), [7, 8, 9])       # لا إعادة إرسال للفاشل
        self.assertIn('نجح: 2', bot.report())
        self.assertIn('فشل: 1', bot.report())
        self.assertIn('معطّل): 0', bot.report())

    def test_429_without_official_value_does_not_sleep(self):
        bot = BroadcastRateLimitBot({8: api_error(
            'Too Many Requests: retry after 17', 429, include_parameters=False)})
        report = self.run_broadcast(bot, [7, 8])
        self.assertEqual(report, {'sent': 1, 'failed': 1, 'removed': 0})
        self.assertEqual(self.sleeps, [])

    def test_blocked_user_cleanup_and_retry_after_both_preserved(self):
        deleted = self.patch_delete(admin_broadcast)
        blocked = api_error('Forbidden: bot was blocked by the user', 403)
        bot = BroadcastRateLimitBot({8: blocked, 9: flood_error(23)})
        report = self.run_broadcast(bot, [7, 8, 9])
        self.assertEqual(report, {'sent': 1, 'failed': 1, 'removed': 1})
        self.assertEqual(deleted, [8])                     # لا حذف لمستلم 429
        self.assertEqual(self.sleeps, [23.0])
        self.assertEqual(bot.copies_to(), [7, 8, 9])
        self.assertIn('معطّل): 1', bot.report())
        self.assertIn('فشل: 1', bot.report())

    def test_batch_interval_still_applies_with_retry_after(self):
        bot = BroadcastRateLimitBot({2: flood_error(5)})
        report = self.run_broadcast(bot, list(range(1, 42)))   # 41 مستلماً
        self.assertEqual(report, {'sent': 40, 'failed': 1, 'removed': 0})
        # نوم الدفعتين المكتملتين فقط (لا نوم بعد الدفعة الأخيرة) + 5
        self.assertEqual(self.sleeps, [5.0, 1.0, 1.0])

    def test_run_broadcast_uses_shared_helper_with_injected_sleeper(self):
        self.assertIn('sleep_after_429', _source(admin_broadcast))
        body = _function_source(admin_broadcast, 'run_broadcast').replace('\n', ' ')
        self.assertIn('sleep_after_429(exc, sleep)', body)


# ---------------------------------------------------------------------------
# 3) heal_archive: 429 رسمي بدل الثابت 35 مع بقاء النوم العادي والنتائج
# ---------------------------------------------------------------------------

class HealRateLimitTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(step=0.5)
        self.videos = [
            {'id': 11, 'message_id': 501, 'chat_id': CHANNEL},
            {'id': 12, 'message_id': 502, 'chat_id': CHANNEL},
        ]
        self.updates = []
        self.forward_errors = []

        def execute_query(sql, params=None, fetch=None, commit=False):
            self.updates.append((' '.join(str(sql).split()), params, commit))
            if fetch == 'all':
                return list(self.videos)
            return True

        self.addCleanup(_put_back, heal_archive, *_install(
            heal_archive,
            execute_query=execute_query,
            update_video_thumbnail=lambda *a, **k: True,
            time=self.clock,
        ))
        original_admins = config.ADMIN_IDS
        config.ADMIN_IDS = list(ADMINS)
        self.addCleanup(setattr, config, 'ADMIN_IDS', original_admins)

    def bot(self):
        outer = self

        class HealBot:
            def __init__(self):
                self.calls = []

            def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
                self.calls.append(('edit_message_text', text))
                return True

            def send_message(self, chat_id, text, **kwargs):
                self.calls.append(('send_message', text))
                return True

            def forward_message(self, chat_id, from_chat_id, message_id, **kwargs):
                self.calls.append(('forward_message', from_chat_id, message_id))
                if outer.forward_errors:
                    error = outer.forward_errors.pop(0)
                    if error is not None:
                        raise error
                return SimpleNamespace(
                    message_id=9900, document=None,
                    video=SimpleNamespace(file_id='v', thumb=None))

            def delete_message(self, *args, **kwargs):
                return True

            def send_message_draft(self, chat_id, draft_id, text, **kwargs):
                self.calls.append(('send_message_draft', text))
                return True

            def sent_texts(self):
                return [c[1] for c in self.calls if c[0] == 'send_message']

            def forwards(self):
                return [c for c in self.calls if c[0] == 'forward_message']

        return HealBot()

    def test_official_retry_after_replaces_fixed_35(self):
        bot = self.bot()
        self.forward_errors = [flood_error(35), None]
        heal_archive.run_heal_archive(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(self.clock.sleeps, [35.0, 1.0, 1.0])
        self.assertIn('• نجاح: 1', bot.sent_texts()[-1])
        self.assertIn('• فشل: 1', bot.sent_texts()[-1])
        self.assertEqual(len(bot.forwards()), 2)            # لا إعادة محاولة للفاشل
        self.assertEqual(len([u for u in self.updates if u[2]]), 1)

    def test_official_value_other_than_35_is_preserved(self):
        bot = self.bot()
        self.forward_errors = [flood_error(7), None]
        heal_archive.run_heal_archive(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(self.clock.sleeps, [7.0, 1.0, 1.0])
        self.assertNotIn(35, self.clock.sleeps)
        self.assertIn('• فشل: 1', bot.sent_texts()[-1])

    def test_non_429_api_error_keeps_normal_sleep_and_failed_count(self):
        bot = self.bot()
        self.forward_errors = [api_error('Bad Request: chat not found', 400, 9), None]
        heal_archive.run_heal_archive(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(self.clock.sleeps, [1.0, 1.0])
        self.assertIn('• فشل: 1', bot.sent_texts()[-1])

    def test_legacy_flood_text_without_official_value_keeps_35_fallback(self):
        bot = self.bot()
        self.forward_errors = [
            api_error('Too Many Requests: flood control exceeded', 400), None]
        heal_archive.run_heal_archive(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(self.clock.sleeps, [35, 1.0, 1.0])
        self.assertIn('• فشل: 1', bot.sent_texts()[-1])

    def test_429_without_official_value_adds_no_extra_sleep(self):
        bot = self.bot()
        self.forward_errors = [
            api_error('Too Many Requests', 429, include_parameters=False), None]
        heal_archive.run_heal_archive(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(self.clock.sleeps, [1.0, 1.0])

    def test_heal_source_calls_helper_before_legacy_fallback(self):
        body = _function_source(heal_archive, 'run_heal_archive')
        self.assertIn('sleep_after_429', body)
        self.assertRegex(body, r'if not sleep_after_429\(e, time\.sleep\) and "flood"')
        self.assertIn('time.sleep(1.0)', body)


# ---------------------------------------------------------------------------
# 4) convert_docs_to_video: 429 رسمي بدل الثابت 35 مع بقاء النتائج
# ---------------------------------------------------------------------------

class ConvertRateLimitTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(step=0.0)
        self.docs = [
            {'id': 21, 'message_id': 601, 'chat_id': CHANNEL, 'file_id': 'f1',
             'caption': 'cap1', 'file_name': 'a.mp4', 'content_type': 'DOCUMENT',
             'thumbnail_file_id': None},
            {'id': 22, 'message_id': 602, 'chat_id': CHANNEL, 'file_id': 'f2',
             'caption': 'cap2', 'file_name': 'b.mp4', 'content_type': 'DOCUMENT',
             'thumbnail_file_id': None},
        ]
        self.converted = []
        self.send_errors = []

        def execute_query(sql, params=None, fetch=None, commit=False):
            if fetch == 'all':
                return list(self.docs)
            if commit:
                self.converted.append(params)
            return True

        self.addCleanup(_put_back, convert_docs_to_video, *_install(
            convert_docs_to_video, execute_query=execute_query, time=self.clock))
        original_admins, original_channel = config.ADMIN_IDS, config.CHANNEL_ID
        config.ADMIN_IDS = list(ADMINS)
        config.CHANNEL_ID = CHANNEL
        self.addCleanup(setattr, config, 'ADMIN_IDS', original_admins)
        self.addCleanup(setattr, config, 'CHANNEL_ID', original_channel)

    def bot(self):
        outer = self

        class ConvertBot:
            def __init__(self):
                self.calls = []
                self.sent = []
                self.video_calls = 0

            def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
                self.calls.append(('edit_message_text', text))
                return True

            def send_message(self, chat_id, text, **kwargs):
                self.sent.append(text)
                return True

            def get_file(self, file_id, **kwargs):
                return SimpleNamespace(file_path='path/' + str(file_id), file_size=1024)

            def download_file(self, file_path, **kwargs):
                return b'video-bytes'

            def send_video(self, chat_id, video, **kwargs):
                self.video_calls += 1
                if outer.send_errors:
                    error = outer.send_errors.pop(0)
                    if error is not None:
                        raise error
                return SimpleNamespace(
                    message_id=7000 + self.video_calls,
                    video=SimpleNamespace(file_id='new-file-id',
                                          thumb=SimpleNamespace(file_id='new-thumb')))

            def delete_message(self, *args, **kwargs):
                return True

            def send_message_draft(self, chat_id, draft_id, text, **kwargs):
                self.calls.append(('send_message_draft', text))
                return True

        return ConvertBot()

    def test_official_retry_after_replaces_fixed_35_and_results_unchanged(self):
        bot = self.bot()
        self.send_errors = [flood_error(35), None]
        convert_docs_to_video.run_convert_all_docs(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(self.clock.sleeps, [35.0, 2.0, 2.0])
        self.assertIn('• ❌ فشل: 1', bot.sent[-1])
        self.assertIn('• ✅ تم تحويله لفيديو: 1', bot.sent[-1])
        self.assertEqual(len(self.converted), 1)
        self.assertEqual(bot.video_calls, 2)              # لا إعادة محاولة للفاشل

    def test_single_video_result_error_and_exact_official_value(self):
        self.send_errors = [flood_error(61)]
        result = convert_docs_to_video.convert_single_video(
            self.bot(), self.docs[0], support.ADMIN_ID, CHANNEL)
        self.assertEqual(result, 'error')
        self.assertEqual(self.clock.sleeps, [61.0])

    def test_non_429_keeps_no_extra_sleep_and_error_result(self):
        self.send_errors = [api_error('Bad Request: wrong file identifier', 400, 12)]
        result = convert_docs_to_video.convert_single_video(
            self.bot(), self.docs[0], support.ADMIN_ID, CHANNEL)
        self.assertEqual(result, 'error')
        self.assertEqual(self.clock.sleeps, [])

    def test_legacy_flood_text_without_official_value_keeps_35_fallback(self):
        self.send_errors = [api_error('Too Many Requests: flood control', 400)]
        result = convert_docs_to_video.convert_single_video(
            self.bot(), self.docs[0], support.ADMIN_ID, CHANNEL)
        self.assertEqual(result, 'error')
        self.assertEqual(self.clock.sleeps, [35])

    def test_429_without_official_value_adds_no_sleep(self):
        self.send_errors = [api_error('Too Many Requests: retry after 17', 429)]
        result = convert_docs_to_video.convert_single_video(
            self.bot(), self.docs[0], support.ADMIN_ID, CHANNEL)
        self.assertEqual(result, 'error')
        self.assertEqual(self.clock.sleeps, [])

    def test_too_large_outcome_unchanged(self):
        class HugeBot:
            def get_file(self, file_id, **kwargs):
                return SimpleNamespace(file_path='p', file_size=10 ** 9)

        result = convert_docs_to_video.convert_single_video(
            HugeBot(), self.docs[0], support.ADMIN_ID, CHANNEL)
        self.assertEqual(result, 'too_large')
        self.assertEqual(self.clock.sleeps, [])

    def test_convert_source_calls_helper_before_legacy_fallback(self):
        body = _function_source(convert_docs_to_video, 'convert_single_video')
        self.assertIn('slept = sleep_after_429(e, time.sleep)', body)
        self.assertRegex(body, r'if not slept and "flood" in error_str:')


# ---------------------------------------------------------------------------
# 5) MaintenanceProgress: 429 في المسودة/التعديل ينام قبل تطبيق الدلالة الحالية
# ---------------------------------------------------------------------------

class ProgressRateLimitBot:
    def __init__(self, draft_error=None, edit_errors=None):
        self.calls = []
        self.draft_error = draft_error
        self.edit_errors = list(edit_errors or [])

    def send_message_draft(self, chat_id, draft_id, text, **kwargs):
        self.calls.append(('send_message_draft', text))
        if self.draft_error is not None:
            raise self.draft_error
        return True

    def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        self.calls.append(('edit_message_text', text))
        if self.edit_errors:
            error = self.edit_errors.pop(0)
            if error is not None:
                raise error
        return True

    def names(self):
        return [c[0] for c in self.calls]


class ProgressRateLimitTest(support.OfflineTestCase):
    def install_sleep(self):
        sleeps = []
        self.addCleanup(_put_back, maintenance_progress,
                        *_install(maintenance_progress, sleep=sleeps.append))
        return sleeps

    def test_draft_429_sleeps_then_edit_fallback(self):
        sleeps = self.install_sleep()
        bot = ProgressRateLimitBot(draft_error=flood_error(13))
        progress = maintenance_progress.create(bot, CHAT_ID, MESSAGE_ID)
        with self.assertLogs('handlers.maintenance_progress', level='INFO'):
            self.assertIs(progress.update('تقدم ١'), True)
        self.assertEqual(sleeps, [13.0])
        self.assertEqual(bot.names(), ['send_message_draft', 'edit_message_text'])
        self.assertEqual(progress.draft_calls, 0)
        self.assertEqual(progress.edit_calls, 1)
        self.assertFalse(progress.drafts_enabled)
        self.assertIs(progress.last_delivered, True)

    def test_draft_429_is_not_a_draft_retry(self):
        sleeps = self.install_sleep()
        bot = ProgressRateLimitBot(draft_error=flood_error(4))
        progress = maintenance_progress.create(bot, CHAT_ID, MESSAGE_ID)
        with self.assertLogs('handlers.maintenance_progress', level='INFO'):
            progress.update('one')
            progress.update('two')
        self.assertEqual(bot.names(), ['send_message_draft', 'edit_message_text',
                                       'edit_message_text'])
        self.assertEqual(sleeps, [4.0])     # نوم عند فشل المسودة فقط

    def test_edit_fallback_429_sleeps_before_returning_false(self):
        sleeps = self.install_sleep()
        bot = ProgressRateLimitBot(draft_error=RuntimeError('draft exploded'),
                                   edit_errors=[flood_error(21)])
        progress = maintenance_progress.create(bot, CHAT_ID, MESSAGE_ID)
        with self.assertLogs('handlers.maintenance_progress', level='INFO'):
            self.assertIs(progress.update('x'), False)
        self.assertEqual(sleeps, [21.0])
        self.assertEqual(bot.names(), ['send_message_draft', 'edit_message_text'])
        self.assertEqual(progress.edit_calls, 0)

    def test_message_not_modified_stays_benign_without_sleep(self):
        sleeps = self.install_sleep()
        bot = ProgressRateLimitBot(
            draft_error=RuntimeError('draft down'),
            edit_errors=[api_error('Bad Request: message is not modified', 400)])
        progress = maintenance_progress.create(bot, CHAT_ID, MESSAGE_ID)
        with self.assertLogs('handlers.maintenance_progress', level='INFO'):
            self.assertIs(progress.update('same'), True)
        self.assertEqual(sleeps, [])
        self.assertEqual(progress.edit_calls, 1)

    def test_429_without_official_value_does_not_sleep(self):
        sleeps = self.install_sleep()
        bot = ProgressRateLimitBot(draft_error=api_error(
            'Too Many Requests', 429, include_parameters=False))
        progress = maintenance_progress.create(bot, CHAT_ID, MESSAGE_ID)
        with self.assertLogs('handlers.maintenance_progress', level='INFO'):
            self.assertIs(progress.update('y'), True)
        self.assertEqual(sleeps, [])
        self.assertEqual(progress.edit_calls, 1)

    def test_429_logs_type_only_without_payload(self):
        sleeps = self.install_sleep()
        bot = ProgressRateLimitBot(draft_error=flood_error(17),
                                   edit_errors=[flood_error(19)])
        progress = maintenance_progress.create(bot, CHAT_ID, MESSAGE_ID)
        with self.assertLogs('handlers.maintenance_progress', level='INFO') as captured:
            progress.update('secret progress text')
        joined = ' '.join(captured.output)
        self.assertIn('ApiTelegramException', joined)
        self.assertNotIn('secret progress text', joined)
        self.assertNotIn('Too Many Requests', joined)
        self.assertEqual(sleeps, [17.0, 19.0])

    def test_progress_source_uses_shared_helper_in_both_paths(self):
        source = _source(maintenance_progress)
        self.assertIn('from .telegram_rate_limit import sleep_after_429', source)
        self.assertIn('ApiTelegramException', source)
        for name in ('_send_draft', '_fallback'):
            with self.subTest(path=name):
                body = _function_source(maintenance_progress, name)
                self.assertIn('ApiTelegramException', body)
                self.assertIn('sleep_after_429', body)
        self.assertNotIn('time.sleep(', source)


# ---------------------------------------------------------------------------
# 6) webhook_bot: مسارات الأدمن الجماعية (مصدر + مسارات Flask حقيقية)
# ---------------------------------------------------------------------------

BOT_TOKEN = "123456:SECRET-BOT-TOKEN"
APP_URL = "https://example.onrender.com"
ADMIN_TOKEN = "management-token-value"
BASE_ENV = {
    "BOT_TOKEN": BOT_TOKEN,
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
    "CHANNEL_ID": "-1001234567890",
    "ADMIN_IDS": str(support.ADMIN_ID),
    "APP_URL": APP_URL,
}
WEBHOOK_ENV_KEYS = ("WEBHOOK_MANAGEMENT_TOKEN", "WEBHOOK_SECRET",
                    "WEBHOOK_DROP_PENDING")

BULK_ROUTES = (
    'admin_update_thumbnails',
    'admin_extract_channel_thumbnails',
    'admin_fix_videos_professional',
    'admin_diagnose_file_ids',
    'admin_fix_content_types',
    'admin_force_refresh_all_file_ids',
)
GENERIC_PATH_ROUTES = (
    'admin_update_thumbnails',
    'admin_extract_channel_thumbnails',
    'admin_fix_videos_professional',
    'admin_diagnose_file_ids',
)


def fresh_module():
    saved = {key: os.environ.get(key) for key in WEBHOOK_ENV_KEYS}
    for key in WEBHOOK_ENV_KEYS:
        os.environ.pop(key, None)
    _sys.modules.pop("webhook_bot", None)
    try:
        with support.env(**BASE_ENV, WEBHOOK_MANAGEMENT_TOKEN=ADMIN_TOKEN):
            return importlib.import_module("webhook_bot")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class WaveWebhookBot:
    """بوت مسارات الأدمن: يرمي الأخطاء المجدولة بالترتيب ثم يعيد نتيجة ناجحة."""

    def __init__(self, errors=None):
        self.calls = []
        self.errors = list(errors or [])
        self.next_message_id = 4000

    def _maybe_fail(self):
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error

    def send_video(self, chat_id, video, **kwargs):
        self.calls.append(('send_video', chat_id))
        self._maybe_fail()
        return SimpleNamespace(message_id=self.next_message_id,
                               video=SimpleNamespace(file_id='fresh-file',
                                                     thumb=SimpleNamespace(file_id='t')))

    def forward_message(self, chat_id=None, from_chat_id=None, message_id=None, **kwargs):
        self.calls.append(('forward_message', from_chat_id, message_id))
        self._maybe_fail()
        return SimpleNamespace(message_id=self.next_message_id, document=None,
                               video=SimpleNamespace(file_id='fresh-file', thumb=None))

    def copy_message(self, chat_id, from_chat_id=None, message_id=None, **kwargs):
        self.calls.append(('copy_message', from_chat_id, message_id))
        self._maybe_fail()
        return SimpleNamespace(message_id=self.next_message_id)

    def send_message(self, chat_id, text, **kwargs):
        self.calls.append(('send_message', text))
        return True

    def delete_message(self, *args, **kwargs):
        return True

    def texts(self):
        return [c[1] for c in self.calls if c[0] == 'send_message']

    def names(self):
        return [c[0] for c in self.calls]


class WebhookSourceTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.module = fresh_module()
        self.addCleanup(_sys.modules.pop, 'webhook_bot', None)
        self.source = _source(self.module)

    def route_segment(self, name):
        marker = 'def %s(' % name
        if marker not in self.source:
            return None                      # لا يوجد مسار/دالة بهذا الاسم
        start = self.source.index(marker)
        end = self.source.find('\n@app.route', start)
        return self.source[start:end if end != -1 else len(self.source)]

    def test_helper_imported_once_at_module_level(self):
        self.assertIs(self.module.rate_limit, rate_limit)
        self.assertEqual(
            self.source.count('from handlers import telegram_rate_limit as rate_limit'), 1)
        tree = ast.parse(self.source)
        nested = [n for n in ast.walk(tree)
                  if isinstance(n, ast.ImportFrom)
                  and n.module == 'handlers.telegram_rate_limit' and n not in tree.body]
        self.assertEqual(nested, [])
        self.assertEqual(self.source.count('telegram_rate_limit'), 1)

    def test_time_imported_once_at_module_level(self):
        tree = ast.parse(self.source)
        module_level = [n for n in tree.body if isinstance(n, ast.Import)
                        and any(a.name == 'time' for a in n.names)]
        self.assertEqual(len(module_level), 1)
        nested = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Import) and n not in tree.body
                  and any(a.name == 'time' for a in n.names)]
        self.assertEqual(nested, [], 'no exception path may import time locally')

    def test_every_bulk_route_exists_and_uses_helper(self):
        for name in BULK_ROUTES:
            with self.subTest(route=name):
                segment = self.route_segment(name)
                self.assertIsNotNone(segment, 'route/function %s missing' % name)
                self.assertIn('sleep_after_429', segment)

    def test_generic_exception_paths_sleep_official_value(self):
        for name in GENERIC_PATH_ROUTES:
            with self.subTest(route=name):
                segment = self.route_segment(name)
                self.assertIsNotNone(segment, 'route/function %s missing' % name)
                self.assertIn('except Exception as e:', segment)
                self.assertRegex(segment, r'rate_limit\.sleep_after_429\(e, time\.sleep\)')

    def test_fix_content_types_keeps_10s_fallback_only_when_helper_returns_false(self):
        segment = self.route_segment('admin_fix_content_types')
        self.assertIsNotNone(segment)
        # الفرع الحقيقي: ApiTelegramException داخل fix_task
        self.assertIn('except ApiTelegramException as e:', segment)
        self.assertRegex(segment, r'if not rate_limit\.sleep_after_429\(e, time\.sleep\) '
                                  r'and "Retry in" in str\(e\):')
        self.assertIn('time.sleep(10)', segment)
        self.assertIn('time.sleep(0.5)', segment)
        self.assertIn('errors += 1', segment)
        self.assertEqual(segment.count('time.sleep(10)'), 1)

    def test_force_refresh_uses_helper_in_loop_and_outer_error(self):
        segment = self.route_segment('admin_force_refresh_all_file_ids')
        self.assertIsNotNone(segment)
        self.assertGreaterEqual(segment.count('rate_limit.sleep_after_429'), 2)
        self.assertIn('time.sleep(0.3)', segment)
        self.assertIn('failed_count += 1', segment)

    def test_diagnose_success_sleep_before_except_and_helper_in_except(self):
        """نوم الإيقاع في مسار النجاح فقط: بعد try الحذف وقبل except."""
        segment = self.route_segment('admin_diagnose_file_ids')
        self.assertIsNotNone(segment)
        body = ('\n'.join(segment.splitlines()[1:])).rstrip()
        self.assertEqual(body.count('time.sleep(0.3)'), 1)
        normal_sleep = body.index('time.sleep(0.3)')
        handler = body.index('except Exception as e:')
        self.assertLess(normal_sleep, handler,
                        'normal cadence sleep must stay on the success path')
        after_handler = body[handler:]
        self.assertIn('rate_limit.sleep_after_429(e, time.sleep)', after_handler)
        self.assertNotIn('time.sleep(0.3)', after_handler)

    def test_force_refresh_success_sleep_before_except_and_helper_in_except(self):
        segment = self.route_segment('admin_force_refresh_all_file_ids')
        self.assertIsNotNone(segment)
        body = ('\n'.join(segment.splitlines()[1:])).rstrip()
        self.assertEqual(body.count('time.sleep(0.3)'), 1)
        normal_sleep = body.index('time.sleep(0.3)')
        loop_handler = body.index('except Exception as e:')
        self.assertLess(normal_sleep, loop_handler,
                        'normal cadence sleep must stay on the success path')
        self.assertIn('rate_limit.sleep_after_429(e, time.sleep)', body[loop_handler:])
        self.assertNotIn('time.sleep(0.3)', body[loop_handler:])

    def test_extract_error_branch_continues_without_normal_sleep(self):
        segment = self.route_segment('admin_extract_channel_thumbnails')
        self.assertIsNotNone(segment)
        handler = segment.index('except Exception as e:')
        branch = segment[handler:segment.index('# إرسال النتيجة', handler)]
        self.assertIn('rate_limit.sleep_after_429(e, time.sleep)', branch)
        self.assertIn('failed_count += 1', branch)
        self.assertIn('continue', branch)
        self.assertNotIn('time.sleep(0.5)', branch)
        # النوم العادي (0.5) يبقى مرة واحدة في مسار النجاح
        self.assertEqual(segment.count('time.sleep(0.5)'), 1)

    def test_bulk_segments_never_shadow_time_locally(self):
        for name in BULK_ROUTES:
            with self.subTest(route=name):
                segment = self.route_segment(name)
                self.assertIsNotNone(segment, 'route/function %s missing' % name)
                self.assertNotRegex(segment, r'^\s+import time\s*$')
                self.assertNotRegex(segment, r'^(def |\s+)[^\n]*\btime\s*=')

    def test_regular_sleeps_and_sql_unchanged(self):
        expected = {
            'admin_update_thumbnails': ('time.sleep(1)', 'time.sleep(5)'),
            'admin_extract_channel_thumbnails': ('time.sleep(0.5)',),
            'admin_fix_videos_professional': ('time.sleep(0.3)',),
            'admin_diagnose_file_ids': ('time.sleep(0.3)',),
            'admin_fix_content_types': ('time.sleep(0.5)', 'time.sleep(10)'),
            'admin_force_refresh_all_file_ids': ('time.sleep(0.3)',),
        }
        for name, fragments in expected.items():
            with self.subTest(route=name):
                segment = self.route_segment(name)
                self.assertIsNotNone(segment, 'route/function %s missing' % name)
                for fragment in fragments:
                    self.assertIn(fragment, segment)


class _StopRequest(BaseException):
    """يقطع تنفيذ العمل عند أول نوم رسمي (لا يبتلعه أي معالج Exception)."""


class ProbeTime:
    """بديل وحدة الزمن في هذه الفترة: ``time()`` حقيقي و``sleep`` يسجّل بلا نوم.

    المساعد الحقيقي يمرّ عبر ``ProbeTime.sleep_after_429``: يقرأ القيمة الرسمية
    ثم ينادي ``self.sleep`` — فتظهر كل الأنوام في ``events`` مع تمييز نوم
    المساعد عن نوم الإيقاع العادي. ``_StopRequest`` (BaseException) يوقف العمل
    إن طُلب مما يمر عبر ``except Exception`` ثم يُمسك في ``call_route``.
    """

    def __init__(self, owner):
        self.owner = owner

    def time(self):
        return time.time()

    def sleep(self, seconds):
        self.owner.events.append(('sleep', seconds))
        self.owner.clock.sleeps.append(seconds)
        if self.owner.stop_requested:
            self.owner.stop_requested = False
            raise _StopRequest

    def sleep_after_429(self, error, sleeper=None):
        seconds = _RATE_LIMIT_HELPER.__globals__['retry_after_seconds'](error)
        if seconds is None:
            return False
        self.owner.events.append(('helper-sleep', seconds))
        self.sleep(seconds)
        return True


class BindingBot(WaveWebhookBot):
    """بوت المسارات: نفس الأخطاء المجدولة (نسخ الرسائل لا تُفشل عمداً)."""


class BoundModule:
    """غلاف وحدة ``webhook_bot`` بالاسمين وحدهما.

    كل مسار يستورد ``time``/``threading`` موضعياً بنفسه، فحقن
    ``sys.modules['time']`` و``sys.modules['threading']`` مدة الطلب يكفي — دون
    إعادة كتابة أي وحدة حقيقية. ``rate_limit`` هو الوحدة الحقيقية كما يربطها
    كائن المسار؛ يُستبدَل فيها ``sleep`` فقط (وهو الاسم الذي يناديه المساعد).
    """

    def __init__(self, module):
        import threading as real_threading
        self.module = module
        self.rate_limit = rate_limit
        self.threading = SimpleNamespace(
            Thread=self._recording_thread(real_threading.Thread))
        self.time = None
        self.app = module.app
        self.calls = []             # (خيط، هل استُدعي start؟)

    def _recording_thread(self, real_thread):
        outer = self

        class RecordingThread(real_thread):
            """خيط حقيقي لا يُبدأ؛ الهدف يُنفَّذ صراحةً عبر ``run()``."""

            def __init__(self, group=None, target=None, name=None, args=(),
                         kwargs=None, daemon=None):
                super().__init__(group=group, target=target, name=name, args=args,
                                 kwargs=kwargs or {}, daemon=daemon)
                outer.calls.append([self, False])

            def start(self):
                for entry in outer.calls:
                    if entry[0] is self:
                        entry[1] = True
                # العامل يُنفَّذ فوراً داخل الطلب بنفس ساعة الاختبار
                self.run()

        return RecordingThread

    def __getattr__(self, name):
        return getattr(self.module, name)


class WebhookBulkBehaviorTest(support.OfflineTestCase):
    """طلبات Flask حقيقية على مسارات الأدمن الجماعية.

    الحقن الفعلي الوحيد هو ساعة الاختبار: نوم الإيقاع العادي (الذي يقرأ
    ``webhook_bot.time``) ونوم المساعد (الذي يقرأ ``rate_limit.sleep`` المربوط
    وقت التحميل) يصلان إلى نفس العدّاد. الأحداث تميّزهما:
    ``('sleep', s)`` = نوم إيقاع عادي، ``('helper-sleep', s)`` = نوم المساعد الرسمي.
    """

    def setUp(self):
        super().setUp()
        self.clock = FakeClock(step=0.0)
        self.events = []
        self.stop_requested = False
        self.videos = [{'id': 1, 'message_id': 11, 'chat_id': CHANNEL,
                        'file_id': 'old-file_id', 'content_type': 'VIDEO',
                        'caption': 'cap'}]
        _restore_hidden_patches()
        self.addCleanup(_restore_hidden_patches)

    # ------------------------------------------------------------------ tools
    def default_sleep(self, seconds):
        self.clock.sleeps.append(seconds)
        self.events.append(('sleep', seconds, False))

    def helper_sleep(self, seconds):
        self.clock.sleeps.append(seconds)
        self.events.append(('helper-sleep', seconds, True))
        if self.stop_requested:
            self.stop_requested = False
            raise _StopRequest

    def webhook(self, stop_on_helper_sleep=False):
        _restore_hidden_patches()
        module = fresh_module()
        module.app.config['TESTING'] = True
        self.addCleanup(_sys.modules.pop, 'webhook_bot', None)
        self.stop_requested = stop_on_helper_sleep
        bound = BoundModule(module)
        self.bound = bound
        # ``webhook_bot`` ربط ``time`` الحقيقي عند التحميل وناداه داخل ``try`` في
        # مسارات العمل؛ نوجّه اسمه إلى وحدة ساعة يسجّل نومها ولا ينام فعلياً.
        module.time = SimpleNamespace(time=time.time, sleep=self.default_sleep)
        return bound

    def _patched(self, bound):
        """يوجّه الساعة وساعة المساعد إلى ساعة الاختبار مدة الطلب فقط."""
        saved_time_attr = bound.module.__dict__.get('time')
        saved_helper = rate_limit.__dict__.get('sleep_after_429')
        saved_threading = _sys.modules.get('threading')
        clock = self.clock
        events = self.events
        bound.module.time = SimpleNamespace(time=time.time, sleep=self.default_sleep)
        # المساعد الحقيقي يمرّ عبر ساعة الاختبار ويسجل نوع نومه (رسمي/عادي)
        rate_limit.sleep_after_429 = ProbeTime(self).sleep_after_429
        _sys.modules['threading'] = bound.threading
        return saved_time_attr, saved_helper, saved_threading

    def _restore(self, bound, saved):
        saved_time_attr, saved_helper, saved_threading = saved
        bound.module.time = saved_time_attr
        if saved_helper is None:
            rate_limit.__dict__.pop('sleep_after_429', None)
        else:
            rate_limit.sleep_after_429 = saved_helper
        _restore_modules({'threading': saved_threading})

    def call_route(self, bound, path):
        """طلب Flask حقيقي فوق وحدة ``webhook_bot`` الحقيقية بساعة الاختبار."""
        saved = self._patched(bound)
        try:
            try:
                return bound.app.test_client().get(
                    path, query_string={'admin_id': support.ADMIN_ID},
                    headers={'X-Admin-Token': ADMIN_TOKEN})
            except _StopRequest:
                self.events.append(('request-stopped', None))
                return None
        finally:
            self._restore(bound, saved)

    def run_workers(self, bound):
        """العامل يعمل فوراً في ``RecordingThread.start``؛ لا عمل إضافي هنا."""
        return None

    def helper_sleeps(self):
        return [event[1] for event in self.events if event[0] == 'helper-sleep']

    def public_bot(self, **patches):
        import db_manager
        originals, missing = _install(db_manager, **patches)
        self.addCleanup(_put_back, db_manager, originals, missing)
        return db_manager

    def fake_psycopg2(self):
        """psycopg2 وهمي بلا شبكة: SELECT صفوف، وUPDATE/COMMIT مسجَّلة."""
        executed = []

        class Cursor:
            def __init__(self):
                self._rows = []

            def execute(self, sql, params=None):
                normalized = ' '.join(str(sql).split())
                executed.append(normalized)
                self._rows = ([{'id': 1, 'message_id': 11, 'chat_id': CHANNEL}]
                              if 'SELECT' in normalized.upper() else [])

            def fetchall(self):
                return list(self._rows)

            def close(self):
                pass

        class Connection:
            def cursor(self, cursor_factory=None):
                return Cursor()

            def commit(self):
                executed.append('COMMIT')

            def rollback(self):
                executed.append('ROLLBACK')

            def close(self):
                pass

        class Extras:
            DictCursor = object

        fake = SimpleNamespace(
            connect=lambda *args, **kwargs: Connection(), extras=Extras,
            pool=SimpleNamespace(SimpleConnectionPool=lambda *a, **k: None))
        saved = {name: _sys.modules.get(name)
                 for name in ('psycopg2', 'psycopg2.extras', 'psycopg2.pool')}
        self.addCleanup(_restore_modules, saved)
        _sys.modules['psycopg2'] = fake
        _sys.modules['psycopg2.extras'] = Extras
        _sys.modules['psycopg2.pool'] = fake.pool
        return executed

    # ------------------------------------------------------------- behaviours
    def test_diagnose_file_ids_sleeps_official_retry_after(self):
        videos = [{'id': 1, 'message_id': 11, 'chat_id': CHANNEL,
                   'file_id': 'old-file_id', 'content_type': 'VIDEO', 'caption': 'cap'}]
        self.public_bot(execute_query=lambda sql, params=None, fetch=None, commit=False: (
            list(videos) if fetch == 'all' else True))
        bound = self.webhook(stop_on_helper_sleep=True)
        bound.module.bot = BindingBot([flood_error(9)])
        self.call_route(bound, '/admin/diagnose_file_ids')
        self.assertEqual(self.clock.sleeps, [9.0])       # لا نوم إيقاع على الخطأ
        self.assertEqual(self.helper_sleeps(), [9.0])    # من داخل المساعد الرسمي
        self.assertEqual(bound.module.bot.names().count('forward_message'), 1)

        # خطأ عادي (غير 429): لا نوم رسمي ولا نوم إيقاع في مسار الخطأ
        self.clock.sleeps.clear()
        self.events.clear()
        bound = self.webhook()
        bound.module.bot = BindingBot([api_error('Bad Request: chat not found', 400, 9)])
        self.call_route(bound, '/admin/diagnose_file_ids')
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.helper_sleeps(), [])
        self.assertIn('خطأ', bound.module.bot.texts()[-1])

        # بلا أخطاء: الطلب يكتمل ويبقى تأخير الإيقاع العادي ثم التقرير كما هو
        self.clock.sleeps.clear()
        self.events.clear()
        bound = self.webhook()
        bound.module.bot = BindingBot()
        response = self.call_route(bound, '/admin/diagnose_file_ids')
        self.assertEqual(self.clock.sleeps, [0.3])
        self.assertEqual(self.helper_sleeps(), [])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn('تقرير التشخيص', bound.module.bot.texts()[-1])

    def test_extract_channel_thumbnails_sleeps_official_retry_after(self):
        videos = [{'id': 1, 'message_id': 11, 'chat_id': CHANNEL, 'file_id': 'old'}]
        self.public_bot(get_videos_without_thumbnail=lambda limit=None: list(videos),
                        update_video_thumbnail=lambda video_id, thumb: True)
        bound = self.webhook(stop_on_helper_sleep=True)
        bound.module.bot = BindingBot([flood_error(12)])
        self.call_route(bound, '/admin/extract_channel_thumbnails')
        self.run_workers(bound)
        self.assertEqual(self.clock.sleeps, [12.0])
        self.assertEqual(self.helper_sleeps(), [12.0])
        self.assertEqual(bound.module.bot.names().count('copy_message'), 1)

        # خطأ عادي (غير 429): continue كما كان، بلا نوم رسمي ولا إيقاع للخطأ
        self.clock.sleeps.clear()
        self.events.clear()
        bound = self.webhook()
        bound.module.bot = BindingBot([api_error('Bad Request: bad request', 400, 7)])
        self.call_route(bound, '/admin/extract_channel_thumbnails')
        self.run_workers(bound)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.helper_sleeps(), [])
        self.assertIn('• فشل: 1', bound.module.bot.texts()[-1])

        # بلا 429: التأخير العادي 0.5 للإدخال الواحد ثم التقرير كما كان
        self.clock.sleeps.clear()
        self.events.clear()
        bound = self.webhook()
        bound.module.bot = BindingBot()
        self.call_route(bound, '/admin/extract_channel_thumbnails')
        self.assertEqual(self.clock.sleeps, [0.5])
        self.assertEqual(self.helper_sleeps(), [])
        self.assertIn('• نجح: 1', bound.module.bot.texts()[-1])
        self.assertIn('• المجموع: 1', bound.module.bot.texts()[-1])

    def test_force_refresh_sleeps_official_retry_after_without_replay(self):
        executed = self.fake_psycopg2()
        bound = self.webhook(stop_on_helper_sleep=True)
        bound.module.bot = BindingBot([flood_error(6)])
        self.call_route(bound, '/admin/force_refresh_all_file_ids')
        self.run_workers(bound)
        self.assertEqual(self.clock.sleeps, [6.0])
        self.assertEqual(self.helper_sleeps(), [6.0])
        self.assertEqual(bound.module.bot.names().count('forward_message'), 1)
        self.assertNotIn('COMMIT', executed)      # لا تحديث للأرشيف بعد الفشل

        # خطأ عادي (غير 429): لا نوم رسمي ولا نوم إيقاع في مسار الخطأ
        self.clock.sleeps.clear()
        self.events.clear()
        bound = self.webhook()
        bound.module.bot = BindingBot([api_error(
            'Bad Request: message to forward not found', 400, 5)])
        self.call_route(bound, '/admin/force_refresh_all_file_ids')
        self.run_workers(bound)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.helper_sleeps(), [])
        self.assertIn('❌ فشل: 1', bound.module.bot.texts()[-1])

        # بلا 429: التحديث يتم ويبقى النوم العادي للفيديو كما كان
        self.clock.sleeps.clear()
        self.events.clear()
        bound = self.webhook()
        bound.module.bot = BindingBot()
        response = self.call_route(bound, '/admin/force_refresh_all_file_ids')
        self.run_workers(bound)
        self.assertEqual(self.clock.sleeps, [0.3])
        self.assertEqual(self.helper_sleeps(), [])
        self.assertIn('🎬 فيديوهات حقيقية: 1', bound.module.bot.texts()[-1])
        self.assertIn('COMMIT', executed)

    def test_update_thumbnails_generic_path_uses_helper(self):
        # صف واحد في الدفعة الأولى ثم لا شيء؛ بعدها تُقرأ الدفعة التالية فارغة
        # فتنتهي الحلقة، ولا نوم إيقاع بين الدفعات إلا بعد دفعة مكتملة تليها دفعة.
        pending = list(self.videos)
        self.public_bot(get_videos_without_thumbnail=lambda limit=None: (
            [pending.pop(0)] if pending else []))
        bound = self.webhook(stop_on_helper_sleep=True)
        bound.module.bot = BindingBot([flood_error(11)])
        self.call_route(bound, '/admin/update_thumbnails')
        self.assertEqual(self.clock.sleeps, [11.0],
                         [c[0] for c in bound.module.bot.calls] + [self.events])
        self.assertEqual(self.helper_sleeps(), [11.0])

        # خطأ عادي (غير 429): continue كما كان، بلا نوم رسمي (الإيقاع كما كان)
        self.clock.sleeps.clear()
        self.events.clear()
        pending[:] = list(self.videos)
        bound = self.webhook()
        bound.module.bot = BindingBot([api_error('Bad Request: wrong file', 400, 3)])
        self.call_route(bound, '/admin/update_thumbnails')
        self.assertEqual(self.clock.sleeps, [5])
        self.assertEqual(self.helper_sleeps(), [])

    def test_fix_content_types_retry_after_in_fix_task(self):
        """الفرع الحقيقي في fix_task: 429 => نوم رسمي؛ 'Retry in' => 10s فقط بلا قيمة."""
        executed = []

        class FixBot:
            """بوت fix_task: ``send_video`` يرمي أول خطأ مجدول ثم ينجح."""

            def __init__(self, error):
                self.error = error
                self.calls = []
                self.next_message_id = 5000

            def send_message(self, chat_id, text, **kwargs):
                self.calls.append(('send_message', text))
                return True

            def send_video(self, chat_id, video, **kwargs):
                self.calls.append(('send_video', chat_id))
                error, self.error = self.error, None
                if error is not None:
                    raise error
                return SimpleNamespace(
                    message_id=self.next_message_id,
                    video=SimpleNamespace(file_id='fresh', thumb=None))

            def delete_message(self, *args, **kwargs):
                return True

            def texts(self):
                return [c[1] for c in self.calls if c[0] == 'send_message']

            def count(self, name):
                return len([c for c in self.calls if c[0] == name])

        class BasicCursor:
            def __init__(self):
                self._rows = []

            def execute(self, sql, params=None):
                normalized = ' '.join(str(sql).split())
                executed.append(normalized)
                self._rows = ([(1, 'file-id-1', 'cap', 'VIDEO')]
                              if 'SELECT' in normalized.upper() else [])

            def fetchall(self):
                return list(self._rows)

            def close(self):
                pass

        class BasicConnection:
            def cursor(self, factory=None):
                return BasicCursor()

            def commit(self):
                executed.append('COMMIT')

            def close(self):
                pass

        saved = _sys.modules.get('psycopg2')
        self.addCleanup(_restore_modules, {'psycopg2': saved})

        combos = [
            # (خطأ الإرسال، الأنوام المتوقعة، من المساعد الرسمي؟)
            # 0.5 بين الصفوف يقع بعد try لكل صف (كما في المصدر) — عدا 429 الرسمي:
            # نومه يرفع _StopRequest من داخل المساعد فيصل إلى ``except Exception``
            # الخاص بالصف ويقطع الطلب قبل 0.5 (وهذا ما يمنع توقفاً مزدوجاً الذي
            # لم يكن ممكناً قبل هذا المساعد).
            (flood_error(8), [8.0], True),
            (api_error('Bad Request: Retry in 10', 400), [10, 0.5], False),
            (api_error('Bad Request: wrong file identifier', 400), [0.5], False),
        ]
        for error, expected_sleeps, official in combos:
            with self.subTest(error=error.description[:40]):
                self.clock.sleeps.clear()
                self.events.clear()
                executed.clear()
                _sys.modules['psycopg2'] = SimpleNamespace(
                    connect=lambda *args, **kwargs: BasicConnection())
                bot = FixBot(error)
                bound = self.webhook(stop_on_helper_sleep=official)
                bound.module.bot = bot
                self.call_route(bound, '/admin/fix_content_types')
                self.assertEqual(self.clock.sleeps, expected_sleeps)
                self.assertEqual(self.helper_sleeps(), [8.0] if official else [])
                if official:
                    # التوقف في _StopRequest قبل تقرير الصف، لكن الخطأ وقع فعلاً
                    self.assertTrue(bot.count('send_video'))
                else:
                    self.assertIn('❌ أخطاء: 1', bot.texts()[-1])
                self.assertNotIn('COMMIT', executed)   # لا حفظ عند خطأ الإرسال

    def test_fix_content_types_between_rows_sleep_unchanged(self):
        """0.5 بعد كل صف يبقى كما هو (خارج try): يقع للصف الناجح والفاشل."""
        executed = []

        class FixBot:
            def __init__(self, error):
                self.error = error
                self.calls = []
                self.next_message_id = 5000

            def send_message(self, chat_id, text, **kwargs):
                self.calls.append(('send_message', text))
                return True

            def send_video(self, chat_id, video, **kwargs):
                self.calls.append(('send_video', chat_id))
                error, self.error = self.error, None
                if error is not None:
                    raise error
                return SimpleNamespace(
                    message_id=self.next_message_id,
                    video=SimpleNamespace(file_id='fresh', thumb=None))

            def delete_message(self, *args, **kwargs):
                return True

            def texts(self):
                return [c[1] for c in self.calls if c[0] == 'send_message']

            def count(self, name):
                return len([c for c in self.calls if c[0] == name])

        class BasicCursor:
            def __init__(self):
                self._rows = []

            def execute(self, sql, params=None):
                normalized = ' '.join(str(sql).split())
                executed.append(normalized)
                self._rows = ([(1, 'file-id-1', 'cap', 'VIDEO'),
                               (2, 'file-id-2', 'cap', 'VIDEO')]
                              if 'SELECT' in normalized.upper() else [])

            def fetchall(self):
                return list(self._rows)

            def close(self):
                pass

        class BasicConnection:
            def cursor(self, factory=None):
                return BasicCursor()

            def commit(self):
                executed.append('COMMIT')

            def close(self):
                pass

        saved = _sys.modules.get('psycopg2')
        self.addCleanup(_restore_modules, {'psycopg2': saved})
        _sys.modules['psycopg2'] = SimpleNamespace(
            connect=lambda *args, **kwargs: BasicConnection())

        bot = FixBot(api_error('Bad Request: wrong file identifier', 400))
        bound = self.webhook(stop_on_helper_sleep=True)
        bound.module.bot = bot
        response = self.call_route(bound, '/admin/fix_content_types')
        # صفّان: كل خطأ 400 غير مصنف -> خطأ واحد + 0.5، ثم ينجح الصف الثاني + 0.5
        self.assertEqual(self.clock.sleeps, [0.5, 0.5])
        self.assertEqual(self.helper_sleeps(), [])
        self.assertEqual(response.status_code, 200)
        self.assertIn('❌ أخطاء: 1', bot.texts()[-1])

def _restore_rate_limit_module():
    """يعيد أسماء المساعد الأصلية كما كانت عند تحميل هذا الملف."""
    current = rate_limit.__dict__.get('sleep')
    if current is not _RATE_LIMIT_SLEEP:
        if _RATE_LIMIT_SLEEP is None:
            rate_limit.__dict__.pop('sleep', None)
        else:
            rate_limit.sleep = _RATE_LIMIT_SLEEP
    if rate_limit.__dict__.get('sleep_after_429') is not _RATE_LIMIT_HELPER:
        rate_limit.sleep_after_429 = _RATE_LIMIT_HELPER
    for name, value in (('handlers.telegram_rate_limit', rate_limit),
                        ('handlers', _sys.modules.get('handlers'))):
        if value is not None and _sys.modules.get(name) is not value:
            _sys.modules[name] = value


def _restore_hidden_patches():
    """ينزع أي استحقان سابق على وحدة المساعد (mock.patch من اختبارات أخرى)."""
    _restore_rate_limit_module()


def _restore_modules(saved):
    """يعيد وحدات sys.modules المحفوظة (أو يزيل ما لم يكن موجوداً)."""
    for name, module in saved.items():
        if module is None:
            _sys.modules.pop(name, None)
        else:
            _sys.modules[name] = module


if __name__ == '__main__':
    unittest.main()
