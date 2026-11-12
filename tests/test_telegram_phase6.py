"""المرحلة 6: قوائم متابعة اختيارية (send_checklist) بعد نجاح عمليات الصيانة.

أوفلاين بالكامل: ``support`` يحجب telebot HTTP و psycopg2 قبل استيراد الإنتاج،
ودوال القاعدة/الاتصال/الوقت تُبدَّل صراحةً بالاسم داخل الوحدة الحقيقية.

قواعد مُتحقَّق منها هنا:
- ``TELEGRAM_BUSINESS_CONNECTION_ID`` غير موجود/فارغ => صفر استدعاءات لـsend_checklist،
  والتقارير النهائية كما هي تماماً (الوضع الإنتاجي الحالي).
- مع معرّف ربط وهمي: استدعاء واحد بالضبط لكل نقطة دخول، بعد التقرير النهائي الناجح
  مباشرةً، بالقائمة الحقيقية ومعرّف الربط والمحادثة نفسها.
- استثناء/``None`` من send_checklist => لا بديل، ولا تغيير في النتيجة النهائية.
- لا يُسجَّل معرّف الربط ولا نص القائمة ولا حمولة الإرسال.
"""

import support  # noqa: F401  (يحجب telebot HTTP وpsycopg2 قبل استيراد الإنتاج)

import importlib
import logging
import os
import re
import unittest
from types import SimpleNamespace

from telebot.types import InputChecklist, InputChecklistTask

import update_metadata
from scripts import convert_docs_to_video
from scripts import heal_archive


CHAT_ID = support.ADMIN_ID
MESSAGE_ID = 4242
ADMINS = [support.ADMIN_ID]
CHANNEL = -1001234567890
ENV_KEY = 'TELEGRAM_BUSINESS_CONNECTION_ID'
# معرّف ربط وهمي للاختبار فقط: لا وجود له في الإنتاج ولا قيمة افتراضية في الكود.
DUMMY_BUSINESS_ID = 'bc-test-not-a-real-connection'

OPERATIONS = {
    'heal_archive': heal_archive,
    'convert_all_docs': convert_docs_to_video,
    'update_metadata': update_metadata,
}
ENTRY_POINTS = {
    'heal_archive': lambda module, bot: module.run_heal_archive(bot, CHAT_ID, MESSAGE_ID),
    'convert_all_docs': lambda module, bot: module.run_convert_all_docs(bot, CHAT_ID, MESSAGE_ID),
    'update_metadata': lambda module, bot: module.run_update_and_report_progress(
        bot, CHAT_ID, MESSAGE_ID),
}


# ---------------------------------------------------------------------------
# أدوات الاختبار
# ---------------------------------------------------------------------------

def _source(module):
    with open(module.__file__, encoding='utf-8') as handle:
        return handle.read()


def helper():
    return importlib.import_module('handlers.maintenance_checklists')


def _install(module, **patches):
    """يبدّل أسماء داخل الوحدة الحقيقية فعلياً ويعيد القيم الأصلية للتنظيف."""
    originals = {name: getattr(module, name) for name in patches}
    for name, value in patches.items():
        setattr(module, name, value)
    return originals, patches


class ChecklistBot:
    """بوت وهمي يتحكم كاملاً في نتيجة send_checklist ويحفظ ترتيب الاستدعاءات."""

    def __init__(self, checklist_mode='ok'):
        self.calls = []
        self.checklist_mode = checklist_mode      # ok | raise | none
        self.next_message_id = 7000

    # --- استدعاءات تيليجرام ---
    def send_checklist(self, business_connection_id, chat_id, checklist, **kwargs):
        self.calls.append(('send_checklist', (business_connection_id, chat_id, checklist), kwargs))
        if self.checklist_mode == 'raise':
            raise RuntimeError('business connection secret-payload-<token> rejected')
        if self.checklist_mode == 'none':
            return None
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id,
                               chat=SimpleNamespace(id=chat_id, type='private'))

    def send_message_draft(self, chat_id, draft_id, text, **kwargs):
        self.calls.append(('send_message_draft', (chat_id, draft_id, text), kwargs))
        return True

    def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        self.calls.append(('edit_message_text', (text, chat_id, message_id), kwargs))
        return True

    def send_message(self, chat_id, text, **kwargs):
        self.calls.append(('send_message', (chat_id, text), kwargs))
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id,
                               chat=SimpleNamespace(id=chat_id, type='private'))

    def forward_message(self, chat_id, from_chat_id, message_id, **kwargs):
        self.calls.append(('forward_message', (chat_id, from_chat_id, message_id), kwargs))
        return SimpleNamespace(message_id=900 + len(self.calls),
                               video=SimpleNamespace(file_id='vid',
                                                     thumb=SimpleNamespace(file_id='thumb')),
                               document=None)

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

    def names(self):
        return [c[0] for c in self.calls]

    def checklists(self):
        return self.of('send_checklist')

    def sent_texts(self):
        return [c[1][1] for c in self.of('send_message')]

    def edit_texts(self):
        return [c[1][0] for c in self.of('edit_message_text')]


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


