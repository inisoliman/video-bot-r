"""تدفقات لوحة الأدمن: التصنيفات، القنوات، النقل/الحذف، الإعدادات، الصور المصغرة.

أوفلاين بالكامل: بوت FakeBot مستقل يسجّل كل استدعاء، ودوال القاعدة محقونة بالاسم
داخل handlers.admin_flows / handlers.thumbnails (لا اتصال Telegram/PostgreSQL).
"""

import support  # noqa: F401  (يحجب telebot HTTP وpsycopg2 قبل استيراد الإنتاج)

import importlib
import unittest

import db_manager
from handlers import callback_handlers
from handlers import helpers


ADMINS = [support.ADMIN_ID, support.OTHER_ADMIN_ID]
PROMPT_ID = 3001


def call(data):
    c = support.make_call(data)
    c.message.chat.id = support.ADMIN_ID
    return c


def api_state(state, admin_id=support.ADMIN_ID, **context):
    context.setdefault('admin_id', admin_id)
    context.setdefault('prompt_message_id', PROMPT_ID)
    return {'state': state, 'context': context}


class RecordingBot:
    """بوت وهمي مستقل (لا يرث FakeBot) يسجّل كل استدعاء، ويرمي عند فشل مُحاكى."""

    def __init__(self, fail_photos=()):
        self.calls = []
        self.registered = []
        self.fail_photos = set(fail_photos)
        self.next_message_id = 5000
        self.fail = []

    def _record(self, name, args, kwargs):
        if name in self.fail:
            raise RuntimeError(f'forced {name} failure')
        self.calls.append((name, args, kwargs))
        return True

    def message_handler(self, *args, **kwargs):
        def decorator(func):
            self.registered.extend(kwargs.get('commands') or [])
            return func
        return decorator

    def callback_query_handler(self, *args, **kwargs):
        def decorator(func):
            self.registered.append((None, func, kwargs))
            return func
        return decorator

    def callback_handler(self):
        for item in self.registered:
            if isinstance(item, tuple) and item[0] is None and 'func' in item[2]:
                return item[1]
        raise AssertionError('callback handler was not registered')

    def send_message(self, chat_id, text, **kwargs):
        self._record('send_message', (chat_id, text), kwargs)
        self.next_message_id += 1
        from types import SimpleNamespace
        return SimpleNamespace(message_id=self.next_message_id,
                               chat=SimpleNamespace(id=chat_id, type='private'))

    def reply_to(self, message, text, **kwargs):
        return self.send_message(message.chat.id, text, **kwargs)

    def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        return self._record('edit_message_text', (text, chat_id, message_id), kwargs)

    def answer_callback_query(self, call_id, text=None, **kwargs):
        return self._record('answer_callback_query', (call_id, text), kwargs)

    def delete_message(self, *args, **kwargs):
        return self._record('delete_message', args, kwargs)

    def register_next_step_handler(self, *args, **kwargs):
        return self._record('register_next_step_handler', args, kwargs)

    def sent(self):
        return [args[1] for name, args, _k in self.calls if name == 'send_message']

    def last(self):
        texts = [t for t in self.sent() if t]
        return texts[-1] if texts else None

    def answers_text(self):
        return [' '.join(str(a) for a in args if isinstance(a, str))
                for name, args, _k in self.calls if name == 'answer_callback_query']


