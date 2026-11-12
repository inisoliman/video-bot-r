"""Phase 7: Mini App Telegram WebApp SDK integrations (offline contract tests).

The production page (``miniapp.render_page()``) must keep exactly one official SDK
``script src`` and add six real, feature-detected integrations:

1. fullscreen — user-click toggle (``requestFullscreen`` / ``exitFullscreen``), state
   read from ``isFullscreen``, ``expand`` fallback, never requested on load;
2. BackButton — one ``onClick`` registration when present; ``close`` or
   ``history.back``; control shown only when supported; never breaks OPEN's close;
3. safe area — ``safeAreaInset`` / ``contentSafeAreaInset`` mapped to CSS variables
   with 0 defaults, ``safeAreaChanged`` / ``contentSafeAreaChanged`` via ``onEvent``,
   missing values/events tolerated;
4. HapticFeedback — guarded calls, ``impactOccurred("light")`` on the primary
   user actions, ``notificationOccurred("success")`` only after confirmed delivery;
5. addToHomeScreen — toolbar action only when available, otherwise clearly disabled;
6. shareMessage — ``shareMessage`` else ``openTelegramLink`` with an HTTPS
   ``t.me/share/url`` URL built from ``location.origin + location.pathname`` only.

No new external script/library is added. The served page is executed for real in the
locally installed Playwright Chromium with injected WebApp stubs that record calls;
every non-localhost request is aborted and the two library GETs / the OPEN POST are
answered in-process. The tests are skipped only when Playwright/Chromium is missing.
"""

import json
import os
import re
import sys
import unittest
from urllib.parse import parse_qs, urlparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # package mode
    from . import support
except ImportError:  # top-level mode
    import support

import miniapp  # noqa: E402

TELEGRAM_SDK_URL = "https://telegram.org/js/telegram-web-app.js"
PAGE_URL = "https://example.invalid" + miniapp.URL_PREFIX
PAGE_PATH = miniapp.URL_PREFIX
HOSTILE_QUERY = ("?evil=1&initData=SECRET_INIT_DATA&tgWebAppData=SECRET_TWA"
                 "&start_param=BIZ_ID")
HOSTILE_MARKERS = ("SECRET_INIT_DATA", "SECRET_TWA", "BIZ_ID", "evil=1", "#")
CARD_IDS = ["41", "40"]
VIDEO_ID = "41"
LIBRARY_JSON = json.dumps({
    "items": [{"id": 41, "title": "فيلم", "category": "أفلام", "thumbnail_url": "",
               "views": 3, "duration": "1:00"},
              {"id": 40, "title": "فيلم 40", "category": "أفلام", "thumbnail_url": "",
               "views": 2, "duration": "0:30"}],
    "total": 2, "page": 0, "page_size": 10, "has_more": False,
}).replace("</", "<\\/")
OPEN_OK = json.dumps({"ok": True, "video_id": 41})

PEEK_SHIM = """
window.__initDataReads = 0;
window.__historyBack = 0;
try {
  var originalBack = window.history.back.bind(window.history);
  window.history.back = function () { window.__historyBack += 1; return originalBack(); };
} catch (e) {}
"""

# A WebApp whose only contract is what every existing page already uses: the
# phase 7 code must treat every missing method/property as "unsupported".
OLDER_STUB = """
window.Telegram = { WebApp: {
  initData: "header=SECRET", initDataUnsafe: { user: { id: 500 } }, colorScheme: "dark",
  ready: function () {}, expand: function () {}, close: function () {}
} };
"""