def _business_env(value):
    """يضبط (أو يزيل عند ``None``) متغير معرّف الربط فقط، ويعيد الحالة الأصلية."""
    saved = os.environ.get(ENV_KEY)
    if value is None:
        os.environ.pop(ENV_KEY, None)
    else:
        os.environ[ENV_KEY] = value
    return saved


class Phase6TestCase(support.OfflineTestCase):
    def set_env(self, value):
        saved = _business_env(value)

        def restore():
            if saved is None:
                os.environ.pop(ENV_KEY, None)
            else:
                os.environ[ENV_KEY] = saved

        self.addCleanup(restore)


# ---------------------------------------------------------------------------
# 1) المساعد: تسلسل حقيقي، معرّفات صحيحة فريدة، صلاحيات، تصرفات الفشل
# ---------------------------------------------------------------------------

class ChecklistHelperTest(Phase6TestCase):
    def setUp(self):
        super().setUp()
        self.module = helper()

    def test_build_checklist_returns_real_types_with_positive_unique_task_ids(self):
        for operation in ('heal_archive', 'convert_all_docs', 'update_metadata'):
            with self.subTest(operation=operation):
                checklist = self.module.build_checklist(operation)
                self.assertIsInstance(checklist, InputChecklist)
                self.assertTrue(checklist.title)
                self.assertTrue(checklist.tasks)
                for task in checklist.tasks:
                    self.assertIsInstance(task, InputChecklistTask)
                    self.assertIs(type(task.id), int)          # عدد صحيح وليس نصاً
                    self.assertGreater(task.id, 0)             # موجب (لا صفر)
                    self.assertTrue(task.text)
                ids = [task.id for task in checklist.tasks]
                self.assertEqual(len(ids), len(set(ids)))      # فريدة
                self.assertEqual(ids, list(range(1, len(ids) + 1)))

    def test_checklist_payload_serializes_with_expected_permissions(self):
        checklist = self.module.build_checklist('convert_all_docs')
        payload = checklist.to_dict()
        self.assertEqual(payload['title'], checklist.title)
        self.assertEqual(len(payload['tasks']), 3)
        self.assertEqual([t['id'] for t in payload['tasks']], [1, 2, 3])
        self.assertTrue(all(isinstance(t['text'], str) and t['text'] for t in payload['tasks']))
        # عمل متابعة يدوي: المالك فقط يضيف مهام، والمشاركون يمكنهم تأكيد الإنجاز
        self.assertFalse(payload['others_can_add_tasks'])
        self.assertTrue(payload['others_can_mark_tasks_as_done'])
        self.assertTrue(payload['others_can_add_tasks'] is False)  # ليست None

    def test_operations_are_distinct_follow_up_checklists(self):
        titles = {op: self.module.build_checklist(op).title for op in OPERATIONS}
        self.assertEqual(len(set(titles.values())), len(titles))

    def test_unknown_operation_raises_and_sends_nothing(self):
        self.set_env(DUMMY_BUSINESS_ID)
        with self.assertRaises(ValueError):
            self.module.build_checklist('not_an_operation')
        bot = ChecklistBot()
        with self.assertLogs('handlers.maintenance_checklists', level='INFO') as captured:
            self.assertFalse(self.module.send_followup_checklist(bot, CHAT_ID, 'not_an_operation'))
        self.assertEqual(bot.checklists(), [])
        self.assertIn('unknown operation', ' '.join(captured.output))

    def test_missing_or_blank_env_means_zero_calls(self):
        for value in (None, '', '   ', '\t\n'):
            with self.subTest(value=value):
                self.set_env(value)
                bot = ChecklistBot()
                self.assertFalse(
                    self.module.send_followup_checklist(bot, CHAT_ID, 'heal_archive'))
                self.assertEqual(bot.calls, [])

    def test_configured_send_uses_real_checklist_and_strips_business_id(self):
        self.set_env('  ' + DUMMY_BUSINESS_ID + '  ')
        bot = ChecklistBot()
        self.assertTrue(self.module.send_followup_checklist(bot, CHAT_ID, 'heal_archive'))
        calls = bot.checklists()
        self.assertEqual(len(calls), 1)
        business_id, chat_id, checklist = calls[0][1]
        self.assertEqual(business_id, DUMMY_BUSINESS_ID)
        self.assertEqual(chat_id, CHAT_ID)
        self.assertIsInstance(checklist, InputChecklist)
        self.assertEqual(checklist.to_dict()['tasks'][0]['id'], 1)
        self.assertNotIn('business_connection_id', calls[0][2])

    def test_send_exception_returns_false_without_fallback_or_leaks(self):
        self.set_env(DUMMY_BUSINESS_ID)
        bot = ChecklistBot(checklist_mode='raise')
        with self.assertLogs('handlers.maintenance_checklists', level='INFO') as captured:
            self.assertFalse(self.module.send_followup_checklist(bot, CHAT_ID, 'heal_archive'))
        self.assertEqual(len(bot.checklists()), 1)          # محاولة واحدة، لا إعادة
        self.assertEqual(bot.names(), ['send_checklist'])   # لا بديل (لا رسالة/تعديل)
        joined = ' '.join(captured.output)
        self.assertIn('RuntimeError', joined)
        self.assertNotIn(DUMMY_BUSINESS_ID, joined)
        self.assertNotIn('secret-payload', joined)
        self.assertNotIn(self.module.build_checklist('heal_archive').title, joined)

    def test_send_none_returns_false_without_fallback(self):
        self.set_env(DUMMY_BUSINESS_ID)
        bot = ChecklistBot(checklist_mode='none')
        self.assertFalse(self.module.send_followup_checklist(bot, CHAT_ID, 'heal_archive'))
        self.assertEqual(len(bot.checklists()), 1)
        self.assertEqual(bot.names(), ['send_checklist'])

    def test_no_production_business_id_or_default(self):
        source = _source(self.module)
        self.assertNotIn('bc-', source)
        self.assertNotIn('business_connection_id=', source)
        saved = _business_env(None)
        try:
            bot = ChecklistBot()
            self.assertFalse(
                self.module.send_followup_checklist(bot, CHAT_ID, 'heal_archive'))
            self.assertEqual(bot.calls, [])
        finally:
            if saved is not None:
                os.environ[ENV_KEY] = saved