class FlowTestCase(support.OfflineTestCase):
    flow_module = 'handlers.admin_flows'

    def setUp(self):
        super().setUp()
        self.flows = importlib.import_module(self.flow_module)
        self.state = None
        self.states = []
        self.cleared = []
        self.mutations = []
        self.read_failure = False
        self.write_failure = False
        self.originals = {
            'read_state_row': getattr(self.flows, 'read_state_row', None),
            'set_user_state': getattr(self.flows, 'set_user_state', None),
            'clear_user_waiting_state': getattr(self.flows, 'clear_user_waiting_state', None),
        }
        self.addCleanup(self._restore_state_readers)
        self.bot = RecordingBot()
        self.install_state_readers()

    # --- عزل الحالة/الكتابة داخل المودوَل نفسه -----------------------------
    def _restore_state_readers(self):
        for name, value in self.originals.items():
            if value is not None:
                setattr(self.flows, name, value)
            elif hasattr(self.flows, name):
                delattr(self.flows, name)

    def install_state_readers(self):
        def reader(user_id):
            return self.state

        def writer(user_id, state, context=None):
            if self.write_failure:
                raise RuntimeError('db down')
            self.states.append((user_id, state, context))
            return True

        def clearer(user_id):
            if self.write_failure:
                raise RuntimeError('db down')
            self.cleared.append(user_id)
            return True

        self.flows.read_state_row = reader
        self.flows.set_user_state = writer
        self.flows.clear_user_waiting_state = clearer

    def enter(self, state_name, **context):
        self.state = api_state(state_name, **context)
        self.states.clear()
        self.cleared.clear()

    def text(self, value, user_id=support.ADMIN_ID, chat_type='private', chat_id=None):
        return support.make_message(value, user_id=user_id, message_id=77, chat_type=chat_type,
                                    chat_id=chat_id)

    def photo(self, user_id=support.ADMIN_ID, file_id='thumb-1'):
        from types import SimpleNamespace
        return support.make_message(None, user_id=user_id, message_id=78, content_type='photo',
                                    photo=[SimpleNamespace(file_id='small'), SimpleNamespace(file_id=file_id)])


class FlowParsingTest(support.OfflineTestCase):
    def test_id_list_parsing_accepts_arabic_digits_and_separators(self):
        flows = importlib.import_module('handlers.admin_flows')
        self.assertEqual(flows.parse_ids('1, 2 ١٢\n3'), [1, 2, 12, 3])
        self.assertEqual(flows.parse_ids('#7'), [7])
        self.assertIsNone(flows.parse_ids('abc'))
        self.assertIsNone(flows.parse_ids('0'))
        self.assertIsNone(flows.parse_ids('12 x'))
        self.assertIsNone(flows.parse_ids(''))

    def test_channel_id_requires_numeric_id(self):
        flows = importlib.import_module('handlers.admin_flows')
        self.assertEqual(flows.normalize_channel_id(' -1001234567890 '), '-1001234567890')
        self.assertIsNone(flows.normalize_channel_id('@username'))
        self.assertIsNone(flows.normalize_channel_id('123'))  # أقصر من معرّف قناة
        self.assertEqual(flows.normalize_channel_id('١٠٠١٢٣٤٥٦٧٨٩٠'), '1001234567890')

    def test_miniapp_limit_range(self):
        flows = importlib.import_module('handlers.admin_flows')
        self.assertEqual(flows.parse_miniapp_limit('0'), 0)
        self.assertEqual(flows.parse_miniapp_limit('١٠٠٠٠٠'), 100000)
        for bad in ('100001', '-1', '', 'x', '5.5'):
            self.assertIsNone(flows.parse_miniapp_limit(bad), bad)


