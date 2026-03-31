from .user_handler import register as register_user_handler
from .callback_handler import register as register_callback_handler
from .inline_handler import register as register_inline_handler
from .group_handler import register as register_group_handler
from .admin_handler import register as register_admin_handler


def register_all_handlers(bot, channel_id, admin_ids):
    register_user_handler(bot, channel_id, admin_ids)
    register_callback_handler(bot, admin_ids)
    register_inline_handler(bot)
    register_group_handler(bot, admin_ids)
    register_admin_handler(bot, admin_ids)