# ---------------------------------------------------------------------------
# 2) نقاط الدخول الحقيقية الثلاث
# ---------------------------------------------------------------------------

class EntryPointBase(Phase6TestCase):
    """يهيّئ الوحدة الحقيقية العملية مع سجلات صغيرة؛ كل تبديل بالاسم ثم يُرد."""

    operation = None

    def setUp(self):
        super().setUp()
        self.module = OPERATIONS[self.operation]
        self.clock = FakeClock()
        self.db_calls = []

    def run_entry(self, bot):
        return ENTRY_POINTS[self.operation](self.module, bot)

    def enable_checklists(self, value=DUMMY_BUSINESS_ID):
        self.set_env(value)

    def disable_checklists(self):
        self.set_env(None)

    def assert_one_checklist_after_final(self, bot, final_call_name):
        checklists = bot.checklists()
        self.assertEqual(len(checklists), 1, 'exactly one checklist call expected')
        business_id, chat_id, checklist = checklists[0][1]
        self.assertEqual(business_id, DUMMY_BUSINESS_ID)
        self.assertEqual(chat_id, CHAT_ID)
        self.assertIsInstance(checklist, InputChecklist)
        payload = checklist.to_dict()
        self.assertTrue(payload['title'])
        self.assertEqual([t['id'] for t in payload['tasks']],
                         list(range(1, len(payload['tasks']) + 1)))
        self.assertFalse(payload['others_can_add_tasks'])
        # الاستدعاء بعد التقرير النهائي مباشرةً وقبل أي شيء آخر
        index = bot.names().index('send_checklist')
        self.assertGreater(index, 0)
        self.assertEqual(bot.names()[index - 1], final_call_name,
                         'checklist must follow the unchanged final report')
        self.assertEqual(index, len(bot.names()) - 1, 'checklist must be the last call')

    def assert_no_checklist(self, bot):
        self.assertEqual(bot.checklists(), [])



