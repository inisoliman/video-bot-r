#!/usr/bin/env python3
# ==============================================================================
# ملف: miniapp.py
# الوصف: مكتبة الفيديوهات كتطبيق مصغّر (Telegram Mini App / Web App).
#        يضيف مسارات قراءة فقط تحت /app، وصفحة HTML واحدة مكتفية بذاتها
#        (بلا CDN)، ووسيطاً للصور المصغرة، وتوثيقاً لتوقيع initData.
#        كل الكود هنا إضافي: لا يعدّل أي سلوك قائم في البوت.
# ==============================================================================

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from functools import wraps
from html import escape as html_escape
from urllib.parse import parse_qsl, quote

import db_manager

logger = logging.getLogger(__name__)

# --- ثوابت التطبيق ---
URL_PREFIX = "/app"
LIBRARY_PAGE_SIZE = 12          # عدد البطاقات في كل صفحة
MAX_PAGE_INDEX = 9999           # حماية من صفحات ضخمة أو مشوّهة
MAX_QUERY_LENGTH = 80           # حد البحث النصي
INIT_DATA_MAX_AGE = 24 * 3600   # صلاحية initData = 24 ساعة (مواصفة WebApp)
API_CACHE_CONTROL = "private, max-age=30"
# هيدر تعليمات CSP للصفحة قد يضيفه خدمة الاستضافة؛ لا نستخدم أي CDN أو خط خارجي.
NO_CACHE_CONTROL = "no-store"

# زر لوحة المفاتيح الرئيسية (يقرأه handlers.helpers، ويطابقه handlers.user_handlers)
MINIAPP_KEYBOARD_TEXT = "📱 مكتبة الميني أب 🟣"

# حدود المتغيرات البيئية (تُقرأ عند الطلب لتبقى قابلة للاختبار دون إعادة استيراد)
_BOT_TOKEN_VARS = ("MINIAPP_BOT_TOKEN", "BOT_TOKEN")


# ==============================================================================
# أدوات مساعدة
# ==============================================================================

def _now():
    """الوقت الحالي (قابل للاستبدال في الاختبارات)."""
    return time.time()


def _bot_token():
    for name in _BOT_TOKEN_VARS:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def app_base_url():
    """أصل التطبيق من APP_URL فقط إن كان HTTPS، وإلا "" (لا نثق بعنوان الطلب)."""
    url = (os.getenv("APP_URL") or "").strip()
    if not url.lower().startswith("https://"):
        return ""
    return url.rstrip("/")


def miniapp_url():
    """رابط صفحة المكتبة، أو None عندما لا يمكن نشر زر Web App."""
    base = app_base_url()
    return f"{base}{URL_PREFIX}" if base else None


def bot_deep_link(username):
    """رابط رجوع إلى البوت (للأجهزة التي لا تدعم فتح رابط Web App)."""
    name = (username or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,64}", name):
        return ""
    return f"https://t.me/{name}"


def _int_value(value, default=0, minimum=0, maximum=MAX_PAGE_INDEX):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed


def _safe_thumbnail_url(video_id):
    return f"{URL_PREFIX}/thumb/{int(video_id)}"


def _metadata_dict(row):
    metadata = row.get('metadata') if hasattr(row, 'get') else None
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = None
    return metadata if isinstance(metadata, dict) else {}


def _row_get(row, key, default=None):
    try:
        value = row.get(key, default)
    except (AttributeError, TypeError):
        try:
            value = row[key]
        except (KeyError, IndexError, TypeError):
            return default
    return default if value is None else value


def _title_for(row):
    """عنوان العرض: display_title ثم أول سطر من caption ثم file_name (بلا بدائل آخر)."""
    for key in ('display_title', 'caption', 'file_name'):
        value = _row_get(row, key)
        if not value:
            continue
        text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
        first = text.split("\n")[0].strip()
        if first:
            return first[:120]
    return "فيديو"


def _duration_text(row, metadata):
    """مدة العرض من الميتاداتا (ثوانٍ) بصيغة mm:ss أو hh:mm:ss."""
    raw = metadata.get('duration') if metadata else None
    try:
        seconds = int(float(raw))
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return (f"{hours:d}:{minutes:02d}:{secs:02d}" if hours
            else f"{minutes:d}:{secs:02d}")


def _card_item(row):
    """بطاقة الفيديو: الحقول المعلنة فقط، بلا file_id ولا chat_id."""
    video_id = _row_get(row, 'id')
    metadata = _metadata_dict(row)
    category = _row_get(row, 'category_name') or _row_get(row, 'category') or ""
    return {
        'id': video_id,
        'title': _title_for(row),
        'category': str(category),
        'category_id': _row_get(row, 'category_id'),  # داخلي: يُسقط في _public_item
        'thumbnail_url': _safe_thumbnail_url(video_id),
        'views': int(_row_get(row, 'view_count', 0) or 0),
        '__duration': _duration_text(row, metadata),  # تُسقط في _public_item
    }


