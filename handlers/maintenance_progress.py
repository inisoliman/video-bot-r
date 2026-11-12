# handlers/maintenance_progress.py
"""تقدم عمليات الصيانة الطويلة: مسودة (send_message_draft) مع بديل التعديل الحالي.

قواعد صارمة في هذا الملف:
- عند كل تحديث تقدم: محاولة واحدة فقط لـ``bot.send_message_draft(chat_id, draft_id, text)``
  (معرّف مسودة غير صفري ثابت لكل عملية، فهي تُحرّك نفس المسودة).
- القبول فقط عندما تكون النتيجة ``True`` بالضبط. استثناء أو ``False`` أو ``None`` أو
  غياب الدالة => تحوّل دائم لهذه العملية إلى ``bot.edit_message_text(text, chat_id,
  message_id)`` بلا أي إعادة محاولة للمسودة.
- «message is not modified» في البديل نتيجة حميدة (لا خطأ).
- يُعاد bool يعني: هل أُرسل نص التقدم (مسودة مؤكدة أو رسالة مُعدّلة)؟ نتائج الصيانة
  نفسها لا تعتمد على هذه القيمة.
- لا يُبتلع أي استثناء من عملية الصيانة نفسها، ولا يُسجل نص/أسرار: نوع الاستثناء فقط.
- التقارير النهائية لا تُرسل كمسودات: تبقى ``edit_message_text``/``send_message`` كما هي.
"""

import itertools
import logging
import time

from telebot.apihelper import ApiTelegramException

from .telegram_rate_limit import sleep_after_429

logger = logging.getLogger(__name__)

# دالة النوم الفعلية (قابلة للاستبدال صراحةً في الاختبارات بالاسم)
sleep = time.sleep

# معرّفات المسودات فريدة لكل عملية داخل العملية (process): تبدأ من 1 وليست صفراً.
_DRAFT_IDS = itertools.count(1)
_NOT_MODIFIED = 'message is not modified'


class MaintenanceProgress:
    """تحديث تقدم عملية صيانة واحدة لمحادثة أدمن واحدة.

    ``draft_id`` معرّف مسودة غير صفري؛ التحديثات المتتالية بنفس المعرّف تُحريك
    المسودة نفسها. إن فشلت المسودة مرة واحدة تُستخدم ``edit_message_text`` لبقية
    العملية.
    """

    def __init__(self, bot, chat_id, message_id, draft_id=None):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.draft_id = int(draft_id if draft_id is not None else next(_DRAFT_IDS))
        self.drafts_enabled = True
        self.draft_calls = 0
        self.edit_calls = 0
        self.last_delivered = False
        self._sender = None

    def update(self, text):
        """يرسل نص التقدم مرة واحدة؛ True إذا تأكد تسليمه (مسودة أو تعديل)."""
        if self.drafts_enabled and self._send_draft(text):
            self.draft_calls += 1
            self.last_delivered = True
            return True
        delivered = self._fallback(text)
        self.last_delivered = delivered
        return delivered

    def _send_draft(self, text):
        """محاولة مسودة واحدة؛ أي فشل يوقف المسودات لهذه العملية نهائياً."""
        if self._sender is None:
            sender = getattr(self.bot, 'send_message_draft', None)
            if not callable(sender):
                logger.info('Draft progress unavailable (send_message_draft missing); using edit')
                self.drafts_enabled = False
                return False
            self._sender = sender
        try:
            result = self._sender(self.chat_id, self.draft_id, text)
        except ApiTelegramException as exc:
            # 429 رسمي: انتظار retry_after المعلن قبل التطبيق (تحول دائم إلى التعديل
            # كما كان، ولا إعادة محاولة للمسودة). القيمة الغائبة لا تضيف نوماً.
            sleep_after_429(exc, sleep)
            # نوع الاستثناء فقط: لا نص الرسالة ولا معرّفات الأسرار
            logger.info('Draft progress failed (%s); using edit for the rest of this job',
                        type(exc).__name__)
            self.drafts_enabled = False
            return False
        except Exception as exc:
            # نوع الاستثناء فقط: لا نص الرسالة ولا معرّفات الأسرار
            logger.info('Draft progress failed (%s); using edit for the rest of this job',
                        type(exc).__name__)
            self.drafts_enabled = False
            return False
        if result is True:
            return True
        logger.info('Draft progress unconfirmed; using edit for the rest of this job')
        self.drafts_enabled = False
        return False

    def _fallback(self, text):
        try:
            self.bot.edit_message_text(text, self.chat_id, self.message_id)
        except ApiTelegramException as exc:
            # 429 رسمي: انتظار retry_after قبل نتيجة الفشل الحالية (لا إعادة محاولة)
            sleep_after_429(exc, sleep)
            if _NOT_MODIFIED in str(getattr(exc, 'description', '') or str(exc)).lower():
                self.edit_calls += 1
                return True
            logger.info('Progress edit failed (%s)', type(exc).__name__)
            return False
        except Exception as exc:
            logger.info('Progress edit failed (%s)', type(exc).__name__)
            return False
        self.edit_calls += 1
        return True


def create(bot, chat_id, message_id, draft_id=None):
    """مساعد تقدم جديد للعملية الحالية (معرّف مسودة فريد ما لم يُمرَّر صراحةً)."""
    return MaintenanceProgress(bot, chat_id, message_id, draft_id=draft_id)
