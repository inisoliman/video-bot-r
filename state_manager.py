import json
import logging
from typing import Optional, Dict, Any, Callable
from db_manager import set_user_state, get_user_state, clear_user_state

logger = logging.getLogger(__name__)

# حالات تُعالج في user_handlers (نصوص حرة يتحقق الحارس من هوية المستخدم قبلها)
DELEGATED_STATES = frozenset({
    'waiting_comment',
    'replying_comment',
    'editing_display_title',
    'waiting_title_video_id',
    'title_rename_search',
    'title_rename_new',
})

# الحالات التي تنتظر رسالة حرة من الأدمن (نص/صورة/وسائط/بث): لا تأكل الأوامر،
# وتُطلب عندها رسالة التلميح (prompt_message_id) لربط الإدخال بالطلب
ADMIN_PROMPT_STATES = frozenset({
    'waiting_category_name',
    'waiting_channel_id',
    'waiting_channel_name',
    'waiting_remove_channel_id',
    'waiting_move_video_id',
    'waiting_delete_video_ids',
    'waiting_broadcast_message',
    'waiting_default_thumb',
    'waiting_video_id_for_thumb',
    'waiting_new_thumb_for_video',
    'waiting_miniapp_limit',
    'waiting_miniapp_ip',
    # مراحل تأكيد/اختيار بنص حر داخل تدفقات الأدمن (تُعالج في handlers.admin_flows)
    'waiting_move_target',
    'waiting_delete_confirm',
})

# حالات تعتمد على نص المستخدم مباشرة: لا يجب أن تستهلك أوامر /command
TEXT_INPUT_STATES = frozenset({
    'waiting_comment',
    'replying_comment',
    'editing_display_title',
    'waiting_title_video_id',
    'title_rename_search',
    'title_rename_new',
    'waiting_search_query',
}) | ADMIN_PROMPT_STATES

# State constants
class States:
    WAITING_CHANNEL_ID = "waiting_channel_id"
    WAITING_CHANNEL_NAME = "waiting_channel_name"
    WAITING_CATEGORY_NAME = "waiting_category_name"
    WAITING_MOVE_VIDEO_ID = "waiting_move_video_id"
    WAITING_DELETE_VIDEO_IDS = "waiting_delete_video_ids"
    WAITING_BROADCAST_MESSAGE = "waiting_broadcast_message"
    WAITING_SEARCH_QUERY = "waiting_search_query"
    WAITING_REMOVE_CHANNEL_ID = "waiting_remove_channel_id"
    # Admin roles for thumbnail management
    WAITING_DEFAULT_THUMB = "waiting_default_thumb"
    WAITING_VIDEO_ID_FOR_THUMB = "waiting_video_id_for_thumb"
    WAITING_NEW_THUMB_FOR_VIDEO = "waiting_new_thumb_for_video"
    # إعدادات التطبيق المصغّر من لوحة الأدمن
    WAITING_MINIAPP_LIMIT = "waiting_miniapp_limit"
    WAITING_MINIAPP_IP = "waiting_miniapp_ip"


