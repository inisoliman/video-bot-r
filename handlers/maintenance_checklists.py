# handlers/maintenance_checklists.py
"""قوائم متابعة اختيارية (Checklists) بعد نجاح عملية صيانة.

قواعد صارمة في هذا الملف:
- الميزة اختيارية تماماً: تُفعّل فقط عندما يكون ``TELEGRAM_BUSINESS_CONNECTION_ID``
  غير فارغ لحظة النداء (``os.getenv``). إن غاب المتغير أو كان فارغاً =>
  ``False`` بدون أي استدعاء لتيليجرام.
- الإرسال ``bot.send_checklist(business_connection_id, chat_id, checklist)`` يتم
  باسم حساب عمل متصل (business connection) يجب تكوينه في البوت. لا يوجد أي معرّف
  إنتاجي ولا قيمة افتراضية هنا، والميزة ليست متاحة لكل البوتات.
- المهام عمل متابعة يدوي مفيد (مراجعة الفشل، تجربة عيّنة، معالجة الملفات الكبيرة
  يدوياً، إعادة تشغيل عند الحاجة) وليست تكراراً لتقدم العملية.
- عند استثناء أو نتيجة ``None``: يُسجَّل نوع الاستثناء فقط ولا يوجد أي بديل؛
  التقرير النهائي للعملية كافٍ بحد ذاته.
- لا يُسجَّل معرّف الربط ولا نص القائمة ولا أي أسرار.
"""

import logging
import os

logger = logging.getLogger(__name__)

ENV_KEY = 'TELEGRAM_BUSINESS_CONNECTION_ID'

# عمليات الصيانة المدعومة: مهام متابعة يدوية محددة لكل عملية.
CHECKLISTS = {
    'heal_archive': ('متابعة إصلاح الأرشيف', (
        'مراجعة الفيديوهات التي فشل إصلاحها وإعادة المحاولة عند الحاجة',
        'فتح عيّنة من الفيديوهات المُصلحة للتأكد من ظهور الصور المصغرة',
    )),
    'convert_all_docs': ('متابعة تحويل المستندات', (
        'رفع الملفات الكبيرة (>20MB) يدوياً',
        'مراجعة الملفات التي فشل تحويلها وإعادة المحاولة عند الحاجة',
        'تجهيز عيّنة من الفيديوهات المحوّلة واختبار التشغيل',
    )),
    'update_metadata': ('متابعة إعادة بناء البيانات', (
        'مراجعة عيّنة من العناوين المحدثة للتأكد من دقة التحليل',
        'إعادة تشغيل إعادة البناء إذا ظهرت عناوين غير سليمة',
    )),
}


def build_checklist(operation):
    """يبني ``InputChecklist`` حقيقياً للعملية.

    معرّفات المهام أعداد صحيحة موجبة وفريدة (1..n) بترتيب القائمة.
    ``ValueError`` لعملية غير معروفة.
    """
    from telebot.types import InputChecklist, InputChecklistTask

    try:
        title, texts = CHECKLISTS[operation]
    except KeyError:
        raise ValueError(f'unknown maintenance operation: {operation!r}') from None

    tasks = [InputChecklistTask(index, text) for index, text in enumerate(texts, start=1)]
    return InputChecklist(
        title,
        tasks,
        others_can_add_tasks=False,
        others_can_mark_tasks_as_done=True,
    )


def send_followup_checklist(bot, chat_id, operation):
    """يرسل قائمة متابعة اختيارية؛ True فقط لنتيجة غير ``None``.

    False: الميزة غير مكافأة، أو العملية غير معروفة، أو فشل الإرسال.
    """
    business_id = (os.getenv(ENV_KEY) or '').strip()
    if not business_id:
        return False

    try:
        checklist = build_checklist(operation)
    except ValueError:
        logger.info('Follow-up checklist skipped: unknown operation')
        return False

    try:
        result = bot.send_checklist(business_id, chat_id, checklist)
    except Exception as exc:
        # نوع الاستثناء فقط: لا معرّف ربط ولا نص قائمة ولا أسرار
        logger.info('Follow-up checklist not sent (%s)', type(exc).__name__)
        return False
    return result is not None
