"""Offline tests for admin alerts sent from the comment handlers."""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode (`python -m unittest discover -s tests` with tests/__init__.py)
    from . import support
except ImportError:  # top-level mode (`python -m unittest discover -s tests`)
    import support

import unittest

import db_manager
from handlers import comment_handlers as ch


class AdminAlertCase(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.db = support.ScriptedExecuteQuery(db_manager).install()
        self.admins = [support.ADMIN_ID, support.OTHER_ADMIN_ID]
        self.original_admins = list(ch.ADMIN_IDS)
        self.original_bot = ch._admin_notify_bot
        self.original_keys = list(ch._NOTIFIED_KEYS)
        ch.ADMIN_IDS[:] = self.admins
        ch._NOTIFIED_KEYS[:] = []
        self.bot = support.FakeBot()
        ch._admin_notify_bot = self.bot

    def tearDown(self):
        self.db.restore()
        ch.ADMIN_IDS[:] = self.original_admins
        ch._admin_notify_bot = self.original_bot
        ch._NOTIFIED_KEYS[:] = self.original_keys

    # helpers -------------------------------------------------------------
    def set_state(self, state):
        db_manager.get_user_state = lambda uid: state
        self.addCleanup(lambda: delattr(db_manager, 'get_user_state'))

    def admin_sends(self):
        return [call for call in self.bot.calls_of("send_message")
                if call[1] and call[1][0] in self.admins]

    def member_sends(self):
        return [call for call in self.bot.calls_of("send_message")
                if call[1] and call[1][0] == support.MEMBER_ID]


class NewInquiryAlertTest(AdminAlertCase):
    def test_each_admin_gets_one_alert_with_reply_buttons(self):
        self.set_state({"state": "waiting_comment",
                        "context": {"video_id": 3, "prompt_id": "p", "prompt_message_id": None}})
        self.db.script = {
            0: [{"id": 7}],  # add_comment RETURNING id
            1: [{"video_id": 3}],  # get_comment_by_id (عنوان الفيديو من re-fetch)
            2: [{"id": 3, "caption": "فيلم الاختبار", "display_title": "فيلم الاختبار"}],
        }
        # إعادة الجلب من القاعدة لا يستهلك الأوامر المسجلة في اختبارات offline (كل رد جديد يُؤخذ من السكربت)
        original_query = db_manager.execute_query
        db_manager.execute_query = self.db_after_add
        self.addCleanup(setattr, db_manager, 'execute_query', original_query)

        self.assertTrue(ch.process_comment_text(
            self.bot, support.make_message("سؤال مهم", user_id=support.MEMBER_ID)))

        sends = self.admin_sends()
        self.assertEqual(sorted(call[1][0] for call in sends),
                         sorted(self.admins), "one alert per admin, no duplicates")
        for _name, args, kwargs in sends:
            text = args[1]
            self.assertIn("محادثة #7", text)
            self.assertIn("فيلم الاختبار", text)
            self.assertIn(str(support.MEMBER_ID), text)
            self.assertIn("سؤال مهم", text)
            callbacks = support.button_callbacks(kwargs.get("reply_markup"))
            self.assertEqual(callbacks, ["reply_comment::7", "thread_view::7::0"])
        self.assertTrue(self.member_sends(), "member still gets the confirmation")
        self.assertIn("تم إرسال تعليقك", self.bot.sent_to(support.MEMBER_ID)[0])

    def db_after_add(self, query, params=None, fetch=None, commit=False):
        text = " ".join(str(query).split())
        if "INSERT INTO video_comments" in text and fetch == "one":
            return {"id": 7}
        if "FROM video_comments" in text:
            return {"id": 7, "video_id": 3, "user_id": support.MEMBER_ID,
                    "username": "member", "comment_text": None}
        if "FROM video_archive" in text:
            return {"id": 3, "caption": "فيلم الاختبار", "display_title": "فيلم الاختبار"}
        return True if commit else None

    def test_alert_is_sent_only_once_per_inquiry(self):
        self.set_state({"state": "waiting_comment",
                        "context": {"video_id": 3, "prompt_id": "p"}})
        self.db.script = {0: [{"id": 7}], 1: [support.comment_row(comment_id=7)]}
        ch._notify_admins_new_comment(7)
        ch._notify_admins_new_comment(7)
        self.assertEqual(len(self.admin_sends()), len(self.admins))

    def test_failing_admin_send_does_not_break_member_confirmation(self):
        self.set_state({"state": "waiting_comment",
                        "context": {"video_id": 3, "prompt_id": "p"}})
        self.db.script = {0: [{"id": 7}], 1: [support.comment_row(comment_id=7)]}
        self.bot.fail_user_ids = {support.OTHER_ADMIN_ID}
        self.assertTrue(ch.process_comment_text(
            self.bot, support.make_message("سؤال", user_id=support.MEMBER_ID)))
        self.assertIn("تم إرسال تعليقك", self.bot.sent_to(support.MEMBER_ID)[0])
        self.assertEqual(len(self.admin_sends()), len(self.admins))

    def test_missing_comment_row_does_not_raise(self):
        self.db.script = {0: [], 1: []}
        self.assertFalse(ch._notify_admins_new_comment(7, bot=self.bot))

    def test_alert_text_escapes_html_and_clips_excerpt(self):
        row = support.comment_row(comment_id=7, username="a&b", caption="<script>x</script>")
        ch._notify_admins_new_comment(7, comment=dict(row, comment_text="x"))
        sends = self.admin_sends()
        self.assertTrue(sends)
        text = sends[0][1][1]
        self.assertNotIn("<script>", text)
        self.assertNotIn("<b>evil", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("a&amp;b", text)
        self.assertLess(len(text), 1200)

    def test_non_admin_members_never_receive_admin_alerts(self):
        self.set_state({"state": "waiting_comment",
                        "context": {"video_id": 3, "prompt_id": "p"}})
        self.db.script = {0: [{"id": 7}]}
        ch.process_comment_text(self.bot, support.make_message("سؤال", user_id=support.MEMBER_ID))
        for call in self.member_sends():
            callbacks = support.button_callbacks(call[2].get("reply_markup"))
            self.assertEqual(callbacks, [], "member confirmation carries no admin actions")
        recipients = {call[1][0] for call in self.bot.calls_of("send_message")}
        self.assertEqual(recipients, {support.MEMBER_ID, *self.admins})
        self.assertNotIn(support.OTHER_MEMBER_ID, recipients)

    def test_author_who_is_admin_is_not_notified(self):
        row = support.comment_row(comment_id=7, user_id=support.ADMIN_ID)
        self.db.script = {0: [row]}
        ch._notify_admins_new_comment(7, comment=row)
        recipients = {call[1][0] for call in self.bot.calls_of("send_message")}
        self.assertEqual(recipients, {support.OTHER_ADMIN_ID})


class FollowupAlertTest(AdminAlertCase):
    ROW = support.comment_row(comment_id=7)

    def start_followup(self):
        self.set_state({"state": "waiting_comment",
                        "context": {"comment_id": 7, "prompt_id": "p"}})
        self.db.script = {0: [{"user_id": support.MEMBER_ID}]}  # get_thread_owner_id
        original_append = db_manager.append_thread_message
        db_manager.append_thread_message = lambda cid, role, uid, body: 33
        self.addCleanup(lambda: setattr(db_manager, 'append_thread_message', original_append))
        # صف المحادثة يُمرَّر صراحة (المسار الحقيقي يجلبه من القاعدة)
        original_notify = ch._notify_admins_new_comment
        ch._notify_admins_new_comment = (
            lambda comment_id, followup=False, **kwargs:
            original_notify(comment_id, followup=followup, comment=self.ROW, **kwargs))
        self.addCleanup(setattr, ch, '_notify_admins_new_comment', original_notify)

    def test_followup_notifies_admins(self):
        self.start_followup()
        ch.process_comment_text(self.bot, support.make_message("متابعة", user_id=support.MEMBER_ID))

        sends = self.admin_sends()
        self.assertEqual(sorted(call[1][0] for call in sends), sorted(self.admins))
        for _name, args, kwargs in sends:
            self.assertIn("متابعة", args[1])
            self.assertIn("محادثة #7", args[1])
            callbacks = support.button_callbacks(kwargs.get("reply_markup"))
            self.assertIn("reply_comment::7", callbacks)
            self.assertIn("thread_view::7::0", callbacks)
        self.assertIn("تم إرسال رسالتك", self.bot.sent_to(support.MEMBER_ID)[0])

    def test_followup_alert_failure_keeps_member_confirmation(self):
        self.start_followup()
        self.bot.fail_user_ids = set(self.admins)

        ch.process_comment_text(self.bot, support.make_message("متابعة", user_id=support.MEMBER_ID))
        self.assertEqual(len(self.admin_sends()), len(self.admins))
        self.assertIn("تم إرسال رسالتك", self.bot.sent_to(support.MEMBER_ID)[0])


if __name__ == "__main__":
    unittest.main()