MODERN_STUB = r"""
(function () {
  var rec = window.__rec;
  rec.ready = 0; rec.expand = 0; rec.close = 0;
  rec.fullscreenRequests = 0; rec.exitRequests = 0;
  rec.isFullscreen = false;
  rec.backRegistrations = 0; rec.backCalls = 0; rec.backShown = 0; rec.backHidden = 0;
  rec.haptics = [];
  rec.homeCalls = 0;
  // Failure switches default to off; tests flip them after load (THROWING_SETUP).
  rec.fullscreenThrows = false; rec.hapticThrows = false;
  rec.homeThrows = false; rec.shareThrows = false;
  rec.shareCalls = []; rec.linkCalls = [];
  rec.events = {};
  function fire(name, payload) {
    var list = rec.events[name] || [];
    for (var i = 0; i < list.length; i += 1) { list[i](payload); }
  }
  var sdk = {
    initData: "", initDataUnsafe: {}, colorScheme: "dark",
    ready: function () { rec.ready += 1; },
    expand: function () { rec.expand += 1; },
    close: function () { rec.close += 1; },
    requestFullscreen: function () {
      rec.fullscreenRequests += 1;
      if (rec.fullscreenThrows) throw new Error("fullscreen refused");
      sdk.isFullscreen = true;
      fire('fullscreenChanged', { isFullscreen: true });
    },
    exitFullscreen: function () {
      rec.exitRequests += 1;
      sdk.isFullscreen = false;
      fire('fullscreenChanged', { isFullscreen: false });
    },
    onEvent: function (name, callback) {
      (rec.events[name] = rec.events[name] || []).push(callback);
    },
    offEvent: function () {},
    addToHomeScreen: function () {
      rec.homeCalls += 1;
      if (rec.homeThrows) throw new Error("unavailable");
    },
    shareMessage: function (messageId, callback) {
      if (rec.shareThrows) throw new Error("share refused");
      rec.shareCalls.push(messageId);
      if (typeof callback === "function") {
        try { callback(true); } catch (e) {}
      }
    },
    openTelegramLink: function (url) { rec.linkCalls.push(url); },
    HapticFeedback: {
      impactOccurred: function (style) {
        rec.haptics.push("impact:" + style);
        if (rec.hapticThrows) throw new Error("impact refused");
      },
      notificationOccurred: function (type) {
        rec.haptics.push("notification:" + type);
        if (rec.hapticThrows) throw new Error("notification refused");
      },
      selectionChanged: function () { rec.haptics.push("selection"); }
    },
    BackButton: {
      isVisible: false,
      show: function () { rec.backShown += 1; this.isVisible = true; },
      hide: function () { rec.backHidden += 1; this.isVisible = false; },
      onClick: function (callback) {
        rec.backRegistrations += 1;
        rec.back = callback;
      },
      offClick: function () {}
    }
  };
  Object.defineProperty(sdk, 'isFullscreen', {
    get: function () { return rec.isFullscreen; },
    set: function (value) { rec.isFullscreen = !!value; }
  });
  Object.defineProperty(sdk, 'safeAreaInset', {
    get: function () { return rec.safe; }
  });
  Object.defineProperty(sdk, 'contentSafeAreaInset', {
    get: function () { return rec.contentSafe; }
  });
  window.Telegram = { WebApp: sdk };
  window.__fireSafe = function (which) {
    fire(which === 'safe' ? 'safeAreaChanged' : 'contentSafeAreaChanged', {});
  };
  window.__fireFullscreen = function (value) {
    rec.isFullscreen = value;
    fire('fullscreenChanged', { isFullscreen: value });
  };
  window.__toggleBack = function () { if (rec.back) rec.back(); };
})();
"""

# Runs before the stub (shim first): the stub reads this state object.
MODERN_SHIM = """
window.__rec = { safe: { top: 12, bottom: 0, left: 0, right: 0 },
  contentSafe: { top: 8, bottom: 0, left: 0, right: 0 }, log: [] };
"""

# Applied to the loaded page: the SDK's methods already exist, so these settings hold.
THROWING_SETUP = ("() => { window.__rec.fullscreenThrows = true; "
                  "window.__rec.hapticThrows = true; window.__rec.homeThrows = true; "
                  "window.__rec.shareThrows = true; }")

TRACKER_STUB = """
window.Telegram = { WebApp: new Proxy({
  initDataUnsafe: {}, colorScheme: "dark",
  ready: function () {}, expand: function () {}
}, {
  get: function (target, key) {
    if (key === 'initData') window.__initDataReads += 1;
    return target[key];
  }
}) };
"""


def _load_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    return sync_playwright


SYNC_PLAYWRIGHT = _load_sync_playwright()


