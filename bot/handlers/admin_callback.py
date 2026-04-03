#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/handlers/admin_callback.py
# الوصف: معالج callbacks الأدمن (فصل عن callback_handlers لتقليل الحجم)
# ==============================================================================

import logging
import threading

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.config import settings
from bot.database.repositories.video_repo import VideoRepository
from bot.database.repositories.category_repo import CategoryRepository
from bot.database.repositories.settings_repo import SettingsRepository
from bot.database.repositories.user_repo import UserRepository
from bot.ui.messages import Messages
from bot.ui.keyboards import Keyboards, _build_tree
from bot.ui.emoji import Emoji
from bot.handlers import admin_handlers, comment_handlers
from update_metadata import run_update_and_report_progress

logger = logging.getLogger(__name__)
DELIMITER = settings.CALLBACK_DELIMITER


def handle_admin_callback(bot, call, data, admin_ids):
    """معالج مركزي لأوامر الأدمن"""
    sub = data[1]
    steps = admin_handlers.admin_steps

    # التعليقات
    if sub == "view_comments":
        bot.answer_callback_query(call.id)
        comment_handlers.show_all_comments(bot, call.from_user.id, admin_ids, 0, False)
    elif sub == "comments_stats":
        bot.answer_callback_query(call.id)
        comment_handlers.handle_comments_stats(bot, call.from_user.id, admin_ids)
    elif sub == "delete_all_comments":
        bot.answer_callback_query(call.id)
        comment_handlers.handle_delete_all_comments(bot, call.from_user.id, admin_ids)
    elif sub == "delete_old_comments":
        bot.answer_callback_query(call.id)
        mk = InlineKeyboardMarkup(row_width=2)
        mk.add(InlineKeyboardButton(f"{Emoji.SUCCESS} نعم", callback_data=f"confirm_delete_old_comments{DELIMITER}30"),
               InlineKeyboardButton(f"{Emoji.ERROR} إلغاء", callback_data="noop"))
        bot.send_message(call.from_user.id, f"{Emoji.WARNING} *تأكيد الحذف*\n\nحذف التعليقات الأقدم من *30 يوم*؟",
                         parse_mode="Markdown", reply_markup=mk)

    # التصنيفات
    elif sub == "add_new_cat":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton(f"{Emoji.FOLDER} رئيسي", callback_data=f"admin{DELIMITER}add_cat_main"))
        kb.add(InlineKeyboardButton(f"🌿 فرعي", callback_data=f"admin{DELIMITER}add_cat_sub_select_parent"))
        bot.edit_message_text(f"{Emoji.ADD} اختر نوع التصنيف:", call.message.chat.id, call.message.message_id, reply_markup=kb)

    elif sub == "add_cat_main":
        steps[call.message.chat.id] = {"parent_id": None}
        msg = bot.send_message(call.message.chat.id, "📝 أرسل اسم التصنيف الرئيسي. (أو /cancel)")
        bot.register_next_step_handler(msg, admin_handlers.handle_add_new_category, bot)

    elif sub == "add_cat_sub_select_parent":
        kb = Keyboards.categories_tree(f"admin{DELIMITER}add_cat_sub_set_parent", add_back=False)
        if not kb.keyboard:
            bot.answer_callback_query(call.id, "أنشئ تصنيفاً أولاً.", show_alert=True)
            return
        kb.add(InlineKeyboardButton(f"{Emoji.BACK} إلغاء", callback_data="back_to_main"))
        bot.edit_message_text("🎯 اختر التصنيف الأب:", call.message.chat.id, call.message.message_id, reply_markup=kb)

    elif sub == "add_cat_sub_set_parent":
        pid = int(data[2])
        steps[call.message.chat.id] = {"parent_id": pid}
        cat = CategoryRepository.get_by_id(pid)
        msg = bot.send_message(call.message.chat.id, f"📝 أرسل اسم التصنيف الفرعي تحت {Emoji.FOLDER} \"{cat['name']}\". (أو /cancel)")
        bot.register_next_step_handler(msg, admin_handlers.handle_add_new_category, bot)

    elif sub == "delete_category_select":
        kb = Keyboards.categories_tree(f"admin{DELIMITER}delete_category_confirm", add_back=False)
        if not kb.keyboard:
            bot.answer_callback_query(call.id, "لا توجد تصنيفات.", show_alert=True)
            return
        kb.add(InlineKeyboardButton(f"{Emoji.BACK} إلغاء", callback_data="back_to_main"))
        bot.edit_message_text(f"{Emoji.DELETE} اختر التصنيف للحذف:", call.message.chat.id, call.message.message_id, reply_markup=kb)

    elif sub == "delete_category_confirm":
        cid = int(data[2])
        cat = CategoryRepository.get_by_id(cid)
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton(f"{Emoji.DELETE} حذف مع الفيديوهات", callback_data=f"admin{DELIMITER}delete_cat_and_videos{DELIMITER}{cid}"),
               InlineKeyboardButton(f"{Emoji.MOVE} نقل الفيديوهات", callback_data=f"admin{DELIMITER}delete_cat_move_select{DELIMITER}{cid}"),
               InlineKeyboardButton(f"{Emoji.BACK} إلغاء", callback_data=f"admin{DELIMITER}cancel_delete_cat"))
        bot.edit_message_text(f"{Emoji.WARNING} حذف \"{cat['name']}\"؟", call.message.chat.id, call.message.message_id, reply_markup=kb)

    elif sub == "delete_cat_and_videos":
        cid = int(data[2])
        cat = CategoryRepository.get_by_id(cid)
        CategoryRepository.delete_with_contents(cid)
        bot.edit_message_text(Messages.category_deleted(cat['name']), call.message.chat.id, call.message.message_id)

    elif sub == "delete_cat_move_select":
        old = int(data[2])
        all_c = CategoryRepository.get_all()
        cats = [c for c in all_c if c['id'] != old]
        if not cats:
            bot.edit_message_text(f"{Emoji.ERROR} لا يوجد تصنيف آخر.", call.message.chat.id, call.message.message_id)
            return
        tree = _build_tree(cats)
        kb = InlineKeyboardMarkup(row_width=1)
        for c in tree:
            kb.add(InlineKeyboardButton(c['display_name'], callback_data=f"admin{DELIMITER}delete_cat_move_confirm{DELIMITER}{old}{DELIMITER}{c['id']}"))
        kb.add(InlineKeyboardButton(f"{Emoji.BACK} إلغاء", callback_data=f"admin{DELIMITER}cancel_delete_cat"))
        bot.edit_message_text("🎯 اختر التصنيف الهدف:", call.message.chat.id, call.message.message_id, reply_markup=kb)

    elif sub == "delete_cat_move_confirm":
        old, new = int(data[2]), int(data[3])
        old_cat = CategoryRepository.get_by_id(old)
        VideoRepository.move_from_category(old, new)
        CategoryRepository.delete_by_id(old)
        new_cat = CategoryRepository.get_by_id(new)
        bot.edit_message_text(f"{Emoji.SUCCESS} نُقلت إلى \"{new_cat['name']}\" وحُذف \"{old_cat['name']}\".",
                             call.message.chat.id, call.message.message_id)

    elif sub == "cancel_delete_cat":
        bot.edit_message_text("👍 تم الإلغاء.", call.message.chat.id, call.message.message_id)

    elif sub == "move_video_by_id":
        msg = bot.send_message(call.message.chat.id, "📝 أرسل رقم/أرقام الفيديو. (أو /cancel)")
        bot.register_next_step_handler(msg, admin_handlers.handle_move_by_id_input, bot)

    elif sub == "delete_videos_by_ids":
        msg = bot.send_message(call.message.chat.id, "📝 أرسل أرقام الفيديوهات. (أو /cancel)")
        bot.register_next_step_handler(msg, admin_handlers.handle_delete_by_ids_input, bot)

    elif sub == "move_confirm":
        if len(data) < 3:
            bot.edit_message_text(f"{Emoji.ERROR} بيانات ناقصة.", call.message.chat.id, call.message.message_id)
            return
        ncid = int(data[2])
        vids = steps.get(call.message.chat.id, {}).get("video_ids", [])
        if not vids:
            bot.edit_message_text(f"{Emoji.ERROR} لا توجد فيديوهات.", call.message.chat.id, call.message.message_id)
            return
        moved = VideoRepository.move_bulk(vids, ncid)
        cat = CategoryRepository.get_by_id(ncid)
        bot.edit_message_text(Messages.video_moved(moved, cat['name'], vids), call.message.chat.id, call.message.message_id)
        steps.pop(call.message.chat.id, None)

    elif sub == "update_metadata":
        msg = bot.edit_message_text(f"{Emoji.LOADING} جاري التحديث...", call.message.chat.id, call.message.message_id)
        threading.Thread(target=run_update_and_report_progress, args=(bot, msg.chat.id, msg.message_id)).start()

    elif sub == "set_active":
        kb = Keyboards.categories_tree(f"admin{DELIMITER}setcat", add_back=False)
        if not kb.keyboard:
            bot.answer_callback_query(call.id, "لا توجد تصنيفات.", show_alert=True)
            return
        kb.add(InlineKeyboardButton(f"{Emoji.BACK} إلغاء", callback_data="back_to_main"))
        bot.edit_message_text("🔘 اختر التصنيف:", call.message.chat.id, call.message.message_id, reply_markup=kb)

    elif sub == "setcat":
        cid = int(data[2])
        if SettingsRepository.set_active_category_id(cid):
            cat = CategoryRepository.get_by_id(cid)
            bot.edit_message_text(f"{Emoji.SUCCESS} تم تفعيل \"{cat['name']}\".", call.message.chat.id, call.message.message_id)

    elif sub == "add_channel":
        msg = bot.send_message(call.message.chat.id, "📝 أرسل معرف القناة. (أو /cancel)")
        bot.register_next_step_handler(msg, admin_handlers.handle_add_channel_step1, bot)

    elif sub == "remove_channel":
        msg = bot.send_message(call.message.chat.id, "📝 أرسل معرف القناة للإزالة. (أو /cancel)")
        bot.register_next_step_handler(msg, admin_handlers.handle_remove_channel_step, bot)

    elif sub == "list_channels":
        admin_handlers.handle_list_channels(call.message, bot)

    elif sub == "broadcast":
        msg = bot.send_message(call.message.chat.id, Messages.broadcast_prompt())
        bot.register_next_step_handler(msg, admin_handlers.handle_rich_broadcast, bot)

    elif sub == "sub_count":
        count = UserRepository.get_count()
        bot.send_message(call.message.chat.id, f"{Emoji.USER} المشتركين: *{count}*", parse_mode="Markdown")

    elif sub == "stats":
        vs = VideoRepository.get_stats()
        cc = CategoryRepository.get_count()
        pop = VideoRepository.get_popular()
        mvt, mvc, hrt, hra = None, 0, None, 0
        if pop["most_viewed"]:
            mv = pop["most_viewed"][0]
            mvt = (mv.get('caption') or "").split('\n')[0] or "فيديو"
            mvc = mv.get('view_count', 0)
        if pop["highest_rated"] and pop["highest_rated"][0].get('avg_rating') is not None:
            hr = pop["highest_rated"][0]
            hrt = (hr.get('caption') or "").split('\n')[0] or "فيديو"
            hra = hr.get('avg_rating', 0)
        bot.send_message(call.message.chat.id,
            Messages.stats(vs['video_count'], cc, vs['total_views'], vs['total_ratings'], mvt, mvc, hrt, hra),
            parse_mode="Markdown")
