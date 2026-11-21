"""بيانات إطلاق المكتبة (initData) عند فتحها من زر تيليجرام.

المشكلة المُصلَحة: الصفحة كانت تعتمد كلياً على سكربت ``telegram-web-app.js``
لقراءة ``initData``. إذا حُجب telegram.org (شبكة/بروكسي) أو تعذّر تحميل السكربت،
يغيب ``Telegram.WebApp`` فتُعلن الصفحة أن المستخدم خارج تيليجرام وتظهر رسالة
«افتح المكتبة من داخل تيليجرام» عند الضغط على «فتح» رغم أن النافذة نافذة تيليجرام.

تيليجرام يضع البيانات الموقّعة في جزء العنوان ``#tgWebAppData=...`` قبل تحميل
الصفحة، لذلك تقرأها الصفحة الآن محلياً كبديل، ولا تقبل إلا ما يحمل ``hash=``
(توقيع حقيقي)، وتُغلق التطبيق عبر ``TelegramWebviewProxy`` إن غاب SDK.

كل شيء أوفلاين: Chromium محلي (Playwright) وطلبات SDK وAPI معترَضة بالكامل.
"""

import json
import os
import sys
import unittest
from urllib.parse import quote

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode
    from . import support
except ImportError:  # top-level mode
    import support

import miniapp  # noqa: E402

TELEGRAM_SDK_URL = "https://telegram.org/js/telegram-web-app.js"
DOCUMENT_URL = "https://example.invalid/app"
RAW_INIT_DATA = (
    "query_id=AAH-test&user=%7B%22id%22%3A500%2C%22first_name%22%3A%22M%22%7D"
    "&auth_date=1700000000&hash=deadbeef"
)
PROXY_SHIM = """
window.__rec = { closed: 0 };
window.TelegramWebviewProxy = {
  postEvent: function (name, data) {
    if (name === "web_app_close") window.__rec.closed += 1;
  }
};
"""


def _load_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    return sync_playwright


SYNC_PLAYWRIGHT = _load_sync_playwright()


class LaunchDataPageContract(unittest.TestCase):
    """فحص ثابت (بلا متصفح): الصفحة تقرأ بيانات الإطلاق بنفسها."""

    @classmethod
    def setUpClass(cls):
        cls.rendered = miniapp.render_page()
        cls.js = cls.rendered.split("<script>", 1)[1]

    def test_page_reads_the_launch_hash_locally(self):
        for token in ("tgWebAppData", "location.hash", "__telegram__initParams",
                      "decodeURIComponent", "signedInitData"):
            self.assertIn(token, self.js, token)

    def test_page_accepts_only_signed_launch_data(self):
        # بديل {} أو أي قيمة بلا hash= ليست توقيعاً ولا تُرسَل للخادم
        self.assertIn("parseQueryString(raw).hash ? raw", self.js)

    def test_page_closes_through_the_client_proxy_without_sdk(self):
        self.assertIn("TelegramWebviewProxy", self.js)
        self.assertIn("web_app_close", self.js)

    def test_inside_telegram_without_launch_data_has_its_own_message(self):
        self.assertIn("لم تصل بيانات تيليجرام إلى المكتبة", self.js)


class ReplyKeyboardContract(unittest.TestCase):
    """لوحة الرد يجب ألا تحمل ``web_app`` — وإلا فشل الإرسال على Desktop.

    العميل يفتح زر ``web_app`` في لوحة الرد في متصفح خارجي: لا
    ``TelegramWebviewProxy`` ولا ``initData``، فتظهر رسالة «افتح المكتبة من داخل
    تيليجرام». المسار الموثوق زر ``web_app`` داخل رسالة (InlineKeyboardButton)،
    وهو ما يرسله /library وزر اللوحة معاً.
    """

    @classmethod
    def setUpClass(cls):
        from handlers import helpers, user_handlers
        cls.helpers = helpers
        cls.user_handlers = user_handlers

    def test_main_menu_has_no_web_app_button(self):
        import json
        from handlers import helpers, button_styles

        with support.env(BOT_TOKEN="123456:X",
                         APP_URL="https://example.onrender.com"):
            markup = helpers.main_menu("video_bot")
        payload = json.loads(markup.to_json())
        buttons = [button for row in payload["keyboard"] for button in row]
        self.assertTrue(buttons)
        for button in buttons:
            self.assertNotIn("web_app", button,
                             "reply keyboard web_app opens an external browser")

    def test_miniapp_text_button_is_a_plain_keyboard_button(self):
        import json
        from handlers import helpers

        with support.env(BOT_TOKEN="123456:X",
                         APP_URL="https://example.onrender.com"):
            markup = helpers.main_menu("video_bot")
        payload = json.loads(markup.to_json())
        rows = [row for row in payload["keyboard"]
                if any(button.get("text") == helpers.MINIAPP_KEYBOARD_TEXT
                       for button in row)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 1)
        button = rows[0][0]
        self.assertEqual(button["text"], helpers.MINIAPP_KEYBOARD_TEXT)
        # نص فقط: لا web_app ولا أي حقل يفتح نافذة خارجية
        self.assertNotIn("web_app", button)

    def test_library_prompt_carries_the_web_app_button(self):
        from handlers import user_handlers

        with support.env(BOT_TOKEN="123456:X",
                         APP_URL="https://example.onrender.com"):
            markup = user_handlers.build_library_markup()
        self.assertIsNotNone(markup)
        button = [b for row in markup.keyboard for b in row][0]
        self.assertEqual(button.web_app.url, "https://example.onrender.com/app")


