"""المرحلة 4: إشعارات الأدمن في المجموعات كرسائل Ephemeral مستهدفة فقط.

أوفلاين بالكامل (لا Telegram ولا PostgreSQL): قارئ الحالة يُستبدل صراحةً في كل
اختبار، والرسائل تُلتقط عبر ``support.FakeBot`` الحقيقي. المساعد المساعد
``handlers/ephemeral_messages`` يُستورد هنا فقط داخل الاختبارات التي تختبره،
حتى تبقى اختبارات المسارات المسجّلة صالحة قبل إضافته.
"""

import support  # noqa: F401  (يحجب telebot HTTP وpsycopg2 قبل استيراد الإنتاج)

import importlib
import logging
import unittest

import state_manager
from handlers import admin_handlers
from handlers import comment_handlers
from handlers import user_handlers


ADMINS = [support.ADMIN_ID, support.OTHER_ADMIN_ID]
GROUP_CHAT_ID = -1001234567890
GROUP_TYPE = 'group'
SUPERGROUP_TYPE = 'supergroup'

ADMIN_ONLY_NOTICE = "⛔ أوامر الإدارة متاحة في المحادثة الخاصة فقط"
ADMIN_ONLY_REFUSAL = "ليس لديك صلاحية الوصول إلى هذا الأمر."
TITLE_NOTICE = "⛔ هذا الأمر متاح في المحادثة الخاصة فقط"
MEMBER_REFUSAL = "⛔ هذا الأمر للإدارة فقط"

ADMIN_COMMENT_COMMANDS = (
    'comments',
    'delete_all_comments',
    'delete_user_comments',
    'delete_old_comments',
    'comments_stats',
)

# دوال التعليقات المفوض إليها من user_handlers (تُستبدل في الاختبارات)
DELEGATES = (
    ('show_all_comments', 'handle_admin_comments_command'),
    ('handle_delete_all_comments', 'handle_delete_all_comments_command'),
    ('handle_delete_user_comments', 'handle_delete_user_comments_command'),
    ('handle_delete_old_comments', 'handle_delete_old_comments_command'),
    ('handle_comments_stats', 'handle_comments_stats_command'),
    ('start_title_edit', 'handle_title_command'),
    ('process_title_text', 'handle_title_command'),
)


