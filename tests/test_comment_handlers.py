import support  # noqa: F401

import unittest
from datetime import datetime

import db_manager
from handlers import comment_handlers as ch


class HandlerCase(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.db = support.ScriptedExecuteQuery(db_manager).install()
        self.admins = [support.ADMIN_ID, support.OTHER_ADMIN_ID]
        self.original_admins = list(ch.ADMIN_IDS)
        ch.ADMIN_IDS[:] = self.admins
        self.bot = support.FakeBot()

    def tearDown(self):
        self.db.restore()
        ch.ADMIN_IDS[:] = self.original_admins

    # helpers -------------------------------------------------------------
    def set_state(self, user_id, state, context):
        self.db.script.setdefault(0, [])
        ch_state = {"state": state, "context": context}
        self.original_get_state = getattr(db_manager, 'get_user_state', None)
        db_manager.get_user_state = lambda uid: ch_state
        self.addCleanup(lambda: setattr(db_manager, 'get_user_state', self.original_get_state)
                        if self.original_get_state is not None
                        else delattr(db_manager, 'get_user_state'))

    def clear_calls(self):
        return [c for c in self.db.calls if c[0].startswith("DELETE FROM user_states")]


class OwnershipAndAuthTest(HandlerCase):
    def test_member_cannot_open_other_member_thread(self):
        self.db.script = {0: [support.comment_row(user_id=support.OTHER_MEMBER_ID)]}
        call = support.make_call("thread::7::0", user_id=support.MEMBER_ID)
        ch.open_member_thread(self.bot, call)
        texts = [t for t in self.bot.sent_texts() if t]
        self.assertTrue(any("لا تملك صلاحية" in t for t in texts) or
                        any("صلاحية" in (a[0] or "") for a in self.bot.answers()))

    def test_group_chat_interaction_refused(self):
        call = support.make_call("thread::7::0", user_id=support.MEMBER_ID, chat_type="group")
        ch.open_member_thread(self.bot, call)
        self.assertIn("المحادثة الخاصة", self.bot.answers()[0][0])

    def test_admin_thread_view_requires_private_chat(self):
        call = support.make_call("thread_view::7::0", chat_type="group")
        ch.view_admin_thread(self.bot, call, self.admins)
        self.assertIn("المحادثة الخاصة", self.bot.answers()[0][0])
        self.assertEqual(self.bot.sent_texts(), [])

    def test_non_admin_reply_callback_refused(self):
        call = support.make_call("reply_comment::7", user_id=support.MEMBER_ID)
        ch.handle_reply_comment(self.bot, call, self.admins)
        self.assertIn("للإدارة فقط", self.bot.answers()[0][0])
        self.assertEqual(self.bot.sent_texts(), [])

    def test_malformed_callbacks_do_not_crash(self):
        for data in ("reply_comment", "reply_comment::abc", "thread::", "mark_read::x",
                     "delete_comment::", "cancel_reply::zz", "thread_view::nan::1"):
            call = support.make_call(data)
            ch.handle_reply_comment(self.bot, call, self.admins)
            ch.handle_mark_read(self.bot, call, self.admins)
            ch.handle_cancel_reply(self.bot, call, self.admins)
            ch.view_admin_thread(self.bot, call, self.admins)
            self.assertTrue(self.bot.answers(), f"no answer for {data}")


class ReplyBindingTest(HandlerCase):
    def test_reply_is_bound_to_prompt_message(self):
        self.db.script = {
            0: [support.comment_row()],          # get_comment_by_id
            1: [support.thread_row(5, "member", "hi")],  # get_latest_thread_message
        }
        call = support.make_call("reply_comment::7")
        ch.handle_reply_comment(self.bot, call, self.admins)
        set_call = [c for c in self.db.calls if "INSERT INTO user_states" in c[0]]
        self.assertEqual(len(set_call), 1)
        context = set_call[0][1][2]
        self.assertIn('"comment_id": 7', context)
        self.assertIn('"prompt_message_id"', context)
        prompt = self.bot.calls_of("send_message")[-1]
        prompt_id = prompt[1][0]
        self.assertEqual(prompt_id, support.ADMIN_ID)

    def test_stale_fingerprint_refused_without_saving(self):
        state = {"state": "replying_comment",
                 "context": {"comment_id": 7, "admin_id": support.ADMIN_ID,
                             "updated_before": "5", "prompt_message_id": None}}
        db_manager.get_user_state = lambda uid: state
        self.addCleanup(lambda: delattr(db_manager, 'get_user_state'))
        self.db.script = {0: [support.thread_row(9, "member", "newer")]}
        message = support.make_message("my reply")
        self.assertTrue(ch.process_reply_text(self.bot, message, self.admins))
        self.assertIn("لم يُحفظ", self.bot.last_text())
        self.assertFalse([c for c in self.db.calls if "INSERT INTO video_comment_messages" in c[0]])

    def test_unbound_other_message_text_is_not_sent_to_thread(self):
        state = {"state": "replying_comment",
                 "context": {"comment_id": 7, "admin_id": support.ADMIN_ID,
                             "prompt_message_id": 5000}}
        db_manager.get_user_state = lambda uid: state
        self.addCleanup(lambda: delattr(db_manager, 'get_user_state'))
        message = support.make_message("wrong message", message_id=9999)  # not the prompt
        self.assertTrue(ch.process_reply_text(self.bot, message, self.admins))
        self.assertIn("غير مرتبطة", self.bot.last_text())
        self.assertFalse([c for c in self.db.calls if "INSERT" in c[0]])

    def test_bound_reply_is_saved_then_notified(self):
        state = {"state": "replying_comment",
                 "context": {"comment_id": 7, "admin_id": support.ADMIN_ID,
                             "updated_before": "5", "prompt_message_id": 5000}}
        db_manager.get_user_state = lambda uid: state
        self.addCleanup(lambda: delattr(db_manager, 'get_user_state'))
        self.db.script = {
            0: [support.thread_row(5, "member", "hi")],
            1: [support.comment_row()],
        }
        reply_result = {"status": "ok", "message_id": 9}
        original_reply = db_manager.reply_in_thread
        db_manager.reply_in_thread = lambda cid, aid, body: reply_result
        self.addCleanup(lambda: setattr(db_manager, 'reply_in_thread', original_reply))
        prompt = support.make_message("prompt text", message_id=5000)
        message = support.make_message("answer", reply_to_message=prompt)
        self.assertTrue(ch.process_reply_text(self.bot, message, self.admins))
        texts = self.bot.sent_texts()
        self.assertTrue(any("تم حفظ ردك" in t for t in texts))
        self.assertTrue(self.bot.sent_to(support.MEMBER_ID))

    def test_persisted_but_failed_notification_is_reported_truthfully(self):
        self.db.script = {0: [support.thread_row(5, "member", "hi")], 1: [support.comment_row()]}
        original_reply = db_manager.reply_in_thread
        db_manager.reply_in_thread = lambda cid, aid, body: {"status": "ok", "message_id": 9}
        self.addCleanup(lambda: setattr(db_manager, 'reply_in_thread', original_reply))

        def send_message(chat_id, *args, **kwargs):
            if chat_id == support.MEMBER_ID:
                raise RuntimeError("bot was blocked by the user")
            return support.FakeBot._record(self.bot, "send_message", chat_id, *args, **kwargs)

        self.bot.send_message = send_message
        state = {"state": "replying_comment",
                 "context": {"comment_id": 7, "admin_id": support.ADMIN_ID,
                             "updated_before": None}}
        original_get = getattr(db_manager, 'get_user_state', None)
        db_manager.get_user_state = lambda uid: state
        self.addCleanup(lambda: setattr(db_manager, 'get_user_state', original_get)
                        if original_get is not None else delattr(db_manager, 'get_user_state'))
        ch.process_reply_text(self.bot, support.make_message("answer"), self.admins)
        admin_texts = [t for t in self.bot.sent_texts() if t]
        self.assertTrue(admin_texts)
        joined = "\n".join(admin_texts)
        self.assertIn("محفوظ", joined)
        self.assertNotIn("تم إشعار العضو", joined)

    def test_missing_comment_does_not_notify_member(self):
        state = {"state": "replying_comment",
                 "context": {"comment_id": 7, "admin_id": support.ADMIN_ID}}
        db_manager.get_user_state = lambda uid: state
        self.addCleanup(lambda: delattr(db_manager, 'get_user_state'))
        self.db.script = {0: [support.thread_row(5, "member", "hi")], 1: []}
        ch.process_reply_text(self.bot, support.make_message("answer"), self.admins)
        self.assertEqual(self.bot.sent_to(support.MEMBER_ID), [])


class MarkReadTest(HandlerCase):
    def test_mark_read_keeps_reply_button(self):
        self.db.script = {0: [support.comment_row(is_read=False)], 1: [{"id": 7}]}
        call = support.make_call("mark_read::7")
        ch.handle_mark_read(self.bot, call, self.admins)
        edits = self.bot.markup_for("edit_message_reply_markup")
        self.assertTrue(edits, "keyboard must be refreshed, never removed")
        callbacks = support.button_callbacks(edits[-1])
        self.assertIn("reply_comment::7", callbacks)
        self.assertNotIn("mark_read::7", callbacks)
        self.assertTrue(any(c[0].startswith("UPDATE video_comments SET is_read = TRUE")
                            for c in self.db.calls))

    def test_mark_read_awaiting_reply_stays_waiting(self):
        from handlers import thread_format
        latest = {"id": 4, "author_role": "member", "created_at": datetime(2024, 1, 1, 10, 0)}
        state = thread_format.compute_thread_state(latest, datetime(2024, 1, 1, 9, 0), "admin")
        self.assertEqual(state, thread_format.STATUS_WAITING)
        self.assertIn("ينتظر الرد", thread_format.status_label(state, "admin"))


class MemberThreadTest(HandlerCase):
    def test_member_cannot_write_followup_to_other_thread(self):
        state = {"state": "waiting_comment",
                 "context": {"comment_id": 7, "prompt_id": "p", "prompt_message_id": None}}
        db_manager.get_user_state = lambda uid: state
        self.addCleanup(lambda: delattr(db_manager, 'get_user_state'))
        self.db.script = {0: [{"user_id": support.OTHER_MEMBER_ID}]}
        message = support.make_message("hi", user_id=support.MEMBER_ID)
        ch.process_comment_text(self.bot, message)
        self.assertIn("لا تملك صلاحية", self.bot.last_text())
        self.assertFalse([c for c in self.db.calls if "INSERT INTO" in c[0]])

    def test_followup_appends_without_touching_history(self):
        state = {"state": "waiting_comment",
                 "context": {"comment_id": 7, "prompt_id": "p"}}
        db_manager.get_user_state = lambda uid: state
        self.addCleanup(lambda: delattr(db_manager, 'get_user_state'))
        self.db.script = {0: [{"user_id": support.MEMBER_ID}]}
        original_append = db_manager.append_thread_message
        db_manager.append_thread_message = lambda cid, role, uid, body: 33
        self.addCleanup(lambda: setattr(db_manager, 'append_thread_message', original_append))
        ch.process_comment_text(self.bot, support.make_message("again", user_id=support.MEMBER_ID))
        self.assertIn("تم إرسال رسالتك", self.bot.last_text())
        self.assertFalse([c for c in self.db.calls if "comment_text =" in c[0]])

    def test_new_comment_uses_caller_identity_from_command(self):
        self.db.script = {0: [], 1: [{"count": 0}]}
        ch.show_user_comments(self.bot, support.MEMBER_ID, 0,
                              message=support.make_message("/my_comments", user_id=support.MEMBER_ID))
        sql = self.db.normalized()[0]
        self.assertEqual(self.db.calls[0][1][0], support.MEMBER_ID)
        self.assertIn("WHERE c.user_id = %s", sql)

    def test_show_all_comments_does_not_raise_name_error(self):
        self.db.script = {0: [], 1: [{"count": 0}], 2: [{"count": 0}]}
        ch.show_all_comments(self.bot, support.ADMIN_ID, self.admins, 0, unread_only=True)
        self.assertIn("لا توجد تعليقات", self.bot.last_text())

    def test_show_all_comments_renders_admin_list(self):
        self.db.script = {
            0: [support.comment_row(comment_id=7, caption="قيد الانتظار")],
            1: [{"count": 1}],       # get_all_comments total
            2: [{"count": 1}],       # get_unread_comments_count
            3: [support.thread_row(2, "member", "أرجو التوضيح")],
        }
        ch.show_all_comments(self.bot, support.ADMIN_ID, self.admins, 0, unread_only=False)
        texts = [t for t in self.bot.sent_texts() if t]
        self.assertIn("غير المقروءة: 1", "\n".join(texts))
        self.assertTrue(any("محادثة #7" in t and "أرجو التوضيح" in t for t in texts), texts)
        self.assertFalse(any("حدث خطأ" in t for t in texts), texts)
        callbacks = [cb for markup in self.bot.markup_for() for cb in support.button_callbacks(markup)]
        self.assertIn("reply_comment::7", callbacks)
        self.assertIn("mark_read::7", callbacks)

    def test_db_layer_tolerates_missing_count_row(self):
        # صف ناقص/مختلف الشكل لا يرفع KeyError في طبقة قاعدة البيانات
        self.db.script = {0: [], 1: [], 2: []}
        comments, total = db_manager.get_all_comments(0, unread_only=False)
        self.assertEqual((comments, total), ([], 0))
        self.assertEqual(db_manager.get_unread_comments_count(), 0)

    def test_messages_have_real_newlines_and_no_literal_backslash_n(self):
        self.db.script = {0: [support.comment_row()], 1: [support.thread_row(2, "member", "x")],
                          2: [{"count": 1}]}
        ch.show_all_comments(self.bot, support.ADMIN_ID, self.admins)
        for text in self.bot.sent_texts():
            self.assertNotIn("\\n", text)


class EscapingTest(HandlerCase):
    def test_admin_summary_escapes_html(self):
        self.db.script = {
            0: [support.comment_row(caption="<b>evil</b> & co", username="a&b")],
            1: [{"count": 1}],
            2: [{"count": 1}],
            3: [support.thread_row(2, "member", "<script>alert(1)</script> & x")],
        }
        ch.show_all_comments(self.bot, support.ADMIN_ID, self.admins)
        joined = "\n".join(self.bot.sent_texts())
        self.assertNotIn("<script>", joined)
        self.assertIn("&lt;script&gt;", joined)
        self.assertIn("&amp; x", joined)
        self.assertIn("a&amp;b", joined)


if __name__ == "__main__":
    unittest.main()
