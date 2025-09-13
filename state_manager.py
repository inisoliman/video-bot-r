import json
import logging
from typing import Optional, Dict, Any, Callable
from db_manager import set_user_state, get_user_state, clear_user_state

logger = logging.getLogger(__name__)

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

class StateManager:
    """Manages user conversation states using database storage."""
    
    def __init__(self):
        self.handlers: Dict[str, Callable] = {}
    
    def register_handler(self, state: str, handler: Callable):
        """Register a handler for a specific state."""
        self.handlers[state] = handler
        logger.info(f"Registered handler for state: {state}")
    
    def set_state(self, user_id: int, state: str, context: Optional[Dict[str, Any]] = None):
        """Set user state with optional context."""
        set_user_state(user_id, state, context)
        logger.info(f"Set state '{state}' for user {user_id} with context: {context}")
    
    def get_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user state and context."""
        result = get_user_state(user_id)
        if result:
            context = json.loads(result['context']) if result['context'] else None
            return {
                'state': result['state'],
                'context': context
            }
        return None
    
    def get_user_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Alias for get_state method for backward compatibility."""
        return self.get_state(user_id)
    
    def clear_state(self, user_id: int):
        """Clear user state."""
        clear_user_state(user_id)
        logger.info(f"Cleared state for user {user_id}")
    
    def handle_message(self, message, bot):
        """Handle incoming message based on user state."""
        user_id = message.from_user.id
        user_state = self.get_state(user_id)
        
        if not user_state:
            return False  # No state, let normal handlers process
        
        state = user_state['state']
        context = user_state.get('context', {})
        
        if state in self.handlers:
            try:
                # Call the appropriate handler
                self.handlers[state](message, bot, context)
                return True
            except Exception as e:
                logger.error(f"Error handling state '{state}' for user {user_id}: {e}")
                self.clear_state(user_id)
                bot.reply_to(message, "حدث خطأ. تم إلغاء العملية الحالية.")
                return True
        else:
            logger.warning(f"No handler found for state '{state}'")
            self.clear_state(user_id)
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
def set_user_waiting_for_input(user_id: int, state: str, context: Dict[str, Any] = None):
    """Set user to wait for specific input."""
    state_manager.set_state(user_id, state, context)

def clear_user_waiting_state(user_id: int):
    """Clear user waiting state."""
    state_manager.clear_state(user_id)

def get_user_waiting_context(user_id: int) -> Optional[Dict[str, Any]]:
    """Get user's waiting context."""
    user_state = state_manager.get_state(user_id)
    return user_state.get('context') if user_state else None

def is_user_waiting(user_id: int) -> bool:
    """Check if user is waiting for input."""
    return state_manager.is_user_in_state(user_id)