class HealArchiveChecklistTest(EntryPointBase):
    operation = 'heal_archive'

    def setUp(self):
        super().setUp()
        self.videos = [
            {'id': 11, 'message_id': 501, 'chat_id': CHANNEL},
            {'id': 12, 'message_id': 502, 'chat_id': CHANNEL},
        ]
        self.db_error = None

        def execute_query(sql, params=None, fetch=None, commit=False):
            self.db_calls.append((' '.join(str(sql).split()), params, commit))
            if self.db_error is not None:
                raise self.db_error
            if fetch == 'all':
                return list(self.videos)
            return True

        originals, _patches = _install(
            heal_archive, execute_query=execute_query, time=self.clock,
            update_video_thumbnail=lambda *a, **k: True)
        for name, value in originals.items():
            self.addCleanup(setattr, heal_archive, name, value)
        import config
        saved = config.ADMIN_IDS
        config.ADMIN_IDS = list(ADMINS)
        self.addCleanup(setattr, config, 'ADMIN_IDS', saved)

    def test_checklist_follows_unchanged_final_report_when_configured(self):
        self.enable_checklists()
        bot = ChecklistBot()
        self.run_entry(bot)
        finals = bot.sent_texts()
        self.assertEqual(len(finals), 1)
        self.assertIn('✅ اكتملت عملية الإصلاح الشامل!', finals[0])
        self.assertIn('• إجمالي ما تم فحصه: 2', finals[0])
        self.assertIn('• نجاح: 2', finals[0])
        self.assertIn('• فشل: 0', finals[0])
        self.assertEqual(bot.edit_texts()[0], '🔍 بدء عملية الفحص والإصلاح الشامل للأرشيف...')
        self.assertEqual(len(self.db_calls), 3)             # SELECT + تحديثان (كما كان)
        self.assertEqual(self.clock.sleeps, [1.0, 1.0])
        self.assert_one_checklist_after_final(bot, 'send_message')

    def test_missing_env_keeps_zero_checklist_calls(self):
        self.disable_checklists()
        bot = ChecklistBot()
        self.run_entry(bot)
        self.assert_no_checklist(bot)
        # التقرير النهائي الناجح نفسه يبقى آخر استدعاء، بلا أي قائمة
        self.assertEqual(len(bot.sent_texts()), 1)
        self.assertIn('✅ اكتملت عملية الإصلاح الشامل!', bot.sent_texts()[0])
        self.assertIn('• نجاح: 2', bot.sent_texts()[0])
        self.assertEqual(bot.names()[-1], 'send_message')

    def test_checklist_exception_or_none_does_not_change_outcome(self):
        self.enable_checklists()
        sent = []
        for mode in ('raise', 'none'):
            with self.subTest(mode=mode):
                bot = ChecklistBot(checklist_mode=mode)
                self.run_entry(bot)                        # لا استثناء يخرج
                self.assertEqual(len(bot.checklists()), 1)  # لا إعادة ولا بديل
                sent.extend(bot.sent_texts())
                used = len(self.db_calls)
                self.assertEqual(used % 3, 0)               # دورة كاملة لكل تشغيل
                runs = used // 3
                # كل تشغيل: SELECT + تحديثان (كما كان قبل الميزة)
                self.assertEqual(len(self.db_calls), 3 * runs,
                                 'each run must issue exactly the same DB calls as before')
                for offset in range(0, used, 3):
                    self.assertEqual(self.db_calls[offset:offset + 3], self.db_calls[:3])
        self.assertEqual(len(sent), 2)                      # تقرير ناجح واحد لكل تشغيل
        for text in sent:
            self.assertIn('✅ اكتملت عملية الإصلاح الشامل!', text)
            self.assertIn('• نجاح: 2', text)
        self.assertEqual(sent[0], sent[1])                  # لا اختلاف في النتيجة

    def test_no_checklist_on_empty_archive_or_db_failure_or_no_admin(self):
        self.enable_checklists()
        self.videos = []
        bot = ChecklistBot()
        self.run_entry(bot)
        self.assert_no_checklist(bot)
        self.assertIn('الأرشيف سليم', bot.edit_texts()[-1])
        self.videos = [{'id': 11, 'message_id': 501, 'chat_id': CHANNEL}]
        self.db_error = RuntimeError('db down')
        bot = ChecklistBot()
        self.run_entry(bot)
        self.assert_no_checklist(bot)
        self.assertIn('خطأ في قاعدة البيانات', bot.edit_texts()[-1])

        self.db_error = None
        import config
        saved_admins = config.ADMIN_IDS
        config.ADMIN_IDS = []
        try:
            bot = ChecklistBot()
            self.run_entry(bot)
        finally:
            config.ADMIN_IDS = saved_admins
        self.assert_no_checklist(bot)
        self.assertIn('لم يتم العثور على معرف أدمن', bot.edit_texts()[-1])


