
# core/state_manager.py

import logging
from enum import Enum, auto
from repositories import user_state_repository

logger = logging.getLogger(__name__)

class States(Enum):
    # General states
    START = auto()
    MAIN_MENU = auto()
    WAITING_SEARCH_QUERY = auto()
    WAITING_COMMENT_TEXT = auto()
    WAITING_ADMIN_REPLY = auto()
    WAITING_BROADCAST_MESSAGE = auto()
    WAITING_VIDEO_ID_FOR_MOVE = auto()
    WAITING_CATEGORY_FOR_MOVE = auto()

    # Admin states
    ADMIN_PANEL = auto()
    ADMIN_ADD_CHANNEL = auto()
    ADMIN_REMOVE_CHANNEL = auto()
    ADMIN_ADD_CATEGORY = auto()
    ADMIN_RENAME_CATEGORY = auto()
    ADMIN_DELETE_CATEGORY = auto()
    ADMIN_BROADCAST_CONFIRM = auto()
    ADMIN_CONFIRM_DELETE_COMMENT = auto()
    ADMIN_CONFIRM_DELETE_ALL_COMMENTS = auto()
    ADMIN_CONFIRM_DELETE_USER_COMMENTS = auto()
    ADMIN_CONFIRM_DELETE_OLD_COMMENTS = auto()

class StateManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._handlers = {}
                logger.info("StateManager initialized.")
            return cls._instance

    def register_handler(self, state, handler_func):
        if not isinstance(state, States):
            raise ValueError("State must be an instance of States Enum.")
        self._handlers[state] = handler_func
        logger.debug(f"Registered handler for state: {state.name}")

    def get_handler(self, state):
        return self._handlers.get(state)

    def set_user_state(self, user_id, state, context=None):
        if not isinstance(state, States):
            raise ValueError("State must be an instance of States Enum.")
        logger.debug(f"Setting state for user {user_id} to {state.name} with context {context}")
        return user_state_repository.set_user_state(user_id, state.name, context)

    def get_user_state(self, user_id):
        state_data = user_state_repository.get_user_state(user_id)
        if state_data and state_data["state"]:
            try:
                state_enum = States[state_data["state"]]
                return {"state": state_enum, "context": state_data["context"]}
            except KeyError:
                logger.warning(f"Invalid state '{state_data["state"]}' found for user {user_id}. Clearing state.")
                self.clear_user_state(user_id)
                return None
        return None

    def clear_user_state(self, user_id):
        logger.debug(f"Clearing state for user {user_id}")
        return user_state_repository.clear_user_state(user_id)

    def handle_message(self, message, bot):
        user_id = message.from_user.id
        current_state = self.get_user_state(user_id)

        if current_state and current_state["state"]:
            handler = self.get_handler(current_state["state"])
            if handler:
                logger.info(f"Handling message for user {user_id} in state {current_state["state"].name}")
                handler(message, bot, current_state["context"])
                return True
        return False

# Singleton instance
state_manager = StateManager()

def state_handler(state):
    def decorator(func):
        state_manager.register_handler(state, func)
        return func
    return decorator

def set_user_waiting_for_input(user_id, state, context=None):
    state_manager.set_user_state(user_id, state, context)

def clear_user_waiting_state(user_id):
    state_manager.clear_user_state(user_id)

def get_user_waiting_context(user_id):
    state_data = state_manager.get_user_state(user_id)
    return state_data["context"] if state_data else None

import threading # Required for _lock
