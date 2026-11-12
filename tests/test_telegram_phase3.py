"""المرحلة 3: تقارير Rich حقيقية للبث وإحصائيات الأدمن مع بديل HTML واحد.

أوفلاين بالكامل: البوتات هنا أدوات وهمية صريحة تسجّل كل استدعاء؛
``supports_rich`` يتحكم بوجود ``send_rich_message`` فعلياً (بوتات الاختبارات
القديمة بلا هذه الدالة تمارس البديل تلقائياً). لا Telegram ولا PostgreSQL.
"""

import support  # noqa: F401  (يحجب telebot HTTP وpsycopg2 قبل استيراد الإنتاج)

import logging
import unittest
from types import SimpleNamespace

import db_manager
from handlers import admin_broadcast as ab
from handlers import callback_handlers
from handlers import helpers
from telebot.apihelper import ApiTelegramException
from telebot.types import (
    InputRichBlockDetails,
    InputRichBlockList,
    InputRichBlockListItem,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichMessage,
)


ADMINS = [support.ADMIN_ID, support.OTHER_ADMIN_ID]
AUDIENCE = [7, 8, 9]
HOSTILE = '<b>خبيث</b> & "x"'


def _restore(module, name, value):
    setattr(module, name, value)


def api_error(description, code=403):
    return ApiTelegramException('copyMessage', {'ok': False},
                                {'error_code': code, 'description': description})


class RichBot:
    """بوت وهمي صريح: ``supports_rich`` أو غيابه (لا خدعة getattr) يقرر المسار."""

    def __init__(self, supports_rich=True, rich_result='ok', copy_errors=None):
        self.calls = []
        self.copies = []
        self.copy_errors = dict(copy_errors or {})
        self.next_message_id = 4000
        self.rich_result = rich_result
        if supports_rich:
            self.send_rich_message = self._send_rich_message

    def _record(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))

    def _message(self, chat_id):
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id,
                               chat=SimpleNamespace(id=chat_id, type='private'))

    def _send_rich_message(self, chat_id, rich_message, **kwargs):
        self._record('send_rich_message', (chat_id, rich_message), kwargs)
        if self.rich_result == 'raise':
            raise RuntimeError('rich endpoint exploded with payload secret')
        if self.rich_result == 'none':
            return None
        return self._message(chat_id)

    def send_message(self, chat_id, text, **kwargs):
        self._record('send_message', (chat_id, text), kwargs)
        return self._message(chat_id)

    def copy_message(self, chat_id, from_chat_id=None, message_id=None, **kwargs):
        self._record('copy_message', (chat_id, from_chat_id, message_id), kwargs)
        error = self.copy_errors.get(chat_id)
        if error is not None:
            raise error
        self.copies.append((chat_id, from_chat_id, message_id))
        return self._message(chat_id)

    def answer_callback_query(self, call_id, text=None, **kwargs):
        self._record('answer_callback_query', (call_id, text), kwargs)
        return True

    def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        self._record('edit_message_text', (text, chat_id, message_id), kwargs)
        return True

    # --- ديكورات التسجيل (حتى نستدعي callback_handler مباشرة) ---
    def message_handler(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def callback_query_handler(self, *args, **kwargs):
        def decorator(func):
            self._callback_func = func
            return func
        return decorator

    def callback_handler(self):
        return self._callback_func

    # --- استعلامات ---
    def rich_calls(self):
        return [(args[0], args[1]) for name, args, _k in self.calls if name == 'send_rich_message']

    def rich_payloads(self):
        return [payload.to_dict() for _chat, payload in self.rich_calls()]

    def plain_report_calls(self):
        """رسائل إكمال البث/الإحصائيات النصية (بديل HTML) بعد الدفعة."""
        return [(args[0], args[1], kwargs.get('parse_mode'))
                for name, args, kwargs in self.calls
                if name == 'send_message' and args and 'إكمال' not in str(args[0])
                and ('اكتمل البث' in str(args[1]) or 'إحصائيات المحتوى' in str(args[1]))]

    def start_status_calls(self):
        return [args for name, args, _k in self.calls
                if name == 'send_message' and 'بدء البث' in str(args[1])]

    def last_plain_report(self):
        reports = self.plain_report_calls()
        return reports[-1][1] if reports else None

    def plain_texts(self):
        return [args[1] for name, args, _k in self.calls
                if name == 'send_message' and len(args) > 1]

    def answers(self):
        return [args[1] if len(args) > 1 else '' for name, args, _k in self.calls
                if name == 'answer_callback_query']


def _blocks(payload):
    return payload.get('blocks') or []


def _texts_of(block):
    """كل النصوص الفعلية داخل كتلة (نصوص عادية لا HTML مُفسَّر)."""
    out = []
    for key in ('text', 'summary'):
        value = block.get(key)
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            out.extend(v for v in value if isinstance(v, str))
    for item in block.get('items') or []:
        for inner in item.get('blocks') or []:
            out.extend(_texts_of(inner))
    for inner in block.get('blocks') or []:
        out.extend(_texts_of(inner))
    return out


def _payload_text(payload):
    return ' | '.join(text for block in _blocks(payload) for text in _texts_of(block))


def _find_block(payload, block_type):
    return [block for block in _blocks(payload) if block.get('type') == block_type]


class RichReportSerializationTest(unittest.TestCase):
    """الأنواع المهيكلة الحقيقية تُسلسل حقولها كما يعرضها تيليجرام (بلا بوت)."""

    def test_real_structured_types_serialize(self):
        item = InputRichBlockListItem(blocks=[InputRichBlockParagraph(text=HOSTILE)])
        message = InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text='عنوان', size=2),
                InputRichBlockList(items=[item]),
                InputRichBlockDetails(summary='تفاصيل', blocks=[
                    InputRichBlockParagraph(text='سطر')], is_open=False),
            ],
            is_rtl=True)
        payload = message.to_dict()
        self.assertIs(payload['is_rtl'], True)
        self.assertIsNone(payload.get('html'))
        self.assertEqual([b['type'] for b in payload['blocks']], ['heading', 'list', 'details'])
        heading, listing, details = payload['blocks']
        self.assertEqual(heading['text'], 'عنوان')
        self.assertEqual(heading['size'], 2)
        # الكابتة العدائية نص عادي حرفي: لا HTML مُفسَّر ولا ترميز
        self.assertEqual(listing['items'][0]['blocks'][0]['text'], HOSTILE)
        self.assertEqual(details['summary'], 'تفاصيل')
        self.assertIs(details['is_open'], False)


class BroadcastRichReportTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.ab = ab
        self.deleted = []
        self.delete_ok = True
        self.original_fetch = db_manager.fetch_all_user_ids
        self.original_delete = db_manager.delete_bot_user
        db_manager.delete_bot_user = lambda uid: (self.deleted.append(uid), self.delete_ok)[1]
        self.addCleanup(_restore, db_manager, 'delete_bot_user', self.original_delete)
        self.addCleanup(_restore, db_manager, 'fetch_all_user_ids', self.original_fetch)
        self.addCleanup(self.ab._active_jobs.clear)
        self.addCleanup(self.ab._JOBS.clear)

    def broadcast(self, bot, user_ids=AUDIENCE, error=None):
        if error is not None:
            db_manager.fetch_all_user_ids = lambda: (None, error)
        else:
            db_manager.fetch_all_user_ids = lambda: (list(user_ids), None)
        self.open_state()
        self.run_inline(bot, 'x')
        return bot

    def open_state(self):
        self.ab._active_jobs.clear()
        self.state = {'state': self.ab.STATE_BROADCAST,
                      'context': {'admin_id': support.ADMIN_ID, 'prompt_message_id': 11}}
        originals = (self.ab.read_state_row, self.ab.set_user_state,
                     self.ab.clear_user_waiting_state)
        self.ab.read_state_row = lambda user_id: self.state
        self.ab.set_user_state = lambda user_id, name, context=None: True
        self.ab.clear_user_waiting_state = lambda user_id: True
        self.addCleanup(lambda: self._restore_state(originals))

    def _restore_state(self, originals):
        (self.ab.read_state_row, self.ab.set_user_state,
         self.ab.clear_user_waiting_state) = originals

    def run_inline(self, bot, text):
        original_start = self.ab._reserve_job
        self.ab._reserve_job = lambda admin_id, runner, bot=None: (runner(), True)[1]
        try:
            message = support.make_message(text, message_id=91)
            self.assertTrue(self.ab.handle_broadcast_message(bot, ADMINS, message))
        finally:
            self.ab._reserve_job = original_start

    # --- مسار Rich ناجح ---

    def test_rich_success_sends_structured_rtl_report_once(self):
        bot = self.broadcast(RichBot())
        reports = bot.rich_calls()
        self.assertEqual(len(reports), 1)
        chat_id, payload = reports[0]
        self.assertEqual(chat_id, support.ADMIN_ID)
        data = payload.to_dict()
        self.assertIsInstance(payload, InputRichMessage)
        self.assertIs(data['is_rtl'], True)
        self.assertIsNone(data.get('html'))
        self.assertEqual(len(_find_block(data, 'heading')), 1)
        listing = _find_block(data, 'list')
        self.assertEqual(len(listing), 1)
        self.assertEqual(len(listing[0]['items']), 3)  # نجح/فشل/محذوف
        text = _payload_text(data)
        self.assertIn('نجح: 3', text)
        self.assertIn('فشل: 0', text)
        self.assertIn('حُذفوا (حظر/حساب معطّل): 0', text)
        # Rich ناجح: بلا تقرير نصي مكرر، وحالة البدء تبقى send_message
        self.assertEqual(bot.plain_report_calls(), [])
        self.assertEqual(len(bot.start_status_calls()), 1)
        self.assertEqual(bot.copies, [(uid, 900, 91) for uid in AUDIENCE])

    def test_rich_report_counts_match_mixed_outcomes(self):
        bot = RichBot(copy_errors={8: api_error('Forbidden: bot was blocked by the user')})
        self.broadcast(bot)
        self.assertEqual(sorted(self.deleted), [8])
        text = _payload_text(bot.rich_calls()[0][1].to_dict())
        self.assertIn('نجح: 2', text)
        self.assertIn('فشل: 0', text)
        self.assertIn('حُذفوا (حظر/حساب معطّل): 1', text)
        self.assertEqual(bot.plain_report_calls(), [])

    # --- البديل النصي عنده فشل Rich أو نتيجة None غير مؤكدة ---

    def test_rich_exception_falls_back_to_plain_once_with_unchanged_counts(self):
        bot = RichBot(rich_result='raise')
        self.broadcast(bot)
        self.assertEqual(len(bot.rich_calls()), 1)  # محاولة Rich واحدة فقط
        reports = bot.plain_report_calls()
        self.assertEqual(len(reports), 1)
        _chat, text, parse_mode = reports[0]
        self.assertEqual(parse_mode, 'HTML')
        self.assertIn('نجح: 3', text)
        self.assertIn('فشل: 0', text)
        self.assertIn('حُذفوا (حظر/حساب معطّل): 0', text)
        self.assertEqual(text.count('اكتمل البث'), 1)

    def test_rich_none_result_is_unconfirmed_and_falls_back(self):
        bot = RichBot(rich_result='none')
        self.broadcast(bot)
        self.assertEqual(len(bot.rich_calls()), 1)
        reports = bot.plain_report_calls()
        self.assertEqual(len(reports), 1)
        self.assertIn('نجح: 3', reports[0][1])

    def test_run_broadcast_returns_counts_when_rich_report_fails(self):
        bot = RichBot(rich_result='raise')
        counts = self.ab.run_broadcast(bot, support.ADMIN_ID, 900, 91, AUDIENCE,
                                       sleeper=lambda _s: None)
        self.assertEqual(counts, {'sent': 3, 'failed': 0, 'removed': 0})
        self.assertEqual(len(bot.rich_calls()), 1)
        self.assertEqual(len(bot.plain_report_calls()), 1)

    def test_run_broadcast_counts_survive_rich_and_plain_failure(self):
        bot = RichBot(rich_result='raise')

        def boom(chat_id, text, **kwargs):
            raise RuntimeError('plain send failed too')

        bot.send_message = boom
        counts = self.ab.run_broadcast(bot, support.ADMIN_ID, 900, 91, AUDIENCE,
                                       sleeper=lambda _s: None)
        self.assertEqual(counts, {'sent': 3, 'failed': 0, 'removed': 0})

    def test_existing_fake_without_rich_method_uses_plain_fallback(self):
        bot = RichBot(supports_rich=False)
        self.assertFalse(hasattr(bot, 'send_rich_message'))
        self.broadcast(bot)
        self.assertEqual(bot.rich_calls(), [])
        reports = bot.plain_report_calls()
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0][2], 'HTML')
        self.assertIn('نجح: 3', reports[0][1])

    def test_rich_failure_logs_only_exception_type(self):
        bot = RichBot(rich_result='raise')
        with self.assertLogs('handlers', level='WARNING') as captured:
            self.broadcast(bot)
        joined = '\n'.join(captured.output)
        self.assertIn('rich', joined.lower())
        self.assertNotIn('payload secret', joined)