class ConvertDocsChecklistTest(EntryPointBase):
    operation = 'convert_all_docs'

    def setUp(self):
        super().setUp()
        self.docs = [
            {'id': 21, 'message_id': 601, 'chat_id': CHANNEL, 'file_id': 'f1',
             'caption': 'cap1', 'file_name': 'a.mp4', 'content_type': 'DOCUMENT',
             'thumbnail_file_id': None},
            {'id': 22, 'message_id': 602, 'chat_id': CHANNEL, 'file_id': 'f2',
             'caption': 'cap2', 'file_name': 'b.mp4', 'content_type': 'DOCUMENT',
             'thumbnail_file_id': None},
        ]
        self.converted = []

        def execute_query(sql, params=None, fetch=None, commit=False):
            self.db_calls.append((' '.join(str(sql).split()), params, commit))
            if fetch == 'all':
                return list(self.docs)
            if commit:
                self.converted.append(params)
            return True

        originals, _patches = _install(
            convert_docs_to_video, execute_query=execute_query, time=self.clock)
        for name, value in originals.items():
            self.addCleanup(setattr, convert_docs_to_video, name, value)
        import config
        saved_admins, saved_channel = config.ADMIN_IDS, config.CHANNEL_ID
        config.ADMIN_IDS = list(ADMINS)
        config.CHANNEL_ID = CHANNEL
        self.addCleanup(setattr, config, 'ADMIN_IDS', saved_admins)
        self.addCleanup(setattr, config, 'CHANNEL_ID', saved_channel)

    def test_checklist_follows_unchanged_final_report_when_configured(self):
        self.enable_checklists()
        bot = ChecklistBot()
        self.run_entry(bot)
        finals = bot.sent_texts()
        self.assertEqual(len(finals), 1)
        self.assertIn('✅ اكتملت عملية التحويل!', finals[0])
        self.assertIn('• إجمالي: 2', finals[0])
        self.assertIn('• ✅ تم تحويله لفيديو: 2', finals[0])
        self.assertIn('• ❌ فشل: 0', finals[0])
        self.assertEqual(self.converted[0][0], 'new-file-id')
        self.assertEqual(self.clock.sleeps, [2.0, 2.0])
        self.assert_one_checklist_after_final(bot, 'send_message')

    def test_checklist_exception_or_none_does_not_change_outcome(self):
        self.enable_checklists()
        sent = []
        conversions_before = len(self.converted)
        for mode in ('raise', 'none'):
            with self.subTest(mode=mode):
                bot = ChecklistBot(checklist_mode=mode)
                self.run_entry(bot)
                self.assertEqual(len(bot.checklists()), 1)  # لا إعادة ولا بديل
                sent.extend(bot.sent_texts())
                # كل Conversions نجحت: اثنتان لكل تشغيل، لا تغيّر بسبب القائمة
                self.assertEqual(len(self.converted) - conversions_before, 2)
                conversions_before = len(self.converted)
        self.assertEqual(len(sent), 2)                      # تقرير ناجح واحد لكل تشغيل
        for text in sent:
            self.assertIn('✅ اكتملت عملية التحويل!', text)
            self.assertIn('• ✅ تم تحويله لفيديو: 2', text)
        self.assertEqual(sent[0], sent[1])

    def test_missing_env_keeps_zero_checklist_calls(self):
        self.disable_checklists()
        bot = ChecklistBot()
        self.run_entry(bot)
        self.assert_no_checklist(bot)
        self.assertEqual(len(bot.sent_texts()), 1)
        self.assertIn('✅ اكتملت عملية التحويل!', bot.sent_texts()[0])
        self.assertEqual(bot.names()[-1], 'send_message')

    def test_no_checklist_when_nothing_to_convert(self):
        self.enable_checklists()
        self.docs = []
        original_get = convert_docs_to_video.get_document_videos
        convert_docs_to_video.get_document_videos = lambda: []
        self.addCleanup(setattr, convert_docs_to_video, 'get_document_videos', original_get)
        bot = ChecklistBot()
        self.run_entry(bot)
        self.assert_no_checklist(bot)
        self.assertIn('لا يوجد أي ملفات', bot.edit_texts()[-1])
        self.assertEqual(bot.sent_texts(), [])


