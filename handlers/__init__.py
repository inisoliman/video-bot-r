from . import user_handlers
from . import admin_handlers
from . import callback_handlers
from . import helpers

def register_all_handlers(bot, channel_id, admin_ids):
    user_handlers.register(bot, channel_id, admin_ids)
    admin_handlers.register(bot, admin_ids)
    callback_handlers.register(bot, admin_ids)
