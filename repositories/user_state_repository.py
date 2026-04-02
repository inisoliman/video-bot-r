
# repositories/user_state_repository.py

from core.db import execute_query
import logging
import json

logger = logging.getLogger(__name__)

def set_user_state(user_id, state, context=None):
    query = """
        INSERT INTO user_states (user_id, state, context)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            state = EXCLUDED.state,
            context = EXCLUDED.context;
    """
    return execute_query(query, (user_id, state, json.dumps(context) if context else None), commit=True)

def get_user_state(user_id):
    query = "SELECT state, context FROM user_states WHERE user_id = %s;"
    result = execute_query(query, (user_id,), fetch="one")
    if result and result["context"]:
        result["context"] = json.loads(result["context"])
    return result

def clear_user_state(user_id):
    query = "DELETE FROM user_states WHERE user_id = %s;"
    return execute_query(query, (user_id,), commit=True)