class ServedPage:
    """``render_page`` output served as-is; WebApp stubs are injected at request time.

    The page is fetched by Playwright and answered locally, then the ``history.back``
    probe, the test counters and the recording SDK stub are injected immediately after
    ``<head>`` in the order ``PEEK_SHIM``, shim, stub: the shim must define
    ``window.__rec`` *before* the stub registers its SDK methods, so callers pass
    ``open_page(MODERN_STUB, MODERN_SHIM)``. The injected code never contains ``&``
    (``render_page`` would unescape it) and never names a bare ``window`` runtime
    method: the page's shared-name resolver turns those into factories.
    """

    def __init__(self):
        os.environ.setdefault("MINIAPP_BOT_USERNAME", "sample_bot")
        self.rendered = miniapp.render_page()
        for text in (PEEK_SHIM, MODERN_SHIM, OLDER_STUB, MODERN_STUB,
                     TRACKER_STUB):
            assert "&" not in text, "injected test code must not contain '&'"

    def document(self, stub, shim):
        inject = ("<script>" + PEEK_SHIM + shim + "</script>"
                  + "<script>" + stub + "</script>")
        return self.rendered.replace("<head>", "<head>" + inject, 1)


def library_payload(request_url):
    query = parse_qs(urlparse(request_url).query)
    body = json.loads(LIBRARY_JSON)
    body["page"] = int((query.get("page") or ["0"])[0])
    return json.dumps(body)


class RawContract(unittest.TestCase):
    """Served-page contract that needs no browser."""

    @classmethod
    def setUpClass(cls):
        cls.served = ServedPage()

    @property
    def js(self):
        return self.served.rendered.split("<script>", 1)[1]

    def test_exactly_one_official_sdk_script(self):
        html = self.served.rendered
        self.assertEqual(html.count(TELEGRAM_SDK_URL), 1)
        self.assertEqual(html.count(f'src="{TELEGRAM_SDK_URL}"'), 1)
        self.assertEqual(re.findall(r"<script[^>]*\ssrc=\"([^\"]+)\"", html),
                         [TELEGRAM_SDK_URL])

    def test_no_new_library_cdn_or_version_gate(self):
        html = self.served.rendered
        for token in ("cdn.", "unpkg.com", "jsdelivr", "cdnjs", "googleapis.com"):
            self.assertNotIn(token, html, token)
        for pattern in ("versionAtLeast", "isVersionAtLeast", ".version >" ):
            self.assertNotIn(pattern, self.js, pattern)

    def test_fullscreen_is_never_requested_during_init(self):
        js = self.js
        start = js.find("function initTelegram()")
        end = js.find("initTelegram();", start)
        self.assertGreater(start, -1)
        self.assertGreater(end, start)
        init_body = js[start:end]
        self.assertNotIn("Fullscreen", init_body)
        self.assertNotIn("expand()", init_body.replace("try { tg.expand(); } catch (e) {}", ""),
                         "load must still only expand")

    def test_toolbar_is_accessibility_marked(self):
        html = self.served.rendered
        self.assertIn('role="toolbar"', html)
        self.assertIn('id="shareBtn"', html)
        self.assertIn('id="fullscreenBtn"', html)
        self.assertIn('id="homeBtn"', html)
        self.assertIn("aria-live", html)


@unittest.skipUnless(SYNC_PLAYWRIGHT is not None,
                     "Playwright is not installed (pip install playwright && "
                     "python -m playwright install chromium)")