class StatsRichReportTest(support.OfflineTestCase):
    STATS = {'video_count': 12, 'category_count': 3, 'total_views': 480, 'total_ratings': 9}
    POPULAR = {
        'most_viewed': [{'caption': HOSTILE + '\nسطر ثانٍ', 'view_count': 77}],
        'highest_rated': [{'caption': 'تقييم', 'avg_rating': 4.25}],
    }

    def setUp(self):
        super().setUp()
        self.original_check = helpers.check_subscription
        helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(_restore, helpers, 'check_subscription', self.original_check)
        self.stats = dict(self.STATS)
        self.popular = self.POPULAR
        self.originals = (callback_handlers.get_bot_stats_or_none,
                          callback_handlers.get_popular_videos)
        callback_handlers.get_bot_stats_or_none = lambda: self.stats
        callback_handlers.get_popular_videos = lambda: self.popular
        self.addCleanup(lambda: setattr(callback_handlers, 'get_bot_stats_or_none',
                                        self.originals[0]))
        self.addCleanup(lambda: setattr(callback_handlers, 'get_popular_videos',
                                        self.originals[1]))

    def click(self, bot):
        callback_handlers.register(bot, ADMINS)
        bot.callback_handler()(support.make_call('admin::stats'))
        return bot

    def test_stats_rich_success_is_structured_rtl_and_has_no_plain_duplicate(self):
        bot = self.click(RichBot())
        reports = bot.rich_calls()
        self.assertEqual(len(reports), 1)
        _chat, payload = reports[0]
        data = payload.to_dict()
        self.assertIs(data['is_rtl'], True)
        self.assertIsNone(data.get('html'))
        self.assertEqual(len(_find_block(data, 'heading')), 1)
        listing = _find_block(data, 'list')
        self.assertEqual(len(listing[0]['items']), 4)  # فيديوهات/تصنيفات/مشاهدات/تقييمات
        text = _payload_text(data)
        for value in ('12', '3', '480', '9'):
            self.assertIn(value, text)
        details = _find_block(data, 'details')
        self.assertEqual(len(details), 1)
        details_text = ' | '.join(t for b in details[0]['blocks'] for t in _texts_of(b))
        self.assertIn('77', details_text)
        self.assertIn('4.2', details_text)
        self.assertEqual(bot.plain_report_calls(), [])
        self.assertTrue(bot.answers())

    def test_stats_counts_are_numeric_in_structured_blocks(self):
        bot = self.click(RichBot())
        data = bot.rich_calls()[0][1].to_dict()
        listing = _find_block(data, 'list')[0]
        numbers = [int(n) for n in _payload_text({'blocks': [listing]}).replace(':', ' ').split()
                   if n.isdigit()]
        for value in self.STATS.values():
            self.assertIn(value, numbers)

    def test_stats_hostile_caption_stays_literal_in_rich(self):
        bot = self.click(RichBot())
        data = bot.rich_calls()[0][1].to_dict()
        text = _payload_text(data)
        self.assertIn('<b>خبيث</b> & "x"', text)
        self.assertNotIn('&lt;b&gt;', text)

    def test_stats_rich_exception_falls_back_to_escaped_html_once(self):
        bot = self.click(RichBot(rich_result='raise'))
        self.assertEqual(len(bot.rich_calls()), 1)
        reports = bot.plain_report_calls()
        self.assertEqual(len(reports), 1)
        _chat, text, parse_mode = reports[0]
        self.assertEqual(parse_mode, 'HTML')
        self.assertIn('إحصائيات المحتوى', text)
        self.assertIn('إجمالي الفيديوهات: 12', text)
        self.assertIn('&lt;b&gt;خبيث&lt;/b&gt; &amp; &quot;x&quot;', text)
        self.assertIn('77 مشاهدة', text)
        self.assertIn('4.2/5', text)
        self.assertTrue(bot.answers())

    def test_stats_rich_none_result_falls_back(self):
        bot = self.click(RichBot(rich_result='none'))
        self.assertEqual(len(bot.rich_calls()), 1)
        self.assertEqual(len(bot.plain_report_calls()), 1)

    def test_stats_without_rich_method_uses_plain_html_only(self):
        bot = self.click(RichBot(supports_rich=False))
        self.assertEqual(bot.rich_calls(), [])
        reports = bot.plain_report_calls()
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0][2], 'HTML')
        self.assertIn('&lt;b&gt;', reports[0][1])

    def test_stats_unavailable_makes_no_rich_call(self):
        self.stats = None
        bot = self.click(RichBot())
        self.assertEqual(bot.rich_calls(), [])
        self.assertEqual(bot.plain_report_calls(), [])
        self.assertIn('تعذّر', bot.answers()[-1])

    def test_stats_plain_failure_does_not_break_callback_answer(self):
        bot = RichBot(rich_result='raise')

        def boom(chat_id, text, **kwargs):
            raise RuntimeError('plain send failed')

        bot.send_message = boom
        self.click(bot)
        self.assertEqual(len(bot.rich_calls()), 1)
        self.assertTrue(bot.answers())


if __name__ == '__main__':
    unittest.main()