def _public_item(item):
    out = {key: value for key, value in item.items()
           if not key.startswith('__') and key != 'category_id'}
    out['duration'] = item.get('__duration', '')
    return out


def _with_category_names(items):
    """إضافة اسم التصنيف من get_categories_tree فقط للأصناف التي لا تحمل اسماً."""
    if not any(not item.get('category') for item in items):
        return items
    try:
        names = {row.get('id'): row.get('name') or '' for row in (db_manager.get_categories_tree() or [])}
    except Exception as exc:  # فشل الأسماء لا يُسقط المكتبة
        logger.warning("Mini App category names unavailable: %s", type(exc).__name__)
        return items
    for item in items:
        if not item.get('category'):
            item['category'] = str(names.get(item.get('category_id')) or "")
    return items


def _video_exists(video_id):
    try:
        return bool(db_manager.get_video_by_id(video_id))
    except Exception as exc:
        logger.warning("Mini App video lookup failed for %s: %s", video_id, type(exc).__name__)
        return False


def _categories():
    """التصنيفات مع العدد من الدالة المخصصة، أو من شجرة التصنيفات (بنية أقدم)."""
    lookup = getattr(db_manager, "get_miniapp_categories", None)
    if callable(lookup):
        return lookup()
    rows = []
    for row in db_manager.get_categories_tree() or []:
        name = _row_get(row, "name")
        if name:
            rows.append({"id": _row_get(row, "id"), "name": name, "count": 0})
    return rows


def _thumbnail_file_id(video_id):
    """thumbnail_file_id من الدالة المخصصة إن وُجدت، وإلا من صف الفيديو (بنية أقدم)."""
    lookup = getattr(db_manager, "get_miniapp_video_thumbnail", None)
    if callable(lookup):
        return lookup(video_id)
    row = db_manager.get_video_by_id(video_id) or {}
    return _row_get(row, "thumbnail_file_id") or None


# ==============================================================================
# توثيق initData (مواصفة Telegram WebApp)
# ==============================================================================

_SAFE_INITDATA_CHARS = re.compile(r"^[\x20-\x7e\t\r\n\u0600-\u06ff]*$")


def _parse_init_data(init_data):
    """تفكيك initData إلى (pairs, data_check_string, hash) أو None إن كان غير صالح."""
    raw = (init_data or "").strip()
    if not raw or len(raw) > 4096 or not _SAFE_INITDATA_CHARS.match(raw):
        return None
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    received_hash = None
    check_pairs = []
    for key, value in pairs:
        if key == "hash":
            if received_hash is not None:
                return None  # تكرار الهاش = محاولة تعمية
            received_hash = value
            continue
        check_pairs.append((key, value))
    if not received_hash or not check_pairs:
        return None
    check_pairs.sort(key=lambda pair: pair[0])
    data_check_string = "\n".join(f"{key}={value}" for key, value in check_pairs)
    return check_pairs, data_check_string, received_hash


def _signature(secret_key, data_check_string):
    return hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()


def validate_init_data(init_data, bot_token=None, now=None, max_age=INIT_DATA_MAX_AGE):
    """التحقق من initData وفق المواصفة.

    secret_key = HMAC_SHA256(key="WebAppData", msg=BOT_TOKEN)
    hash       = hex(HMAC_SHA256(key=secret_key, msg=data_check_string))

    Returns:
        dict | None: يحوي user (dict أو None) وauth_date (int) عند النجاح، وإلا None
        (توقيع خاطئ أو منتهي، auth_date غائب/قديم/مستقبلي).
    """
    token = (bot_token if bot_token is not None else _bot_token()) or ""
    if not token:
        logger.warning("Mini App initData rejected: no bot token configured")
        return None
    parsed = _parse_init_data(init_data)
    if parsed is None:
        return None
    _pairs, data_check_string, received_hash = parsed

    secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    expected = _signature(secret_key, data_check_string)
    if not hmac.compare_digest(expected, received_hash):
        return None

    data = dict(_pairs)
    try:
        auth_date = int(data.get("auth_date") or 0)
    except (TypeError, ValueError):
        return None
    current = int(now if now is not None else _now())
    if auth_date <= 0:
        return None
    if current - auth_date > max_age:
        return None          # منتهٍ
    if auth_date - current > 300:
        return None          # تاريخ مستقبلي (ساعة الخادم لا توثق)

    user = None
    try:
        parsed_user = json.loads(data.get("user") or "")
    except (TypeError, ValueError):
        parsed_user = None
    if isinstance(parsed_user, dict):
        user_id = parsed_user.get("id")
        if isinstance(user_id, int) and not isinstance(user_id, bool) and user_id > 0:
            user = parsed_user
        elif isinstance(user_id, str) and user_id.isdigit():
            parsed_user = dict(parsed_user)
            parsed_user["id"] = int(user_id)
            user = parsed_user
    return {"user": user, "auth_date": auth_date, "data": data}


