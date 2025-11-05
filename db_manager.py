# Re-expose legacy functions for handlers compatibility
from psycopg2.extras import DictCursor
import json
from . import *  # keep existing symbols

# add_bot_user

def add_bot_user(user_id, username, first_name):
    return execute_query(
        "INSERT INTO bot_users (user_id, username, first_name) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
        (user_id, username, first_name), commit=True)

# get_random_video

def get_random_video():
    return execute_query("SELECT * FROM video_archive ORDER BY RANDOM() LIMIT 1", fetch='one')

# increment_video_view_count

def increment_video_view_count(video_id):
    return execute_query("UPDATE video_archive SET view_count = view_count + 1 WHERE id = %s", (video_id,), commit=True)

# get_categories_tree

def get_categories_tree():
    return execute_query("SELECT * FROM categories ORDER BY name", fetch='all')

# add_video

def add_video(message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id=None):
    metadata_json = json.dumps(metadata)
    row = execute_query(
        """
        INSERT INTO video_archive (message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (message_id) DO UPDATE SET caption=EXCLUDED.caption,file_name=EXCLUDED.file_name,file_id=EXCLUDED.file_id,metadata=EXCLUDED.metadata,grouping_key=EXCLUDED.grouping_key,category_id=EXCLUDED.category_id
        RETURNING id
        """,
        (message_id, caption, chat_id, file_name, file_id, metadata_json, grouping_key, category_id), fetch='one', commit=True)
    return row['id'] if row else None

# get_popular_videos

def get_popular_videos():
    most_viewed = execute_query(
        "SELECT * FROM video_archive ORDER BY view_count DESC, id DESC LIMIT 10", fetch='all'
    )
    highest_rated = execute_query(
        """
        SELECT v.*, r.avg_rating 
        FROM video_archive v 
        JOIN (
            SELECT video_id, AVG(rating) as avg_rating 
            FROM video_ratings 
            GROUP BY video_id
        ) r ON v.id = r.video_id 
        ORDER BY r.avg_rating DESC, v.view_count DESC 
        LIMIT 10
        """, fetch='all'
    )
    return {"most_viewed": most_viewed, "highest_rated": highest_rated}