class UpdateMetadataChecklistTest(EntryPointBase):
    operation = 'update_metadata'

    def setUp(self):
        super().setUp()
        self.conn = FakeMetadataConn(self.rows())
        self.conn_error = None
        self.parsed = []

        def get_db_connection():
            return None if self.conn_error else self.conn

        def parse(caption, file_name):
            self.parsed.append((caption, file_name))
            return {'title': caption}

        originals, _patches = _install(
            update_metadata, get_db_connection=get_db_connection,
            extract_video_metadata=parse, time=self.clock)
        for name, value in originals.items():
            self.addCleanup(setattr, update_metadata, name, value)

    @staticmethod
    def rows():
        return [
            {'id': 31, 'caption': 'cap', 'file_name': 'a.mp4'},
            {'id': 32, 'caption': 'cap', 'file_name': 'b.mp4'},
        ]

    def final_edit(self):
        return '✅ اكتملت إعادة بناء البيانات بنجاح!\n\n- تم فحص وتحديث: 2 فيديو.'

    def test_checklist_follows_unchanged_final_edit_when_configured(self):
        self.enable_checklists()
        bot = ChecklistBot()
        self.run_entry(bot)
        self.assertEqual(bot.edit_texts(), [self.final_edit()])
        self.assertEqual(self.conn.committed, 1)
        self.assertEqual(self.conn.rolledback, 0)
        self.assertEqual(self.conn.closed, 1)
        self.assertEqual(len(self.parsed), 2)
        self.assert_one_checklist_after_final(bot, 'edit_message_text')

    def test_missing_env_keeps_zero_checklist_calls(self):
        self.disable_checklists()
        bot = ChecklistBot()
        self.run_entry(bot)
        self.assert_no_checklist(bot)
        self.assertEqual(bot.edit_texts(), [self.final_edit()])

    def test_checklist_exception_or_none_does_not_change_outcome(self):
        self.enable_checklists()
        runs = 0
        for mode in ('raise', 'none'):
            with self.subTest(mode=mode):
                runs += 1
                bot = ChecklistBot(checklist_mode=mode)
                self.run_entry(bot)
                self.assertEqual(len(bot.checklists()), 1)
                self.assertEqual(bot.edit_texts(), [self.final_edit()])
                # كل تشغيل: commit واحد، بلا rollback، وتحديثان فقط في القاعدة
                self.assertEqual(self.conn.committed, runs)
                self.assertEqual(self.conn.rolledback, 0)
                self.assertEqual(len(self.parsed), 2 * runs)

    def test_no_checklist_on_empty_data_or_db_failure_or_sql_error(self):
        self.enable_checklists()
        self.conn = FakeMetadataConn([])
        bot = ChecklistBot()
        self.run_entry(bot)
        self.assert_no_checklist(bot)
        self.assertEqual(bot.edit_texts(),
                         ['✅ لا توجد فيديوهات في قاعدة البيانات لتحديثها.'])

        self.conn_error = True
        bot = ChecklistBot()
        self.run_entry(bot)
        self.assert_no_checklist(bot)
        self.assertEqual(bot.edit_texts(), ['❌ فشل الاتصال بقاعدة البيانات.'])

        self.conn_error = False

        class ExplodingCursor(FakeMetadataCursor):
            def execute(self, sql, params=None):
                raise RuntimeError('update failed')

        class ExplodingConn(FakeMetadataConn):
            def cursor(self, cursor_factory=None):
                return ExplodingCursor(self)

        failing_conn = ExplodingConn(self.rows())
        original_get = update_metadata.get_db_connection
        update_metadata.get_db_connection = lambda: failing_conn
        self.addCleanup(setattr, update_metadata, 'get_db_connection', original_get)
        bot = ChecklistBot()
        self.run_entry(bot)
        self.assert_no_checklist(bot)
        self.assertIn('❌ حدث خطأ أثناء التحديث', bot.edit_texts()[-1])
        self.assertEqual(failing_conn.committed, 0)
        self.assertEqual(failing_conn.rolledback, 1)
        self.assertEqual(failing_conn.closed, 1)


