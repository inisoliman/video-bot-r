
# config/constants.py

# --- UI Constants ---
COMMENTS_PER_PAGE = 10 # Default, can be overridden by Config.COMMENTS_PER_PAGE
VIDEOS_PER_PAGE = 10 # number of videos per page pagination
CALLBACK_DELIMITER = "::"

# --- Emojis ---
EMOJI_FOLDER = "📁"
EMOJI_LEAF = "🍃"
EMOJI_DIAMOND = "💎"
EMOJI_BACK = "🔙"
EMOJI_SEARCH = "🔍"
EMOJI_STAR = "⭐"
EMOJI_FIRE = "🔥"
EMOJI_EYE = "👁️"
EMOJI_FILM = "🎬"
EMOJI_FAVORITE = "❤️"
EMOJI_HISTORY = "📜"
EMOJI_COMMENT = "💬"
EMOJI_CHECK = "✅"
EMOJI_UNSUBSCRIBE = "🚫"
EMOJI_ERROR = "❌"
EMOJI_WARNING = "⚠️"
EMOJI_ADMIN = "⚙️"
EMOJI_SEASON = "🗓️"
EMOJI_EPISODE = "📺"
EMOJI_POPULAR = "📈"
EMOJI_QUALITY = "🌟"
EMOJI_STATUS = "🏷️"
EMOJI_RANDOM_VIDEO = "🎲"
EMOJI_BROADCAST = "📢"
EMOJI_DELETE = "🗑️"
EMOJI_MOVE = "➡️"
EMOJI_CLOCK = "⏰"

# --- Parse Modes ---
PARSE_MODE_HTML = "HTML"
PARSE_MODE_MARKDOWN_V2 = "MarkdownV2"

# --- Messages ---
MSG_WELCOME = """
أهلاً بك في بوت أرشيف الفيديوهات! 🎬

يمكنك استخدام الأزرار أدناه لتصفح الفيديوهات، البحث، أو مشاهدة المفضلة وسجل المشاهدة.
"""
MSG_WELCOME_UNSUBSCRIBED = """
أهلاً بك في بوت أرشيف الفيديوهات! 🎬

للوصول إلى محتوى البوت، يرجى الاشتراك في القنوات التالية:
"""
MSG_NOT_SUBSCRIBED = """
عذراً، يجب عليك الاشتراك في جميع القنوات المطلوبة للوصول إلى محتوى البوت. يرجى الاشتراك ثم الضغط على زر "لقد اشتركت، تحقق الآن".
"""
MSG_ADMIN_ONLY = """
عذراً، هذه الميزة متاحة للمشرفين فقط.
"""
MSG_NO_VIDEOS = """
عذراً، لا توجد فيديوهات متاحة حالياً في هذا القسم.
"""
MSG_NO_FAVORITES = """
قائمة مفضلتك فارغة حالياً. يمكنك إضافة فيديوهات إلى المفضلة من صفحة الفيديو.
"""
MSG_NO_HISTORY = """
سجل مشاهداتك فارغ حالياً. ابدأ بمشاهدة الفيديوهات لتظهر هنا.
"""
MSG_SEARCH_PROMPT = """
أرسل لي كلمة البحث التي تريدها.
"""
MSG_SEARCH_TYPE_PROMPT = """
نتائج البحث عن "{query}":
اختر نوع البحث:
"""
MSG_SEARCH_SCOPE_PROMPT = """
أين تريد البحث عن "{query}"؟
"""
MSG_SEARCH_NO_RESULTS = """
عذراً، لم يتم العثور على أي نتائج لـ "{query}".
"""
MSG_NO_MORE_RESULTS = """
لا توجد المزيد من النتائج.
"""
MSG_VIDEO_NOT_AVAILABLE = """
عذراً، هذا الفيديو غير متاح حالياً.
"""
MSG_CHANNEL_NOT_AVAILABLE = """
عذراً، القناة التي تحتوي على هذا الفيديو غير متاحة.
"""
MSG_ERROR_SENDING_VIDEO = """
عذراً، حدث خطأ أثناء محاولة إرسال الفيديو.
"""
MSG_UNEXPECTED_ERROR = """
حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى.
"""
MSG_RATING_SAVED = """
شكراً لك! تم حفظ تقييمك {rating} نجوم.
"""
MSG_RATING_ERROR = """
حدث خطأ أثناء حفظ تقييمك. يرجى المحاولة مرة أخرى.
"""
MSG_INVALID_RATING = """
تقييم غير صالح. يرجى اختيار تقييم من 1 إلى 5 نجوم.
"""
MSG_CATEGORY_NOT_FOUND = """
عذراً، التصنيف المطلوب غير موجود.
"""

