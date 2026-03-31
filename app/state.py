from .repositories.user_repo import get_user_state, set_user_state, clear_user_state


def get_state(user_id):
    return get_user_state(user_id)


def set_state(user_id, state, context=None):
    set_user_state(user_id, state, context)


def clear_state(user_id):
    clear_user_state(user_id)