# ---------------------------------------------------------------------------
# 3) حماية الشبكة والحمايات الثابتة
# ---------------------------------------------------------------------------

class NoRealNetworkOrDefaultsTest(Phase6TestCase):
    def test_support_blocks_telegram_http_and_psycopg2(self):
        import telebot.apihelper
        import psycopg2
        with self.assertRaises(support.NetworkBlocked):
            telebot.apihelper._make_request('token', 'getMe', {})
        with self.assertRaises(support.NetworkBlocked):
            psycopg2.connect('postgres://user:pass@host/db')

    def test_real_helper_send_never_reaches_http(self):
        from telebot import TeleBot

        self.set_env(DUMMY_BUSINESS_ID)
        bot = TeleBot('123:token', threaded=False)
        with self.assertLogs('handlers.maintenance_checklists', level='INFO') as captured:
            self.assertFalse(
                helper().send_followup_checklist(bot, CHAT_ID, 'update_metadata'))
        joined = ' '.join(captured.output)
        self.assertIn('NetworkBlocked', joined)
        self.assertNotIn(DUMMY_BUSINESS_ID, joined)

    def test_entry_points_do_not_log_business_id_or_payload(self):
        for module in (heal_archive, convert_docs_to_video, update_metadata):
            text = _source(module)
            self.assertNotIn(ENV_KEY, text)                    # لا قراءة مباشرة للمتغير
            self.assertNotIn('business_connection_id', text)
            self.assertNotIn('InputChecklist', text)

    def test_entry_points_call_only_the_helper_with_their_operation(self):
        expected = {
            heal_archive: "send_followup_checklist(bot, chat_id, 'heal_archive')",
            convert_docs_to_video: "send_followup_checklist(bot, chat_id, 'convert_all_docs')",
            update_metadata: "send_followup_checklist(bot, chat_id, 'update_metadata')",
        }
        for module, needle in expected.items():
            with self.subTest(module=module.__name__):
                text = _source(module)
                # استيراد محلي + نداء واحد (السطران التاليان)
                self.assertEqual(len(re.findall(r'^\s*send_followup_checklist\(', text, re.M)), 1)
                self.assertIn(needle, text)
                self.assertNotIn('send_checklist(', text)

    def test_final_reports_texts_and_outcomes_are_unchanged_in_source(self):
        self.assertIn('bot.send_message(chat_id, final_text)', _source(heal_archive))
        self.assertIn('bot.send_message(chat_id, final)', _source(convert_docs_to_video))
        self.assertIn('bot.edit_message_text(f"✅ اكتملت إعادة بناء البيانات بنجاح!',
                      _source(update_metadata))


if __name__ == '__main__':
    logging.disable(logging.NOTSET)
    unittest.main()