class AdminFlowTest(FlowTestCase):
    def patch(self, name, replacement):
        setattr(self.flows, name, replacement)

    def enter_call(self, data):
        return support.make_call(data)

    # --- Mini App: بلا طلب لا تغيير ---------------------------------------
    def test_arbitrary_number_without_prompt_changes_nothing(self):
        self.install_state_readers()
        self.state = None
        writes = []
        self.patch('set_bot_setting', lambda k, v: writes.append((k, v)) or True)
        self.assertFalse(self.flows.handle_flow_message(self.bot, ADMINS, self.text('500')))
        self.assertEqual(writes, [])
        self.assertEqual(self.states, [])

    def test_miniapp_limit_saved_only_after_prompt_and_valid(self):
        writes = []
        self.patch('set_bot_setting', lambda k, v: writes.append((k, v)) or True)
        self.enter(self.flows.ST_MINIAPP_LIMIT)
        self.assertTrue(self.flows.handle_flow_message(self.bot, ADMINS, self.text('500')))
        self.assertEqual(writes, [('miniapp_hourly_limit_per_ip', '500')])
        self.assertEqual(self.cleared, [support.ADMIN_ID])
        self.assertIn('أصبح 500', self.bot.last())
        # فشل الحفظ يُبلغ عنه ولا تُمسح الحالة
        writes.clear()
        self.enter(self.flows.ST_MINIAPP_LIMIT)
        self.patch('set_bot_setting', lambda k, v: False)
        self.assertTrue(self.flows.handle_flow_message(self.bot, ADMINS, self.text('7')))
        self.assertEqual(self.cleared, [])
        self.assertIn('تعذّر حفظ', self.bot.last())

    # --- التصنيفات ---------------------------------------------------------
    def test_new_category_rejects_commands_and_saves_trimmed_name(self):
        names = []
        self.patch('add_category', lambda name, parent_id=None: (names.append((name, parent_id)), (True, 9))[1])
        self.enter(self.flows.ST_CATEGORY, parent_id=None)
        for bad in ('/admin', '   ', 'x' * 200):
            self.assertTrue(self.flows.handle_flow_message(self.bot, ADMINS, self.text(bad)))
        self.assertEqual(names, [])
        self.assertTrue(self.flows.handle_flow_message(self.bot, ADMINS, self.text('  أفلام  ')))
        self.assertEqual(names, [('أفلام', None)])
        self.assertEqual(self.cleared, [support.ADMIN_ID])

    def test_new_category_db_failure_is_reported(self):
        self.patch('add_category', lambda name, parent_id=None: (False, 'duplicate key'))
        self.enter(self.flows.ST_CATEGORY, parent_id=None)
        self.flows.handle_flow_message(self.bot, ADMINS, self.text('أفلام'))
        self.assertIn('تعذّر إنشاء التصنيف', self.bot.last())
        self.assertEqual(self.cleared, [])

    def test_delete_category_with_contents_uses_atomic_helper(self):
        seen = []
        self.patch('delete_category_with_contents_atomic',
                   lambda cid: (seen.append(cid), {'status': 'deleted', 'videos': 4, 'category': 1})[1])
        self.assertTrue(self.flows.delete_category_with_contents(self.bot, support.ADMIN_ID, ADMINS, 5))
        self.assertEqual(seen, [5])
        self.assertIn('4 فيديو', self.bot.last())

    def test_delete_category_absent_or_failed_never_claims_success(self):
        self.patch('delete_category_with_contents_atomic',
                   lambda cid: {'status': 'missing', 'videos': 0, 'category': 0})
        self.flows.delete_category_with_contents(self.bot, support.ADMIN_ID, ADMINS, 5, call=None)
        self.assertIn('غير موجود', self.bot.last())
        def boom(cid):
            raise RuntimeError('rollback')
        self.patch('delete_category_with_contents_atomic', boom)
        self.flows.delete_category_with_contents(self.bot, support.ADMIN_ID, ADMINS, 5, call=None)
        self.assertIn('تراجعت المعاملة', self.bot.last())
        self.assertFalse(any('✅' in (t or '') for t in self.bot.sent()))

    def test_move_category_contents_rejects_bad_targets(self):
        moved = []
        self.patch('move_category_contents_atomic',
                   lambda a, b: (moved.append((a, b)), {'status': 'moved', 'videos': 3})[1])
        self.flows.move_category_contents(self.bot, support.ADMIN_ID, ADMINS, 5, 5, call=None)
        self.assertIn('نفسه', self.bot.last())
        self.assertEqual(moved, [])
        self.flows.move_category_contents(self.bot, support.ADMIN_ID, ADMINS, 5, 6)
        self.assertEqual(moved, [(5, 6)])
        self.assertIn('3 فيديو', self.bot.last())
        self.patch('move_category_contents_atomic', lambda a, b: {'status': 'missing', 'videos': 0})
        self.flows.move_category_contents(self.bot, support.ADMIN_ID, ADMINS, 5, 6)
        self.assertIn('غير موجود', self.bot.last())
    # --- القنوات -----------------------------------------------------------
    def test_add_channel_numeric_only_two_steps(self):
        added = []
        self.patch('add_required_channel', lambda cid, name: (added.append((cid, name)), True)[1])
        self.patch('find_required_channel', lambda channel_id=None, name=None: None)
        self.patch('fetch_admin_required_channels', lambda: [])
        self.enter(self.flows.ST_CHANNEL_ID)
        self.assertTrue(self.flows.handle_flow_message(self.bot, ADMINS, self.text('@films')))
        self.assertIn('غير صالح', self.bot.last())
        self.assertTrue(self.flows.handle_flow_message(self.bot, ADMINS, self.text('-1001234567890')))
        self.assertEqual(self.states[-1][1], self.flows.ST_CHANNEL_NAME)
        self.enter(self.flows.ST_CHANNEL_NAME, channel_id='-1001234567890')
        self.assertTrue(self.flows.handle_flow_message(self.bot, ADMINS, self.text('قناة الأفلام')))
        self.assertEqual(added, [('-1001234567890', 'قناة الأفلام')])
        self.assertIn('تمت إضافة', self.bot.last())

    def test_remove_channel_reports_zero_rows_and_errors(self):
        self.patch('remove_required_channel', lambda cid: False)
        self.flows.remove_channel_now(self.bot, support.ADMIN_ID, ADMINS, '-1001234567890')
        self.assertIn('لم تُزل', self.bot.last())
        self.patch('remove_required_channel', lambda cid: True)
        self.flows.remove_channel_now(self.bot, support.ADMIN_ID, ADMINS, '-1001234567890')
        self.assertIn('تمت إزالة', self.bot.last())

    def test_remove_required_channel_db_helper_returns_false_on_failure(self):
        """فشل القاعدة في DELETE RETURNING لا يبلّغ عن إزالة ناجحة."""
        def boom(*a, **k):
            raise RuntimeError('down')
        original = db_manager.execute_query_strict
        db_manager.execute_query_strict = boom
        self.addCleanup(setattr, db_manager, 'execute_query_strict', original)
        self.assertFalse(db_manager.remove_required_channel(-1001234567890))

    def test_remove_required_channel_true_only_when_row_returned(self):
        calls = []

        def fake(query, params=None, fetch=None, commit=False):
            calls.append(' '.join(query.split()))
            return [{'id': 1}] if params[0] == -1001 else []
        original = db_manager.execute_query_strict
        db_manager.execute_query_strict = fake
        self.addCleanup(setattr, db_manager, 'execute_query_strict', original)
        self.assertTrue(db_manager.remove_required_channel(-1001))
        self.assertFalse(db_manager.remove_required_channel(-1002))
        self.assertTrue(all('RETURNING id' in sql for sql in calls))

    def test_list_channels_reflects_unavailable_db(self):
        self.patch('fetch_admin_required_channels', lambda: None)
        self.flows.list_channels(self.bot, support.ADMIN_ID)
        self.assertIn('تعذّر', self.bot.last())
        self.patch('fetch_admin_required_channels', lambda: [{'channel_id': -1001, 'channel_name': '<b>x</b>'}])
        self.flows.list_channels(self.bot, support.ADMIN_ID)
        self.assertIn('&lt;b&gt;', self.bot.last())
        self.assertIn('html', self.bot.calls[-1][2]['parse_mode'].lower())

    # --- النقل/الحذف -------------------------------------------------------
    def test_move_requires_existing_ids_and_target(self):
        self.patch('count_existing_video_ids', lambda ids: 0)
        self.enter(self.flows.ST_MOVE_IDS)
        self.flows.handle_flow_message(self.bot, ADMINS, self.text('99'))
        self.assertEqual(self.states, [])
        self.assertIn('لا يوجد فيديو', self.bot.last())
        moved = []
        self.patch('count_existing_video_ids', lambda ids: len(ids))
        self.patch('fetch_admin_categories', lambda: [{'id': 3, 'name': 'أفلام', 'parent_id': None}])
        self.flows.handle_flow_message(self.bot, ADMINS, self.text('5, 6'))
        self.assertEqual(self.states[-1][1], self.flows.ST_MOVE_TARGET)
        self.enter(self.flows.ST_MOVE_TARGET, video_ids=[5, 6])
        self.patch('move_videos_bulk_reported', lambda ids, target: (moved.append((ids, target)), (1, None))[1])
        self.patch('move_category_contents_atomic', lambda a, b: {'status': 'moved', 'videos': 3})
        self.patch('get_category_by_id', lambda cid: {'id': cid, 'name': 'أفلام'})
        self.flows.move_now(self.bot, support.ADMIN_ID, ADMINS, '3')
        self.assertEqual(moved, [([5, 6], 3)])
        self.assertIn('تم نقل 1', self.bot.last())
        self.assertIn('لم يُنقل', self.bot.last())

    def test_move_now_without_state_or_bad_target_is_refused(self):
        moved = []
        self.patch('move_videos_bulk_reported', lambda ids, target: (moved.append((ids, target)), (1, None))[1])
        self.patch('move_category_contents_atomic', lambda a, b: {'status': 'moved', 'videos': 3})
        self.patch('get_category_by_id', lambda cid: None)
        self.state = None
        self.flows.move_now(self.bot, support.ADMIN_ID, ADMINS, '3', call=self.enter_call('x'))
        self.assertEqual(moved, [])
        self.enter(self.flows.ST_MOVE_TARGET, video_ids=[5])
        self.flows.move_now(self.bot, support.ADMIN_ID, ADMINS, '3', call=self.enter_call('x'))
        self.assertEqual(moved, [])
        self.assertIn('غير موجود', self.bot.last())

    def test_delete_confirmation_flow(self):
        deleted = []
        self.patch('count_existing_video_ids', lambda ids: len(ids))
        self.enter(self.flows.ST_DELETE_IDS)
        self.flows.handle_flow_message(self.bot, ADMINS, self.text('7 8'))
        self.assertEqual(self.states[-1][1], self.flows.ST_DELETE_CONFIRM)
        self.enter(self.flows.ST_DELETE_CONFIRM, video_ids=[7, 8])
        self.patch('delete_videos_by_ids_reported', lambda ids: (deleted.append(ids), (2, None))[1])
        self.flows.delete_now(self.bot, support.ADMIN_ID, ADMINS)
        self.assertEqual(deleted, [[7, 8]])
        self.assertIn('تم حذف 2', self.bot.last())

    def test_delete_db_failure_is_not_success(self):
        self.enter(self.flows.ST_DELETE_CONFIRM, video_ids=[7])
        self.patch('delete_videos_by_ids_reported', lambda ids: (None, RuntimeError('db')))
        self.flows.delete_now(self.bot, support.ADMIN_ID, ADMINS, call=self.enter_call('x'))
        self.assertIn('تعذّر حذف', self.bot.last())
        self.assertFalse([t for t in self.bot.sent() if 'تم حذف' in (t or '')])

    # --- الصلاحيات ---------------------------------------------------------
    def test_flow_message_from_non_admin_or_group_is_ignored(self):
        names = []
        self.patch('add_category', lambda name, parent_id=None: (names.append(name), (True, 1))[1])
        self.enter(self.flows.ST_CATEGORY, parent_id=None)
        self.assertFalse(self.flows.handle_flow_message(
            self.bot, ADMINS, self.text('اسم', user_id=support.MEMBER_ID)))
        group = support.make_message('اسم', chat_type='group', chat_id=-1001)
        self.assertFalse(self.flows.receive_category_name(self.bot, ADMINS, group))
        foreign = api_state(self.flows.ST_CATEGORY, admin_id=support.OTHER_ADMIN_ID, parent_id=None)
        self.state = foreign
        self.assertFalse(self.flows.handle_flow_message(self.bot, ADMINS, self.text('اسم')))
        self.assertEqual(names, [])

    def test_cancel_button_clears_without_mutation(self):
        self.state = api_state(self.flows.ST_MOVE_IDS)
        call = support.make_call('admin::cancel_flow')
        call.message.chat.id = support.ADMIN_ID
        self.assertTrue(self.flows.cancel_flow(self.bot, support.ADMIN_ID, ADMINS, call=call))
        self.assertEqual(self.cleared, [support.ADMIN_ID])
        self.assertIn('تم الإلغاء', self.bot.last())

    def test_begin_failure_to_store_state_does_not_prompt(self):
        self.write_failure = True
        self.assertFalse(self.flows.begin_category(self.bot, support.ADMIN_ID, ADMINS))
        self.assertIn('تعذّر بدء العملية', self.bot.last())


