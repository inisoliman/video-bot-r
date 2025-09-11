# handlers/__init__.py

# استيراد الوحدات لجعل دوال التسجيل متاحة
from . import user_handlers
from . import admin_handlers
from . import callback_handlers
from . import admin_message_handler
from . import favorites_handlers
from . import advanced_search_handlers
from . import helpers

def register_all_handlers(bot, channel_id, admin_ids):
    """
    تقوم هذه الدالة بتسجيل كل المعالجات من الوحدات المنفصلة.
    """
    # تمرير الكائنات الضرورية (bot, admin_ids, etc.) لكل دالة تسجيل
    user_handlers.register(bot, channel_id, admin_ids)
    admin_handlers.register(bot, admin_ids)
    callback_handlers.register(bot, admin_ids)
    admin_message_handler.register_admin_message_handler(bot, admin_ids)
    favorites_handlers.register(bot, admin_ids)
    advanced_search_handlers.register(bot, admin_ids)
