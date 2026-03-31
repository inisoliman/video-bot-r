from ..database import execute


def add_or_update_video(message_id, chat_id, file_id, caption, file_name, category_id, metadata_json, content_type):
    query = """
    INSERT INTO video_archive (message_id, chat_id, file_id, caption, file_name, category_id, metadata, content_type)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (message_id) DO UPDATE SET
      file_id = EXCLUDED.file_id,
      caption = EXCLUDED.caption,
      file_name = EXCLUDED.file_name,
      category_id = EXCLUDED.category_id,
      metadata = EXCLUDED.metadata,
      content_type = EXCLUDED.content_type
    RETURNING id;
    """
    result = execute(query, (message_id, chat_id, file_id, caption, file_name, category_id, metadata_json, content_type), fetch='one')
    return result['id'] if result else None


def get_video_by_id(video_id):
    return execute('SELECT * FROM video_archive WHERE id = %s', (video_id,), fetch='one')


def get_video_by_message_id(message_id):
    return execute('SELECT * FROM video_archive WHERE message_id = %s', (message_id,), fetch='one')


def get_videos(page=0, per_page=10):
    offset = page * per_page
    return execute('SELECT * FROM video_archive ORDER BY id DESC LIMIT %s OFFSET %s', (per_page, offset), fetch='all')


def search_videos(query, page=0, per_page=10, category_id=None, quality=None, status=None):
    where_clauses = ['(caption ILIKE %s OR file_name ILIKE %s)']
    params = [f'%{query}%', f'%{query}%']
    if category_id:
        where_clauses.append('category_id = %s')
        params.append(category_id)
    if quality:
        where_clauses.append("metadata->>'quality_resolution' = %s")
        params.append(quality)
    if status:
        where_clauses.append("metadata->>'status' = %s")
        params.append(status)

    where_stmt = ' AND '.join(where_clauses)
    all_query = f"SELECT * FROM video_archive WHERE {where_stmt} ORDER BY id DESC LIMIT %s OFFSET %s"
    videos = execute(all_query, (*params, per_page, page * per_page), fetch='all')
    count = execute(f"SELECT COUNT(*) as total FROM video_archive WHERE {where_stmt}", tuple(params), fetch='one')
    return videos or [], count['total'] if count else 0


def increment_view_count(video_id):
    execute('UPDATE video_archive SET view_count = view_count + 1 WHERE id = %s', (video_id,))


def get_top_videos(limit=10):
    return execute('SELECT * FROM video_archive ORDER BY view_count DESC LIMIT %s', (limit,), fetch='all')
