# handlers package


def register_all_handlers(bot, channel_id, admin_ids):
    """تسجيل جميع المعالجات بترتيب صحيح لتجنب circular imports"""
    from . import user_handlers
    from . import admin_handlers
    from . import callback_handlers
    from . import inline_handlers
    from . import comment_handlers
    from . import group_handlers

    user_handlers.register(bot, admin_ids)
    admin_handlers.register(bot, admin_ids)
    callback_handlers.register(bot, admin_ids)
    inline_handlers.register(bot)
    comment_handlers.register(bot, admin_ids)
    group_handlers.register(bot, admin_ids)