# ==============================================================================
# الصفحة (HTML+CSS+JS في ملف واحد، RTL، وضع داكن)
# ==============================================================================

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<meta name="referrer" content="no-referrer">
<title>مكتبة الفيديو</title>
<style>
:root{
  --bg:#0e1015;--surface:#161a22;--surface-2:#1d2230;--line:#2a3040;
  --text:#eef1f7;--muted:#98a2b6;--accent:#4f8cff;--accent-2:#26c6a8;
  --danger:#ff6b6b;--radius:16px;--shadow:0 6px 18px rgba(0,0,0,.28);
}
@media (prefers-color-scheme: light){
  :root{--bg:#f5f7fb;--surface:#ffffff;--surface-2:#eef2f9;--line:#dbe2ee;
        --text:#121722;--muted:#5b667a;--shadow:0 6px 16px rgba(20,30,60,.08);}
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);
  font-family:system-ui,-apple-system,"Segoe UI",Tahoma,Arial,sans-serif;
  -webkit-text-size-adjust:100%}
body{padding-bottom:calc(16px + env(safe-area-inset-bottom))}
header{position:sticky;top:0;z-index:5;background:var(--bg);
  border-bottom:1px solid var(--line);padding:12px 14px 10px}
.brand{display:flex;align-items:center;gap:8px;font-size:18px;font-weight:700}
.brand span{font-size:22px}
.search{display:flex;align-items:center;gap:8px;margin-top:10px;
  background:var(--surface-2);border:1px solid var(--line);border-radius:12px;padding:0 10px}
.search input{flex:1;min-width:0;background:transparent;border:0;outline:0;color:var(--text);
  font-size:16px;padding:11px 0}
.search input::placeholder{color:var(--muted)}
.chips{display:flex;gap:8px;overflow-x:auto;padding:10px 0 2px;scrollbar-width:none;
  -webkit-overflow-scrolling:touch}
.chips::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;border:1px solid var(--line);background:var(--surface);
  color:var(--text);border-radius:999px;padding:7px 13px;font-size:13px;cursor:pointer}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
main{padding:12px 14px}
.status{padding:26px 8px;text-align:center;color:var(--muted);font-size:15px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(2,minmax(0,1fr))}
@media (min-width:700px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (min-width:1024px){.grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  overflow:hidden;box-shadow:var(--shadow);display:flex;flex-direction:column}
.thumb{position:relative;aspect-ratio:16/9;background:var(--surface-2);overflow:hidden}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}
.thumb .placeholder{position:absolute;inset:0;display:grid;place-items:center;
  font-size:26px;color:var(--muted)}
