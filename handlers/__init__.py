# handlers/__init__.py
from . import user_handlers
from . import admin_handlers
from . import callback_handlers
from . import inline_handlers
from . import group_handlers
from . import helpers

def register_all_handlers(bot, channel_id, admin_ids):
    # ترتيب التسجيل حاسم: TeleBot يضيف كل معالج إلى bot.message_handlers بالترتيب
    # (append) ثم يمرّ عليها بالترتيب نفسه (ليس معكوساً) ويتوقف عند أول معالج
    # مُطابق لا يرجع ContinueHandling. لذلك يُسجَّل إدخال الأدمن admin_state_input
    # (والأوامر /admin و/cancel) قبل معالج النص العام في user_handlers، وإلا التقط
    # النص العام رسائل الأدمن في حالات البث/التدفقات واختصره إلى البحث.
    # admin_state_input نفسه يرجع ContinueHandling للعناوين/التعليقات/البحث.
    admin_handlers.register(bot, admin_ids)
    user_handlers.register(bot, channel_id, admin_ids)
    callback_handlers.register(bot, admin_ids)
    inline_handlers.register(bot)
    group_handlers.register(bot, admin_ids)