class BrowserContract(unittest.TestCase):
    """Runs the served page in local Chromium with recording WebApp stubs."""

    @classmethod
    def setUpClass(cls):
        cls.served = ServedPage()
        cls._playwright = SYNC_PLAYWRIGHT().start()
        try:
            cls.browser = cls._playwright.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - Chromium may not be downloaded
            cls._playwright.stop()
            raise unittest.SkipTest(f"local Chromium cannot start: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()

    # -- harness -----------------------------------------------------------

    def open_page(self, stub, shim="", url=PAGE_URL, deliver="ok", init_data=False,
                  setup=None):
        is_modern = "__rec" in shim
        page = self.browser.new_page()
        self.addCleanup(page.close)
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        posts = []
        gets = []
        document_url = url.split("?", 1)[0].split("#", 1)[0]

        def respond(route, request):
            if request.url.startswith("data:"):
                return route.continue_()
            if request.url == TELEGRAM_SDK_URL:
                return route.fulfill(status=200, content_type="application/javascript",
                                     body=stub)
            if request.url.split("?", 1)[0] == document_url:
                return route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                     body=self.served.document(stub, shim))
            target = urlparse(request.url)
            if target.path == PAGE_PATH + "/api/library":
                gets.append(parse_qs(target.query))
                return route.fulfill(status=200, content_type="application/json",
                                     body=library_payload(request.url))
            if target.path == PAGE_PATH + "/api/categories":
                return route.fulfill(status=200, content_type="application/json",
                                     body='{"items":[]}')
            if target.path == PAGE_PATH + "/api/open-video":
                posts.append(json.loads(request.post_data or "{}"))
                if deliver == "ok":
                    return route.fulfill(status=200, content_type="application/json",
                                         body=OPEN_OK)
                return route.fulfill(status=502, content_type="application/json",
                                     body='{"error": "delivery_unavailable"}')
            return route.abort()

        def hook_init_data():
            # Hand the page its (fake) signed initData only after the cards loaded,
            # so the delivered state is observable and nothing is posted early.
            if not is_modern:
                return
            page.evaluate("""signed => {
              const tg = window.Telegram.WebApp;
              tg.initData = signed ? 'header=SECRET' : '';
              tg.initDataUnsafe = signed ? { user: { id: 500 } } : {};
            }""", init_data)

        page.route("**/*", lambda route: respond(route, route.request))
        page.goto(url)
        page.wait_for_selector(".card", timeout=10000)
        page.wait_for_timeout(150)
        hook_init_data()
        page.wait_for_timeout(150)
        page.evaluate("() => { window.__expands = 0; "
                      "window.Telegram.WebApp.expand = () => { window.__expands += 1; }; }")
        if setup:
            page.evaluate(setup)
        return page, errors, gets, posts

    def rec(self, page):
        return page.evaluate("() => window.__rec")

    def css_var(self, page, name):
        return page.evaluate(
            "n => getComputedStyle(document.documentElement)"
            ".getPropertyValue(n).trim()", name)

    def _toast(self, page):
        return page.evaluate("() => document.getElementById('toast').textContent")

    def toast(self, page):
        return page.evaluate("() => document.getElementById('toast').textContent")

    # -- 1) fullscreen -----------------------------------------------------

    def test_fullscreen_toggles_only_on_user_click_and_tracks_isFullscreen(self):
        page, errors, _gets, _posts = self.open_page(MODERN_STUB, MODERN_SHIM)
        rec = self.rec(page)
        self.assertEqual(rec["fullscreenRequests"], 0,
                         "fullscreen must never be requested on load")
        button = page.locator("#fullscreenBtn")
        self.assertTrue(button.is_visible())
        self.assertEqual(button.get_attribute("aria-pressed"), "false")

        button.click()
        page.wait_for_timeout(100)
        rec = self.rec(page)
        self.assertEqual(rec["fullscreenRequests"], 1)
        self.assertEqual(rec["exitRequests"], 0)
        self.assertEqual(page.evaluate("() => window.Telegram.WebApp.isFullscreen"), True)
        self.assertEqual(button.get_attribute("aria-pressed"), "true",
                         "the click must update the button from isFullscreen")
        self.assertIn("light", [h.split(":", 1)[1] for h in rec["haptics"]
                                if h.startswith("impact:")],
                      "primary user actions give a light haptic")

        button.click()
        page.wait_for_timeout(100)
        rec = self.rec(page)
        self.assertEqual(rec["fullscreenRequests"], 1)
        self.assertEqual(rec["exitRequests"], 1)
        self.assertEqual(button.get_attribute("aria-pressed"), "false",
                         "the click must clear the state from isFullscreen")

        # An SDK-driven change (the client's own gesture) updates the button state:
        # the next toggle must therefore call exitFullscreen, not requestFullscreen.
        page.evaluate("() => window.__fireFullscreen(true)")
        page.wait_for_timeout(50)
        button.click()
        page.wait_for_timeout(100)
        rec = self.rec(page)
        self.assertEqual(rec["fullscreenRequests"], 1)
        self.assertEqual(rec["exitRequests"], 2)
        self.assertEqual(button.get_attribute("aria-pressed"), "false")
        self.assertEqual(errors, [])

    def test_fullscreen_falls_back_to_expand_when_unavailable(self):
        page, errors, _gets, _posts = self.open_page(OLDER_STUB)
        page.evaluate("""() => {
          window.__expands = 0;
          window.Telegram.WebApp.expand = () => { window.__expands += 1; };
        }""")
        button = page.locator("#fullscreenBtn")
        # An installed client can still expand even without fullscreen support.
        self.assertTrue(button.is_visible())
        button.click()
        page.wait_for_timeout(100)
        self.assertEqual(page.evaluate("() => window.__expands"), 1,
                         "unsupported fullscreen falls back to expand()")
        self.assertEqual(button.get_attribute("aria-pressed"), "false")
        self.assertIn("ملء الشاشة", self.toast(page))
        self.assertEqual(errors, [])

    # -- 2) BackButton -----------------------------------------------------

    def test_back_button_registers_once_and_closes_once(self):
        page, errors, _gets, posts = self.open_page(MODERN_STUB, MODERN_SHIM)
        rec = self.rec(page)
        self.assertEqual(rec["backRegistrations"], 1)
        self.assertEqual(rec["backShown"], 1, "supported BackButton is shown")
        self.assertEqual(posts, [], "nothing is sent before a click")
        # BackButton closes; history.back is only the fallback.
        self.assertEqual(page.evaluate("() => typeof window.__rec.back"), "function",
                         "no onClick handler was registered")
        page.evaluate("() => window.__toggleBack()")
        page.wait_for_timeout(100)
        self.assertEqual(self.rec(page)["close"], 1)
        self.assertEqual(page.evaluate("() => window.__historyBack"), 0)
        self.assertEqual(self.rec(page)["backRegistrations"], 1)
        self.assertEqual(errors, [])

    def test_back_button_unsupported_does_not_error(self):
        page, errors, _gets, _posts = self.open_page(OLDER_STUB)
        self.assertEqual(page.locator(".card").count(), 2)
        self.assertEqual(errors, [])

    def test_open_still_closes_after_confirmed_delivery(self):
        page, errors, _gets, posts = self.open_page(MODERN_STUB, MODERN_SHIM, init_data=True)
        page.locator(f".btn.open[data-video-id='{VIDEO_ID}']").click()
        page.wait_for_function("() => window.__rec.close >= 1", timeout=5000)
        self.assertEqual(len(posts), 1)
        self.assertEqual(self.rec(page)["close"], 1)

    # -- 3) safe area ------------------------------------------------------

    def test_safe_area_values_map_to_css_variables_and_events(self):
        page, errors, _gets, _posts = self.open_page(MODERN_STUB, MODERN_SHIM)
        self.assertEqual(self.css_var(page, "--tg-safe-top"), "12px")
        self.assertEqual(self.css_var(page, "--tg-safe-bottom"), "0px")
        self.assertEqual(self.css_var(page, "--tg-content-safe-top"), "8px")
        self.assertEqual(self.css_var(page, "--tg-content-safe-bottom"), "0px")
        rec = self.rec(page)
        self.assertIn("safeAreaChanged", rec["events"])
        self.assertIn("contentSafeAreaChanged", rec["events"])

        page.evaluate("""() => {
          window.__rec.safe = undefined;
          window.__fireSafe('safe');
          window.__rec.contentSafe = { top: 20, bottom: 0, left: 0, right: 0 };
          window.__fireSafe('content');
        }""")
        page.wait_for_timeout(100)
        self.assertEqual(self.css_var(page, "--tg-safe-top"), "0px",
                         "missing values fall back to 0")
        self.assertEqual(self.css_var(page, "--tg-content-safe-top"), "20px")
        self.assertEqual(errors, [])

    def test_safe_area_defaults_without_sdk_support(self):
        page, errors, _gets, _posts = self.open_page(OLDER_STUB)
        for name in ("--tg-safe-top", "--tg-safe-bottom", "--tg-safe-left",
                     "--tg-safe-right", "--tg-content-safe-top"):
            self.assertEqual(self.css_var(page, name), "0px", name)
        self.assertEqual(errors, [])

    # -- 4) HapticFeedback -------------------------------------------------

    def test_haptics_on_primary_actions_and_only_on_confirmed_delivery(self):
        page, errors, _gets, posts = self.open_page(MODERN_STUB, MODERN_SHIM, init_data=True)
        page.locator(".chip").first.click()
        page.wait_for_timeout(100)
        page.locator(".btn.fav").first.click()
        page.wait_for_timeout(100)
        impacts = [h for h in self.rec(page)["haptics"] if h.startswith("impact:light")]
        self.assertGreaterEqual(len(impacts), 2, self.rec(page)["haptics"])
        self.assertEqual([h for h in self.rec(page)["haptics"]
                          if h.startswith("notification:")], [],
                         "no success notification before a delivery")
        page.locator(f".btn.open[data-video-id='{VIDEO_ID}']").click()
        page.wait_for_function(
            "() => window.__rec.haptics.indexOf('notification:success') >= 0", timeout=5000)
        self.assertEqual(len(posts), 1)
        self.assertEqual(errors, [])

    def test_failed_delivery_gives_no_success_haptic(self):
        page, errors, _gets, posts = self.open_page(MODERN_STUB, MODERN_SHIM,
                                                   deliver="fail", init_data=True)
        page.locator(f".btn.open[data-video-id='{VIDEO_ID}']").click()
        page.wait_for_function(
            "() => document.getElementById('toast').textContent.indexOf('تعذر') >= 0",
            timeout=5000)
        self.assertEqual(len(posts), 1)
        self.assertEqual([h for h in self.rec(page)["haptics"]
                          if h.startswith("notification:")], [])
        self.assertEqual(self.rec(page)["close"], 0)
        self.assertEqual(errors, [])

    def test_throwing_or_missing_haptics_never_block_actions(self):
        page, errors, _gets, _posts = self.open_page(MODERN_STUB, MODERN_SHIM)
        page.locator(".btn.fav").first.click()
        page.wait_for_timeout(150)
        self.assertEqual(page.locator(".btn.fav").first.get_attribute("aria-pressed"), "true")
        self.assertEqual(errors, [])

        page, errors, _gets, _posts = self.open_page(OLDER_STUB)
        page.locator(".chip").first.click()
        page.wait_for_timeout(250)
        self.assertTrue(page.locator(".chip").first.get_attribute("aria-pressed") == "true")
        self.assertEqual(errors, [])

    # -- 5) addToHomeScreen ------------------------------------------------

    def test_add_to_home_calls_sdk_only_when_available(self):
        page, errors, _gets, _posts = self.open_page(MODERN_STUB, MODERN_SHIM)
        button = page.locator("#homeBtn")
        self.assertTrue(button.is_visible())
        self.assertNotEqual(button.get_attribute("aria-disabled"), "true")
        button.evaluate("b => b.click()")   # programmatic click: nothing overlaps it

        page.wait_for_timeout(100)
        self.assertEqual(self.rec(page)["homeCalls"], 1)
        self.assertIn("notification:success", self.rec(page)["haptics"])
        self.assertEqual(errors, [])

    def test_add_to_home_throwing_is_nonfatal(self):
        page, errors, _gets, _posts = self.open_page(MODERN_STUB, MODERN_SHIM,
                                                     setup=THROWING_SETUP)
        page.evaluate("() => document.getElementById('homeBtn').click()")
        page.wait_for_timeout(150)
        self.assertEqual(self.rec(page)["homeCalls"], 1)
        self.assertIn("notification:error", self.rec(page)["haptics"], self.rec(page))
        self.assertEqual(errors, [])

    def test_add_to_home_unsupported_is_clearly_disabled(self):
        page, errors, _gets, _posts = self.open_page(OLDER_STUB)
        state = page.evaluate("""() => {
          const b = document.getElementById('homeBtn');
          return { disabled: b.disabled, aria: b.getAttribute('aria-disabled'),
                   hidden: b.hidden || getComputedStyle(b).display === 'none',
                   label: b.getAttribute('aria-label') || '',
                   title: b.getAttribute('title') || '' };
        }""")
        # Either hidden or clearly disabled — never a fake workaround button.
        self.assertTrue(state["hidden"] or state["disabled"], state)
        self.assertTrue("غير متاح" in state["label"] + state["title"], state)
        page.evaluate("() => { const b = document.getElementById('homeBtn'); "
                      "b.hidden = false; b.disabled = false; b.click(); }")
        page.wait_for_timeout(250)
        self.assertEqual(self._toast(page), "الإضافة إلى الشاشة الرئيسية غير مدعومة هنا.")
        self.assertEqual(errors, [])

    # -- 6) shareMessage ---------------------------------------------------

    def test_share_uses_shareMessage_with_a_safe_app_url(self):
        page, errors, _gets, _posts = self.open_page(MODERN_STUB, MODERN_SHIM,
                                                    url=PAGE_URL + HOSTILE_QUERY)
        page.locator("#shareBtn").click()
        page.wait_for_timeout(150)
        rec = self.rec(page)
        self.assertEqual(len(rec["shareCalls"]), 1)
        self.assertEqual(rec["linkCalls"], [])
        shared = rec["shareCalls"][0]
        self.assertIsInstance(shared, str)
        self.assertIn(PAGE_URL, shared)
        for marker in HOSTILE_MARKERS:
            self.assertNotIn(marker, shared)
        self.assertIn("تمت مشاركة المكتبة", self.toast(page))
        self.assertEqual(rec["haptics"].count("notification:success"), 1)
        self.assertEqual(errors, [])

    def test_share_falls_back_to_openTelegramLink_without_shareMessage(self):
        stub = MODERN_STUB.replace("shareMessage:", "shareMessageDisabled:")
        page, errors, _gets, _posts = self.open_page(stub, MODERN_SHIM,
                                                    url=PAGE_URL + HOSTILE_QUERY)
        page.locator("#shareBtn").click()
        page.wait_for_timeout(150)
        rec = self.rec(page)
        self.assertEqual(len(rec["linkCalls"]), 1, rec)
        link = rec["linkCalls"][0]
        self.assertTrue(link.startswith("https://t.me/share/url?"), link)
        target = parse_qs(urlparse(link).query)["url"][0]
        self.assertEqual(target.split("?")[0].split("#")[0], PAGE_URL)
        for marker in HOSTILE_MARKERS:
            self.assertNotIn(marker, link)
            self.assertNotIn(marker, target)
        self.assertNotIn(link, self.rec(page)["shareCalls"])

    def test_share_unsupported_is_truthful_and_leaks_nothing(self):
        page, errors, _gets, _posts = self.open_page(OLDER_STUB,
                                                    url=PAGE_URL + HOSTILE_QUERY)
        page.locator("#shareBtn").click()
        page.wait_for_timeout(150)
        message = self.toast(page)
        self.assertTrue(message)
        for marker in HOSTILE_MARKERS:
            self.assertNotIn(marker, message)
        self.assertNotIn("تم مشاركة", message)
        self.assertEqual(page.evaluate("() => window.__historyBack"), 0)
        self.assertEqual(errors, [])

    # -- preserved behavior ------------------------------------------------

    def test_init_data_is_not_sent_by_confined_actions(self):
        page, errors, _gets, posts = self.open_page(TRACKER_STUB, MODERN_SHIM,
                                                   url=PAGE_URL + HOSTILE_QUERY)
        # Clicks that never need a network destination: nothing may carry initData.
        page.evaluate("() => document.getElementById('shareBtn').click()")
        page.evaluate("() => document.getElementById('homeBtn').click()")
        page.evaluate("() => document.getElementById('fullscreenBtn').click()")
        page.wait_for_timeout(150)
        self.assertEqual(posts, [],
                         "share/home/fullscreen never post initData anywhere")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
