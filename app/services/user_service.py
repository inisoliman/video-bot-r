from ..repositories import user_repo


def register_user(user_id, username, first_name):
    """تسجيل مستخدم جديد"""
    return user_repo.add_user(user_id, username, first_name)


def add_favorite(user_id, video_id):
    """إضافة فيديو للمفضلة"""
    return user_repo.add_favorite(user_id, video_id)


def remove_favorite(user_id, video_id):
    """إزالة فيديو من المفضلة"""
    return user_repo.remove_favorite(user_id, video_id)


def is_favorite(user_id, video_id):
    """التحقق مما إذا كان الفيديو في المفضلة"""
    return user_repo.is_favorite(user_id, video_id)


def get_favorites(user_id, page=0, per_page=10):
    """الحصول على المفضلة"""
    return user_repo.get_user_favorites(user_id, page, per_page)


def add_history(user_id, video_id):
    """إضافة إلى سجل المشاهدة"""
    return user_repo.add_history(user_id, video_id)


def get_history(user_id, page=0, per_page=10):
    """الحصول على سجل المشاهدة"""
    return user_repo.get_user_history(user_id, page, per_page)


def set_user_rating(user_id, video_id, rating):
    """تعيين تقييم للمستخدم"""
    return user_repo.set_user_rating(user_id, video_id, rating)


def get_user_rating(user_id, video_id):
    """الحصول على تقييم المستخدم"""
    return user_repo.get_user_rating(user_id, video_id)


def set_user_state(user_id, state, context):
    """تعيين حالة المستخدم"""
    return user_repo.set_user_state(user_id, state, context)


def get_user_state(user_id):
    """الحصول على حالة المستخدم"""
    return user_repo.get_user_state(user_id)


def clear_user_state(user_id):
    """مسح حالة المستخدم"""
    return user_repo.clear_user_state(user_id)
