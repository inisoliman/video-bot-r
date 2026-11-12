# handlers/telegram_rate_limit.py
"""المساعد المشترك لاحترام 429 / retry_after من Telegram (نصوص وهيكل صريحان).

الشكل الرسمي للاستثناء في pyTelegramBotAPI 4.x هو::

    ApiTelegramException(function_name, result, result_json)

حيث ``result_json`` للخطأ 429 هكذا بالضبط::

    {
        "ok": False,
        "error_code": 429,
        "description": "Too Many Requests: retry after 17",
        "parameters": {"retry_after": 17},
    }

وعليه ``exc.error_code == 429`` و ``exc.result_json['parameters']['retry_after']``
هي المصادر الموثوقة الوحيدة. لا خاصية ``exc.parameters`` في هذا الإصدار، ولا نستنتج
القيمة من نص الوصف ولا من نوع الاستثناء (أي "flood" نصي ليس دليلاً كافياً).

قواعد هذه الوحدة:
- القيمة الرسمية الموجبة والمنتهية فقط هي المقبولة (``int`` أو ``float`` أو نص رقمي)،
  و``bool`` مرفوض (لأنه ``int`` في بايثون)، وكذلك صفر/سالب و``NaN`` و``inf`` والقيم
  التالفة أو الغائبة => ``None``.
- القيمة تُعاد كما وردت من Telegram (تُحوّل فقط إلى ``float``): لا سقف ولا قيمة مخترعة.
- الانتظار يؤخّر العمل التالي فقط، ولا يعيد تشغيل العملية الفاشلة تلقائياً.
- لا تُسجَّل أي حمولة أو أسرار: لا مشاهدة داخل ``result``/``result_json``.
"""

import logging
import math
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# رمز HTTP/Telegram الرسمي لـ "Too Many Requests"
RATE_LIMIT_ERROR_CODE = 429


def retry_after_seconds(error: BaseException) -> Optional[float]:
    """عدد الثواني الرسمي في ``retry_after`` لخطأ 429، وإلا ``None``.

    يُقرأ فقط من ``error.error_code == 429`` و ``error.result_json`` بالشكل::

        result_json = {
            "error_code": 429,
            "description": "Too Many Requests: retry after 17",
            "parameters": {"retry_after": 17},
        }

    المقبول: ``int``/``float`` موجب ومنتهٍ، أو نص رقمي يحوّله ``float`` (مثل ``"17"``).
    المرفوض (``None``): ``bool``، الصفر والسالب، ``NaN``/``inf``، النصوص غير الرقمية،
    الهياكل التالفة، وغياب ``parameters`` أو ``retry_after``. القيمة تُعاد كما هي
    (``float``) بلا سقف وبلا قيمة احتياطية مخترعة.
    """
    if not _is_rate_limit_error(error):
        return None
    payload = getattr(error, 'result_json', None)
    if not isinstance(payload, dict):
        return None
    parameters = payload.get('parameters')
    if not isinstance(parameters, dict):
        return None
    if 'retry_after' not in parameters:
        return None
    return _positive_seconds(parameters.get('retry_after'))


def sleep_after_429(error: BaseException, sleeper: Callable[[float], object]) -> bool:
    """ينام المدة الرسمية بعد خطأ 429 عبر ``sleeper``، ويعيد هل نام فعلاً.

    - عند وجود ``retry_after`` رسمي صالح: ``sleeper(seconds)`` مرة واحدة بالضبط ثم
      ``True``. أي استثناء من ``sleeper`` نفسه لا يُبتلع (``False`` تعني غياب القيمة
      لا فشل النوم).
    - غير ذلك: لا نوم إطلاقاً و``False``، ليبقى لأي بديل قديم (مثل نص 'flood' ومهلة
      ثابتة) أن يعمل كما كان.

    الانتظار يؤخر العمل التالي فقط (الرسائل/المعرّفات التالية) ولا يعيد تشغيل العملية
    الفاشلة: لا إعادة محاولة ولا إعادة إرسال من هنا، والعدّ/النتائج لا تتغير.
    لا يُسجَّل أي حمولة أو أسرار.
    """
    seconds = retry_after_seconds(error)
    if seconds is None:
        return False
    sleeper(seconds)
    return True


# ---------------------------------------------------------------------------
# أدوات داخلية
# ---------------------------------------------------------------------------

def _is_rate_limit_error(error) -> bool:
    """هل هو كائن استثناء فعلاً برمز ``error_code`` يساوي 429 (لا True ولا نص)؟"""
    if not isinstance(error, BaseException):
        return False
    code = getattr(error, 'error_code', None)
    if isinstance(code, bool):
        return False
    return code == RATE_LIMIT_ERROR_CODE


def _positive_seconds(value) -> Optional[float]:
    """يحوّل ``retry_after`` إلى ``float`` موجب منتهٍ، أو ``None`` لأي قيمة غير صالحة."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            seconds = float(value)
        except (OverflowError, ValueError):
            return None
    elif isinstance(value, str):
        try:
            seconds = float(value.strip())
        except (OverflowError, ValueError):
            return None
    else:
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return seconds