@unittest.skipUnless(SYNC_PLAYWRIGHT is not None,
                     "Playwright is not installed (pip install playwright && "
                     "python -m playwright install chromium)")
class LaunchDataBrowserTest(unittest.TestCase):
    """السيناريو الحقيقي: SDK محجوب والبيانات موجودة في جزء العنوان."""

    @classmethod
    def setUpClass(cls):
        cls.rendered = miniapp.render_page()
        cls._playwright = SYNC_PLAYWRIGHT().start()
        try:
            cls.browser = cls._playwright.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - Chromium may be missing
            cls._playwright.stop()
            raise unittest.SkipTest(f"local Chromium cannot start: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()

    def open_page(self, fragment, with_proxy=True):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        posts = []

        def respond(route, request):
            if request.url.split("?")[0] == TELEGRAM_SDK_URL:
                return route.abort()  # telegram.org محجوب: لا SDK إطلاقاً
            if request.url.split("#")[0].split("?")[0] == DOCUMENT_URL:
                shim = ("<script>" + PROXY_SHIM + "</script>") if with_proxy else ""
                body = self.rendered.replace("<head>", "<head>" + shim, 1)
                return route.fulfill(status=200,
                                     content_type="text/html; charset=utf-8",
                                     body=body)
            target = request.url.split("?")[0]
            if target == DOCUMENT_URL + "/api/library":
                payload = {"items": [{"id": 41, "title": "فيلم", "category": "",
                                      "thumbnail_url": "", "views": 3,
                                      "duration": "1:00"}],
                           "total": 1, "page": 0, "page_size": 12,
                           "has_more": False}
                return route.fulfill(status=200, content_type="application/json",
                                     body=json.dumps(payload))
            if target == DOCUMENT_URL + "/api/categories":
                return route.fulfill(status=200, content_type="application/json",
                                     body='{"items": []}')
            if target == DOCUMENT_URL + "/api/open-video":
                posts.append(("open", json.loads(request.post_data or "{}")))
                return route.fulfill(status=200, content_type="application/json",
                                     body='{"ok": true, "video_id": 41}')
            return route.abort()

        page.route("**/*", lambda route: respond(route, route.request))
        page.goto(DOCUMENT_URL + "#" + fragment)
        page.wait_for_selector(".card", timeout=10000)
        return page, errors, posts

    @staticmethod
    def toast(page):
        return page.evaluate("() => document.getElementById('toast').textContent")

    def test_open_uses_hash_init_data_when_the_sdk_is_blocked(self):
        fragment = ("tgWebAppData=" + quote(RAW_INIT_DATA, safe="")
                    + "&tgWebAppVersion=7.0&tgWebAppPlatform=tdesktop")
        page, errors, posts = self.open_page(fragment)
        page.locator(".btn.open").first.click()
        page.wait_for_function("() => window.__rec.closed >= 1", timeout=5000)
        self.assertEqual(errors, [])
        self.assertEqual([kind for kind, _body in posts], ["open"])
        body = posts[0][1]
        self.assertEqual(body["initData"], RAW_INIT_DATA)
        self.assertEqual(body["video_id"], 41)
        self.assertEqual(set(body), {"video_id", "initData", "request_id"})

    def test_placeholder_launch_data_is_not_sent(self):
        page, errors, posts = self.open_page("tgWebAppData=%7B%7D", with_proxy=False)
        page.locator(".btn.open").first.click()
        page.wait_for_timeout(200)
        self.assertEqual(errors, [])
        self.assertEqual(posts, [])
        self.assertIn("افتح المكتبة من داخل تيليجرام", self.toast(page))

    def test_inside_telegram_without_data_gets_an_accurate_message(self):
        page, errors, posts = self.open_page("")
        page.locator(".btn.open").first.click()
        page.wait_for_timeout(200)
        self.assertEqual(errors, [])
        self.assertEqual(posts, [])
        self.assertIn("لم تصل بيانات تيليجرام إلى المكتبة", self.toast(page))


if __name__ == "__main__":
    unittest.main()
