
# repositories/user_repository.py

from core.db import execute_query
import logging

logger = logging.getLogger(__name__)

def add_bot_user(user_id, username, first_name):
    query = """
        INSERT INTO bot_users (user_id, username, first_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name;
    """
    return execute_query(query, (user_id, username, first_name), commit=True)

def get_all_users():
    query = "SELECT user_id FROM bot_users;"
    return execute_query(query, fetch="all")

def get_user_by_id(user_id):
    query = "SELECT * FROM bot_users WHERE user_id = %s;"
    return execute_query(query, (user_id,), fetch="one")
