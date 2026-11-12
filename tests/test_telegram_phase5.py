"""المرحلة 5: تقدم عمليات الصيانة كمسودات (send_message_draft) مع بديل التعديل الحالي.

أوفلاين بالكامل: ``support`` يحجب telebot HTTP و psycopg2 قبل استيراد الإنتاج،
وكل دوال القاعدة/الاتصال/الوقت تُبدَّل صراحةً بالاسم داخل الوحدة الحقيقية لكل اختبار.
تُستدعى نقاط الدخول الحقيقية الثلاث (heal_archive / convert_all_docs /
update_metadata) بمجموعة سجلات صغيرة للتأكد أن لكل منها مسودة تقدم فعلية.
"""

import support  # noqa: F401  (يحجب telebot HTTP وpsycopg2 قبل استيراد الإنتاج)

import importlib
import logging
import unittest
from types import SimpleNamespace

from telebot.apihelper import ApiTelegramException

import config
import update_metadata
from scripts import convert_docs_to_video
from scripts import heal_archive


CHAT_ID = support.ADMIN_ID
MESSAGE_ID = 4242
ADMINS = [support.ADMIN_ID]
CHANNEL = -1001234567890


# ---------------------------------------------------------------------------
# أدوات الاختبار
# ---------------------------------------------------------------------------

def _restore(module, name, value):
    setattr(module, name, value)


def _source(module):
    with open(module.__file__, encoding='utf-8') as handle:
        return handle.read()


def api_error(description, code=400):
    return ApiTelegramException('sendMessageDraft', {'ok': False},
                                {'error_code': code, 'description': description})


class ProgressBot:
    """بوت وهمي صريح: يتحكم بالكامل أكثر من ``support.FakeBot`` في نتيجة المسودة."""

    def __init__(self, draft_mode='true', supports_draft=True, edit_results=None,
                 forwarded=None):
        self.calls = []
        self.draft_mode = draft_mode          # true | false | none | raise | missing
        self.supports_draft = supports_draft
        self.edit_results = list(edit_results or [])
        self.forwarded = list(forwarded or [])
        self.fail_edits = set()
        self.next_message_id = 7000

    # --- إعدادات المسودة ---
    def send_message_draft(self, chat_id, draft_id, text, **kwargs):
        self.calls.append(('send_message_draft', (chat_id, draft_id, text), kwargs))
        mode = self.draft_mode if self.supports_draft else 'missing'
        if mode == 'raise':
            raise RuntimeError('draft exploded with secret-payload-<token>')
        if mode == 'false':
            return False
        if mode == 'none':
            return None
        return True

    # --- بقية استدعاءات تيليجرام ---
    def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        self.calls.append(('edit_message_text', (text, chat_id, message_id), kwargs))
        if 'edit_message_text' in self.fail_edits:
            raise RuntimeError('edit failed')
        if self.edit_results:
            result = self.edit_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return True

    def send_message(self, chat_id, text, **kwargs):
        self.calls.append(('send_message', (chat_id, text), kwargs))
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id,
                               chat=SimpleNamespace(id=chat_id, type='private'))

    def forward_message(self, chat_id, from_chat_id, message_id, **kwargs):
        self.calls.append(('forward_message', (chat_id, from_chat_id, message_id), kwargs))
        if self.forwarded:
            return self.forwarded.pop(0)
        return SimpleNamespace(message_id=900 + len(self.calls), video=None, document=None)

    def delete_message(self, *args, **kwargs):
        self.calls.append(('delete_message', args, kwargs))
        return True

    def get_file(self, file_id, **kwargs):
        self.calls.append(('get_file', (file_id,), kwargs))
        return SimpleNamespace(file_path='path/' + str(file_id), file_size=1024)

    def download_file(self, file_path, **kwargs):
        self.calls.append(('download_file', (file_path,), kwargs))
        return b'video-bytes'

    def send_video(self, chat_id, video, **kwargs):
        self.calls.append(('send_video', (chat_id,), kwargs))
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id,
                               video=SimpleNamespace(
                                   file_id='new-file-id',
                                   thumb=SimpleNamespace(file_id='new-thumb-id')))

    # --- استعلامات ---
    def of(self, name):
        return [c for c in self.calls if c[0] == name]

    def drafts(self):
        return [c for c in self.of('send_message_draft')]

    def edits(self):
        return [c for c in self.of('edit_message_text')]

    def draft_texts(self):
        return [c[1][2] for c in self.drafts()]

    def edit_texts(self):
        return [c[1][0] for c in self.edits()]

    def sent_texts(self):
        return [c[1][1] for c in self.of('send_message')]

    def draft_kwargs(self):
        return [c[2] for c in self.drafts()]

    def draft_ids(self):
        return [c[1][1] for c in self.drafts()]

    def assert_draft_first(self, testcase):
        testcase.assertTrue(self.calls, 'no telegram calls at all')
        testcase.assertEqual(self.calls[0][0], 'send_message_draft',
                             'the first progress update must be a draft, not an edit')


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