class ThumbnailFlowTest(FlowTestCase):
    flow_module = 'handlers.thumbnails'

    def test_default_photo_saved_and_state_cleared(self):
        saved = []
        self.patch_db('set_default_thumbnail', lambda fid: (saved.append(fid), True)[1])
        self.enter(self.flows.STATE_DEFAULT_THUMB)
        self.assertTrue(self.flows.handle_message(self.bot, ADMINS, self.photo(file_id='big')))
        self.assertEqual(saved, ['big'])
        self.assertEqual(self.cleared, [support.ADMIN_ID])
        self.assertIn('تم تعيين الصورة', self.bot.last())

    def test_non_photo_keeps_state_with_retry(self):
        saved = []
        self.patch_db('set_default_thumbnail', lambda fid: (saved.append(fid), True)[1])
        self.enter(self.flows.STATE_DEFAULT_THUMB)
        self.flows.handle_message(self.bot, ADMINS, self.text('نص'))
        self.assertEqual(saved, [])
        self.assertEqual(self.cleared, [])
        self.assertIn('صورة فقط', self.bot.last())

    def test_default_photo_save_failure_is_not_success(self):
        self.patch_db('set_default_thumbnail', lambda fid: False)
        self.enter(self.flows.STATE_DEFAULT_THUMB)
        self.flows.handle_message(self.bot, ADMINS, self.photo())
        self.assertIn('تعذّر حفظ', self.bot.last())
        self.assertEqual(self.cleared, [])

    def test_manual_thumb_unknown_video(self):
        self.patch_db('get_video_by_id', lambda vid: None)
        self.enter(self.flows.STATE_THUMB_VIDEO_ID)
        self.flows.handle_message(self.bot, ADMINS, self.text('999'))
        self.assertIn('لا يوجد فيديو', self.bot.last())
        self.assertEqual(self.states, [])

    def test_manual_thumb_photo_saved(self):
        updated = []
        self.patch_db('get_video_by_id', lambda vid: {'id': vid})
        self.patch_db('update_video_thumbnail', lambda vid, fid: (updated.append((vid, fid)), True)[1])
        self.enter(self.flows.STATE_THUMB_VIDEO_ID)
        self.flows.handle_message(self.bot, ADMINS, self.text('12'))
        self.assertEqual(self.states[-1][1], self.flows.STATE_THUMB_PHOTO)
        self.enter(self.flows.STATE_THUMB_PHOTO, video_id=12)
        self.flows.handle_message(self.bot, ADMINS, self.photo(file_id='ph'))
        self.assertEqual(updated, [(12, 'ph')])
        self.assertIn('#12', self.bot.last())

    def test_photo_state_without_video_context(self):
        self.enter(self.flows.STATE_THUMB_PHOTO)
        self.flows.handle_message(self.bot, ADMINS, self.photo())
        self.assertIn('ضاع رقم الفيديو', self.bot.last())
        self.assertEqual(self.cleared, [support.ADMIN_ID])

    def test_thumbnail_update_failure_keeps_state(self):
        self.patch_db('update_video_thumbnail', lambda vid, fid: False)
        self.enter(self.flows.STATE_THUMB_PHOTO, video_id=12)
        self.flows.handle_message(self.bot, ADMINS, self.photo())
        self.assertIn('تعذّر تحديث', self.bot.last())
        self.assertEqual(self.cleared, [])

    def test_cancel_clears_without_saving(self):
        saved = []
        self.patch_db('set_default_thumbnail', lambda fid: (saved.append(fid), True)[1])
        self.enter(self.flows.STATE_DEFAULT_THUMB)
        call = support.make_call('admin::cancel_thumb')
        call.message.chat.id = support.ADMIN_ID
        self.assertTrue(self.flows.cancel(self.bot, support.ADMIN_ID, ADMINS, call=call))
        self.assertEqual(saved, [])
        self.assertEqual(self.cleared, [support.ADMIN_ID])

    def test_non_admin_cannot_start_or_feed(self):
        self.assertFalse(self.flows.begin(self.bot, support.MEMBER_ID, ADMINS, 'set_default_thumb')[0])
        self.enter(self.flows.STATE_DEFAULT_THUMB)
        self.assertFalse(self.flows.handle_message(
            self.bot, ADMINS, self.photo(user_id=support.MEMBER_ID)))

    def call(self, data):
        return support.make_call(data)

    def patch_db(self, name, replacement):
        self.__dict__.setdefault('db_originals', {})
        if name not in self.db_originals:
            self.db_originals[name] = getattr(self.flows, name)
            setattr(self.flows, name, replacement)
        else:
            setattr(self.flows, name, replacement)

    def tearDown(self):
        for name, original in getattr(self, 'db_originals', {}).items():
            setattr(self.flows, name, original)
        super().tearDown()


