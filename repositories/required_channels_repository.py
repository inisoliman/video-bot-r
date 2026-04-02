"""repositories/required_channels_repository.py"""

from core.db import execute_query
import logging

logger = logging.getLogger(__name__)

def add_required_channel(channel_id, channel_name):
    query = """
        INSERT INTO required_channels (channel_id, channel_name)
        VALUES (%s, %s)
        ON CONFLICT (channel_id) DO UPDATE SET
            channel_name = EXCLUDED.channel_name;
    """
    return execute_query(query, (channel_id, channel_name), commit=True)

def get_required_channels():
    query = "SELECT channel_id, channel_name FROM required_channels;"
    return execute_query(query, fetch="all")

def remove_required_channel(channel_id):
    query = "DELETE FROM required_channels WHERE channel_id = %s;"
    return execute_query(query, (channel_id,), commit=True)
