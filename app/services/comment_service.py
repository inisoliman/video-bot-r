from ..repositories import comment_repo


def add_comment(user_id, video_id, username, comment_text):
    return comment_repo.add_comment(user_id, video_id, username, comment_text)


def get_comments(video_id, unread_only=False):
    return comment_repo.get_comments(video_id, unread_only)


def mark_comment_read(comment_id):
    return comment_repo.mark_comment_read(comment_id)
