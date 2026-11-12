"""Browser-level regression check for the Mini App (needs no network, DB or Telegram).

Serves the production ``render_page()`` HTML through a local Flask app, routes browser
API calls to a small in-process HTTP stub, then drives the page with the locally
installed Playwright Chromium:

* NEXT / PREVIOUS change and restore the rendered card ids; the final page disables NEXT;
* a genuinely overlapping stale response cannot overwrite newer content, and a failed
  library request exposes a working retry button;
* the OPEN button posts to ``/app/api/open-video`` with the signed ``initData`` that the
  stubbed official Telegram SDK exposes, sends exactly once on rapid clicks, and closes
  the WebApp only after a confirmed success;
* the production SDK ``script src`` is fulfilled locally, while aborted and delayed SDK
  setup leave the library usable and never fabricate an OPEN request;
* a narrow RTL chips bar consumes wheel input only while it can move, preserves page
  scrolling at both ends, exposes overflow fades, and keeps category filtering intact.

The stub calls the production ``validate_init_data`` HMAC validator; route delivery and
DB behavior are covered by the offline unittest modules. No BOT_TOKEN/DATABASE_URL from
the environment is used (the process is started with them cleared). The official SDK URL
is intercepted and fulfilled locally (or aborted for fallback checks); no request leaves
the machine. Real Telegram in-app initialization and native phone touch gestures cannot
be verified by this offline Chromium harness.

    python tests/miniapp_browser_check.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Offline before any production import: blocks telebot HTTP and psycopg2.connect.
from tests import support  # noqa: E402

import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
from urllib.parse import parse_qs, urlparse, quote  # noqa: E402

import db_manager  # noqa: E402
import miniapp  # noqa: E402
from flask import Flask  # noqa: E402
from handlers import helpers  # noqa: E402

TEST_BOT_TOKEN = "123456:BROWSER-CHECK-TOKEN"
TELEGRAM_SDK_URL = "https://telegram.org/js/telegram-web-app.js"
MEMBER_ID = support.MEMBER_ID
MODERN_PAGE_PATH = "/__modern"                    # phase 7 test-only page route
PAGE_SIZE = db_manager.VIDEOS_PER_PAGE          # must come from the DB layer
TOTAL_VIDEOS = 25                                 # three pages: 10 / 10 / 5
OPEN_VIDEO_ID = 25

STATE_LOCK = threading.Lock()
STATE = {
    "library_requests": [],
    "open_requests": [],
    "tm_data": "",
    "sends": [],
    "deliver_mode": "ok",       # ok | error
    "close_calls": 0,
    "library_mode": "ok",       # ok | slow_extras | reject
    "videos": list(range(TOTAL_VIDEOS, 0, -1)),   # ORDER BY id DESC
    "categories": [{"id": 1, "name": "أفلام", "count": TOTAL_VIDEOS}],
}


# ---------------------------------------------------------------------------
# In-process stub: the browser's fetch() is redirected here by the page shim
# ---------------------------------------------------------------------------

class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # keep the check output clean
        pass

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        args = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        if parsed.path.endswith("/library"):
            with STATE_LOCK:
                STATE["library_requests"].append(args)
                mode = STATE["library_mode"]
                videos = list(STATE["videos"])
            if mode == "slow_extras":
                # Only the stale request is slow; the threaded stub serves the newer one.
                with STATE_LOCK:
                    STATE["library_mode"] = "ok"
                time.sleep(1.5)
            if mode == "reject":
                return self._reply({"error": "server_error"}, 500)
            page = int(args.get("page", "0"))
            start = page * PAGE_SIZE
            rows = [{"id": vid, "caption": f"فيلم {vid}", "display_title": None,
                     "file_name": f"v{vid}.mp4", "metadata": {"duration": 60},
                     "view_count": 3, "thumbnail_file_id": None, "category_id": 1,
                     "category_name": "أفلام"} for vid in videos[start:start + PAGE_SIZE]]
            total = len(videos)
            return self._reply({"items": [{
                "id": row["id"], "title": miniapp._title_for(row), "category": "أفلام",
                "thumbnail_url": "", "views": row["view_count"], "duration": "1:00",
            } for row in rows],
                "total": total, "page": page, "page_size": PAGE_SIZE,
                "has_more": bool(rows) and start + len(rows) < total})
        if parsed.path.endswith("/categories"):
            with STATE_LOCK:
                categories = list(STATE["categories"])
            return self._reply({"items": categories})
        if parsed.path == "/tm":
            with STATE_LOCK:
                tm = STATE["tm_data"]
            return self._reply({"initData": tm})
        if parsed.path == "/state":
            with STATE_LOCK:
                snapshot = dict(STATE)
                snapshot.pop("videos", None)
            return self._reply(snapshot)
        return self._reply({"error": "not_found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.endswith("/open-video"):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._reply({"error": "invalid_body"}, 400)
            with STATE_LOCK:
                STATE["open_requests"].append(body)
                mode = STATE["deliver_mode"]
                verified = miniapp.validate_init_data(body.get("initData") or "",
                                                     bot_token=TEST_BOT_TOKEN)
                video_id = body.get("video_id")
                if not verified:
                    return self._reply({"error": "invalid_init_data"}, 401)
                user_id = (verified.get("user") or {}).get("id")
                if not isinstance(video_id, int) or video_id <= 0:
                    return self._reply({"error": "invalid_video_id"}, 400)
                if video_id not in STATE["videos"]:
                    return self._reply({"error": "not_found"}, 404)
                if mode == "error":
                    return self._reply({"error": "delivery_unavailable"}, 502)
                STATE["sends"].append({"user_id": user_id, "video_id": video_id,
                                       "chat_id": user_id,
                                       "request_id": body.get("request_id")})
            return self._reply({"ok": True, "video_id": video_id})
        return self._reply({"error": "not_found"}, 404)


def make_init_data(bot_token, user_id, auth_date=None):
    """Signed initData exactly per the Telegram WebApp spec."""
    fields = {
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "عضو", "username": "member"},
                           ensure_ascii=False),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return "&".join([f"{key}={quote(fields[key], safe='')}" for key in sorted(fields)]
                    + [f"hash={digest}"])


# The page shim: keeps window.fetch on our stub, records the WebApp close call and
# gives the SDK stub a way to publish (or change) the signed initData.
SHIM = """
<script>
window.__closeCalls = 0;
window.__stubBase = __STUB_BASE__;
window.__realFetch = window.fetch.bind(window);
window.fetch = function (url, options) {
  var target = window.__stubBase + (url.charAt(0) === "/" ? url : "/" + url);
  return window.__realFetch(target, options);
};
window.__sdk = { initData: "", initDataUnsafe: {}, colorScheme: "dark",
  ready: function () {}, expand: function () {}, close: function () {
    window.__closeCalls += 1;
  } };