def _parse_context(raw) -> Dict[str, Any]:
    """سياق الحالة كقاموس دائماً (JSONB نصي/تالف/غياب المفتاح لا يرفع استثناءً)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("Stored user state context is malformed JSON; treating it as empty")
            return {}
    return raw if isinstance(raw, dict) else {}


def read_state_row(user_id: int) -> Optional[Dict[str, Any]]:
    """صف الحالة المخزّن (state + context) بعد تطبيع JSONB النصي، أو None."""
    try:
        result = get_user_state(user_id)
    except Exception as exc:
        logger.error("Could not read user state for %s: %s", user_id, type(exc).__name__)
        return None
    if not result:
        return None
    if hasattr(result, 'get'):
        state = result.get('state')
        raw_context = result.get('context')
    else:
        state = getattr(result, 'state', None)
        raw_context = getattr(result, 'context', None)
    return {'state': state, 'context': _parse_context(raw_context)}


class StateManager:
    """Manages user conversation states using database storage."""

    def __init__(self):
        self.handlers: Dict[str, Callable] = {}

    def register_handler(self, state: str, handler: Callable):
        """Register a handler for a specific state."""
        self.handlers[state] = handler
        logger.info(f"Registered handler for state: {state}")

    def set_state(self, user_id: int, state: str, context: Optional[Dict[str, Any]] = None):
        """Set user state with optional context.

        يعيد True فقط عند تأكيد الحفظ (قيمة set_user_state الصريحة)، وFalse عند فشل
        القاعدة الذي يُعاد كقيمة بدل الاستثناء — حتى لا يُعلن مستدعٍ نجاحاً وهمياً.
        لا يُسجَّل السياق نفسه: قد يحمل نص بث.
        """
        try:
            saved = set_user_state(user_id, state, context)
        except Exception as exc:
            logger.error("Could not store state '%s' for %s: %s", state, user_id, type(exc).__name__)
            return False
        if saved is not True:
            logger.error("State '%s' for %s was not confirmed by the database", state, user_id)
            return False
        keys = sorted(context.keys()) if isinstance(context, dict) else []
        logger.info(f"Set state '{state}' for user {user_id} (context keys: {keys})")
        return True

    def get_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user state and context."""
        return read_state_row(user_id)

    def get_user_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Alias for get_state method for backward compatibility."""
        return self.get_state(user_id)

    def clear_state(self, user_id: int) -> bool:
        """Clear user state; True only when the database confirmed the delete."""
        try:
            cleared = clear_user_state(user_id)
        except Exception as exc:
            logger.error("Could not clear state for %s: %s", user_id, type(exc).__name__)
            return False
        if cleared is not True:
            logger.error("Clearing state for %s was not confirmed by the database", user_id)
            return False
        logger.info(f"Cleared state for user {user_id}")
        return True

    def handle_message(self, message, bot):
        """Handle incoming message based on user state."""
        user_id = message.from_user.id
        text = (getattr(message, 'text', None) or "").strip()

        # /cancel يُعالج قبل توجيه أي حالة، فلا يستهلكه معالج حالة ولا يصل كعنوان/رد.
        # يمسح الحالة المحفوظة دائماً، ويؤكد الإلغاء فقط إن كانت هناك عملية قائمة
        # ونجح مسحها فعلاً (فشل المسح يُبلَّغ صراحةً بدل إعلان نجاح وهمي).
        if text == "/cancel" or text.startswith("/cancel "):
            had_state = self.get_state(user_id) is not None
            cleared = self.clear_state(user_id)
            if not cleared:
                bot.reply_to(message, "❌ تعذّر إلغاء العملية (خطأ في قاعدة البيانات). حاول مرة أخرى.")
            elif had_state:
                bot.reply_to(message, "✅ تم إلغاء العملية")
            else:
                bot.reply_to(message, "ℹ️ لا توجد عملية قائمة لإلغائها.")
            return True

        user_state = self.get_state(user_id)

        if not user_state:
            return False  # No state, let normal handlers process

        state = user_state['state']
        context = user_state.get('context') or {}

        # الأوامر لا تدخل حالة نصية مفتوحة (مثل عنوان جديد أو رد)
        if text.startswith("/") and state in TEXT_INPUT_STATES:
            return False  # Let the command handlers process it

        # حالات التعليقات والعناوين تُعالج في user_handlers مباشرة
        if state in DELEGATED_STATES:
            return False  # Let user_handlers process these

        # حالات الأدمن ذات الطلب المفتوح (بث/تصنيف/قناة/نقل/حذف/إعدادات/صور مصغرة)
        # تُمرَّر إلى معالج إدخال الأدمن المسجّل في handlers.admin_handlers
        # (admin_state_input -> handle_broadcast_message/handle_flow_message/thumbnails).
        # تُمرَّر دائماً كي يتحقق ذلك المعالج من الملكية والصلاحية ويجيب برسالة صادقة
        # (أو يتجاهل رسالة غير أدمن)؛ لا تسجّل "No handler found" هنا.
        if state in ADMIN_PROMPT_STATES:
            return False  # Let the admin handlers process these

        if state in self.handlers:
            try:
                # Call the appropriate handler
                self.handlers[state](message, bot, context)
                return True
            except Exception as e:
                logger.error(f"Error handling state '{state}' for user {user_id}: {e}")
                # لا ندّعي إلغاءً تم فعلاً إلا إذا أكدت القاعدة المسح
                if self.clear_state(user_id) is True:
                    bot.reply_to(message, "حدث خطأ. تم إلغاء العملية الحالية.")
                else:
                    bot.reply_to(message, "حدث خطأ، وتعذّر تنظيف الحالة (خطأ في قاعدة البيانات). أرسل /cancel.")
                return True
        else:
            logger.warning(f"No handler found for state '{state}'")
            return False

    def is_user_in_state(self, user_id: int, state: str = None) -> bool:
        """Check if user is in a specific state or any state."""
        user_state = self.get_state(user_id)
        if not user_state:
            return False

        if state:
            return user_state['state'] == state
        return True  # User is in some state

# Global state manager instance
state_manager = StateManager()

# Decorator for state handlers
def state_handler(state: str):
    """Decorator to register state handlers."""
    def decorator(func):
        state_manager.register_handler(state, func)
        return func
    return decorator

# Helper functions for common state operations
def set_user_waiting_for_input(user_id: int, state: str, context: Dict[str, Any] = None) -> bool:
    """Set user to wait for specific input; True only when the database confirmed it."""
    return state_manager.set_state(user_id, state, context)

def clear_user_waiting_state(user_id: int) -> bool:
    """Clear user waiting state; True only when the database confirmed the delete."""
    return state_manager.clear_state(user_id)

def get_user_waiting_context(user_id: int) -> Optional[Dict[str, Any]]:
    """Get user's waiting context."""
    user_state = state_manager.get_state(user_id)
    if not user_state:
        return None
    return user_state.get('context')

def is_user_waiting(user_id: int) -> bool:
    """Check if user is waiting for input."""
    return state_manager.is_user_in_state(user_id)

def current_state(user_id: int) -> Optional[str]:
    """اسم الحالة الحالية للمستخدم أو None (بلا استثناء عند فشل القراءة)."""
    user_state = read_state_row(user_id)
    return user_state.get('state') if user_state else None
