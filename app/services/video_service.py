from ..repositories import video_repo, category_repo


def add_video(record):
    return video_repo.add_or_update_video(
        message_id=record['message_id'],
        chat_id=record['chat_id'],
        file_id=record['file_id'],
        caption=record.get('caption'),
        file_name=record.get('file_name'),
        category_id=record.get('category_id'),
        metadata_json=record.get('metadata_json'),
        content_type=record.get('content_type')
    )


def list_videos(page=0, per_page=10):
    return video_repo.get_videos(page=page, per_page=per_page)


def search(query, page=0, per_page=10, category_id=None, quality=None, status=None):
    return video_repo.search_videos(query, page, per_page, category_id, quality, status)


def top_videos(limit=10):
    return video_repo.get_top_videos(limit)
