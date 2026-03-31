from ..database import execute


def add_comment(user_id, video_id, username, comment_text):
    return execute('INSERT INTO video_comments (video_id, user_id, username, comment_text) VALUES (%s, %s, %s, %s)',
                   (video_id, user_id, username, comment_text))


def get_comments(video_id, unread_only=False):
    if unread_only:
        return execute('SELECT * FROM video_comments WHERE video_id = %s AND is_read = FALSE ORDER BY created_at DESC', (video_id,), fetch='all')
    return execute('SELECT * FROM video_comments WHERE video_id = %s ORDER BY created_at DESC', (video_id,), fetch='all')


def mark_comment_read(comment_id):
    return execute('UPDATE video_comments SET is_read = TRUE, replied_at = NOW() WHERE id = %s', (comment_id,))