class FakeMetadataConn:
    """اتصال DBAPI وهمي لـupdate_metadata: SELECT يعيد الصفوف، UPDATE يُسجَّل."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []
        self.committed = 0
        self.rolledback = 0
        self.closed = 0

    def cursor(self, cursor_factory=None):
        return FakeMetadataCursor(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolledback += 1

    def close(self):
        self.closed += 1


class FakeMetadataCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((' '.join(str(sql).split()), params))
        if 'SELECT' in str(sql).upper():
            self._rows = list(self.conn.rows)
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)


def _install(module, **patches):
    """يبدّل أسماء داخل الوحدة الحقيقية ويعيدها في التنظيف (لا mock.patch يخفي الغياب)."""
    originals = {name: getattr(module, name) for name in patches}
    for name, value in patches.items():
        setattr(module, name, value)
    return originals


# ---------------------------------------------------------------------------
# 1) المساعد المشترك
# ---------------------------------------------------------------------------

class MaintenanceProgressHelperTest(support.OfflineTestCase):
    def helper(self):
        return importlib.import_module('handlers.maintenance_progress')

    def test_successful_repeated_drafts_share_one_nonzero_id(self):
        mp = self.helper()
        bot = ProgressBot()
        progress = mp.create(bot, CHAT_ID, MESSAGE_ID)
        self.assertNotEqual(progress.draft_id, 0)
        self.assertTrue(progress.update('١'))
        self.assertTrue(progress.update('٢'))
        self.assertTrue(progress.update('٣'))
        self.assertEqual(bot.draft_texts(), ['١', '٢', '٣'])
        self.assertEqual(bot.draft_ids(), [progress.draft_id] * 3)
        self.assertTrue(all(isinstance(i, int) and i != 0 for i in bot.draft_ids()))
        self.assertTrue(all(c[1][0] == CHAT_ID for c in bot.drafts()))
        self.assertEqual(bot.edits(), [])
        self.assertTrue(progress.drafts_enabled)

    def test_distinct_ids_across_operations(self):
        mp = self.helper()
        bot = ProgressBot()
        first = mp.create(bot, CHAT_ID, MESSAGE_ID)
        second = mp.create(bot, CHAT_ID, MESSAGE_ID)
        third = mp.create(bot, CHAT_ID, MESSAGE_ID)
        first.update('a')
        second.update('b')
        third.update('a')
        ids = bot.draft_ids()
        self.assertEqual(len(set(ids)), 3)
        self.assertNotIn(0, ids)

    def test_no_can_stop_or_extra_flags_leak_into_draft_call(self):
        mp = self.helper()
        bot = ProgressBot()
        mp.create(bot, CHAT_ID, MESSAGE_ID).update('x')
        for kwargs in bot.draft_kwargs():
            self.assertNotIn('can_stop', kwargs)
            self.assertNotIn('keep_on_stop', kwargs)
            self.assertNotIn('parse_mode', kwargs)

    def test_fallback_on_exception_logs_type_only_and_never_retries_drafts(self):
        mp = self.helper()
        bot = ProgressBot(draft_mode='raise')
        progress = mp.create(bot, CHAT_ID, MESSAGE_ID)
        with self.assertLogs('handlers.maintenance_progress', level='INFO') as captured:
            self.assertTrue(progress.update('secret text ١'))
            self.assertTrue(progress.update('secret text ٢'))
        self.assertEqual(len(bot.drafts()), 1)          # محاولة واحدة فقط
        self.assertEqual(bot.edit_texts(), ['secret text ١', 'secret text ٢'])
        self.assertEqual([c[1][1:] for c in bot.edits()], [(CHAT_ID, MESSAGE_ID)] * 2)
        joined = ' '.join(captured.output)
        self.assertIn('RuntimeError', joined)
        self.assertNotIn('secret text', joined)
        self.assertNotIn('exploded', joined)

    def test_fallback_on_false_and_none_without_later_draft_attempts(self):
        mp = self.helper()
        for mode in ('false', 'none'):
            with self.subTest(mode=mode):
                bot = ProgressBot(draft_mode=mode)
                progress = mp.create(bot, CHAT_ID, MESSAGE_ID)
                self.assertTrue(progress.update('one'))
                self.assertTrue(progress.update('two'))
                self.assertEqual(len(bot.drafts()), 1)
                self.assertEqual(bot.edit_texts(), ['one', 'two'])

    def test_missing_method_uses_edit_without_any_draft_call(self):
        mp = self.helper()

        class LegacyBot:
            """بوت قديم بلا send_message_draft إطلاقاً (لا getattr سحري)."""

            def __init__(self):
                self.edits = []

            def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
                self.edits.append((text, chat_id, message_id))
                return True

        bot = LegacyBot()
        self.assertFalse(hasattr(bot, 'send_message_draft'))
        progress = mp.create(bot, CHAT_ID, MESSAGE_ID)
        self.assertTrue(progress.update('only'))
        self.assertEqual(bot.edits, [('only', CHAT_ID, MESSAGE_ID)])
        self.assertFalse(progress.drafts_enabled)

    def test_message_not_modified_is_benign(self):
        mp = self.helper()
        bot = ProgressBot(draft_mode='false', edit_results=[
            api_error('Bad Request: message is not modified')])
        progress = mp.create(bot, CHAT_ID, MESSAGE_ID)
        with self.assertLogs('handlers.maintenance_progress', level='INFO') as captured:
            self.assertTrue(progress.update('same'))
        self.assertEqual(len(bot.edits()), 1)
        self.assertNotIn('Error', ' '.join(captured.output))

    def test_other_api_edit_errors_report_undelivered_without_raising(self):
        mp = self.helper()
        bot = ProgressBot(draft_mode='raise',
                          edit_results=[api_error('Forbidden: bot was blocked'),
                                        RuntimeError('boom')])
        progress = mp.create(bot, CHAT_ID, MESSAGE_ID)
        self.assertFalse(progress.update('a'))
        self.assertFalse(progress.update('b'))
        self.assertTrue(progress.update('c'))     # الاستثناءات لا تُبتلع إلى الأبد

    def test_delivery_result_is_boolean_and_not_required_for_flow(self):
        mp = self.helper()
        bot = ProgressBot(draft_mode='true', edit_results=[RuntimeError('x')])
        progress = mp.create(bot, CHAT_ID, MESSAGE_ID)
        self.assertIs(progress.update('a'), True)
        bot.draft_mode = 'none'
        second = mp.create(bot, CHAT_ID, MESSAGE_ID)
        self.assertIs(second.update('b'), False)

    def test_helper_never_swallows_job_exceptions(self):
        mp = self.helper()
        bot = ProgressBot()
        progress = mp.create(bot, CHAT_ID, MESSAGE_ID)

        def boom(_text):
            raise RuntimeError('maintenance failure')

        with self.assertRaises(RuntimeError):
            progress.update('a')
            boom('b')
        self.assertEqual(bot.draft_texts(), ['a'])


# ---------------------------------------------------------------------------
# 2) نقاط الدخول الحقيقية
# ---------------------------------------------------------------------------

class HealArchiveEntryPointTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(step=0.5)
        self.updates = []
        self.db_error = None
        self.videos = [
            {'id': 11, 'message_id': 501, 'chat_id': CHANNEL},
            {'id': 12, 'message_id': 502, 'chat_id': CHANNEL},
        ]

        def execute_query(sql, params=None, fetch=None, commit=False):
            self.updates.append((' '.join(str(sql).split()), params, commit))
            if self.db_error is not None:
                raise self.db_error
            if fetch == 'all':
                return list(self.videos)
            return True

        originals = _install(
            heal_archive,
            execute_query=execute_query,
            update_video_thumbnail=lambda *a, **k: True,
            time=self.clock,
        )
        for name, value in originals.items():
            self.addCleanup(_restore, heal_archive, name, value)
        original_admins = config.ADMIN_IDS
        config.ADMIN_IDS = list(ADMINS)
        self.addCleanup(_restore, config, 'ADMIN_IDS', original_admins)

    def forwarded(self, thumb=True, document=False):
        if document:
            return SimpleNamespace(
                message_id=1, video=None,
                document=SimpleNamespace(file_id='doc', thumb=(
                    SimpleNamespace(file_id='thumb') if thumb else None)))
        return SimpleNamespace(
            message_id=1,
            video=SimpleNamespace(file_id='vid', thumb=(
                SimpleNamespace(file_id='thumb') if thumb else None)),
            document=None)

    def test_progress_is_sent_as_draft_and_final_report_unchanged(self):
        bot = ProgressBot(forwarded=[self.forwarded(document=True), self.forwarded()])
        heal_archive.run_heal_archive(bot, CHAT_ID, MESSAGE_ID)

        drafts = bot.drafts()
        self.assertEqual(len(drafts), 1)                   # سجلان: كل 10 + الأخير = (2/2)
        self.assertIsInstance(bot.draft_ids()[0], int)
        self.assertNotEqual(bot.draft_ids()[0], 0)
        self.assertEqual(bot.drafts()[0][1][0], CHAT_ID)
        only = bot.draft_texts()[0]
        self.assertIn('(2/2)', only)
        self.assertIn('جارِ إصلاح الأرشيف', only)
        self.assertIn('✅ تم إصلاح: 2', only)
        self.assertIn('❌ فشل: 0', only)
        self.assertIn('📊 التقدم: 100.0%', only)
        self.assertIn('🕒 الوقت المنقضي: 1 ثانية', only)

        # بداية العملية تبقى تعديلاً كما كان (ليست تقدم دوري)
        self.assertEqual(bot.edit_texts()[0], '🔍 بدء عملية الفحص والإصلاح الشامل للأرشيف...')
        # لا تعديل دوري أثناء العمل: المسودات حلّت محلها
        self.assertEqual(len(bot.edits()), 1)

        # التقرير النهائي كما هو تماماً
        finals = bot.sent_texts()
        self.assertEqual(len(finals), 1)
        self.assertIn('✅ اكتملت عملية الإصلاح الشامل!', finals[0])
        self.assertIn('• إجمالي ما تم فحصه: 2', finals[0])
        self.assertIn('• نجاح: 2', finals[0])
        self.assertIn('• فشل: 0', finals[0])
        self.assertEqual([c[1] for c in bot.of('forward_message')],
                         [(support.ADMIN_ID, CHANNEL, 501), (support.ADMIN_ID, CHANNEL, 502)])
        self.assertEqual(len(self.updates), 3)         # SELECT + تحديثان (كما كان)
        self.assertTrue(all(commit for _q, _p, commit in self.updates[1:]))
        self.assertFalse(self.updates[0][2])           # SELECT للقراءة فقط
        self.assertEqual(self.clock.sleeps, [1.0, 1.0])
        self.assertEqual(len(bot.of('send_message')), 1)

    def test_draft_failure_falls_back_to_edit_and_outcome_unchanged(self):
        bot = ProgressBot(draft_mode='raise',
                          forwarded=[self.forwarded(), self.forwarded()])
        heal_archive.run_heal_archive(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(len(bot.drafts()), 1)         # محاولة واحدة وقف التحول
        progress_edits = bot.edit_texts()[1:]
        self.assertEqual(len(progress_edits), 1)
        self.assertIn('(2/2)', progress_edits[-1])
        self.assertEqual(len(self.updates), 3)         # SELECT + تحديثان (لا واحد أكثر)
        self.assertEqual(len(bot.forwarded), 0)
        self.assertIn('• نجاح: 2', bot.sent_texts()[-1])

    def test_no_draft_when_no_work_or_no_admin(self):
        self.videos = []
        bot = ProgressBot()
        heal_archive.run_heal_archive(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(bot.drafts(), [])
        self.assertIn('الأرشيف سليم', bot.edit_texts()[-1])
        self.assertEqual(bot.sent_texts(), [])

        self.videos = [{'id': 11, 'message_id': 501, 'chat_id': CHANNEL}]
        config.ADMIN_IDS = []
        bot = ProgressBot()
        heal_archive.run_heal_archive(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(bot.drafts(), [])
        self.assertIn('لم يتم العثور على معرف أدمن', bot.edit_texts()[-1])
        self.assertEqual(bot.of('forward_message'), [])

    def test_db_failure_still_reports_error_without_draft(self):
        self.db_error = RuntimeError('db down')
        bot = ProgressBot()
        heal_archive.run_heal_archive(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(bot.drafts(), [])
        self.assertIn('خطأ في قاعدة البيانات', bot.edit_texts()[-1])


class ConvertDocsEntryPointTest(support.OfflineTestCase):
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
        self.db_calls = []
        self.converted = []

        def execute_query(sql, params=None, fetch=None, commit=False):
            self.db_calls.append((' '.join(str(sql).split()), params, commit))
            if fetch == 'all':
                return list(self.docs)
            if commit:
                self.converted.append(params)
            return True

        originals = _install(
            convert_docs_to_video,
            execute_query=execute_query,
            time=self.clock,
        )
        for name, value in originals.items():
            self.addCleanup(_restore, convert_docs_to_video, name, value)
        original_admins, original_channel = config.ADMIN_IDS, config.CHANNEL_ID
        config.ADMIN_IDS = list(ADMINS)
        config.CHANNEL_ID = CHANNEL
        self.addCleanup(_restore, config, 'ADMIN_IDS', original_admins)
        self.addCleanup(_restore, config, 'CHANNEL_ID', original_channel)

    def test_progress_is_sent_as_draft_and_final_report_unchanged(self):
        bot = ProgressBot()
        convert_docs_to_video.run_convert_all_docs(bot, CHAT_ID, MESSAGE_ID)
        drafts = bot.drafts()
        self.assertEqual(len(drafts), 1)                   # سجلان: تحديث واحد عند (2/2)
        self.assertIsInstance(bot.draft_ids()[0], int)
        self.assertNotEqual(bot.draft_ids()[0], 0)
        only = bot.draft_texts()[0]
        self.assertIn('🔄 جارِ التحويل... (2/2)', only)
        self.assertIn('✅ نجاح: 2', only)
        self.assertIn('📦 كبير جداً: 0', only)
        self.assertIn('❌ فشل: 0', only)
        self.assertIn('📊 التقدم: 100%', only)
        self.assertIn('🕒 2 ثانية', only)

        # رسائل البداية تبقى تعديلات كما هي
        self.assertEqual(bot.edit_texts()[:2], [
            '🔍 جارِ البحث عن الملفات المسجلة كمستندات...',
            '🔄 بدء تحويل 2 ملف إلى فيديوهات حقيقية...\n⚠️ الملفات أكبر من 20MB سيتم تجاوزها (يجب رفعها يدوياً).',
        ])
        self.assertEqual(len(bot.edits()), 2)

        finals = bot.sent_texts()
        self.assertEqual(len(finals), 1)
        self.assertIn('✅ اكتملت عملية التحويل!', finals[0])
        self.assertIn('• إجمالي: 2', finals[0])
        self.assertIn('• ✅ تم تحويله لفيديو: 2', finals[0])
        self.assertEqual(len(self.converted), 2)
        self.assertEqual(self.converted[0][0], 'new-file-id')
        self.assertEqual(self.clock.sleeps, [2.0, 2.0])

    def test_draft_failure_falls_back_to_edit(self):
        bot = ProgressBot(draft_mode='none')
        convert_docs_to_video.run_convert_all_docs(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(len(bot.drafts()), 1)             # محاولة واحدة ثم تحول دائم
        progress_edits = bot.edit_texts()[2:]
        self.assertEqual(len(progress_edits), 1)
        self.assertIn('(2/2)', progress_edits[0])
        self.assertIn('📊 التقدم: 100%', progress_edits[0])
        self.assertIn('• ✅ تم تحويله لفيديو: 2', bot.sent_texts()[-1])
        self.assertEqual(len(self.converted), 2)

    def test_no_draft_when_nothing_to_convert(self):
        self.docs = []
        bot = ProgressBot()
        convert_docs_to_video.run_convert_all_docs(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(bot.drafts(), [])
        self.assertIn('لا يوجد أي ملفات', bot.edit_texts()[-1])
        self.assertEqual(bot.sent_texts(), [])


class UpdateMetadataEntryPointTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(step=0.0)
        self.conn = FakeMetadataConn([
            {'id': 31, 'caption': 'cap', 'file_name': 'a.mp4'},
            {'id': 32, 'caption': 'cap', 'file_name': 'b.mp4'},
        ])
        self.conn_error = None
        self.parsed = []

        def get_db_connection():
            return None if self.conn_error else self.conn

        def parse(caption, file_name):
            self.parsed.append((caption, file_name))
            return {'title': caption}

        originals = _install(
            update_metadata,
            get_db_connection=get_db_connection,
            extract_video_metadata=parse,
            time=self.clock,
        )
        for name, value in originals.items():
            self.addCleanup(_restore, update_metadata, name, value)

    def test_progress_is_sent_as_draft_and_final_edit_unchanged(self):
        bot = ProgressBot()
        update_metadata.run_update_and_report_progress(bot, CHAT_ID, MESSAGE_ID)
        drafts = bot.drafts()
        self.assertEqual(len(drafts), 2)                 # (1/2) و (2/2)
        self.assertEqual(set(bot.draft_ids()), {bot.draft_ids()[0]})
        self.assertNotEqual(bot.draft_ids()[0], 0)
        self.assertEqual(bot.draft_texts()[0], '⏳ جارِ إعادة بناء البيانات... (1/2) - 50%')
        self.assertEqual(bot.draft_texts()[1], '⏳ جارِ إعادة بناء البيانات... (2/2) - 100%')
        # التقرير النهائي تعديل كما هو (لا مسودة)
        self.assertEqual(bot.edit_texts(), [
            '✅ اكتملت إعادة بناء البيانات بنجاح!\n\n- تم فحص وتحديث: 2 فيديو.'])
        self.assertEqual(self.conn.committed, 1)
        self.assertEqual(self.conn.rolledback, 0)
        self.assertEqual(self.conn.closed, 1)
        self.assertEqual(len(self.parsed), 2)
        updates = [e for e in self.conn.executed if e[0].startswith('UPDATE')]
        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0][1][1], 31)

    def test_throttle_controls_how_many_progress_updates_are_sent(self):
        self.clock = FakeClock(step=0.1)                 # أقل من 1.5 ثانية لكل خطوة
        self.conn.rows = [{'id': i, 'caption': 'c', 'file_name': 'f'} for i in range(1, 4)]
        bot = ProgressBot()
        update_metadata.run_update_and_report_progress(bot, CHAT_ID, MESSAGE_ID)
        # (1/3) عند الحلقة الأولى (من 0) ثم الحلقة الأخيرة (3/3) فقط — لا (2/3)
        self.assertEqual(bot.draft_texts(), [
            '⏳ جارِ إعادة بناء البيانات... (1/3) - 33%',
            '⏳ جارِ إعادة بناء البيانات... (3/3) - 100%'])
        self.assertEqual(self.conn.committed, 1)

    def test_draft_failure_falls_back_to_final_edit(self):
        bot = ProgressBot(draft_mode='raise')
        update_metadata.run_update_and_report_progress(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(len(bot.drafts()), 1)
        self.assertEqual(bot.edit_texts()[:-1], [
            '⏳ جارِ إعادة بناء البيانات... (1/2) - 50%',
            '⏳ جارِ إعادة بناء البيانات... (2/2) - 100%'])
        self.assertIn('تم فحص وتحديث: 2', bot.edit_texts()[-1])
        self.assertEqual(self.conn.committed, 1)

    def test_message_not_modified_in_fallback_is_ignored(self):
        bot = ProgressBot(draft_mode='raise', edit_results=[
            api_error('Bad Request: message is not modified'), True])
        update_metadata.run_update_and_report_progress(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(self.conn.committed, 1)
        self.assertEqual(len(bot.edits()), 3)

    def test_empty_data_and_db_failure_messages_unchanged(self):
        self.conn.rows = []
        bot = ProgressBot()
        update_metadata.run_update_and_report_progress(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(bot.drafts(), [])
        self.assertEqual(bot.edit_texts(),
                         ['✅ لا توجد فيديوهات في قاعدة البيانات لتحديثها.'])

        self.conn_error = True
        bot = ProgressBot()
        update_metadata.run_update_and_report_progress(bot, CHAT_ID, MESSAGE_ID)
        self.assertEqual(bot.drafts(), [])
        self.assertEqual(bot.edit_texts(), ['❌ فشل الاتصال بقاعدة البيانات.'])


class NoRealNetworkOrDbTest(support.OfflineTestCase):
    def test_support_blocks_telegram_http_and_psycopg2(self):
        import telebot.apihelper
        import psycopg2
        with self.assertRaises(support.NetworkBlocked):
            telebot.apihelper._make_request('token', 'getMe', {})
        with self.assertRaises(support.NetworkBlocked):
            psycopg2.connect('postgres://user:pass@host/db')

    def test_real_helper_draft_call_never_reaches_http(self):
        mp = importlib.import_module('handlers.maintenance_progress')
        from telebot import TeleBot
        bot = TeleBot('123:token', threaded=False)
        progress = mp.create(bot, CHAT_ID, MESSAGE_ID)
        # الهاتف الحقيقي محجوب: الاستثناء يُلتقط، والبديل يرمي لأنه بوت حقيقي بلا HTTP
        with self.assertLogs('handlers.maintenance_progress', level='INFO') as captured:
            progress.update('trial')
        self.assertIn('NetworkBlocked', ' '.join(captured.output))
        self.assertFalse(progress.drafts_enabled)

    def test_thread_control_buttons_are_unchanged(self):
        """المسار القديم (edit_message_text) يبقى موجوداً في الوحدات الثلاث."""
        for module in (heal_archive, convert_docs_to_video, update_metadata):
            text = _source(module)
            self.assertIn('edit_message_text', text)
        self.assertTrue(hasattr(update_metadata, 'run_update_and_report_progress'))
        self.assertTrue(hasattr(heal_archive, 'run_heal_archive'))
        self.assertTrue(hasattr(convert_docs_to_video, 'run_convert_all_docs'))

    def test_draft_calls_never_use_can_stop(self):
        mp = importlib.import_module('handlers.maintenance_progress')
        source = _source(mp)
        self.assertNotIn('can_stop', source)
        for module in (heal_archive, convert_docs_to_video, update_metadata):
            text = _source(module)
            self.assertNotIn('can_stop', text)
            self.assertNotIn('stopped_message_generation', text)


if __name__ == '__main__':
    logging.disable(logging.NOTSET)
    unittest.main()
