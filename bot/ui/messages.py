#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/ui/messages.py
# الوصف: رسائل البوت الاحترافية الموحدة
# ==============================================================================

from bot.ui.emoji import Emoji


class Messages:
    """مركز الرسائل الموحد - جميع نصوص البوت"""

    # ==============================================================================
    # ترحيب وقوائم رئيسية
    # ==============================================================================

    @staticmethod
    def welcome(user_name: str = "صديقي") -> str:
        return (
            f"{Emoji.VIDEO} *مرحباً بك {user_name}!*\n\n"
            f"{Emoji.SPARKLE} أهلاً بك في بوت البحث عن الفيديوهات\n\n"
            f"🎯 *ما يمكنك فعله:*\n"
            f"  {Emoji.DOT} {Emoji.VIDEO} عرض كل الفيديوهات\n"
            f"  {Emoji.DOT} {Emoji.FIRE} مشاهدة الأكثر شعبية\n"
            f"  {Emoji.DOT} {Emoji.POPCORN} اقتراح عشوائي\n"
            f"  {Emoji.DOT} {Emoji.SEARCH} بحث ذكي\n\n"
            f"استمتع بوقتك! 😊"
        )

    @staticmethod
    def main_menu() -> str:
        return f"{Emoji.HOME} *القائمة الرئيسية*\n\nاختر ما تريد من الأزرار أدناه:"

    @staticmethod
    def subscription_required() -> str:
        return (
            f"{Emoji.WARNING} *يجب الاشتراك أولاً*\n\n"
            f"للاستمرار في استخدام البوت، يرجى الاشتراك في القنوات التالية:"
        )

    @staticmethod
    def subscription_success() -> str:
        return f"{Emoji.SUCCESS} *تم التحقق بنجاح!*\n\nيمكنك الآن استخدام البوت. استمتع! 🎉"

    @staticmethod
    def subscription_failed() -> str:
        return f"{Emoji.ERROR} *لم تشترك بعد*\n\nيرجى الاشتراك في جميع القنوات المطلوبة أعلاه."

    # ==============================================================================
    # بحث
    # ==============================================================================

    @staticmethod
    def search_prompt() -> str:
        return (
            f"{Emoji.SEARCH} *البحث عن فيديوهات*\n\n"
            f"أرسل كلمة البحث الآن...\n"
            f"مثال: `أكشن` أو `كوميدي`"
        )

    @staticmethod
    def search_type_select(query: str) -> str:
        return f"{Emoji.SEARCH} اختر نوع البحث عن *\"{query}\"*:"

    @staticmethod
    def search_scope_select(query: str) -> str:
        return f"🎯 أين تريد البحث عن *\"{query}\"*?"

    @staticmethod
    def search_results(query: str) -> str:
        return f"{Emoji.SEARCH} نتائج البحث عن *\"{query}\"*:"

    @staticmethod
    def search_no_results(query: str) -> str:
        return f"{Emoji.ERROR} لا توجد نتائج لـ *\"{query}\"*."

    # ==============================================================================
    # فيديوهات
    # ==============================================================================

    @staticmethod
    def rate_video() -> str:
        return f"{Emoji.STAR} قيّم هذا الفيديو:"

    @staticmethod
    def rated(rating: int) -> str:
        return f"{Emoji.STAR} تم تقييم الفيديو بـ {rating} نجوم! شكراً لك."

    @staticmethod
    def fav_added() -> str:
        return f"{Emoji.STAR} تم إضافة الفيديو إلى المفضلة بنجاح!"

    @staticmethod
    def fav_removed() -> str:
        return f"{Emoji.ERROR} تم إزالة الفيديو من المفضلة."

    @staticmethod
    def video_not_found() -> str:
        return f"{Emoji.ERROR} الفيديو غير متاح حالياً. ربما تم حذفه من القناة."

    @staticmethod
    def category_header(name: str, content_info: str) -> str:
        return (
            f"{Emoji.FOLDER} محتويات تصنيف *\"{name}\"*\n"
            f"{Emoji.STATS} المحتوى: {content_info}"
        )

    @staticmethod
    def category_empty(name: str) -> str:
        return (
            f"{Emoji.FOLDER} التصنيف *\"{name}\"*\n\n"
            f"هذا التصنيف فارغ حالياً. لا توجد أقسام فرعية أو فيديوهات."
        )

    @staticmethod
    def popular_most_viewed() -> str:
        return f"{Emoji.FIRE} الفيديوهات الأكثر مشاهدة:"

    @staticmethod
    def popular_highest_rated() -> str:
        return f"{Emoji.STAR} الفيديوهات الأعلى تقييماً:"

    @staticmethod
    def no_popular() -> str:
        return "لا توجد فيديوهات كافية لعرضها حالياً."

    @staticmethod
    def favorites_header() -> str:
        return f"{Emoji.STAR} قائمة مفضلاتك:"

    @staticmethod
    def history_header() -> str:
        return f"📺 سجل مشاهداتك:"

    # ==============================================================================
    # أدمن
    # ==============================================================================

    @staticmethod
    def admin_panel() -> str:
        return f"{Emoji.SETTINGS} *لوحة تحكم الإدارة*\n\nاختر أحد الخيارات:"

    @staticmethod
    def broadcast_prompt() -> str:
        return f"{Emoji.BROADCAST} أرسل الرسالة التي تريد بثها. (أو /cancel)"

    @staticmethod
    def broadcast_complete(sent: int, failed: int, removed: int) -> str:
        return (
            f"{Emoji.SUCCESS} *اكتمل البث!*\n\n"
            f"{Emoji.DOT} رسائل ناجحة: *{sent}*\n"
            f"{Emoji.DOT} رسائل فاشلة: *{failed}*\n"
            f"{Emoji.DOT} مشتركين محذوفين: *{removed}*"
        )

    @staticmethod
    def stats(video_count, category_count, total_views, total_ratings,
              most_viewed_title=None, most_viewed_count=0,
              highest_rated_title=None, highest_rated_avg=0):
        text = (
            f"{Emoji.STATS} *إحصائيات المحتوى*\n\n"
            f"  {Emoji.DOT} إجمالي الفيديوهات: *{video_count}*\n"
            f"  {Emoji.DOT} إجمالي التصنيفات: *{category_count}*\n"
            f"  {Emoji.DOT} إجمالي المشاهدات: *{total_views}*\n"
            f"  {Emoji.DOT} إجمالي التقييمات: *{total_ratings}*"
        )
        if most_viewed_title:
            text += f"\n\n{Emoji.FIRE} الأكثر مشاهدة: {most_viewed_title} ({most_viewed_count} مشاهدة)"
        if highest_rated_title:
            text += f"\n{Emoji.STAR} الأعلى تقييماً: {highest_rated_title} ({highest_rated_avg:.1f}/5)"
        return text

    @staticmethod
    def category_created(name: str, parent: str = None) -> str:
        info = f" تحت التصنيف {Emoji.FOLDER} \"{parent}\"" if parent else ""
        return f"{Emoji.SUCCESS} تم إنشاء التصنيف الجديد بنجاح: \"{name}\"{info}."

    @staticmethod
    def category_deleted(name: str) -> str:
        return f"{Emoji.SUCCESS} تم حذف التصنيف \"{name}\" وكل محتوياته."

    @staticmethod
    def video_moved(video_count: int, category_name: str, video_ids: list = None) -> str:
        if video_count == 1 and video_ids:
            return f"{Emoji.SUCCESS} تم نقل الفيديو رقم {video_ids[0]} بنجاح إلى تصنيف \"{category_name}\"."
        text = f"{Emoji.SUCCESS} تم نقل {video_count} فيديو بنجاح إلى تصنيف \"{category_name}\"."
        if video_ids:
            text += f"\n\n📝 الأرقام المنقولة: {', '.join(map(str, video_ids))}"
        return text

    @staticmethod
    def videos_deleted(count: int) -> str:
        return f"{Emoji.SUCCESS} تم حذف {count} فيديو بنجاح."

    @staticmethod
    def operation_cancelled() -> str:
        return f"{Emoji.SUCCESS} تم إلغاء العملية الحالية بنجاح."

    @staticmethod
    def no_operation() -> str:
        return "لا توجد عملية لإلغائها."

    @staticmethod
    def unauthorized() -> str:
        return "ليس لديك صلاحية الوصول إلى هذا الأمر."

    # ==============================================================================
    # تعليقات
    # ==============================================================================

    @staticmethod
    def comment_prompt() -> str:
        return (
            f"📝 *إضافة تعليق*\n\n"
            f"الرجاء كتابة تعليقك أو استفسارك عن هذا الفيديو.\n"
            f"سيتم إرساله للإدارة وسيتم الرد عليك في أقرب وقت.\n\n"
            f"💡 _للإلغاء، اضغط /cancel_"
        )

    @staticmethod
    def comment_sent() -> str:
        return (
            f"{Emoji.SUCCESS} *تم إرسال تعليقك بنجاح!*\n\n"
            f"سيتم مراجعته من قبل الإدارة والرد عليك في أقرب وقت.\n"
            f"يمكنك متابعة تعليقاتك من خلال الأمر /my\\_comments"
        )

    @staticmethod
    def no_comments() -> str:
        return (
            f"📭 *لا توجد تعليقات*\n\n"
            f"لم تقم بإضافة أي تعليقات بعد.\n"
            f"يمكنك إضافة تعليق على أي فيديو من خلال زر 'إضافة تعليق' {Emoji.COMMENT}"
        )

    # ==============================================================================
    # أخطاء
    # ==============================================================================

    @staticmethod
    def generic_error() -> str:
        return f"{Emoji.ERROR} حدث خطأ غير متوقع. حاول مرة أخرى."

    @staticmethod
    def connection_error() -> str:
        return f"{Emoji.ERROR} حدث خطأ في الاتصال. حاول مرة أخرى."