# Admin Messages
MSG_BROADCAST_PROMPT = """
أرسل الرسالة التي تريد بثها لجميع المستخدمين.
"""
MSG_BROADCAST_CONFIRM = """
هل أنت متأكد من إرسال هذه الرسالة إلى {user_count} مستخدم؟
"""
MSG_BROADCAST_SENT = """
تم إرسال البث بنجاح إلى {success_count} من أصل {total_count} مستخدم.
"""
MSG_BROADCAST_CANCELLED = """
تم إلغاء عملية البث.
"""
MSG_BROADCAST_NO_USERS = """
لا يوجد مستخدمون لإرسال البث إليهم.
"""
MSG_VIDEO_MOVE_PROMPT = """
أرسل معرف الفيديو الذي تريد نقله.
"""
MSG_VIDEO_MOVE_CATEGORY_PROMPT = """
اختر التصنيف الجديد للفيديو رقم {video_id}:
"""
MSG_VIDEO_MOVE_CANCELLED = """
تم إلغاء عملية نقل الفيديو.
"""
MSG_VIDEO_MOVE_INVALID_ID = """
معرف الفيديو غير صالح أو الفيديو غير موجود.
"""
MSG_VIDEO_MOVE_NO_CATEGORY = """
لم يتم اختيار تصنيف جديد.
"""
MSG_VIDEO_MOVE_SAME_CATEGORY = """
الفيديو موجود بالفعل في هذا التصنيف.
"""
MSG_VIDEO_MOVED = """
تم نقل الفيديو بنجاح.
"""
MSG_UPDATE_METADATA_STARTED = """
بدأت عملية تحديث البيانات الوصفية للفيديوهات في الخلفية. ستصلك إشعارات عند الانتهاء.
"""
MSG_THUMBNAIL_UPDATE_STARTED = """
بدأت عملية تحديث الصور المصغرة للفيديوهات في الخلفية. ستصلك إشعارات عند الانتهاء.
"""
MSG_DB_OPTIMIZER_STARTED = """
بدأت عملية تحسين قاعدة البيانات في الخلفية. ستصلك إشعارات عند الانتهاء.
"""
MSG_HISTORY_CLEANER_STARTED = """
بدأت عملية تنظيف سجل المشاهدة القديم في الخلفية. ستصلك إشعارات عند الانتهاء.
"""
MSG_RESET_FILE_IDS_STARTED = """
بدأت عملية إعادة تعيين معرفات الملفات في الخلفية. ستصلك إشعارات عند الانتهاء.
"""
MSG_FIX_DATABASE_FILE_IDS_STARTED = """
بدأت عملية إصلاح معرفات الملفات في قاعدة البيانات في الخلفية. ستصلك إشعارات عند الانتهاء.
"""
MSG_CHECK_FILE_IDS_STARTED = """
بدأت عملية التحقق من معرفات الملفات في الخلفية. ستصلك إشعارات عند الانتهاء.
"""
MSG_EXTRACT_CHANNEL_THUMBNAILS_STARTED = """
بدأت عملية استخراج الصور المصغرة للقنوات في الخلفية. ستصلك إشعارات عند الانتهاء.
"""
MSG_FIX_VIDEOS_PROFESSIONAL_STARTED = """
بدأت عملية إصلاح الفيديوهات الاحترافية في الخلفية. ستصلك إشعارات عند الانتهاء.
"""
MSG_MIGRATE_DATABASE_STARTED = """
بدأت عملية ترحيل قاعدة البيانات في الخلفية. ستصلك إشعارات عند الانتهاء.
"""
