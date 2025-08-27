# utils.py

import re
from pymediainfo import MediaInfo
import logging

# إعداد المسجل (logger) لهذا الملف
logger = logging.getLogger(__name__)

def arabic_word_to_int(word):
    """يحول الكلمات العربية للأرقام إلى أعداد صحيحة."""
    if not word:
        return None
    num_map = {
        'الاول': 1, 'الأول': 1, 'الاولى': 1, 'الأولى': 1, 'واحد': 1,
        'الثاني': 2, 'الثانية': 2, 'اثنين': 2,
        'الثالث': 3, 'الثالثة': 3, 'ثلاثة': 3,
        'الرابع': 4, 'الرابعة': 4, 'اربعة': 4,
        'الخامس': 5, 'الخامسة': 5, 'خمسة': 5,
        'السادس': 6, 'السادسة': 6, 'ستة': 6,
        'السابع': 7, 'السابعة': 7, 'سبعة': 7,
        'الثامن': 8, 'الثامنة': 8, 'ثمانية': 8,
        'التاسع': 9, 'التاسعة': 9, 'تسعة': 9,
        'العاشر': 10, 'العاشرة': 10, 'عشرة': 10,
    }
    return num_map.get(word.strip())

def extract_video_metadata(caption, file_name=""):
    """
    محلل بيانات وصفية ذكي ومتقدم مصمم للتعامل مع صيغ كابشن متعددة ومعقدة.
    """
    if not caption:
        caption = ""

    text_content = caption + " " + file_name
    metadata = {}

    if re.search(r'مترجم|subbed|sub', text_content, re.IGNORECASE):
        metadata['status'] = 'مترجم'
    elif re.search(r'مدبلج|dubbed|dub', text_content, re.IGNORECASE):
        metadata['status'] = 'مدبلج'
    elif re.search(r'متحدث عربي', text_content, re.IGNORECASE):
        metadata['status'] = 'متحدث'

    quality_match = re.search(r'(\d{3,4}[pP])|([Hh][Dd])|([48][Kk])', text_content)
    if quality_match:
        quality = next((q for q in quality_match.groups() if q is not None), None)
        metadata['quality_resolution'] = quality.upper().replace('P','p')

    season_match = re.search(r'(?:الموسم|season|S)\s*(\d+)|الموسم\s+([^\s\d]+)', text_content, re.IGNORECASE)
    if season_match:
        if season_match.group(1):
            metadata['season_number'] = int(season_match.group(1))
        elif season_match.group(2):
            num = arabic_word_to_int(season_match.group(2))
            if num:
                metadata['season_number'] = num

    episode_match = re.search(r'(?:الحلقة|episode|E)\s*(\d+)(?![pP])|الحلقة\s+([^\s\d]+)', text_content, re.IGNORECASE)
    if episode_match:
        if episode_match.group(1):
            metadata['episode_number'] = int(episode_match.group(1))
        elif episode_match.group(2):
            num = arabic_word_to_int(episode_match.group(2))
            if num:
                metadata['episode_number'] = num

    if re.search(r'الاخيرة|الأخيرة', text_content, re.IGNORECASE):
        metadata['is_final_episode'] = True

    raw_name = ""
    name_match = re.search(r'(?:مسلسل|فيلم|series|movie)\s+([^\n#|]+)', caption, re.IGNORECASE)
    if name_match:
        raw_name = name_match.group(1)
    else:
        raw_name = caption.split('\n')[0]

    if raw_name:
        cleaned_name = re.sub(r'^[\d\s\W_-]+', '', raw_name).strip()
        cleaned_name = re.sub(r'(الحلقة|الموسم|episode|season|S|E)\s*(\d+|[^\s\d]+)', '', cleaned_name, re.IGNORECASE).strip()
        cleaned_name = re.sub(r'\b(مترجم|مدبلج|عربي|HD|1080p|720p|480p|360p|جودة عالية|جودة متعددة)\b', '', cleaned_name, re.IGNORECASE).strip()
        cleaned_name = re.sub(r'[\s\W_-]+$', '', cleaned_name).strip()
        cleaned_name = re.sub(r'\s{2,}', ' ', cleaned_name).strip()

        if cleaned_name:
            metadata['series_name'] = cleaned_name

    production_match = re.search(r'إنتاج\s+([^\n|]+)', caption, re.IGNORECASE)
    if production_match:
        metadata['production'] = production_match.group(1).strip()

    return metadata

def get_video_info(file_path):
    """استخلاص معلومات الفيديو باستخدام MediaInfo."""
    try:
        media_info = MediaInfo.parse(file_path)
        video_track = next((t for t in media_info.tracks if t.track_type == 'Video'), None)

        if video_track:
            duration_ms = video_track.duration
            duration_seconds = duration_ms / 1000 if duration_ms else 0

            return {
                "duration": duration_seconds,
                "width": video_track.width,
                "height": video_track.height,
                "file_size": video_track.stream_size
            }
    except Exception as e:
        logger.error(f"Could not get video info for {file_path}. Error: {e}")
    return {}
