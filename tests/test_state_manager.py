import support  # noqa: F401

import unittest


class StateManagerRoutingTest(support.OfflineTestCase):
    def setUp(self):
        super().setUp()
        import state_manager
        self.module = state_manager
        self.state = {"state": "editing_display_title", "context": {"video_id": 3}}
        self.cleared = []
        self.handled = []
        self.original_get = state_manager.get_user_state
        self.original_clear = state_manager.clear_user_state

        def fake_get(user_id):
            return self.state

        def fake_clear(user_id):
            self.cleared.append(user_id)

        state_manager.get_user_state = fake_get
        state_manager.clear_user_state = fake_clear
        self.manager = state_manager.StateManager()

    def tearDown(self):
        self.module.get_user_state = self.original_get
        self.module.clear_user_state = self.original_clear

    def test_cancel_clears_state_before_dispatch(self):
        message = support.make_message("/cancel")
        bot = support.FakeBot()
        self.assertTrue(self.manager.handle_message(message, bot))
        self.assertEqual(self.cleared, [support.ADMIN_ID])
        self.assertIn("تم إلغاء", bot.last_text())

    def test_editing_title_state_is_not_cleared_before_user_handler(self):
        message = support.make_message("New movie title")
        bot = support.FakeBot()
        self.assertFalse(self.manager.handle_message(message, bot))
        self.assertEqual(self.cleared, [])

    def test_command_inside_text_state_does_not_become_title(self):
        message = support.make_message("/my_comments")
        bot = support.FakeBot()
        self.assertFalse(self.manager.handle_message(message, bot))
        self.assertEqual(self.cleared, [])

    def test_replying_state_delegated_too(self):
        self.state = {"state": "replying_comment", "context": {"comment_id": 7}}
        self.assertFalse(self.manager.handle_message(support.make_message("reply text"), support.FakeBot()))

    def test_registered_state_handler_runs(self):
        self.state = {"state": "custom_state", "context": {}}
        called = []

        def handler(message, bot, context):
            called.append(context)

        self.manager.register_handler("custom_state", handler)
        self.assertTrue(self.manager.handle_message(support.make_message("x"), support.FakeBot()))
        self.assertEqual(called, [{}])


if __name__ == "__main__":
    unittest.main()