.badge{position:absolute;bottom:8px;inset-inline-start:8px;background:rgba(0,0,0,.68);
  color:#fff;font-size:12px;padding:3px 8px;border-radius:999px;direction:ltr}
.cat{position:absolute;top:8px;inset-inline-end:8px;background:var(--accent);
  color:#fff;font-size:11px;padding:3px 8px;border-radius:999px;max-width:70%;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.body{padding:10px;display:flex;flex-direction:column;gap:8px;flex:1}
.title{font-size:14px;font-weight:600;line-height:1.45;margin:0;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
  overflow-wrap:anywhere}
.meta{font-size:12px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap}
.actions{display:flex;gap:6px;margin-top:auto}
.btn{flex:1;border:1px solid var(--line);background:var(--surface-2);color:var(--text);
  border-radius:10px;padding:8px 6px;font-size:13px;cursor:pointer;
  text-align:center;text-decoration:none;font-family:inherit}
.btn.open{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
.btn.fav[aria-pressed="true"]{border-color:var(--accent-2);color:var(--accent-2)}
.btn:disabled{opacity:.5;cursor:default}
.pager{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:16px 0}
.pager .btn{flex:0 0 auto;min-width:96px}
.pager .label{font-size:13px;color:var(--muted)}
.toast{position:fixed;inset-inline:0;bottom:18px;margin:auto;max-width:88%;width:max-content;
  background:#222a3a;color:#fff;border-radius:12px;padding:10px 14px;font-size:14px;
  opacity:0;transform:translateY(8px);transition:opacity .18s,transform .18s;pointer-events:none}
.toast.show{opacity:1;transform:none}
.skeleton{height:170px;border-radius:var(--radius);background:linear-gradient(90deg,
  var(--surface) 25%,var(--surface-2) 50%,var(--surface) 75%);background-size:200% 100%;
  animation:slide 1.2s infinite}
@keyframes slide{0%{background-position:200% 0}100%{background-position:-200% 0}}
@media (prefers-reduced-motion:reduce){.skeleton{animation:none}.toast{transition:none}}
</style>
</head>
<body>
<header>
  <div class="brand"><span aria-hidden="true">🎬</span> مكتبة الفيديو</div>
  <div class="search">
    <input id="q" type="search" inputmode="search" autocomplete="off" enterkeyhint="search"
           placeholder="ابحث عن فيلم أو مسلسل…" aria-label="بحث">
  </div>
  <div class="chips" id="chips" role="group" aria-label="التصنيفات">
    <button class="chip" type="button" data-cat="" aria-pressed="true">الكل</button>
  </div>
</header>
<main>
  <div id="status" class="status" role="status" aria-live="polite">جارٍ التحميل…</div>
  <div id="grid" class="grid" hidden></div>
  <div class="pager" id="pager" hidden>
    <button class="btn" id="prev" type="button">◀ السابق</button>
    <span class="label" id="pageLabel"></span>
    <button class="btn" id="next" type="button">التالي ▶</button>
  </div>
</main>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script>
(function () {
  "use strict";
  var tg = (window.Telegram && window.Telegram.WebApp) || null;
  var BOT_URL = __BOT_URL_JSON__;
  var PAGE_SIZE = __PAGE_SIZE__;
  var state = { query: "", category: "", page: 0, loading: false, inflight: 0,
                favorites: {}, hasMore: false };

  if (tg) {
    try { tg.ready(); } catch (e) {}
    try { tg.expand(); } catch (e) {}
    try {
      if (tg.colorScheme === "light") document.documentElement.style.colorScheme = "light";
    } catch (e) {}
  }

  var el = function (id) { return document.getElementById(id); };
  var statusEl = el("status"), gridEl = el("grid"), chipsEl = el("chips"),
      pagerEl = el("pager"), prevEl = el("prev"), nextEl = el("next"),
      pageLabelEl = el("pageLabel"), searchEl = el("q"), toastEl = el("toast");

  function text(value) { return value == null ? "" : String(value); }

  function toast(message) {
    toastEl.textContent = message;
    toastEl.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { toastEl.classList.remove("show"); }, 2600);
  }

  function initData() {
    return (tg && typeof tg.initData === "string") ? tg.initData : "";
  }

  function botUser() {
    try { return (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) || null; }
    catch (e) { return null; }
  }

  function favKey(videoId) {
    var user = botUser();
    return (user && user.id ? user.id : "guest") + ":" + videoId;
  }

  function loadFavorites() {
    try {
      state.favorites = JSON.parse(localStorage.getItem("miniapp_fav") || "{}") || {};
    } catch (e) { state.favorites = {}; }
  }

  function saveFavorites() {
    try { localStorage.setItem("miniapp_fav", JSON.stringify(state.favorites)); } catch (e) {}
  }

  function setStatus(message) {
    statusEl.textContent = message || "";
    statusEl.hidden = !message;
  }

  function showSkeletons() {
    gridEl.hidden = false;
    gridEl.textContent = "";
    for (var i = 0; i < Math.min(PAGE_SIZE, 6); i += 1) {
      var box = document.createElement("div");
      box.className = "skeleton";
      gridEl.appendChild(box);
    }
  }

  function formatViews(count) {
    var n = Number(count) || 0;
    if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\\.0$/, "") + "M";
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\\.0$/, "") + "K";
    return String(n);
  }

  function makeCard(item) {
    var card = document.createElement("article");
    card.className = "card";

    var thumb = document.createElement("div");
    thumb.className = "thumb";
    var placeholder = document.createElement("div");
    placeholder.className = "placeholder";
    placeholder.textContent = "🎞️";
    thumb.appendChild(placeholder);
    if (item.thumbnail_url) {
      var img = document.createElement("img");
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = "";
      img.src = item.thumbnail_url;
      img.addEventListener("error", function () { img.remove(); });
      thumb.appendChild(img);
    }
    if (item.duration) {
      var dur = document.createElement("span");
      dur.className = "badge";
      dur.textContent = text(item.duration);
      thumb.appendChild(dur);
    }
    if (item.category) {
      var cat = document.createElement("span");
      cat.className = "cat";
      cat.textContent = text(item.category);
      thumb.appendChild(cat);
    }
    card.appendChild(thumb);

    var body = document.createElement("div");
    body.className = "body";
    var title = document.createElement("h2");
    title.className = "title";
    title.textContent = text(item.title) || "بدون عنوان";
    body.appendChild(title);

    var meta = document.createElement("div");
    meta.className = "meta";
    var views = document.createElement("span");
    views.textContent = "👁️ " + formatViews(item.views);
    meta.appendChild(views);
    body.appendChild(meta);

    var actions = document.createElement("div");
    actions.className = "actions";

    var openBtn = document.createElement("button");
    openBtn.type = "button";
    openBtn.className = "btn open";
    openBtn.textContent = "▶ فتح";
    openBtn.addEventListener("click", function () { openVideo(item); });
    actions.appendChild(openBtn);

    var favBtn = document.createElement("button");
    favBtn.type = "button";
    favBtn.className = "btn fav";
    var key = favKey(item.id);
    var on = !!state.favorites[key];
    favBtn.setAttribute("aria-pressed", on ? "true" : "false");
    favBtn.textContent = on ? "♥ مفضل" : "♡ مفضلة";
    favBtn.addEventListener("click", function () { toggleFavorite(item.id, favBtn); });
    actions.appendChild(favBtn);

    body.appendChild(actions);
    card.appendChild(body);
    return card;
  }

  function openVideo(item) {
    if (tg && typeof tg.openTelegramLink === "function" && BOT_URL) {
      try {
        tg.openTelegramLink(BOT_URL);
        toast("افتح البوت وأرسل رقم الفيديو " + item.id);
        return;
      } catch (e) {}
    }
    toast(BOT_URL ? "افتح Bot من تيليجرام: " + BOT_URL : "افتح البوت وأرسل رقم الفيديو " + item.id);
  }

  function toggleFavorite(videoId, button) {
    var key = favKey(videoId);
    var adding = !state.favorites[key];
    var previous = !!state.favorites[key];
    state.favorites[key] = adding;
    saveFavorites();
    button.setAttribute("aria-pressed", adding ? "true" : "false");
    button.textContent = adding ? "♥ مفضل" : "♡ مفضلة";

    var data = initData();
    if (!data) {
      toast("سجّل الدخول من داخل تيليجرام لحفظ المفضلة.");
      return;
    }
    fetch("__PREFIX__/api/favorite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_id: videoId, action: adding ? "add" : "remove", initData: data })
    }).then(function (response) {
      if (response.status === 401) { throw new Error("auth"); }
      if (!response.ok) { throw new Error("http"); }
      toast(adding ? "أُضيف إلى المفضلة" : "أُزيل من المفضلة");
    }).catch(function (error) {
      state.favorites[key] = previous;
      saveFavorites();
      button.setAttribute("aria-pressed", previous ? "true" : "false");
      button.textContent = previous ? "♥ مفضل" : "♡ مفضلة";
      toast(error && error.message === "auth"
        ? "لم يتم التحقق من حسابك، سجّل الدخول من تيليجرام."
        : "تعذر حفظ المفضلة الآن.");
    });
  }

  function renderChips(categories) {
    chipsEl.textContent = "";
    var all = document.createElement("button");
    all.className = "chip";
    all.type = "button";
    all.setAttribute("data-cat", "");
    all.textContent = "الكل";
    all.addEventListener("click", function () { selectCategory("", all); });
    chipsEl.appendChild(all);
    categories.forEach(function (category) {
      if (!category || category.id == null) return;
      var chip = document.createElement("button");
      chip.className = "chip";
      chip.type = "button";
      chip.setAttribute("data-cat", String(category.id));
      chip.textContent = text(category.name) +
        (category.count ? " (" + Number(category.count) + ")" : "");
      chip.addEventListener("click", function () {
        selectCategory(String(category.id), chip);
      });
      chipsEl.appendChild(chip);
    });
    syncChips();
  }

  function syncChips() {
    var chips = chipsEl.querySelectorAll(".chip");
    for (var i = 0; i < chips.length; i += 1) {
      chips[i].setAttribute("aria-pressed",
        chips[i].getAttribute("data-cat") === state.category ? "true" : "false");
    }
  }

  function selectCategory(value, element) {
    state.category = value || "";
    syncChips();
    state.page = 0;
    loadLibrary();
  }

  function loadCategories() {
    fetch("__PREFIX__/api/categories", { headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("http");
        return response.json();
      })
      .then(function (payload) { renderChips((payload && payload.items) || []); })
      .catch(function () { /* التصنيفات اختيارية: يبقى زر الكل */ });
  }

  function loadLibrary() {
    var ticket = ++state.inflight;
    state.loading = true;
    setStatus("");
    pagerEl.hidden = true;
    showSkeletons();
    var params = ["page=" + encodeURIComponent(state.page), "page_size=" + PAGE_SIZE];
    if (state.query) params.push("query=" + encodeURIComponent(state.query));
    if (state.category) params.push("category_id=" + encodeURIComponent(state.category));
    fetch("__PREFIX__/api/library?" + params.join("&"),
          { headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("http");
        return response.json();
      })
      .then(function (payload) {
        if (ticket !== state.inflight) return;
        var items = (payload && payload.items) || [];
        state.hasMore = !!(payload && payload.has_more);
        gridEl.textContent = "";
        if (!items.length) {
          gridEl.hidden = true;
          setStatus(state.query ? "لا توجد نتائج مطابقة لبحثك."
                                : "لا توجد فيديوهات في هذا التصنيف بعد.");
        } else {
          setStatus("");
          gridEl.hidden = false;
          items.forEach(function (item) { gridEl.appendChild(makeCard(item)); });
        }
        pagerEl.hidden = items.length === 0 && state.page === 0;
        prevEl.disabled = state.page <= 0;
        nextEl.disabled = !state.hasMore;
        pageLabelEl.textContent = "صفحة " + (state.page + 1);
      })
      .catch(function () {
        if (ticket !== state.inflight) return;
        gridEl.hidden = true;
        pagerEl.hidden = true;
        setStatus("تعذر تحميل المكتبة. تحقق من الاتصال ثم أعد المحاولة.");
      })
      .then(function () { state.loading = false; });
  }

  var debounceTimer = null;
  searchEl.addEventListener("input", function () {
    clearTimeout(debounceTimer);
    var value = searchEl.value.trim().slice(0, 80);
    debounceTimer = setTimeout(function () {
      state.query = value;
      state.page = 0;
      loadLibrary();
    }, 350);
  });
  searchEl.addEventListener("search", function () {
    clearTimeout(debounceTimer);
    state.query = searchEl.value.trim().slice(0, 80);
    state.page = 0;
    loadLibrary();
  });

  prevEl.addEventListener("click", function () {
    if (state.loading || state.page <= 0) return;
    state.page -= 1;
    loadLibrary();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  nextEl.addEventListener("click", function () {
    if (state.loading || !state.hasMore) return;
    state.page += 1;
    loadLibrary();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  loadFavorites();
  loadCategories();
  loadLibrary();
})();
</script>
</body>
</html>
"""


def render_page():
    """HTML المكتبة جاهزاً للنشر: بلا أسرار ولا تلقيم من الطلب."""
    safe_bot_url = bot_deep_link(os.getenv("MINIAPP_BOT_USERNAME", ""))
    page = (_PAGE_TEMPLATE
            .replace("__BOT_URL_JSON__", json.dumps(safe_bot_url))
            .replace("__PAGE_SIZE__", str(LIBRARY_PAGE_SIZE))
            .replace("__PREFIX__", URL_PREFIX))
    # الوسوم المرئية أولاً حتى تظهر العربية ونجوم التقييم بشكل صحيح،
    # ثم الأسطر المعلّقة (// ...) التي كانت تُعرض على الصفحة عند A/B فقط
    try:
        from html import unescape
        page = unescape(unescape(page))
    except Exception:  # noqa: BLE001 - الترميز الإضافي لا يجب أن يُسقط الصفحة
        logger.warning("Mini App page unescape failed; serving encoded variants")
    page = "\n".join(
        line for line in page.splitlines() if not line.strip().startswith("// ")
    )
    return page


# ==============================================================================
# المسارات
# ==============================================================================

def _json(payload, status=200, cache=True):
    from flask import jsonify
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = API_CACHE_CONTROL if cache else NO_CACHE_CONTROL
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _error(message, status):
    return _json({"error": message}, status=status, cache=False)


def _text_error(status):
    """404 بلا JSON وبلا قائمة: نص فارغ فقط (وسيط الصور)."""
    from flask import Response
    response = Response("", status=status, mimetype="text/plain")
    response.headers["Cache-Control"] = NO_CACHE_CONTROL
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _html(body, status=200):
    from flask import Response
    response = Response(body, status=status, mimetype="text/html")
    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


def _read_lock():
    """قفل قراءة رخيص لكل عميل (بلا حد يضر webview): منع البحث المتوازي المفرط فقط.

    ``hourly_limit_provider`` (اختياري) يقرأ حد فتح المكتبة لكل IP في الساعة من
    إعدادات الأدمن (0 أو غياب المزود = بلا حد).
    """
    from flask import request
    address = (request.remote_addr or "anon")
    path = request.path

    if path in _OPEN_PATHS:
        limit = 0
        try:
            if _HOURLY_LIMIT_PROVIDER is not None:
                limit = int(_HOURLY_LIMIT_PROVIDER() or 0)
        except Exception as exc:
            logger.warning("Mini App hourly limit unavailable: %s", type(exc).__name__)
            limit = 0
        if limit > 0:
            hour = int(_now() // 3600)
            with _LOCK_GUARD:
                counter = _OPEN_COUNTERS.get((address, hour))
                if counter is None:
                    _OPEN_COUNTERS.clear()
                    counter = _OPEN_COUNTERS[(address, hour)] = [0]
                if counter[0] >= limit:
                    return False
                counter[0] += 1

    now = _now()
    with _LOCK_GUARD:
        last, count = _READ_LOCKS.get((address, path), (0.0, 0))
        window = now - last >= 1.0
        current = 1 if window else count + 1
        _READ_LOCKS[(address, path)] = (now if window else last, current)
        if len(_READ_LOCKS) > 2048:
            _READ_LOCKS.clear()
        return current <= READ_BURST


_READ_LOCKS = {}
_OPEN_COUNTERS = {}
_OPEN_PATHS = frozenset({URL_PREFIX})
_HOURLY_LIMIT_PROVIDER = None
_LOCK_GUARD = threading.Lock()
READ_BURST = 45  # طلبات قراءة في الثانية للعميل الواحد


def set_hourly_limit_provider(provider):
    """تحديد مزوّد حد فتح المكتبة لكل IP (يُعاد None لإيقافه)."""
    global _HOURLY_LIMIT_PROVIDER
    _HOURLY_LIMIT_PROVIDER = provider if callable(provider) else None
    return _HOURLY_LIMIT_PROVIDER


def _read_only(func):
    """HTTP GET فقط، بلا عدّاد يضر webview، مع ترويسة منع التخزين الوسيط للأسرار."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        from flask import request
        from werkzeug.exceptions import HTTPException
        if request.method != "GET":
            return _error("method_not_allowed", 405)
        if not _read_lock():
            return _json({"error": "too_many_requests"}, status=429, cache=False)
        try:
            return func(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            # لا نص خطأ ولا معرفات حساسة في الردود
            logger.error("Mini App route %s failed: %s", request.path, type(exc).__name__)
            return _error("server_error", 500)
    return wrapper


def _require_init_data(payload):
    """(user_id, None) عند توقيع صالح، أو (None, رد 401). هوية المستخدم من initData فقط."""
    candidate = ""
    if isinstance(payload, dict):
        candidate = payload.get("initData") or payload.get("init_data") or ""
    if not isinstance(candidate, str):
        candidate = ""
    if not candidate.strip():
        return None, _error("missing_init_data", 401)
    verified = validate_init_data(candidate)
    if not verified:
        return None, _error("invalid_init_data", 401)
    user = verified.get("user") or {}
    user_id = user.get("id")
    if not isinstance(user_id, int) or user_id <= 0:
        return None, _error("missing_user", 401)
    return user_id, None


def register_miniapp(app, bot=None, hourly_limit_provider=None):
    """تسجيل مسارات Mini App على تطبيق Flask القائم (إضافي بالكامل)."""
    if app is None:
        return False
    if hourly_limit_provider is not None:
        set_hourly_limit_provider(hourly_limit_provider)
    from flask import redirect, request, url_for
    from werkzeug.exceptions import HTTPException

    rules = {getattr(rule, "rule", "") for rule in app.url_map.iter_rules()}
    if URL_PREFIX in rules:
        return False  # مسجّل مسبقاً

    def thumbnail_endpoint(file_id):
        for name in ("get_thumbnail", "thumbnail"):
            if name in app.view_functions:
                return url_for(name, file_id=file_id)
        # تطبيق بلا المسار القديم (مثلاً اختبار معزول): بناء المسار من الرابط نفسه
        return f"/thumbnail/{quote(file_id, safe='')}"

    @app.route(URL_PREFIX, methods=["GET"])
    @_read_only
    def miniapp_index():
        return _html(render_page())

    @app.route(f"{URL_PREFIX}/api/library", methods=["GET"])
    @_read_only
    def miniapp_library():
        query = (request.args.get("query") or "").strip()[:MAX_QUERY_LENGTH]
        category_id = None
        raw_category = (request.args.get("category_id") or "").strip()
        if raw_category:
            if not raw_category.isdigit():
                return _error("invalid_category", 400)
            category_id = int(raw_category)
        page = _int_value(request.args.get("page"), default=0, minimum=0)
        page_size = _int_value(request.args.get("page_size"), default=LIBRARY_PAGE_SIZE,
                               minimum=1, maximum=48)

        if query:
            videos, total = db_manager.search_videos(query, page=page)
        elif category_id is not None:
            videos, total = db_manager.get_videos(category_id, page=page)
        else:
            videos, total = db_manager.search_videos("", page=page)

        items = [_card_item(row) for row in (videos or [])]
        _with_category_names(items)
        items = [_public_item(item) for item in items]
        try:
            total = int(total or 0)
        except (TypeError, ValueError):
            total = 0
        fetched_so_far = page * page_size + len(items)
        has_more = len(items) >= page_size and fetched_so_far < total
        return _json({
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
        })

    @app.route(f"{URL_PREFIX}/api/categories", methods=["GET"])
    @_read_only
    def miniapp_categories():
        try:
            rows = _categories()
        except AttributeError:
            rows = []
        items = []
        for row in rows or []:
            name = row.get("name") if hasattr(row, "get") else None
            if not name:
                continue
            items.append({"id": row.get("id"), "name": str(name),
                          "count": int(row.get("count") or 0)})
        return _json({"items": items})

    @app.route(f"{URL_PREFIX}/api/video/<int:video_id>", methods=["GET"])
    @_read_only
    def miniapp_video(video_id):
        if video_id <= 0:
            return _error("not_found", 404)
        lookup = getattr(db_manager, "get_miniapp_video", None)
        video = lookup(video_id) if callable(lookup) else db_manager.get_video_by_id(video_id)
        if not video:
            return _error("not_found", 404)
        metadata = _metadata_dict(video)
        caption = str(video.get("caption") or "").strip()
        excerpt = caption[:400]
        rating = {"avg": 0.0, "count": 0}
        try:
            stats = db_manager.get_video_rating_stats(video_id) or {}
            rating = {"avg": round(float(stats.get("avg") or 0), 2),
                      "count": int(stats.get("count") or 0)}
        except Exception as exc:
            logger.warning("Mini App rating lookup failed: %s", type(exc).__name__)
        category = video.get("category_name") or ""
        if not category and metadata.get("category"):
            category = str(metadata.get("category"))
        return _json({
            "id": video.get("id"),
            "title": _title_for(video),
            "caption": excerpt,
            "category": str(category),
            "duration": _duration_text(video, metadata),
            "rating": rating,
            "views": int(video.get("view_count") or 0),
            "thumbnail_url": _safe_thumbnail_url(video_id),
        })

    @app.route(f"{URL_PREFIX}/api/favorite", methods=["POST"])
    def miniapp_favorite():
        try:
            payload = request.get_json(silent=True)
        except HTTPException:
            payload = None
        if not isinstance(payload, dict):
            return _error("invalid_body", 400)
        video_id = payload.get("video_id")
        if isinstance(video_id, str) and video_id.strip().isdigit():
            video_id = int(video_id.strip())
        if not isinstance(video_id, int) or isinstance(video_id, bool) or video_id <= 0:
            return _error("invalid_video_id", 400)
        action = str(payload.get("action") or "").strip().lower()
        if action not in ("add", "remove"):
            return _error("invalid_action", 400)
        user_id, failure = _require_init_data(payload)
        if failure is not None:
            return failure  # 401: لا كتابة في قاعدة البيانات
        if not _video_exists(video_id):
            return _error("not_found", 404)
        try:
            if action == "add":
                db_manager.add_to_favorites(user_id, video_id)
            else:
                db_manager.remove_from_favorites(user_id, video_id)
        except Exception as exc:
            logger.error("Mini App favorite write failed: %s", type(exc).__name__)
            return _error("server_error", 500)
        return _json({"ok": True, "action": action, "video_id": video_id}, cache=False)

    @app.route(f"{URL_PREFIX}/thumb/<int:video_id>", methods=["GET"])
    @_read_only
    def miniapp_thumb(video_id):
        if video_id <= 0:
            return _text_error(404)
        file_id = _thumbnail_file_id(video_id)
        if not file_id:
            return _text_error(404)
        response = redirect(thumbnail_endpoint(file_id), code=302)
        # لا يصل file_id إلى العميل ولا إلى وسائط التخزين
        response.headers["Cache-Control"] = API_CACHE_CONTROL
        return response

    logger.info("✅ Mini App registered under %s (page, library, categories, video, favorite, thumb)",
                URL_PREFIX)
    return True