setInterval(function () {
  window.__realFetch(window.__stubBase + "/tm").then(function (r) { return r.json(); })
    .then(function (data) {
      if (window.__sdk.initData !== data.initData) {
        window.__sdk.initData = data.initData;
        window.__sdk.initDataUnsafe = data.initData
          ? { user: { id: __USER_ID__ } } : {};
      }
    }).catch(function () {});
}, 100);
</script>
"""

SDK_STUB = """
window.Telegram = window.Telegram || {};
window.Telegram.WebApp = window.__sdk;
"""

LATE_SDK_STUB = """
window.__publishSdk = function () {
  window.Telegram = window.Telegram || {};
  window.Telegram.WebApp = window.__sdk;
};
"""

# Phase 7: the modern SDK contract. The page runtime resolves a shared name through
# $sbitmap(name); this getter returns the real value for page data names and, for
# every SDK control method/attribute name, a recorder that behaves like the SDK
# exports it (controls resolve to an object whose prototype is not a browser DOM
# node, so writing through a wrong target throws exactly like the real SDK). The
# stubbed methods below only send their own event data down the channel, exactly
# as the official script does, and nothing ever leaves the machine.
MODERN_RECORDER = """
(function () {
  var rec = window.__sdkRec;
  var MAX = 400;
  function note(label) {
    if (rec.calls.length < MAX) rec.calls.push(label);
  }
  function bounded(name, label) {
    return function () {
      if (label === "method") rec.counts[name] = (rec.counts[name] || 0) + 1;
      note(label + ":" + name);
      return Object.create(Object.prototype, {
        nodeType: { value: 1 }, tagName: { value: "DIV" },
        hidden: { get: function () { return false; }, set: function () {} },
        title: { get: function () { return ""; }, set: function () {} }
      });
    };
  }
  window.__sbmap = function (name) {
    var getter = Object.getOwnPropertyDescriptor(window, "__sbitmap").get;
    if (name in rec.ops) return bounded(name, rec.ops[name]);
    return getter.call(window, name);
  };
})();
"""

# Offline fetch bridge (same contract as SHIM): every relative request of the page is
# answered by the local request stub, never by the Flask app or the network.
MODERN_BRIDGE = """
var __stubBase = __STUB_BASE__;
window.__realFetch = window.fetch.bind(window);
window.fetch = function (url, options) {
  var target = __stubBase + (url.charAt(0) === "/" ? url : "/" + url);
  return window.__realFetch(target, options);
};
"""

MODERN_SETUP = """
window.__sdkRec = { calls: [], counts: {}, haptics: [], handlers: {},
  shareCalls: [], linkCalls: [], safe: __SAFE__, contentSafe: __CONTENT_SAFE__,
  ready: 0, close: 0, expand: 0, backRegistrations: 0, backShown: 0, backHidden: 0,
  fullscreenRequests: 0, exitRequests: 0, isFullscreen: false, homeCalls: 0,
  hapticThrows: false, homeThrows: false, back: null,
  ops: { requestFullscreen: "method", exitFullscreen: "method",
         addToHomeScreen: "method", shareMessage: "method",
         openTelegramLink: "method", onClick: "method" } };