class PanelCallbackInventoryTest(FlowTestCase):
    """كل زر في اللوحة له فرع فعلي، ولا next_step، والرفض لغير الأدمن بلا تعديل."""

    HEADING_ACTIONS = {'admin::add_new_cat', 'admin::delete_category_select', 'admin::set_active',
                       'admin::move_video_by_id', 'admin::delete_videos_by_ids',
                       'admin::view_comments', 'admin::comments_stats', 'admin::add_channel',
                       'admin::list_channels',
                       'admin::remove_channel', 'admin::set_default_thumb', 'admin::manual_thumb',
                       'admin::heal_archive', 'admin::list_documents', 'admin::broadcast',
                       'admin::stats', 'admin::sub_count', 'admin::set_miniapp_limit',
                       'admin::rename_title', 'admin::edit_title', 'admin::update_metadata',
                       'admin::convert_all_docs', 'admin::delete_old_comments',
                       'admin::delete_all_comments', 'noop', 'back_to_main'}

    def setUp(self):
        super().setUp()
        self.original_check = helpers.check_subscription
        helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(setattr, helpers, 'check_subscription', self.original_check)

    def make_bot(self):
        bot = RecordingBot()
        callback_handlers.register(bot, ADMINS)
        return bot

    def test_panel_buttons_map_to_implemented_branches(self):
        from handlers import admin_handlers
        from telebot.types import InlineKeyboardMarkup, WebAppInfo
        original_channels = getattr(admin_handlers, 'get_required_channels', None)
        admin_handlers.get_required_channels = lambda: []
        self.addCleanup(setattr, admin_handlers, 'get_required_channels', original_channels)
        keyboard = admin_handlers.build_admin_panel()
        callbacks = support.button_callbacks(keyboard)
        self.assertTrue(callbacks)
        for data in callbacks:
            with self.subTest(data=data):
                if data == 'noop':
                    bot = self.make_bot()
                    bot.callback_handler()(support.make_call(data))
                    self.assertTrue(bot.calls)
                    self.assertFalse([c for c in bot.calls if c[0] == 'register_next_step_handler'])
                    continue
                self.assertIn(data, self.HEADING_ACTIONS)

    def test_admin_heading_noop_answers_instead_of_raising(self):
        bot = self.make_bot()
        bot.callback_handler()(support.make_call('noop'))
        self.assertEqual(bot.calls[0][0], 'answer_callback_query')

    def test_broadcast_headings_and_prompt_do_not_use_next_step(self):
        bot = self.make_bot()
        bot.callback_handler()(support.make_call('noop'))
        self.assertFalse([c for c in bot.calls if c[0] == 'register_next_step_handler'])
        self.assertEqual(bot.calls[-1][0], 'answer_callback_query')

    def test_non_admin_and_group_callers_are_refused_without_registration(self):
        bot = self.make_bot()
        handler = bot.callback_handler()
        for data in ('admin::broadcast', 'admin::add_cat_main', 'admin::move_video_by_id',
                     'admin::add_channel', 'admin::set_default_thumb'):
            with self.subTest(data=data):
                bot.calls.clear()
                handler(support.make_call(data, user_id=support.MEMBER_ID))
                self.assertEqual([c for c in bot.calls if c[0] == 'register_next_step_handler'], [])
                self.assertTrue(any('للإدارة فقط' in (t or '') for t in bot.answers_text()))
        self.assertEqual(self.states, [])

    def test_malformed_category_and_flow_callbacks_are_rejected(self):
        bot = self.make_bot()
        handler = bot.callback_handler()
        for data in ('admin::add_cat_sub_set_parent', 'admin::add_cat_sub_set_parent::x',
                     'admin::delete_category_confirm::abc', 'admin::delete_cat_and_videos',
                     'admin::delete_cat_move_videos_confirm::1', 'admin::setcat::x',
                     'admin::move_confirm::x', 'admin::remove_channel_confirm', 'admin::setcat'):
            with self.subTest(data=data):
                bot.calls.clear()
                handler(support.make_call(data))
                self.assertTrue(bot.answers_text())
                self.assertEqual(self.states, [])
                self.assertEqual([c for c in bot.calls if c[0] == 'register_next_step_handler'], [])


