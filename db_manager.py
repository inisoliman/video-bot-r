# ==============================================================================
# db_manager.py (compat layer additions): restore handler-facing functions
# ==============================================================================

import json

# ==== Backward-compatible functions used by handlers ====

def add_bot_user(user_id, username, first_name):
    return execute_query(
        "INSERT INTO bot_users (user_id, username, first_name) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
        (user_id, username, first_name),
        commit=True
    )


def get_user_favorites(user_id, page=0):
    offset = page * VIDEOS_PER_PAGE
    videos_query = """
        SELECT v.* FROM video_archive v
        JOIN user_favorites f ON v.id = f.video_id
        WHERE f.user_id = %s
        ORDER BY f.date_added DESC
        LIMIT %s OFFSET %s
    """
    videos = execute_query(videos_query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    total = execute_query("SELECT COUNT(*) as count FROM user_favorites WHERE user_id = %s", (user_id,), fetch="one")
    return videos, (total['count'] if total else 0)


def get_user_history(user_id, page=0):
    offset = page * VIDEOS_PER_PAGE
    videos_query = """
        SELECT v.* FROM video_archive v
        JOIN user_history h ON v.id = h.video_id
        WHERE h.user_id = %s
        ORDER BY h.last_watched DESC
        LIMIT %s OFFSET %s
    """
    videos = execute_query(videos_query, (user_id, VIDEOS_PER_PAGE, offset), fetch="all")
    total = execute_query("SELECT COUNT(*) as count FROM user_history WHERE user_id = %s", (user_id,), fetch="one")
    return videos, (total['count'] if total else 0)


def add_to_history(user_id, video_id):
    query = """
        INSERT INTO user_history (user_id, video_id, last_watched) 
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, video_id) DO UPDATE SET last_watched = CURRENT_TIMESTAMP
    """
    return execute_query(query, (user_id, video_id), commit=True)


def increment_video_view_count(video_id):
    return execute_query("UPDATE video_archive SET view_count = view_count + 1 WHERE id = %s", (video_id,), commit=True)


def get_random_video():
    return execute_query("SELECT * FROM video_archive ORDER BY RANDOM() LIMIT 1", fetch="one")


def get_active_category_id():
    res = execute_query("SELECT setting_value FROM bot_settings WHERE setting_key = 'active_category_id'", fetch="one")
    return int(res['setting_value']) if res and (res['setting_value'] or '').isdigit() else None


def set_active_category_id(category_id):
    return execute_query(
        "INSERT INTO bot_settings (setting_key, setting_value) VALUES ('active_category_id', %s) "
        "ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
        (str(category_id),),
        commit=True
    )


def add_video(message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id=None):
    metadata_json = json.dumps(metadata or {})
    query = """
        INSERT INTO video_archive (message_id, caption, chat_id, file_name, file_id, metadata, grouping_key, category_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (message_id) DO UPDATE SET
            caption = EXCLUDED.caption,
            file_name = EXCLUDED.file_name,
            file_id = EXCLUDED.file_id,
            metadata = EXCLUDED.metadata,
            grouping_key = EXCLUDED.grouping_key,
            category_id = EXCLUDED.category_id
        RETURNING id
    """
    params = (message_id, caption, chat_id, file_name, file_id, metadata_json, grouping_key, category_id)
    result = execute_query(query, params, fetch="one", commit=True)
    return result['id'] if result else None


def add_required_channel(channel_id, channel_name):
    return execute_query(
        "INSERT INTO required_channels (channel_id, channel_name) VALUES (%s, %s) ON CONFLICT(channel_id) DO NOTHING",
        (str(channel_id), channel_name),
        commit=True
    )


def remove_required_channel(channel_id):
    return execute_query("DELETE FROM required_channels WHERE channel_id = %s", (str(channel_id),), commit=True)


def get_required_channels():
    return execute_query("SELECT * FROM required_channels", fetch="all")


def get_video_by_id(video_id):
    return execute_query("SELECT * FROM video_archive WHERE id = %s", (video_id,), fetch="one")


def move_video_to_category(video_id, new_category_id):
    return execute_query("UPDATE video_archive SET category_id = %s WHERE id = %s", (new_category_id, video_id), commit=True)


def delete_videos_by_ids(video_ids):
    if not video_ids:
        return 0
    res = execute_query("DELETE FROM video_archive WHERE id = ANY(%s) RETURNING id", (video_ids,), fetch="all", commit=True)
    return len(res) if isinstance(res, list) else 0


def delete_category_and_contents(category_id):
    execute_query("DELETE FROM video_archive WHERE category_id = %s", (category_id,), commit=True)
    execute_query("DELETE FROM categories WHERE id = %s", (category_id,), commit=True)
    return True


def move_videos_from_category(old_category_id, new_category_id):
    return execute_query("UPDATE video_archive SET category_id = %s WHERE category_id = %s", (new_category_id, old_category_id), commit=True)


def delete_category_by_id(category_id):
    return execute_query("DELETE FROM categories WHERE id = %s", (category_id,), commit=True)


def add_video_rating(video_id, user_id, rating):
    return execute_query(
        "INSERT INTO video_ratings (video_id, user_id, rating) VALUES (%s, %s, %s) "
        "ON CONFLICT (video_id, user_id) DO UPDATE SET rating = EXCLUDED.rating",
        (video_id, user_id, rating),
        commit=True
    )


def get_video_rating_stats(video_id):
    return execute_query("SELECT AVG(rating) as avg, COUNT(id) as count FROM video_ratings WHERE video_id = %s",
                         (video_id,), fetch="one")


def get_user_video_rating(video_id, user_id):
    res = execute_query("SELECT rating FROM video_ratings WHERE video_id = %s AND user_id = %s",
                        (video_id, user_id), fetch="one")
    return res['rating'] if res else None


def is_video_favorite(user_id, video_id):
    res = execute_query("SELECT 1 FROM user_favorites WHERE user_id = %s AND video_id = %s",
                        (user_id, video_id), fetch="one")
    return bool(res)


def add_to_favorites(user_id, video_id):
    return execute_query("INSERT INTO user_favorites (user_id, video_id) VALUES (%s, %s) ON CONFLICT (user_id, video_id) DO NOTHING",
                         (user_id, video_id), commit=True)


def remove_from_favorites(user_id, video_id):
    return execute_query("DELETE FROM user_favorites WHERE user_id = %s AND video_id = %s",
                         (user_id, video_id), commit=True)
