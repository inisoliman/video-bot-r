from ..database import execute


def add_user(user_id, username, first_name):
    return execute('INSERT INTO bot_users (user_id, username, first_name) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name',
                   (user_id, username, first_name))


def add_favorite(user_id, video_id):
    return execute('INSERT INTO user_favorites (user_id, video_id) VALUES (%s, %s) ON CONFLICT DO NOTHING', (user_id, video_id))


def remove_favorite(user_id, video_id):
    return execute('DELETE FROM user_favorites WHERE user_id = %s AND video_id = %s', (user_id, video_id))


def get_user_favorites(user_id, page=0, per_page=10):
    offset = page * per_page
    videos = execute('SELECT v.* FROM video_archive v JOIN user_favorites f ON f.video_id = v.id WHERE f.user_id = %s ORDER BY f.date_added DESC LIMIT %s OFFSET %s',
                     (user_id, per_page, offset), fetch='all')
    total = execute('SELECT COUNT(*) as total FROM user_favorites WHERE user_id = %s', (user_id,), fetch='one')
    return videos or [], total['total'] if total else 0


def add_history(user_id, video_id):
    return execute('INSERT INTO user_history (user_id, video_id, last_watched) VALUES (%s, %s, NOW()) ON CONFLICT (user_id, video_id) DO UPDATE SET last_watched = NOW()',
                   (user_id, video_id))


def get_user_history(user_id, page=0, per_page=10):
    offset = page * per_page
    videos = execute('SELECT v.* FROM video_archive v JOIN user_history h ON h.video_id = v.id WHERE h.user_id = %s ORDER BY h.last_watched DESC LIMIT %s OFFSET %s',
                     (user_id, per_page, offset), fetch='all')
    total = execute('SELECT COUNT(*) as total FROM user_history WHERE user_id = %s', (user_id,), fetch='one')
    return videos or [], total['total'] if total else 0


def set_user_state(user_id, state, context):
    return execute('INSERT INTO user_states (user_id, state, context) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET state = EXCLUDED.state, context = EXCLUDED.context',
                   (user_id, state, context))


def get_user_state(user_id):
    return execute('SELECT * FROM user_states WHERE user_id = %s', (user_id,), fetch='one')


def clear_user_state(user_id):
    return execute('DELETE FROM user_states WHERE user_id = %s', (user_id,))