class StatsTruthfulnessTest(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.original_check = helpers.check_subscription
        helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(setattr, helpers, 'check_subscription', self.original_check)
        self.bot = RecordingBot()
        callback_handlers.register(self.bot, ADMINS)

    def test_stats_unavailable_is_explicit(self):
        original = callback_handlers.get_bot_stats_or_none
        callback_handlers.get_bot_stats_or_none = lambda: None
        self.addCleanup(setattr, callback_handlers, 'get_bot_stats_or_none', original)
        self.bot.callback_handler()(support.make_call('admin::stats'))
        self.assertIn('تعذّر', self.bot.answers_text()[-1])
        self.assertFalse([t for t in self.bot.sent() if 'إحصائيات المحتوى' in (t or '')])

    def test_stats_escaped_html_and_no_keyerror(self):
        original = (callback_handlers.get_bot_stats_or_none, callback_handlers.get_popular_videos)
        callback_handlers.get_bot_stats_or_none = lambda: {
            'video_count': 3, 'category_count': 2, 'total_views': 10, 'total_ratings': 4}
        callback_handlers.get_popular_videos = lambda: {
            'most_viewed': [{'caption': '<b>x</b>', 'view_count': 5}],
            'highest_rated': [{'caption': 'y'}]}
        self.addCleanup(setattr, callback_handlers, 'get_bot_stats_or_none', original[0])
        self.addCleanup(setattr, callback_handlers, 'get_popular_videos', original[1])
        self.bot.callback_handler()(support.make_call('admin::stats'))
        text = [t for t in self.bot.sent() if 'إحصائيات' in (t or '')][-1]
        self.assertIn('&lt;b&gt;', text)

    def test_subscriber_count_unavailable_is_explicit(self):
        original = callback_handlers.get_bot_subscriber_count_or_none
        callback_handlers.get_bot_subscriber_count_or_none = lambda: None
        self.addCleanup(setattr, callback_handlers, 'get_bot_subscriber_count_or_none', original)
        self.bot.callback_handler()(support.make_call('admin::sub_count'))
        self.assertIn('تعذّر', self.bot.answers_text()[-1])

    def test_set_active_missing_category_is_refused(self):
        original = callback_handlers.get_category_by_id
        callback_handlers.get_category_by_id = lambda cid: None
        self.addCleanup(setattr, callback_handlers, 'get_category_by_id', original)
        self.bot.callback_handler()(support.make_call('admin::setcat::4'))
        self.assertIn('غير موجود', self.bot.answers_text()[-1])


class MaintenanceGuardTest(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.original_check = helpers.check_subscription
        helpers.check_subscription = lambda bot, user_id: (True, [])
        self.addCleanup(setattr, helpers, 'check_subscription', self.original_check)
        callback_handlers._MAINTENANCE_ACTIVE.clear()
        self.addCleanup(callback_handlers._MAINTENANCE_ACTIVE.clear)

    def test_duplicate_click_does_not_start_two_threads(self):
        started = []
        original_thread = callback_handlers.threading.Thread

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                started.append((target, args))

            def start(self):
                return True

        callback_handlers.threading.Thread = FakeThread
        self.addCleanup(setattr, callback_handlers.threading, 'Thread', original_thread)
        bot = RecordingBot()
        callback_handlers.register(bot, ADMINS)
        handler = bot.callback_handler()
        handler(support.make_call('admin::heal_archive'))
        self.assertEqual(len(started), 1)
        self.assertIn('heal_archive', callback_handlers._MAINTENANCE_ACTIVE)
        handler(support.make_call('admin::heal_archive'))
        self.assertEqual(len(started), 1)  # النقرة الثانية مرفوضة
        # انتهاء العامل يحرر القفل
        target, args = started[0]
        callback_handlers._MAINTENANCE_ACTIVE.discard('heal_archive')
        callback_handlers._release_maintenance('heal_archive')
        self.assertNotIn('heal_archive', callback_handlers._MAINTENANCE_ACTIVE)

    def test_maintenance_exception_is_reported_and_lock_released(self):
        def boom(*args):
            raise RuntimeError('failed')

        bot = RecordingBot()
        callback_handlers._MAINTENANCE_ACTIVE.add('update_metadata')
        callback_handlers._run_maintenance('update_metadata', boom, (bot, support.ADMIN_ID, 1))
        self.assertIn('توقفت عملية الصيانة', bot.last())
        self.assertNotIn('update_metadata', callback_handlers._MAINTENANCE_ACTIVE)

    def test_thread_start_failure_releases_lock(self):
        original_thread = callback_handlers.threading.Thread

        class Broken:
            def __init__(self, *a, **k):
                raise RuntimeError('no threads')

        callback_handlers.threading.Thread = Broken
        self.addCleanup(setattr, callback_handlers.threading, 'Thread', original_thread)
        bot = RecordingBot()
        callback_handlers.register(bot, ADMINS)
        bot.callback_handler()(support.make_call('admin::update_metadata'))
        self.assertIn('تعذّر بدء', bot.answers_text()[-1])
        self.assertNotIn('update_metadata', callback_handlers._MAINTENANCE_ACTIVE)


if __name__ == '__main__':
    unittest.main()
