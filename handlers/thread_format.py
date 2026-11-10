"""HTML formatting helpers for private inquiry threads (offline-testable).

No user text is ever truncated silently: long bodies are split into complete
Telegram-sized chunks and long threads into pages, so every original message is
readable. Escaping happens per source segment, never on an already-escaped string.
"""

from datetime import datetime
from html import escape

MEMBER_LABEL = "👤 المستخدم"
ADMIN_LABEL = "👨‍💻 الإدارة"

MAX_MESSAGE_LENGTH = 4000
MAX_TITLE_CHARS = 80
# ميزانية النص في الصفحة الواحدة (حتى تبقى الرسائل مقروءة ولا تُقطع)
PAGE_BODY_BUDGET = 3000

STATUS_WAITING = "waiting"    # آخر رسالة من العضو → الإدارة تنتظر الرد
STATUS_ANSWERED = "answered"  # آخر رسالة من الإدارة → العضو تمت الإجابة
STATUS_READ = "read"          # تمت قراءة آخر رسالة من الطرف الآخر


def escape_html(value):
    """تهريب HTML لكل نص يقدمه المستخدم أو الأدمن."""
    if value is None:
        return ""
    return escape(str(value), quote=False)


def clip(value, limit):
    """تقليم نص عادي (غير مهروب) لاستخدامات العناوين فقط."""
    text = value or ""
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def safe_html(value, limit=None):
    """تهريب نص حر؛ التقليم فقط عندما يمرَّر حد (للعناوين)، لا للنصوص الكاملة."""
    text = escape_html(value)
    if limit is None:
        return text
    return clip(text, limit)


def label_for_role(role):
    return ADMIN_LABEL if role == "admin" else MEMBER_LABEL


def chunk_text(text, size=MAX_MESSAGE_LENGTH):
    """تقطيع نص طويل إلى قطع كاملة بلا فقدان حروف (تقاطع عند حدود منطقية)."""
    text = text or ""
    if size <= 0:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind("\n", start, end)
            if cut <= start + size // 2:
                cut = text.rfind(" ", start, end)
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end])
        start = end
    return chunks or [""]


def format_thread_message(message):
    """HTML لرسالة واحدة (كامل النص، مهروب بأمان) مع ترويسة كاتبها."""
    message = message or {}
    body = escape_html(message.get('body'))
    if not body.strip():
        body = "<i>(فارغ)</i>"
    return f"<b>{label_for_role(message.get('author_role'))}:</b>\n{body}"


def status_label(state, viewer_role):
    """وصف واضح لحالة المحادثة (قرئت / في انتظار الرد) من منظور الطرف القارئ."""
    if state == STATUS_WAITING:
        return "⏳ في انتظار رد الإدارة" if viewer_role == "member" else "🔔 ينتظر الرد"
    if state == STATUS_ANSWERED:
        return "✅ ردت الإدارة" if viewer_role == "member" else "✅ تم الرد (بانتظار قراءة العضو)"
    if state == STATUS_READ:
        return "📖 تمت قراءة آخر رسالة" if viewer_role == "member" else "📖 مقروء"
    return "ℹ️ غير محدد"


def _as_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def compute_thread_state(latest, read_at, viewer_role="admin"):
    """
    حالة المحادثة من منظور الطرف الذي يقرأ.

    - آخر رسالة من العضو + لم تُقرأ → waiting (ينتظر الرد)
    - آخر رسالة من العضو + قُرئت بعدها → read (ليست بانتظار الرد)
    - آخر رسالة من الإدارة → answered
    """
    if latest is None:
        return STATUS_WAITING
    last_role = latest.get('author_role')
    if last_role == "admin":
        return STATUS_ANSWERED
    last_time = _as_datetime(latest.get('created_at'))
    read_time = _as_datetime(read_at)
    if read_time is not None and last_time is not None and read_time >= last_time:
        return STATUS_READ
    return STATUS_WAITING


def thread_fingerprint(latest):
    """بصمة قصيرة لآخر رسالة - تُربط طلب الرد/المتابعة بالمحادثة وقت طلبه."""
    if latest is None:
        return "0"
    return f"{latest.get('id', 0)}"


def paginate_messages(messages, budget=PAGE_BODY_BUDGET):
    """
    تقسيم رسائل (مرتبة) إلى صفحات بحجم نص كامل دون قطع أي رسالة:
    كل صفحة قائمة رسائل، والرسالة الطويلة تُقسم على عدة صفحات كاملة.

    Returns:
        list[list[dict]] حيث كل dict نسخة من الرسالة مع حقل chunks (قائمة HTML كاملة)
    """
    pages = [[]]
    used = 0
    for message in messages:
        rendered = format_thread_message(message)
        pieces = chunk_text(rendered, budget)
        for index, piece in enumerate(pieces):
            item = dict(message)
            item['chunk'] = piece
            item['chunk_index'] = index
            item['chunk_total'] = len(pieces)
            if pages[-1] and used + len(piece) > budget:
                pages.append([])
                used = 0
            pages[-1].append(item)
            used += len(piece)
    return pages


def build_thread_text(comment, page_items, page, total_pages, viewer_role, prompt_hint=None):
    """نص HTML لصفحة محادثة واحدة (رسائل كاملة + حالة + تلميح الطلب)."""
    comment = comment or {}
    title = safe_html(
        comment.get('display_title') or comment.get('video_caption') or comment.get('video_name') or "فيديو",
        MAX_TITLE_CHARS,
    )
    header = f"💬 <b>محادثة #{comment.get('id', '?')}</b>\n📹 الفيديو: {title}\n"
    if viewer_role == "admin":
        owner = safe_html(comment.get('username') or comment.get('user_id'), 40)
        header += f"👤 العضو: @{owner} (ID: {comment.get('user_id', '?')})\n"
    header += f"📄 صفحة {page + 1}/{max(total_pages, 1)}\n\n"

    parts = []
    for item in page_items or []:
        chunk = item.get('chunk')
        if chunk is None:
            chunk = format_thread_message(item)
        if item.get('chunk_total', 1) > 1:
            chunk = f"<i>(تكملة {item.get('chunk_index', 0) + 1}/{item.get('chunk_total')})</i>\n{chunk}"
        parts.append(chunk)

    body = "\n\n".join(parts) if parts else "<i>لا توجد رسائل بعد.</i>"
    text = header + body
    if prompt_hint:
        text += f"\n\n{prompt_hint}"
    return text