"""

MODERN_SDK_STUB = """
(function () {
  var rec = window.__sdkRec;
  var builders = {
    ready: function () { rec.ready += 1; },
    expand: function () { rec.expand += 1; },
    close: function () { rec.close += 1; },
    requestFullscreen: function () {
      rec.fullscreenRequests += 1;
      rec.isFullscreen = true;
      fire("fullscreenChanged", { isFullscreen: true });
    },
    exitFullscreen: function () {
      rec.exitRequests += 1;
      rec.isFullscreen = false;
      fire("fullscreenChanged", { isFullscreen: false });
    },
    onEvent: function (name, callback) {
      (rec.handlers[name] = rec.handlers[name] || []).push(callback);
    },
    offEvent: function () {},
    addToHomeScreen: function () {
      rec.homeCalls += 1;
      if (rec.homeThrows) throw new Error("unavailable here");
    },
    shareMessage: function (message, callback) {
      rec.shareCalls.push(String(message));
      if (typeof callback === "function") { try { callback(true); } catch (e) {} }
    },
    openTelegramLink: function (url) { rec.linkCalls.push(String(url)); }
  };
  function fire(name, payload) {
    var list = rec.handlers[name] || [];
    for (var i = 0; i < list.length; i += 1) { try { list[i](payload); } catch (e) {} }
  }
  var sdk = {};
  Object.keys(builders).forEach(function (name) {
    sdk[name] = function () {
      rec.counts[name] = (rec.counts[name] || 0) + 1;
      return builders[name].apply(sdk, arguments);
    };
  });
  sdk.initData = "";
  sdk.initDataUnsafe = {};
  sdk.colorScheme = "dark";
  sdk.HapticFeedback = {
    impactOccurred: function (style) {
      rec.haptics.push("impact:" + style);
      if (rec.hapticThrows) throw new Error("haptics unavailable");
    },
    notificationOccurred: function (type) {
      rec.haptics.push("notification:" + type);
      if (rec.hapticThrows) throw new Error("haptics unavailable");
    },
    selectionChanged: function () { rec.haptics.push("selection:"); }
  };
  sdk.BackButton = {
    isVisible: true,
    show: function () { rec.backShown += 1; },
    hide: function () { rec.backHidden += 1; },
    onClick: function (callback) {
      rec.backRegistrations += 1;
      rec.back = callback;
    },
    offClick: function () {}
  };
  Object.defineProperty(sdk, "isFullscreen", {
    get: function () { return rec.isFullscreen === true; },
    set: function (value) { rec.isFullscreen = !!value; }
  });
  Object.defineProperty(sdk, "safeAreaInset", {
    get: function () { return rec.safe; }
  });
  Object.defineProperty(sdk, "contentSafeAreaInset", {
    get: function () { return rec.contentSafe; }
  });
  window.Telegram = { WebApp: sdk };
  window.__fireSafe = function (which) {
    fire(which === "safe" ? "safeAreaChanged" : "contentSafeAreaChanged", {});
  };
  window.__fireBack = function () { if (rec.back) rec.back(); };
})();
"""

# This Chromium build denies the Clipboard API by default; the clipboard fallback is a
# browser-policy rejection, not a page error, so the modern page opts in explicitly.
MODERN_PAGE_HEADERS = {"Permissions-Policy": "clipboard-write=(self)"}

# An older client: the normal runtime contract and the legacy WebApp surface only,
# with none of the phase 7 methods, attributes or controls.
LEGACY_ONLY_STUB = """
window.Telegram = { WebApp: {
  initData: "", initDataUnsafe: {}, colorScheme: "dark",
  ready: function () {}, expand: function () { window.__sdkRec.expand += 1; },
  close: function () { window.__sdkRec.close += 1; }
} };
"""


# ---------------------------------------------------------------------------
# Fakes for the real Flask app (the page itself is still the production one)
# ---------------------------------------------------------------------------

class FakeDb:
    def __init__(self):
        self.calls = []

    def search_videos(self, query, page=0, category_id=None, quality=None, status=None):
        self.calls.append(("search_videos", query, page, category_id))
        ids = STATE["videos"]
        return [{"id": i} for i in ids], len(ids)

    def get_videos(self, category_id, page=0):
        return [{"id": i} for i in STATE["videos"]], len(STATE["videos"])

    def get_categories_tree(self):
        return [{"id": 1, "name": "أفلام"}]

    def get_miniapp_categories(self):
        return [{"id": 1, "name": "أفلام", "count": TOTAL_VIDEOS}]


class FakeBot:
    def get_chat_member(self, channel_id, user_id):
        return type("Member", (), {"status": "member"})()

    def copy_message(self, chat_id, from_chat_id, message_id):
        raise RuntimeError("the stub answers open-video; the bot must not be reached")


def install_fakes():
    fake = FakeDb()
    for name in ("search_videos", "get_videos", "get_categories_tree", "get_miniapp_categories"):
        setattr(db_manager, name, getattr(fake, name))
    helpers.get_required_channels = lambda: []
    return fake


def build_page(stub_base):
    template = miniapp.render_page()
    if template.count(TELEGRAM_SDK_URL) != 1:
        raise RuntimeError("Mini App page must load the official Telegram SDK exactly once")
    shim = (SHIM.replace("__STUB_BASE__", json.dumps(stub_base))
                .replace("__USER_ID__", str(MEMBER_ID)))
    return template.replace("<head>", "<head>" + shim, 1)


def build_modern_page(stub_base, safe, content_safe):
    """The same production page with the phase 7 recorder instead of the old WebApp.

    The legacy recording WebApp (``SHIM``) is replaced by the recorder setup, which runs
    before the production script: ``MODERN_RECORDER`` waits for the runtime's registry
    to appear (it is created by the official SDK script, loaded asynchronously), then
    provides the SDK contract while the page's own controls remain the real ones. The
    offline fetch bridge of ``SHIM`` stays: every relative request of the page (library,
    categories, OPEN, favourites) goes to the local stub instead of the Flask app.
    """
    template = miniapp.render_page()
    if template.count(TELEGRAM_SDK_URL) != 1:
        raise RuntimeError("Mini App page must load the official Telegram SDK exactly once")
    setup = (MODERN_SETUP.replace("__SAFE__", json.dumps(safe, ensure_ascii=False))
                         .replace("__CONTENT_SAFE__", json.dumps(content_safe, ensure_ascii=False)))
    bridge = MODERN_BRIDGE.replace("__STUB_BASE__", json.dumps(stub_base))
    for text in (bridge, MODERN_RECORDER, MODERN_SDK_STUB, setup):
        if "&" in text:
            raise RuntimeError("render_page unescapes entities; injected code must not use '&'")
    inject = "<script>" + bridge + setup + MODERN_RECORDER + "</script>"
    return template.replace("<head>", "<head>" + inject, 1)


def route_offline(route, stub_base, app_base, sdk_script):
    """Serve only localhost and the intercepted official SDK; never use the network."""
    if route.request.url == TELEGRAM_SDK_URL:
        if sdk_script is None:
            route.abort()
        else:
            route.fulfill(status=200, content_type="application/javascript", body=sdk_script)
    elif route.request.url.startswith((stub_base, app_base)):
        route.continue_()
    else:
        route.abort()


# ---------------------------------------------------------------------------
# Playwright checks
# ---------------------------------------------------------------------------

class BrowserCheck:
    def __init__(self, page):
        self.page = page

    def card_ids(self):
        return self.page.eval_on_selector_all(
            ".card .btn.open[data-video-id]", "els => els.map(e => e.dataset.videoId)")

    def wait_ids(self, expected, timeout=8000):
        deadline = time.time() + timeout / 1000.0
        last = None
        while time.time() < deadline:
            try:
                last = self.card_ids()
            except Exception:  # page may be mid-render
                last = None
            if last == expected:
                return last
            self.page.wait_for_timeout(50)
        raise AssertionError(f"card ids {last} != {expected}")

    def state(self):
        return self.page.evaluate("() => fetch('/state').then(r => r.json())")

    def toast_text(self):
        return self.page.eval_on_selector("#toast", "e => e.textContent")

    def toast_shown(self):
        return self.page.eval_on_selector("#toast", "e => e.classList.contains('show')")

    def button(self, video_id):
        return self.page.locator(f".btn.open[data-video-id='{video_id}']")

    def next_disabled(self):
        return self.page.eval_on_selector("#next", "e => e.disabled === true")

    def click_next(self, force=False):
        self.page.click("#next", force=force)

    def click_prev(self, force=False):
        self.page.click("#prev", force=force)


def wait_until(predicate, timeout=8.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def run_checks(browser, page_url, stub_base, app_base):
    failures = []

    def check(label, condition, detail=""):
        print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not condition else ""))
        if not condition:
            failures.append(label)

    rendered = miniapp.render_page()
    check("official Telegram SDK script is present exactly once",
          rendered.count(TELEGRAM_SDK_URL) == 1 and f'src="{TELEGRAM_SDK_URL}"' in rendered)

    context = browser.new_context(locale="ar", viewport={"width": 420, "height": 900})
    page = context.new_page()
    console_errors = []
    page.on("pageerror", lambda exc: console_errors.append(str(exc)))
    # Execute the production script src from a local fake response, never the network.
    page.route("**/*", lambda route: route_offline(route, stub_base, app_base, SDK_STUB))
    bc = BrowserCheck(page)

    with STATE_LOCK:
        STATE["tm_data"] = make_init_data(TEST_BOT_TOKEN, MEMBER_ID)
    page.goto(page_url)
    page.wait_for_selector(".card", timeout=10000)

    first_page = [str(i) for i in range(TOTAL_VIDEOS, TOTAL_VIDEOS - PAGE_SIZE, -1)]
    second_page = [str(i) for i in range(TOTAL_VIDEOS - PAGE_SIZE, TOTAL_VIDEOS - 2 * PAGE_SIZE, -1)]
    third_page = [str(i) for i in range(TOTAL_VIDEOS - 2 * PAGE_SIZE, 0, -1)]

    check("page 1 renders the server page size", bc.card_ids() == first_page, bc.card_ids())
    check("successful initial load hides retry", not page.locator("#retry").is_visible())
    with STATE_LOCK:
        STATE["library_requests"].clear()

    # NEXT then PREVIOUS restores the same cards.
    bc.click_next(force=True)
    bc.wait_ids(second_page)
    check("NEXT shows page 2 without repeating page 1",
          not set(bc.card_ids()) & set(first_page), bc.card_ids())
    bc.click_prev(force=True)
    bc.wait_ids(first_page)
    check("PREVIOUS restores page 1", bc.card_ids() == first_page, bc.card_ids())
    with STATE_LOCK:
        requests = list(STATE["library_requests"])
    check("library requests never send page_size", all("page_size" not in r for r in requests),
          requests)
    check("requested pages are 1 then 0", [r.get("page") for r in requests] == ["1", "0"], requests)

    # Walk to the final page: NEXT must not advance past the last page.
    bc.click_next(force=True)
    bc.wait_ids(second_page)
    bc.click_next(force=True)
    bc.wait_ids(third_page)
    check("final page shows the remaining videos", bc.card_ids() == third_page, bc.card_ids())
    check("NEXT is disabled on the final page", bc.next_disabled())
    label = page.eval_on_selector("#pageLabel", "e => e.textContent")
    check("page label counts the final page", "3" in label, label)
    bc.click_next(force=True)
    page.wait_for_timeout(400)
    check("clicking a disabled NEXT keeps the final page", bc.card_ids() == third_page)

    # Out-of-order response: start a slow PREVIOUS request, then a newer search.
    with STATE_LOCK:
        STATE["library_mode"] = "slow_extras"
        STATE["library_requests"].clear()
    bc.click_prev(force=True)
    wait_until(lambda: any(r.get("page") == "1" for r in bc.state()["library_requests"]),
               timeout=2)
    page.evaluate("() => { const q = document.getElementById('q'); "
                  "q.value = ''; q.dispatchEvent(new Event('search')); }")
    bc.wait_ids(first_page)
    page.wait_for_timeout(1800)   # let the stale page-1 response land after page 0
    check("stale slow response does not overwrite the newest page",
          bc.card_ids() == first_page, bc.card_ids())
    next_ok = page.eval_on_selector(
        "#next", "e => !e.disabled && e.getAttribute('aria-disabled') === 'false'")
    check("loading state settles after the late response", next_ok)
    with STATE_LOCK:
        requests = list(STATE["library_requests"])
        STATE["library_mode"] = "ok"
    check("stale and newest requests actually overlapped",
          [r.get("page") for r in requests] == ["1", "0"], requests)
    bc.click_next(force=True)
    bc.wait_ids(second_page)
    check("pager works again after a late response", bc.card_ids() == second_page)

    # OPEN: rapid clicks must produce exactly one send with the signed initData.
    with STATE_LOCK:
        STATE["open_requests"].clear()
        STATE["sends"].clear()
        STATE["deliver_mode"] = "ok"
    with STATE_LOCK:
        STATE["tm_data"] = make_init_data(TEST_BOT_TOKEN, MEMBER_ID, auth_date=time.time() - 20)
    wait_until(lambda: page.evaluate("() => window.__sdk && window.__sdk.initData.length > 0"))
    target = second_page[0]
    button = bc.button(target)
    button.click(click_count=1, delay=10)
    for _ in range(4):
        try:
            button.click(force=True, timeout=2000)
        except Exception:
            button = bc.button(target)
            try:
                button.click(force=True, timeout=2000)
            except Exception:
                break
    ok = wait_until(lambda: len(bc.state()["sends"]) >= 1, timeout=6)
    page.wait_for_timeout(600)
    snapshot = bc.state()
    check("OPEN sends exactly once on rapid clicks",
          ok and len(snapshot["open_requests"]) >= 1 and len(snapshot["sends"]) == 1,
          snapshot["open_requests"])
    if snapshot["open_requests"]:
        body = snapshot["open_requests"][0]
        check("POST carries the video id", str(body.get("video_id")) == target, body)
        check("POST carries the signed initData accepted by the real HMAC validator",
              len(snapshot["sends"]) == 1, body)
        check("POST does not carry a client destination",
              "chat_id" not in body and "user_id" not in body, body)
    sent = snapshot["sends"][0] if snapshot["sends"] else {}
    check("delivery targets the signed user's private chat",
          sent.get("user_id") == MEMBER_ID and sent.get("chat_id") == MEMBER_ID, sent)
    busy_or_done = page.eval_on_selector_all(
        ".card .btn.open[data-video-id='%s']" % target,
        "els => els.length === 1 && (els[0].disabled || els[0].textContent.indexOf('المحادثة') >= 0)")
    check("button is busy/confirmed after the single send", busy_or_done)
    page.wait_for_function("() => window.__closeCalls >= 1", timeout=5000)
    check("WebApp closes after a confirmed success",
          page.evaluate("() => window.__closeCalls") == 1)

    # Failure: no close, truthful message, button usable again for a retry.
    with STATE_LOCK:
        STATE["deliver_mode"] = "error"
        STATE["tm_data"] = make_init_data(TEST_BOT_TOKEN, MEMBER_ID)
    page.goto(page_url)
    page.wait_for_selector(".card", timeout=10000)
    wait_until(lambda: page.evaluate("() => window.__sdk && window.__sdk.initData.length > 0"))
    with STATE_LOCK:
        STATE["open_requests"].clear()
        STATE["sends"].clear()
    failing = first_page[1]
    bc.button(failing).click()
    wait_until(lambda: len(bc.state()["open_requests"]) >= 1, timeout=6)
    wait_until(lambda: "تعذر" in bc.toast_text(), timeout=6)
    snapshot = bc.state()
    check("failed send reports a truthful Arabic failure",
          "تعذر إرسال الفيديو" in bc.toast_text() and bc.toast_shown(), bc.toast_text())
    check("failed send closes nothing", page.evaluate("() => window.__closeCalls") == 0)
    check("failed send records no delivery", snapshot["sends"] == [], snapshot["sends"])
    check("failed send re-enables the button",
          not page.eval_on_selector(".card .btn.open[data-video-id='%s']" % failing,
                                    "e => e.disabled"))
    with STATE_LOCK:
        STATE["deliver_mode"] = "ok"
    bc.button(failing).click()
    ok = wait_until(lambda: len(bc.state()["sends"]) == 1, timeout=6)
    check("retry after a failure delivers", ok, bc.state())

    # Library failure: expose a real retry control and recover with fresh content.
    with STATE_LOCK:
        STATE["library_mode"] = "reject"
    page.goto(page_url)
    retry_visible = wait_until(lambda: page.locator("#retry").is_visible(), timeout=6)
    check("failed library load shows a truthful error",
          "تعذر تحميل المكتبة" in page.eval_on_selector("#status", "e => e.textContent"))
    check("failed library load exposes retry", retry_visible)
    check("failed library load hides the pager", not page.locator("#pager").is_visible())
    with STATE_LOCK:
        STATE["library_mode"] = "ok"
    if retry_visible:
        page.click("#retry")
        bc.wait_ids(first_page)
        check("library retry restores the first page", bc.card_ids() == first_page)
        check("successful retry hides the retry control",
              not page.locator("#retry").is_visible())

    # A page opened outside Telegram has no initData: never posts fabricated data.
    with STATE_LOCK:
        STATE["tm_data"] = ""
    page.goto(page_url)
    page.wait_for_selector(".card", timeout=10000)
    with STATE_LOCK:
        STATE["open_requests"].clear()
    bc.button(first_page[0]).click()
    page.wait_for_timeout(700)
    check("no initData means no POST", bc.state()["open_requests"] == [],
          bc.state()["open_requests"])
    check("no initData explains it is Telegram-only", "تيليجرام" in bc.toast_text(),
          bc.toast_text())
    check("no uncaught page errors", not console_errors, console_errors)
    context.close()
    return failures


def run_sdk_resilience_checks(browser, page_url, stub_base, app_base):
    failures = []

    def check(label, condition, detail=""):
        print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not condition else ""))
        if not condition:
            failures.append(label)

    first_page = [str(i) for i in range(TOTAL_VIDEOS, TOTAL_VIDEOS - PAGE_SIZE, -1)]

    # The production script tag is requested, but its network response fails.
    context = browser.new_context(locale="ar", viewport={"width": 420, "height": 700})
    page = context.new_page()
    bc = BrowserCheck(page)
    page_errors = []
    sdk_requests = []
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))

    def abort_sdk(route):
        if route.request.url == TELEGRAM_SDK_URL:
            sdk_requests.append(route.request.url)
        route_offline(route, stub_base, app_base, None)

    page.route("**/*", abort_sdk)
    with STATE_LOCK:
        STATE["tm_data"] = ""
        STATE["open_requests"].clear()
        STATE["sends"].clear()
    page.goto(page_url)
    bc.wait_ids(first_page)
    check("aborted SDK response leaves the library usable",
          bc.card_ids() == first_page and sdk_requests == [TELEGRAM_SDK_URL], sdk_requests)

    with STATE_LOCK:
        STATE["library_requests"].clear()
    page.evaluate("""() => {
      const query = document.getElementById('q');
      query.value = 'فيلم';
      query.dispatchEvent(new Event('search'));
    }""")
    search_ok = wait_until(
        lambda: any(r.get("query") == "فيلم" for r in bc.state()["library_requests"])
        and page.eval_on_selector("#next", "e => !e.disabled"), timeout=4)
    bc.wait_ids(first_page)
    check("aborted SDK keeps search usable", search_ok and bc.card_ids() == first_page)
    second_page = [str(i) for i in range(TOTAL_VIDEOS - PAGE_SIZE,
                                         TOTAL_VIDEOS - 2 * PAGE_SIZE, -1)]
    bc.click_next(force=True)
    bc.wait_ids(second_page)
    bc.click_prev(force=True)
    bc.wait_ids(first_page)
    check("aborted SDK keeps paging usable", bc.card_ids() == first_page)

    favorite = page.locator(".card .btn.fav").first
    favorite.click()
    page.wait_for_timeout(400)
    stored_favorite = page.evaluate("""videoId => {
      const favorites = JSON.parse(localStorage.getItem('miniapp_fav') || '{}');
      return favorites['guest:' + videoId] === true;
    }""", int(first_page[0]))
    check("aborted SDK keeps local favorite toggling usable",
          favorite.get_attribute("aria-pressed") == "true" and stored_favorite)

    bc.button(first_page[0]).click()
    page.wait_for_timeout(400)
    snapshot = bc.state()
    check("aborted SDK OPEN is truthful and posts nothing",
          snapshot["open_requests"] == [] and snapshot["sends"] == []
          and "تيليجرام" in bc.toast_text() and bc.toast_shown(), snapshot["open_requests"])
    check("aborted SDK causes no uncaught page errors", not page_errors, page_errors)
    context.close()

    # The real script src executes, but WebApp is published only after the first OPEN.
    context = browser.new_context(locale="ar", viewport={"width": 420, "height": 700})
    page = context.new_page()
    bc = BrowserCheck(page)
    page_errors = []
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    page.route("**/*", lambda route: route_offline(route, stub_base, app_base, LATE_SDK_STUB))
    with STATE_LOCK:
        STATE["tm_data"] = make_init_data(TEST_BOT_TOKEN, MEMBER_ID)
        STATE["open_requests"].clear()
        STATE["sends"].clear()
        STATE["deliver_mode"] = "ok"
    page.goto(page_url)
    bc.wait_ids(first_page)
    sdk_absent = page.evaluate("() => !window.Telegram || !window.Telegram.WebApp")
    check("delayed WebApp absence does not block the library", sdk_absent and bc.card_ids() == first_page)
    bc.button(first_page[0]).click()
    page.wait_for_timeout(400)
    snapshot = bc.state()
    check("OPEN before delayed WebApp is truthful and posts nothing",
          snapshot["open_requests"] == [] and snapshot["sends"] == []
          and "تيليجرام" in bc.toast_text(), snapshot["open_requests"])
    published = page.evaluate("""() => {
      if (typeof window.__publishSdk !== 'function') return false;
      window.__publishSdk();
      return true;
    }""")
    ready = wait_until(lambda: page.evaluate(
        "() => window.Telegram && window.Telegram.WebApp && window.__sdk.initData.length > 0"))
    if published and ready:
        bc.button(first_page[0]).click()
    delivered = wait_until(lambda: len(bc.state()["sends"]) == 1, timeout=6)
    check("OPEN re-reads a late WebApp and delivers", published and ready and delivered, bc.state())
    check("delayed WebApp path causes no uncaught page errors", not page_errors, page_errors)
    context.close()
    return failures


def run_chips_checks(browser, page_url, stub_base, app_base):
    failures = []

    def check(label, condition, detail=""):
        print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not condition else ""))
        if not condition:
            failures.append(label)

    context = browser.new_context(locale="ar", viewport={"width": 420, "height": 600})
    page = context.new_page()
    bc = BrowserCheck(page)
    page_errors = []
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    page.route("**/*", lambda route: route_offline(route, stub_base, app_base, None))
    with STATE_LOCK:
        STATE["categories"] = [
            {"id": i, "name": f"تصنيف {i}", "count": 1} for i in range(1, 13)]
        STATE["tm_data"] = ""
        STATE["library_mode"] = "ok"
    try:
        page.goto(page_url)
        page.wait_for_selector(".card", timeout=10000)
        wait_until(lambda: page.locator(".chip").count() == 13)
        page.wait_for_timeout(200)
        initial = page.evaluate("""() => {
          const chips = document.getElementById('chips');
          const wrap = document.getElementById('chipsWrap');
          return {
            rtl: getComputedStyle(chips).direction === 'rtl',
            overflow: chips.scrollWidth > chips.clientWidth,
            position: chips.scrollLeft,
            startFade: wrap.classList.contains('fade-start'),
            endFade: wrap.classList.contains('fade-end'),
            endOpacity: getComputedStyle(wrap, '::after').opacity
          };
        }""")
        check("narrow RTL chips overflow with a visible end fade",
              initial["rtl"] and initial["overflow"] and initial["position"] == 0
              and not initial["startFade"] and initial["endFade"]
              and initial["endOpacity"] == "1", initial)

        chips_box = page.locator("#chips").bounding_box()
        page.mouse.move(chips_box["x"] + chips_box["width"] / 2,
                        chips_box["y"] + chips_box["height"] / 2)
        page.mouse.wheel(0, 240)
        page.wait_for_timeout(250)
        moved = page.evaluate("""() => ({
          position: document.getElementById('chips').scrollLeft,
          pageY: window.scrollY
        })""")
        check("vertical wheel scrolls RTL chips without moving the page",
              moved["position"] < 0 and moved["pageY"] == 0, moved)

        end_position = page.eval_on_selector("#chips", """e => {
          e.scrollLeft = -(e.scrollWidth - e.clientWidth);
          return e.scrollLeft;
        }""")
        page.wait_for_timeout(200)
        end_fades = page.evaluate("""() => {
          const wrap = document.getElementById('chipsWrap');
          return {
            start: wrap.classList.contains('fade-start'),
            end: wrap.classList.contains('fade-end'),
            startOpacity: getComputedStyle(wrap, '::before').opacity
          };
        }""")
        check("RTL overflow fades follow the scroll end",
              end_fades["start"] and not end_fades["end"]
              and end_fades["startOpacity"] == "1", end_fades)
        page.mouse.wheel(0, 300)
        page.wait_for_timeout(350)
        at_left_end = page.evaluate("""() => ({
          position: document.getElementById('chips').scrollLeft,
          pageY: window.scrollY
        })""")
        check("page scroll is not hijacked at the RTL left end",
              at_left_end["position"] == end_position and at_left_end["pageY"] > 0, at_left_end)

        before_right_wheel = page.eval_on_selector("#chips", """e => {
          e.scrollLeft = 0;
          return { position: e.scrollLeft, pageY: window.scrollY };
        }""")
        page.mouse.wheel(0, -260)
        page.wait_for_timeout(350)
        at_right_end = page.evaluate("""() => ({
          position: document.getElementById('chips').scrollLeft,
          pageY: window.scrollY
        })""")
        check("page scroll is not hijacked at the RTL right end",
              at_right_end["position"] == before_right_wheel["position"]
              and at_right_end["pageY"] < before_right_wheel["pageY"], at_right_end)

        with STATE_LOCK:
            STATE["library_requests"].clear()
        chip = page.locator(".chip[data-cat='8']")
        chip.click()
        wait_until(lambda: any(r.get("category_id") == "8"
                               for r in bc.state()["library_requests"]), timeout=4)
        requests = bc.state()["library_requests"]
        pressed = page.locator(".chip[aria-pressed='true']")
        check("chip click still filters and preserves aria-pressed",
              any(r.get("category_id") == "8" for r in requests)
              and chip.get_attribute("aria-pressed") == "true" and pressed.count() == 1,
              requests)
        check("chips interactions cause no uncaught page errors", not page_errors, page_errors)
    finally:
        context.close()
        with STATE_LOCK:
            STATE["categories"] = [{"id": 1, "name": "أفلام", "count": TOTAL_VIDEOS}]
    return failures


def run_modern_sdk_checks(browser, stub_base, app_base):
    """Phase 7 integrations against the real page in Chromium with a recording SDK."""
    failures = []

    def check(label, condition, detail=""):
        print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not condition else ""))
        if not condition:
            failures.append(label)

    hostile = "?evil=1&initData=SECRET_INIT_DATA&tgWebAppData=SECRET_TWA&start_param=BIZ_ID"
    markers = ("SECRET_INIT_DATA", "SECRET_TWA", "BIZ_ID", "evil=1", "#")
    # The production /app route belongs to register_miniapp; the phase 7 scenario uses
    # its own test-only route so location.origin + location.pathname stays realistic.
    app_url = app_base + MODERN_PAGE_PATH

    context = browser.new_context(locale="ar", viewport={"width": 420, "height": 900})
    # The Chromium default denies Clipboard API writes; grant it so the page's guarded
    # clipboard fallback is a page decision rather than a browser-policy rejection.
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=app_base)
    context.add_init_script("window.__historyBack = 0; try { var ob = window.history.back.bind(window.history);"
                            " window.history.back = function () { window.__historyBack += 1; return ob(); }; }"
                            " catch (e) {}")
    page = context.new_page()
    bc = BrowserCheck(page)
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.route("**/*", lambda route: route_offline(route, stub_base, app_base,
                                                  MODERN_SDK_STUB))
    def rec():
        return page.evaluate("() => window.__sdkRec")

    def load_modern_page():
        """Load the phase 7 page and wait for the recording SDK to initialise it."""
        page.goto(app_url + hostile)
        page.wait_for_selector(".card", timeout=10000)
        wait_until(lambda: page.evaluate(
            "() => !!window.Telegram && !!window.__sdkRec && window.__sdkRec.ready >= 1"
            " && typeof window.Telegram.WebApp.requestFullscreen === 'function'"))
        # The production script synchronises its controls from the SDK load handler,
        # which runs after the official script publishes WebApp.
        page.wait_for_function(
            "() => !document.getElementById('fullscreenBtn').hidden", timeout=5000)

    load_modern_page()

    def css(name):
        return page.evaluate("n => getComputedStyle(document.documentElement)"
                             ".getPropertyValue(n).trim()", name)

    snapshot = rec()
    check("phase 7: SDK does not request fullscreen on load",
          snapshot["counts"].get("requestFullscreen", 0) == 0, snapshot["counts"])
    check("phase 7: BackButton registers once, is shown, and gets the real handler",
          snapshot["backRegistrations"] == 1 and snapshot["backShown"] == 1
          and page.evaluate("() => typeof window.__sdkRec.back") == "function", snapshot["counts"])
    check("phase 7: safe-area insets map to CSS variables (0 defaults)",
          css("--tg-safe-top") == "12px" and css("--tg-safe-bottom") == "0px"
          and css("--tg-content-safe-top") == "8px" and css("--tg-content-safe-bottom") == "0px",
          [css("--tg-safe-top"), css("--tg-content-safe-top")])
    check("phase 7: both safe-area events are subscribed",
          "safeAreaChanged" in snapshot["handlers"]
          and "contentSafeAreaChanged" in snapshot["handlers"], list(snapshot["handlers"]))

    page.evaluate("""() => { window.__sdkRec.safe = { top: 30, bottom: 0, left: 0, right: 0 };
                           window.__fireSafe('safe'); }""")
    page.wait_for_timeout(100)
    check("phase 7: safeAreaChanged updates the CSS variable", css("--tg-safe-top") == "30px",
          css("--tg-safe-top"))
    page.evaluate("""() => { window.__sdkRec.safe = undefined;
                           window.__sdkRec.contentSafe = { top: 4, bottom: 0, left: 0, right: 0 };
                           window.__fireSafe('safe'); window.__fireSafe('content'); }""")
    page.wait_for_timeout(100)
    check("phase 7: missing inset values fall back to 0px",
          css("--tg-safe-top") == "0px" and css("--tg-content-safe-top") == "4px",
          [css("--tg-safe-top"), css("--tg-content-safe-top")])

    page.click("#fullscreenBtn")
    page.wait_for_timeout(120)
    snapshot = rec()
    check("phase 7: fullscreen click requests fullscreen once",
          snapshot["fullscreenRequests"] == 1 and snapshot["exitRequests"] == 0, snapshot["counts"])
    check("phase 7: fullscreen button state follows isFullscreen",
          page.eval_on_selector("#fullscreenBtn", "e => e.getAttribute('aria-pressed')") == "true")
    page.click("#fullscreenBtn")
    page.wait_for_timeout(120)
    snapshot = rec()
    check("phase 7: second fullscreen click exits",
          snapshot["fullscreenRequests"] == 1 and snapshot["exitRequests"] == 1, snapshot["counts"])

    page.click("#homeBtn")
    page.wait_for_timeout(120)
    snapshot = rec()
    check("phase 7: addToHomeScreen calls the SDK once",
          snapshot["homeCalls"] == 1, snapshot["counts"])
    page.evaluate("() => { window.__sdkRec.homeThrows = true; }")
    page.click("#homeBtn")
    page.wait_for_timeout(120)
    snapshot = rec()
    check("phase 7: a throwing addToHomeScreen is nonfatal and reports the failure",
          snapshot["homeCalls"] == 2 and "notification:error" in snapshot["haptics"]
          and "تعذر" in bc.toast_text(), snapshot["haptics"])

    page.click("#shareBtn")
    page.wait_for_timeout(150)
    snapshot = rec()
    shared = snapshot["shareCalls"]
    check("phase 7: share uses shareMessage", len(shared) == 1 and snapshot["linkCalls"] == [],
          shared)
    app_url_full = app_base + MODERN_PAGE_PATH
    leaked = [m for m in markers if shared and m in shared[0]]
    check("phase 7: shared app URL is origin + pathname only",
          shared and app_url_full in shared[0] and not leaked, shared)

    with STATE_LOCK:
        STATE["tm_data"] = make_init_data(TEST_BOT_TOKEN, MEMBER_ID)
        STATE["open_requests"].clear()
        STATE["sends"].clear()
        STATE["deliver_mode"] = "ok"
    with STATE_LOCK:
        signed = STATE["tm_data"]
    # The modern WebApp has no signed initData of its own; hand it the one the stub
    # validator accepts and wait for OPEN to read it, so delivery is exercised.
    page.evaluate("s => { window.Telegram.WebApp.initData = s; "
                  "window.Telegram.WebApp.initDataUnsafe = { user: { id: 500 } }; }", signed)
    wait_until(lambda: page.evaluate("() => window.Telegram.WebApp.initData.length > 0"))
    with STATE_LOCK:
        STATE["open_requests"].clear()
        STATE["sends"].clear()
    before = rec()
    page.eval_on_selector(".btn.open[data-video-id='%s']" % OPEN_VIDEO_ID, "b => b.click()")
    delivered = wait_until(lambda: rec()["close"] == 1 or page.evaluate(
        "() => document.getElementById('toast').textContent.indexOf('تعذر') >= 0"), timeout=6)
    snapshot = rec()
    after = snapshot["haptics"][len(before["haptics"]):]
    check("phase 7: success haptic only after confirmed delivery",
          delivered and after.count("notification:success") == 1
          and "impact:light" in after, after)
    check("phase 7: OPEN's confirmed close still happens exactly once",
          snapshot["close"] == 1 and snapshot["backRegistrations"] == 1
          and not page.evaluate("() => window.__historyBack"), snapshot["close"])
    check("phase 7: modern SDK path has no uncaught page errors", not errors, errors)

    with STATE_LOCK:
        STATE["tm_data"] = ""
        STATE["sends"].clear()
        STATE["deliver_mode"] = "ok"
    load_modern_page()
    page.evaluate("() => { window.__sdkRec.hapticThrows = true; }")
    page.click("#shareBtn")
    page.wait_for_timeout(150)
    page.click("#homeBtn")
    page.wait_for_timeout(150)
    check("phase 7: throwing haptics never block the toolbar actions",
          len(rec()["shareCalls"]) == 1 and rec()["homeCalls"] == 1, rec()["counts"])
    context.close()

    # Unsupported client: an older client without any phase 7 method, so every
    # integration must degrade without errors or fabricated success.
    context = browser.new_context(locale="ar", viewport={"width": 420, "height": 900})
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=app_base)
    page = context.new_page()
    bc = BrowserCheck(page)
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.route("**/*", lambda route: route_offline(route, stub_base, app_base,
                                                  LEGACY_ONLY_STUB))
    with STATE_LOCK:
        STATE["tm_data"] = ""
    load_modern_page()
    state = page.evaluate("""() => {
      const home = document.getElementById('homeBtn');
      return { homeHidden: home.hidden || getComputedStyle(home).display === 'none',
               homeDisabled: home.disabled,
               homeLabel: home.getAttribute('aria-label') || '',
               homeTitle: home.title || '',
               fullscreenVisible: !document.getElementById('fullscreenBtn').hidden,
               safeTop: getComputedStyle(document.documentElement).getPropertyValue('--tg-safe-top').trim() };
    }""")
    check("phase 7: unsupported addToHomeScreen is hidden/disabled and labelled",
          (state["homeHidden"] or state["homeDisabled"])
          and "غير متاح" in (state["homeLabel"] + state["homeTitle"]), state)
    check("phase 7: unsupported fullscreen falls back to expand (kept usable)",
          state["fullscreenVisible"] and state["safeTop"] == "0px", state)
    page.click("#shareBtn")
    page.wait_for_timeout(200)
    message = bc.toast_text()
    # Without a Telegram path the page may copy the app URL as a non-Telegram
    # fallback, but the toast must never claim a Telegram share happened.
    check("phase 7: unsupported share is truthful and leaks nothing",
          message and not [m for m in markers if m in message]
          and "تيليجرام" not in message and "تمت مشاركة" not in message, message)
    check("phase 7: unsupported BackButton registers nothing and does not error",
          page.evaluate("() => !window.__historyBack") and not errors, errors)
    context.close()
    return failures


def main():
    os.environ.pop("BOT_TOKEN", None)      # never use real credentials in this check
    os.environ.pop("DATABASE_URL", None)
    os.environ["MINIAPP_BOT_TOKEN"] = TEST_BOT_TOKEN
    os.environ["APP_URL"] = "https://example.invalid"

    install_fakes()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright is not installed (pip install playwright && "
              "python -m playwright install chromium)")
        return 2

    stub = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    stub_base = f"http://127.0.0.1:{stub.server_port}"

    app = Flask("miniapp-browser-check")
    app.config["TESTING"] = True
    miniapp.register_miniapp(app, bot=FakeBot())
    page_source = build_page(stub_base)
    app.add_url_rule("/__page", "page", lambda: page_source, methods=["GET"])
    # The phase 7 page lives on its own test-only route (register_miniapp owns /app).
    modern_source = build_modern_page(stub_base, {"top": 12, "bottom": 0, "left": 0, "right": 0},
                                      {"top": 8, "bottom": 0, "left": 0, "right": 0})

    def serve_modern_page():
        response = app.response_class(modern_source, mimetype="text/html")
        for name, value in MODERN_PAGE_HEADERS.items():
            response.headers[name] = value
        return response

    app.add_url_rule(MODERN_PAGE_PATH, "modern_page", serve_modern_page, methods=["GET"])

    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page_url = f"http://127.0.0.1:{server.server_port}/__page"
    app_base = f"http://127.0.0.1:{server.server_port}"
    print(f"Mini App browser check: page {page_url} (stub {stub_base})")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                failures = run_checks(browser, page_url, stub_base, app_base)
                failures += run_sdk_resilience_checks(browser, page_url, stub_base, app_base)
                failures += run_chips_checks(browser, page_url, stub_base, app_base)
                failures += run_modern_sdk_checks(browser, stub_base, app_base)
            finally:
                browser.close()
    finally:
        server.shutdown()
        stub.shutdown()

    if failures:
        print(f"FAILED {len(failures)} check(s): {failures}")
        return 1
    print("OK: all Mini App browser checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