class CapturingBot(support.FakeBot):
    """FakeBot يسجّل كل معالج مسجّل على bot (بالترتيب) مع فلاتره."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.registered = []

    def _capture(self, *args, **kwargs):
        def decorator(func):
            self.registered.append({'func': func, 'args': args, 'kwargs': kwargs})
            return func
        if args and callable(args[0]) and not args[0].__class__ is type and not kwargs:
            # @bot.message_handler ساذج بلا أقواس (نادر هنا لكنه مسموح)
            self.registered.append({'func': args[0], 'args': (), 'kwargs': {}})
            return args[0]
        return decorator

    def message_handler(self, *args, **kwargs):
        return self._capture(*args, **kwargs)


def _ephemeral_module():
    """استيراد مساعد Ephemeral كسولاً (غيابه فشل صادق في الاختبارات الخاصة به)."""
    return importlib.import_module('handlers.ephemeral_messages')


def _commands_of(entry):
    commands = entry['kwargs'].get('commands') or []
    return tuple(str(name).lower() for name in commands)


class Phase4Base(support.OfflineTestCase):
    """قاعدة مشتركة: وحدة المساعد محمّلة صراحةً وقارئ الحالة مستبدل في كل الوحدات."""

    def setUp(self):
        super().setUp()
        self.state_seen = []
        self.state_row = None
        self.helper = _ephemeral_module()
        # سجل زائف صامت في الاختبارات (لا استعلام بيانات، لا ضجيج لوغ)
        self.silent_db = {}
        for name in ('get_all_comments', 'get_unread_comments_count',
                     'get_comments_stats', 'delete_all_comments',
                     'delete_user_comments', 'delete_old_comments',
                     'get_video_title_source'):
            had = name in vars(comment_handlers.db)
            original = getattr(comment_handlers.db, name, None)
            setattr(comment_handlers.db, name, self._silent_call(name))
            self.addCleanup(self._restore_db, name, original, had)
        self._install_state_reader()

    def _silent_call(self, name):
        def recorder(*args, **kwargs):
            self.silent_db[name] = args
            if name == 'get_comments_stats':
                return {'total_comments': 0, 'unread_comments': 0,
                        'replied_comments': 0, 'unique_users': 0}
            if name == 'get_unread_comments_count':
                return 0
            if name == 'get_video_title_source':
                return None
            if name == 'get_all_comments':
                return [], 0
            return 0
        return recorder

    @staticmethod
    def _restore_db(name, original, had):
        if had:
            setattr(comment_handlers.db, name, original)
        elif hasattr(comment_handlers.db, name):
            delattr(comment_handlers.db, name)

    def _install_state_reader(self):
        # القارئ مرتبط صراحةً في كل وحدة تستهلكه (لا اتصال قاعدة في الاختبار)
        owners = [state_manager, self.helper]
        for owner in (user_handlers, admin_handlers):
            if hasattr(owner, 'read_state_row'):
                owners.append(owner)
        originals = [(owner, owner.read_state_row) for owner in owners]
        reader = self._reader
        for owner in owners:
            owner.read_state_row = reader
        self.patched_readers = owners
        self.addCleanup(self._restore_state, originals)

    def _reader(self, user_id):
        self.state_seen.append(user_id)
        return self.state_row

    def _restore_state(self, originals):
        for owner, original in originals:
            owner.read_state_row = original

    def install_ephemeral_module(self):
        """ربط المساعد بالوحدات التي تستدعيه كما يفعل الاستيراد الحقيقي."""
        module = self.helper
        for owner in (admin_handlers, user_handlers):
            module_name = getattr(getattr(owner, 'ephemeral_messages', None),
                                  '__name__', None)
            if module_name == 'handlers.ephemeral_messages':
                continue
            had = hasattr(owner, 'ephemeral_messages')
            original = getattr(owner, 'ephemeral_messages', None)
            setattr(owner, 'ephemeral_messages', module)
            self.addCleanup(self._restore_attr, owner, original, had)
        return module

    @staticmethod
    def _restore_attr(owner, original, had):
        if had:
            setattr(owner, 'ephemeral_messages', original)
        elif hasattr(owner, 'ephemeral_messages'):
            delattr(owner, 'ephemeral_messages')

    # --- تسجيل المعالج الحقيقي ---
    def admin_bot(self):
        bot = CapturingBot()
        admin_handlers.register(bot, ADMINS)
        return bot

    def user_bot(self):
        bot = CapturingBot()
        user_handlers.register(bot, '-1009876543210', ADMINS)
        return bot

    def admin_command_handler(self, command):
        bot = self.admin_bot()
        for entry in bot.registered:
            if command in _commands_of(entry):
                return bot, entry['func']
        raise AssertionError(f'no registered admin handler for /{command}')

    def user_command_handler(self, command):
        bot = self.user_bot()
        for entry in bot.registered:
            if command in _commands_of(entry):
                return bot, entry['func']
        raise AssertionError(f'no registered user handler for /{command}')

    # --- استعلامات ---
    @staticmethod
    def ephemeral_sends(bot):
        return [(args[0], args[1], kwargs) for name, args, kwargs in bot.calls
                if name == 'send_message' and kwargs.get('ephemeral_message_parameters') is not None]

    @staticmethod
    def public_sends(bot):
        return [(args[0], args[1], kwargs) for name, args, kwargs in bot.calls
                if name == 'send_message' and kwargs.get('ephemeral_message_parameters') is None]

    def assert_only_ephemeral_to_owner(self, bot, owner_id=support.ADMIN_ID,
                                       chat_id=GROUP_CHAT_ID, contains='المحادثة الخاصة'):
        self.assertEqual(self.public_sends(bot), [], f'public send leaked: {bot.calls}')
        self.assertEqual(bot.calls_of('reply_to'), [], f'public reply_to leaked: {bot.calls}')
        sends = self.ephemeral_sends(bot)
        self.assertEqual(len(sends), 1, sends)
        sent_chat, text, kwargs = sends[0]
        self.assertEqual(sent_chat, chat_id)
        self.assertIn(contains, text)
        self.assertEqual(kwargs.get('parse_mode'), 'HTML')
        params = kwargs['ephemeral_message_parameters']
        self.assertEqual(params.to_dict(), {'receiver_user_id': owner_id})
        self.assertNotIn('receiver_user_id', kwargs)
        self.assertNotIn('reply_to_message_id', kwargs)

    def assert_nothing_sent(self, bot):
        self.assertEqual(bot.calls, [])


# ---------------------------------------------------------------------------
# 1) المساعد نفسه: تسلسل الرسالة المستهدفة، المالك، ولا بديل عام
# ---------------------------------------------------------------------------

class EphemeralHelperTest(Phase4Base):
    NOTICE = '<b>تنبيه أدمن</b> & نص'

    def setUp(self):
        super().setUp()
        self.helper = _ephemeral_module()

    def send(self, bot, message, admin_ids=ADMINS, text=None):
        return self.helper.send_admin_notice(
            bot, message, self.NOTICE if text is None else text, admin_ids)
    def group_message(self, chat_type=GROUP_TYPE, user_id=support.ADMIN_ID, text='/admin'):
        return support.make_message(text, user_id=user_id, chat_type=chat_type,
                                    chat_id=GROUP_CHAT_ID)

    def test_helper_accepts_required_four_arguments_only(self):
        import inspect
        signature = inspect.signature(self.helper.send_admin_notice)
        params = list(signature.parameters.values())
        self.assertEqual([p.name for p in params], ['bot', 'message', 'text', 'admin_ids'])
        for param in params:
            self.assertIs(param.default, inspect.Parameter.empty)

    def test_group_and_supergroup_serialize_exact_ephemeral_receiver(self):
        for chat_type in (GROUP_TYPE, SUPERGROUP_TYPE):
            with self.subTest(chat_type=chat_type):
                bot = support.FakeBot()
                self.assertTrue(self.send(bot, self.group_message(chat_type)))
                sends = self.ephemeral_sends(bot)
                self.assertEqual(len(sends), 1)
                sent_chat, text, kwargs = sends[0]
                self.assertEqual(sent_chat, GROUP_CHAT_ID)
                self.assertEqual(text, self.NOTICE)
                self.assertEqual(kwargs['parse_mode'], 'HTML')
                self.assertEqual(kwargs['ephemeral_message_parameters'].to_dict(),
                                 {'receiver_user_id': support.ADMIN_ID})
                self.assertEqual(set(kwargs), {'parse_mode', 'ephemeral_message_parameters'})
                self.assertEqual(self.public_sends(bot), [])

    def test_no_state_notice_is_allowed(self):
        self.state_row = None
        bot = support.FakeBot()
        self.assertTrue(self.send(bot, self.group_message()))
        self.assertEqual(len(self.ephemeral_sends(bot)), 1)
        self.assertEqual(self.state_seen, [support.ADMIN_ID])

    def test_same_owner_state_is_accepted(self):
        self.state_row = {'state': 'waiting_broadcast_message',
                          'context': {'admin_id': support.ADMIN_ID, 'text': 'secret'}}
        bot = support.FakeBot()
        self.assertTrue(self.send(bot, self.group_message()))
        self.assertEqual(len(self.ephemeral_sends(bot)), 1)
        self.assertEqual(self.state_seen, [support.ADMIN_ID])

    def test_state_without_owner_key_is_not_owned(self):
        self.state_row = {'state': 'waiting_category_name', 'context': {'parent_id': 4}}
        bot = support.FakeBot()
        self.assertTrue(self.send(bot, self.group_message()))
        self.assertEqual(len(self.ephemeral_sends(bot)), 1)

    def test_mismatched_owned_state_fails_closed_without_send(self):
        self.state_row = {'state': 'waiting_broadcast_message',
                          'context': {'admin_id': support.OTHER_ADMIN_ID}}
        bot = support.FakeBot()
        self.assertFalse(self.send(bot, self.group_message()))
        self.assert_nothing_sent(bot)

    def test_none_owner_in_context_is_not_a_free_pass(self):
        self.state_row = {'state': 'waiting_broadcast_message', 'context': {'admin_id': None}}
        bot = support.FakeBot()
        self.assertFalse(self.send(bot, self.group_message()))
        self.assert_nothing_sent(bot)

    def test_state_reader_failure_fails_closed(self):
        def boom(user_id):
            raise RuntimeError('db down')
        original = self.helper.read_state_row
        self.helper.read_state_row = boom
        self.addCleanup(setattr, self.helper, 'read_state_row', original)
        bot = support.FakeBot()
        self.assertFalse(self.send(bot, self.group_message()))
        self.assert_nothing_sent(bot)

    def test_non_admin_in_group_gets_no_ephemeral(self):
        bot = support.FakeBot()
        self.assertFalse(self.send(bot, self.group_message(user_id=support.MEMBER_ID)))
        self.assert_nothing_sent(bot)

    def test_private_chat_is_not_an_ephemeral_target(self):
        bot = support.FakeBot()
        message = support.make_message('/admin', chat_type='private')
        self.assertFalse(self.send(bot, message))
        self.assert_nothing_sent(bot)

    def test_other_chat_types_are_rejected(self):
        for chat_type in ('channel', None, ''):
            with self.subTest(chat_type=chat_type):
                bot = support.FakeBot()
                message = support.make_message('/admin', chat_type=chat_type,
                                               chat_id=GROUP_CHAT_ID)
                self.assertFalse(self.send(bot, message))
                self.assert_nothing_sent(bot)

    def test_missing_from_user_is_rejected(self):
        bot = support.FakeBot()
        message = support.make_message('/admin', chat_type=GROUP_TYPE, chat_id=GROUP_CHAT_ID)
        message.from_user = None
        self.assertFalse(self.send(bot, message))
        self.assert_nothing_sent(bot)

    def test_send_failure_has_no_public_fallback_and_logs_type_only(self):
        bot = support.FakeBot(raise_on=('send_message',))
        with self.assertLogs('handlers.ephemeral_messages', level='DEBUG') as captured:
            result = self.send(bot, self.group_message())
        self.assertFalse(result)
        # المحاولة الوحيدة هي إرسال ephemeral؛ لا reply_to ولا إرسال عام
        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(bot.calls[0][0], 'send_message')
        self.assertIsNotNone(bot.calls[0][2].get('ephemeral_message_parameters'))
        self.assertEqual(self.public_sends(bot), [])
        self.assertEqual(bot.calls_of('reply_to'), [])
        joined = '\n'.join(captured.output)
        self.assertIn('RuntimeError', joined)
        self.assertNotIn('forced failure', joined)
        self.assertNotIn('secret', joined)

    def test_notice_text_and_state_context_are_never_logged(self):
        self.state_row = {'state': 'waiting_broadcast_message',
                          'context': {'admin_id': support.ADMIN_ID, 'text': 'private payload'}}
        bot = support.FakeBot()
        logger = logging.getLogger('handlers.ephemeral_messages')
        records = []

        class Collector(logging.Handler):
            def emit(self, record):
                records.append(record)

        collector = Collector(level=logging.DEBUG)
        previous_level = logger.level
        logger.addHandler(collector)
        logger.setLevel(logging.DEBUG)
        self.addCleanup(logger.removeHandler, collector)
        self.addCleanup(logger.setLevel, previous_level)
        try:
            self.assertTrue(self.send(bot, self.group_message()))
        finally:
            logger.removeHandler(collector)
        # النجاح مسموح بلا لوغ، لكن لا يُسجَّل النص/السياق في أي حالة
        joined = '\n'.join(record.getMessage() for record in records)
        self.assertNotIn('private payload', joined)
        self.assertNotIn(self.NOTICE, joined)


# ---------------------------------------------------------------------------
# 2) غلاف check_admin في admin_handlers: الخاص كما هو، والمجموعة ephemeral فقط
# ---------------------------------------------------------------------------

class AdminWrapperTest(Phase4Base):
    def setUp(self):
        super().setUp()
        self.ephemeral = self.install_ephemeral_module()

    def test_admin_group_notice_is_ephemeral_and_skips_panel(self):
        for chat_type in (GROUP_TYPE, SUPERGROUP_TYPE):
            with self.subTest(chat_type=chat_type):
                bot, handler = self.admin_command_handler('admin')
                message = support.make_message('/admin', chat_type=chat_type,
                                               chat_id=GROUP_CHAT_ID)
                handler(message)
                self.assert_only_ephemeral_to_owner(bot, contains=ADMIN_ONLY_NOTICE)
                self.assertEqual(bot.calls_of('get_me'), [])

    def test_non_admin_group_refusal_stays_ordinary_without_ephemeral(self):
        bot, handler = self.admin_command_handler('admin')
        message = support.make_message('/admin', user_id=support.MEMBER_ID,
                                       chat_type=GROUP_TYPE, chat_id=GROUP_CHAT_ID)
        handler(message)
        self.assertEqual(self.ephemeral_sends(bot), [])
        self.assertEqual(len(bot.calls_of('reply_to')), 1)
        self.assertIn(ADMIN_ONLY_NOTICE, bot.last_text())

    def test_non_admin_private_refusal_is_unchanged(self):
        bot, handler = self.admin_command_handler('admin')
        handler(support.make_message('/admin', user_id=support.MEMBER_ID))
        self.assertEqual(self.ephemeral_sends(bot), [])
        self.assertEqual(len(bot.calls_of('reply_to')), 1)
        self.assertIn(ADMIN_ONLY_REFUSAL, bot.last_text())

    def test_private_admin_panel_is_unchanged(self):
        bot, handler = self.admin_command_handler('admin')
        handler(support.make_message('/admin', user_id=support.ADMIN_ID))
        self.assertEqual(self.ephemeral_sends(bot), [])
        self.assertEqual(len(bot.calls_of('reply_to')) + len(bot.calls_of('send_message')), 1)
        self.assertEqual(bot.calls_of('get_me'), [])  # send_admin_panel لا يستدعي get_me
        text = bot.last_text()
        self.assertIn('لوحة تحكم', text)
        markup = bot.markup_for()
        self.assertTrue(markup and markup[0] is not None)

    def test_mismatched_state_blocks_group_notice(self):
        self.state_row = {'state': 'waiting_broadcast_message',
                          'context': {'admin_id': support.OTHER_ADMIN_ID}}
        bot, handler = self.admin_command_handler('admin')
        message = support.make_message('/admin', chat_type=GROUP_TYPE, chat_id=GROUP_CHAT_ID)
        handler(message)
        self.assert_nothing_sent(bot)

    def test_same_owner_state_still_notifies_ephemerally(self):
        self.state_row = {'state': 'waiting_broadcast_message',
                          'context': {'admin_id': support.ADMIN_ID}}
        bot, handler = self.admin_command_handler('admin')
        message = support.make_message('/admin', chat_type=GROUP_TYPE, chat_id=GROUP_CHAT_ID)
        handler(message)
        self.assert_only_ephemeral_to_owner(bot, contains=ADMIN_ONLY_NOTICE)

    def test_group_send_failure_never_falls_back_publicly(self):
        bot, handler = self.admin_command_handler('admin')
        bot.raise_on.add('send_message')
        message = support.make_message('/admin', chat_type=GROUP_TYPE, chat_id=GROUP_CHAT_ID)
        handler(message)
        self.assertEqual(self.public_sends(bot), [])
        self.assertEqual(bot.calls_of('reply_to'), [])
        self.assertEqual(len(self.ephemeral_sends(bot)), 1)

    def test_title_command_group_does_not_start_edit(self):
        calls = []
        self._stub_delegates(calls)
        bot, handler = self.user_command_handler('title')
        handler(support.make_message('/title 3 عنوان', chat_type=GROUP_TYPE,
                                     chat_id=GROUP_CHAT_ID))
        self.assertEqual(calls, [])
        self.assert_only_ephemeral_to_owner(bot, contains=TITLE_NOTICE)

    def _stub_delegates(self, calls, result=True):
        for name, _owner in DELEGATES:
            original = getattr(comment_handlers, name)
            had = name in vars(comment_handlers)

            def recorder(*args, _name=name, **kwargs):
                calls.append((_name, args[1:] if args else ()))
                return result

            setattr(comment_handlers, name, recorder)
            self.addCleanup(self._restore_delegate, name, original, had)

    @staticmethod
    def _restore_delegate(name, original, had):
        if had:
            setattr(comment_handlers, name, original)
        elif hasattr(comment_handlers, name):
            delattr(comment_handlers, name)


# ---------------------------------------------------------------------------
# 3) user_handlers: /title وأوامر التعليقات الإدارية في المجموعات
# ---------------------------------------------------------------------------

class UserGroupGuardTest(Phase4Base):
    def setUp(self):
        super().setUp()
        self.ephemeral = self.install_ephemeral_module()
        self.delegate_calls = []
        self.writes = []
        original_waiting = user_handlers.set_user_waiting_for_input
        user_handlers.set_user_waiting_for_input = (
            lambda uid, state, context=None: self.writes.append((uid, state, context)) or True)
        self.addCleanup(setattr, user_handlers, 'set_user_waiting_for_input', original_waiting)
        self._stub_delegates()

    def _stub_delegates(self, result=True):
        for name in {name for name, _owner in DELEGATES}:
            had = name in vars(comment_handlers)
            original = getattr(comment_handlers, name, None)

            def recorder(*args, _name=name, **kwargs):
                self.delegate_calls.append(_name)
                return result

            setattr(comment_handlers, name, recorder)
            self.addCleanup(self._restore_delegate, name, original, had)

    @staticmethod
    def _restore_delegate(name, original, had):
        if had:
            setattr(comment_handlers, name, original)
        elif hasattr(comment_handlers, name):
            delattr(comment_handlers, name)

    def test_title_in_group_does_not_start_editing(self):
        for chat_type in (GROUP_TYPE, SUPERGROUP_TYPE):
            with self.subTest(chat_type=chat_type):
                self.delegate_calls.clear()
                self.writes.clear()
                bot, handler = self.user_command_handler('title')
                handler(support.make_message('/title 3 عنوان جديد', chat_type=chat_type,
                                             chat_id=GROUP_CHAT_ID))
                self.assertEqual(self.delegate_calls, [])
                self.assertEqual(self.writes, [])
                self.assert_only_ephemeral_to_owner(bot, contains=TITLE_NOTICE)
                # الحارس لا يقرأ الحالة بنفسه: القراءة الوحيدة داخل المساعد
                self.assertEqual(self.state_seen, [support.ADMIN_ID])
                self.state_seen.clear()

    def test_title_in_private_still_starts_editing(self):
        bot, handler = self.user_command_handler('title')
        handler(support.make_message('/title 3 عنوان جديد'))
        self.assertEqual(self.ephemeral_sends(bot), [])
        self.assertIn('start_title_edit', self.delegate_calls)
        self.assertIn('process_title_text', self.delegate_calls)

    def test_admin_comment_commands_refused_before_delegate_in_groups(self):
        for command in ADMIN_COMMENT_COMMANDS:
            with self.subTest(command=command):
                self.delegate_calls.clear()
                bot, handler = self.user_command_handler(command)
                handler(support.make_message(f'/{command} 123456789', chat_type=GROUP_TYPE,
                                             chat_id=GROUP_CHAT_ID))
                self.assertEqual(self.delegate_calls, [], command)
                self.assert_only_ephemeral_to_owner(bot, contains=ADMIN_ONLY_NOTICE)
                # لا استعلامات بيانات إدارية (الإحصائيات/التعليقات) في المجموعة
                self.assertEqual(bot.calls_of('send_chat_action'), [])

    def test_admin_comment_commands_refused_in_supergroups(self):
        for command in ADMIN_COMMENT_COMMANDS:
            with self.subTest(command=command):
                self.delegate_calls.clear()
                bot, handler = self.user_command_handler(command)
                handler(support.make_message(f'/{command}', chat_type=SUPERGROUP_TYPE,
                                             chat_id=GROUP_CHAT_ID))
                self.assertEqual(self.delegate_calls, [], command)
                self.assert_only_ephemeral_to_owner(bot, contains=ADMIN_ONLY_NOTICE)

    def test_non_admin_comment_commands_keep_ordinary_refusal(self):
        for command in ADMIN_COMMENT_COMMANDS:
            with self.subTest(command=command):
                self.delegate_calls.clear()
                bot, handler = self.user_command_handler(command)
                handler(support.make_message(f'/{command}', user_id=support.MEMBER_ID,
                                             chat_type=GROUP_TYPE, chat_id=GROUP_CHAT_ID))
                self.assertEqual(self.delegate_calls, [], command)
                self.assertEqual(self.ephemeral_sends(bot), [], command)
                self.assertIn(MEMBER_REFUSAL, bot.last_text())
                self.assertEqual(bot.calls_of('reply_to')[0][1], bot.calls_of('reply_to')[-1][1])

    def test_mismatched_owner_state_blocks_group_notice(self):
        self.state_row = {'state': 'waiting_broadcast_message',
                          'context': {'admin_id': support.OTHER_ADMIN_ID}}
        self.delegate_calls.clear()
        bot, handler = self.user_command_handler('comments')
        handler(support.make_message('/comments', chat_type=GROUP_TYPE, chat_id=GROUP_CHAT_ID))
        self.assert_nothing_sent(bot)
        self.assertEqual(self.delegate_calls, [])

    def test_comment_commands_work_in_private(self):
        for command in ADMIN_COMMENT_COMMANDS:
            with self.subTest(command=command):
                self.delegate_calls.clear()
                bot, handler = self.user_command_handler(command)
                handler(support.make_message(f'/{command} 123456789'))
                self.assertEqual(len(self.delegate_calls), 1, self.delegate_calls)
                self.assertEqual(self.ephemeral_sends(bot), [])

    def test_private_non_admin_comment_command_is_still_delegated(self):
        # السلوك الخاص غير مبدَّل: التفويض يحدث كما كان (الحارس الخاص للمجموعة فقط)
        self.delegate_calls.clear()
        bot, handler = self.user_command_handler('comments_stats')
        handler(support.make_message('/comments_stats', user_id=support.MEMBER_ID))
        self.assertEqual(self.delegate_calls, ['handle_comments_stats'])

    # --- دور المالك المخزّن لا يمنح أي صلاحية في الحارس نفسه -------------

    def test_state_owner_not_in_admin_ids_is_not_granted_ephemeral(self):
        # حارس user_handlers يحسم بالأدمن الإداري فقط؛ ملكية الحالة لا ترقّي أحداً.
        # الحالة مملوكة لأدمن آخر (901) وباحثوها مختلفون: العضو 500 خارجه أصلاً،
        # والأدمن الإداري 900 يمرّ للحارس ثم يرفضه المساعد (fail closed) بلا إرسال.
        self.state_row = {'state': 'waiting_broadcast_message',
                          'context': {'admin_id': support.OTHER_ADMIN_ID}}
        for user_id in (support.MEMBER_ID, support.ADMIN_ID):
            for command in ('comments', 'title'):
                with self.subTest(user_id=user_id, command=command):
                    self.delegate_calls.clear()
                    bot, handler = self.user_command_handler(command)
                    handler(support.make_message(f'/{command} 3 نص', user_id=user_id,
                                                 chat_type=GROUP_TYPE,
                                                 chat_id=GROUP_CHAT_ID))
                    self.assertEqual(self.delegate_calls, [])
                    if user_id == support.MEMBER_ID:
                        self.assertEqual(self.ephemeral_sends(bot), [])
                        self.assertEqual(len(bot.calls_of('reply_to')), 1)
                        _name, args, _kwargs = bot.calls_of('reply_to')[0]
                        self.assertEqual(args[1], MEMBER_REFUSAL)
                    else:
                        self.assert_nothing_sent(bot)

    def test_reader_sentinel_never_called_by_user_handlers_guard(self):
        original = state_manager.read_state_row

        def forbidden(user_id):
            raise AssertionError('user_handlers guard must not read state')

        state_manager.read_state_row = forbidden
        self.ephemeral.read_state_row = original
        bot, handler = self.user_command_handler('comments')
        handler(support.make_message('/comments', chat_type=GROUP_TYPE,
                                     chat_id=GROUP_CHAT_ID))
        self.assertEqual(len(self.ephemeral_sends(bot)), 1)

    # --- فشل الإرسال أو قراءة الحالة: لا إرسال عام إطلاقاً -------------

    def test_send_failure_has_no_public_send_for_title_and_comments(self):
        for command in ('title',) + ADMIN_COMMENT_COMMANDS:
            with self.subTest(command=command):
                self.delegate_calls.clear()
                bot, handler = self.user_command_handler(command)
                bot.raise_on.add('send_message')
                handler(support.make_message(f'/{command} 3 نص', chat_type=GROUP_TYPE,
                                             chat_id=GROUP_CHAT_ID))
                self.assertEqual(self.delegate_calls, [], command)
                self.assertEqual(self.public_sends(bot), [], command)
                self.assertEqual(bot.calls_of('reply_to'), [], command)
                ephemeral = self.ephemeral_sends(bot)
                self.assertEqual(len(ephemeral), 1, command)
                self.assertEqual(ephemeral[0][0], GROUP_CHAT_ID, command)

    def test_send_failure_has_no_public_send_in_supergroups(self):
        for command in ('title', 'comments'):
            with self.subTest(command=command):
                bot, handler = self.user_command_handler(command)
                bot.raise_on.add('send_message')
                handler(support.make_message(f'/{command} 3 نص', chat_type=SUPERGROUP_TYPE,
                                             chat_id=GROUP_CHAT_ID))
                self.assertEqual(self.public_sends(bot), [])
                self.assertEqual(bot.calls_of('reply_to'), [])
                self.assertEqual(len(self.ephemeral_sends(bot)), 1)

    def test_state_reader_failure_has_no_public_send(self):
        def boom(user_id):
            raise RuntimeError('db down')

        self.ephemeral.read_state_row = boom
        for command in ('title',) + ADMIN_COMMENT_COMMANDS:
            with self.subTest(command=command):
                self.delegate_calls.clear()
                bot, handler = self.user_command_handler(command)
                handler(support.make_message(f'/{command} 3 نص', chat_type=GROUP_TYPE,
                                             chat_id=GROUP_CHAT_ID))
                self.assertEqual(self.delegate_calls, [], command)
                self.assert_nothing_sent(bot)

    def test_non_admin_refusal_survives_send_and_reader_failure(self):
        def boom(user_id):
            raise RuntimeError('db down')

        self.ephemeral.read_state_row = boom
        for command in ('title',) + ADMIN_COMMENT_COMMANDS:
            with self.subTest(command=command):
                self.delegate_calls.clear()
                bot, handler = self.user_command_handler(command)
                bot.raise_on.add('send_message')
                handler(support.make_message(f'/{command} 3 نص', user_id=support.MEMBER_ID,
                                             chat_type=GROUP_TYPE, chat_id=GROUP_CHAT_ID))
                self.assertEqual(self.delegate_calls, [], command)
                self.assertEqual(self.ephemeral_sends(bot), [], command)
                replies = bot.calls_of('reply_to')
                self.assertEqual(len(replies), 1, command)
                _name, args, _kwargs = replies[0]
                self.assertEqual(args[1], MEMBER_REFUSAL)

    def test_private_behavior_unchanged_under_failures(self):
        def boom(user_id):
            raise RuntimeError('db down')

        self.ephemeral.read_state_row = boom
        bot, handler = self.user_command_handler('title')
        handler(support.make_message('/title 3 عنوان', chat_type='private'))
        self.assertEqual(self.ephemeral_sends(bot), [])
        self.assertIn('start_title_edit', self.delegate_calls)

    def test_my_comments_member_feature_is_untouched(self):
        shown = []
        original = comment_handlers.show_user_comments

        def recorder(*args, **kwargs):
            shown.append(args[1] if len(args) > 1 else kwargs.get('user_id'))

        comment_handlers.show_user_comments = recorder
        had = 'show_user_comments' in vars(comment_handlers)
        self.addCleanup(self._restore_delegate, 'show_user_comments', original, had)
        bot, handler = self.user_command_handler('my_comments')
        handler(support.make_message('/my_comments', chat_type=GROUP_TYPE,
                                     chat_id=GROUP_CHAT_ID))
        self.assertEqual(shown, [support.ADMIN_ID])
        self.assertEqual(self.ephemeral_sends(bot), [])
        self.assertEqual(bot.calls_of('reply_to'), [])


if __name__ == '__main__':
    unittest.main()
