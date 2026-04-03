#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/handlers/comment_handlers.py
# الوصف: معالجات نظام التعليقات
# ==============================================================================

import logging
from telebot import types

from bot.database.repositories.comment_repo import CommentRepository
from bot.database.repositories.user_repo import UserRepository
from bot.database.repositories.video_repo import VIDEOS_PER_PAGE
from bot.ui.emoji import Emoji
from bot.ui.messages import Messages

logger = logging.getLogger(__name__)


def _escape_md(text):
    if not text: return ""
    text = str(text)
    for c in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(c, '\\' + c)
    return text


def register(bot, admin_ids):
    """تسجيل أوامر التعليقات (تُسجل فقط أوامر /my_comments إذا لزم)"""
    pass  # الأوامر تُعالج في user_handlers و callback_handlers


def handle_add_comment(bot, call):
    try:
        user_id = call.from_user.id
        video_id = int(call.data.split("::")[1])
        UserRepository.set_state(user_id, "waiting_comment", {"video_id": video_id})
        bot.answer_callback_query(call.id)
        bot.send_message(user_id, Messages.comment_prompt(), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"add_comment error: {e}", exc_info=True)
        bot.answer_callback_query(call.id, f"{Emoji.ERROR} حدث خطأ")


def process_comment_text(bot, message):
    try:
        uid = message.from_user.id
        state = UserRepository.get_state(uid)
        if not state or state['state'] != 'waiting_comment':
            return
        ctx = state.get('context', {})
        vid = ctx.get('video_id')
        if not vid:
            bot.send_message(uid, f"{Emoji.ERROR} خطأ، حاول مجدداً")
            UserRepository.clear_state(uid)
            return
        username = message.from_user.username or message.from_user.first_name or "مستخدم"
        cid = CommentRepository.add(vid, uid, username, message.text)
        if cid:
            UserRepository.clear_state(uid)
            bot.send_message(uid, Messages.comment_sent(), parse_mode="Markdown")
        else:
            bot.send_message(uid, f"{Emoji.ERROR} فشل إرسال التعليق")
    except Exception as e:
        logger.error(f"process_comment error: {e}", exc_info=True)


def show_user_comments(bot, message, page=0):
    try:
        uid = message.from_user.id
        comments, total = CommentRepository.get_by_user(uid, page)
        if not comments:
            bot.send_message(uid, Messages.no_comments(), parse_mode="Markdown")
            return
        for c in comments:
            vt = _escape_md(c['video_caption'] or c['video_name'])
            ct = _escape_md(c['comment_text'])
            msg = f"📹 *الفيديو:* {vt}\n\n💬 *تعليقك:*\n{ct}\n\n📅 {c['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
            if c['admin_reply']:
                msg += f"\n{Emoji.SUCCESS} *رد الإدارة:*\n{_escape_md(c['admin_reply'])}"
            else:
                msg += f"\n{Emoji.LOADING} في انتظار الرد"
            bot.send_message(uid, msg, parse_mode="Markdown")
        if total > VIDEOS_PER_PAGE:
            mk = types.InlineKeyboardMarkup()
            btns = []
            if page > 0: btns.append(types.InlineKeyboardButton("⬅️", callback_data=f"my_comments::{page-1}"))
            btns.append(types.InlineKeyboardButton(f"{page+1}/{(total-1)//VIDEOS_PER_PAGE+1}", callback_data="noop"))
            if (page+1)*VIDEOS_PER_PAGE < total: btns.append(types.InlineKeyboardButton("➡️", callback_data=f"my_comments::{page+1}"))
            mk.row(*btns)
            bot.send_message(uid, "🔽 التنقل:", reply_markup=mk)
    except Exception as e:
        logger.error(f"show_user_comments error: {e}", exc_info=True)


def show_all_comments(bot, user_id, admin_ids, page=0, unread_only=False):
    try:
        if user_id not in admin_ids: return
        comments, total = CommentRepository.get_all(page, unread_only)
        ft = "غير المقروءة" if unread_only else "جميع"
        if not comments:
            bot.send_message(user_id, f"📭 *لا توجد تعليقات {ft}*", parse_mode="Markdown")
            return
        uc = CommentRepository.get_unread_count()
        bot.send_message(user_id, f"📬 *التعليقات {ft}*\n🔔 غير المقروءة: {uc}", parse_mode="Markdown")
        for c in comments:
            si = "🔴" if not c['is_read'] else "✅"
            un = _escape_md(c['username'])
            vt = _escape_md(c['video_caption'] or c['video_name'])
            ct = _escape_md(c['comment_text'])
            msg = f"{si} *تعليق #{c['id']}*\n\n👤 @{un} (ID: {c['user_id']})\n📹 {vt}\n\n💬 {ct}\n📅 {c['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
            if c['admin_reply']:
                msg += f"\n{Emoji.SUCCESS} *رد:* {_escape_md(c['admin_reply'])}"
            mk = types.InlineKeyboardMarkup()
            btns = []
            if not c['admin_reply']: btns.append(types.InlineKeyboardButton("✍️ رد", callback_data=f"reply_comment::{c['id']}"))
            if not c['is_read']: btns.append(types.InlineKeyboardButton("✓ مقروء", callback_data=f"mark_read::{c['id']}"))
            btns.append(types.InlineKeyboardButton("🗑️", callback_data=f"delete_comment::{c['id']}"))
            mk.row(*btns)
            bot.send_message(user_id, msg, parse_mode="Markdown", reply_markup=mk)
    except Exception as e:
        logger.error(f"show_all_comments error: {e}", exc_info=True)


def handle_reply_comment(bot, call, admin_ids):
    try:
        uid = call.from_user.id
        if uid not in admin_ids: return
        cid = int(call.data.split("::")[1])
        UserRepository.set_state(uid, "replying_comment", {"comment_id": cid})
        bot.answer_callback_query(call.id)
        bot.send_message(uid, f"✍️ *الرد على التعليق #{cid}*\n\nاكتب ردك. /cancel للإلغاء.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"reply_comment error: {e}", exc_info=True)


def process_reply_text(bot, message, admin_ids):
    try:
        uid = message.from_user.id
        if uid not in admin_ids: return
        state = UserRepository.get_state(uid)
        if not state or state['state'] != 'replying_comment': return
        cid = state.get('context', {}).get('comment_id')
        if not cid:
            UserRepository.clear_state(uid)
            return
        comment = CommentRepository.get_by_id(cid)
        if not comment:
            bot.send_message(uid, f"{Emoji.ERROR} التعليق غير موجود")
            UserRepository.clear_state(uid)
            return
        if CommentRepository.reply(cid, message.text):
            UserRepository.clear_state(uid)
            bot.send_message(uid, f"{Emoji.SUCCESS} *تم الرد على #{cid}*", parse_mode="Markdown")
            try:
                vt = _escape_md(comment['video_caption'] or comment['video_name'])
                bot.send_message(comment['user_id'],
                    f"📬 *رد جديد!*\n\n📹 {vt}\n\n💬 {_escape_md(comment['comment_text'])}\n\n{Emoji.SUCCESS} *الرد:*\n{_escape_md(message.text)}",
                    parse_mode="Markdown")
            except Exception as ne:
                logger.warning(f"Notify error: {ne}")
    except Exception as e:
        logger.error(f"process_reply error: {e}", exc_info=True)


def handle_mark_read(bot, call, admin_ids):
    try:
        if call.from_user.id not in admin_ids: return
        cid = int(call.data.split("::")[1])
        if CommentRepository.mark_read(cid):
            bot.answer_callback_query(call.id, f"{Emoji.SUCCESS} مقروء")
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        logger.error(f"mark_read error: {e}", exc_info=True)


def handle_delete_comment(bot, call, admin_ids):
    try:
        if call.from_user.id not in admin_ids: return
        cid = int(call.data.split("::")[1])
        mk = types.InlineKeyboardMarkup()
        mk.row(types.InlineKeyboardButton(f"{Emoji.SUCCESS} نعم", callback_data=f"confirm_delete_comment::{cid}"),
               types.InlineKeyboardButton(f"{Emoji.ERROR} لا", callback_data="noop"))
        bot.answer_callback_query(call.id)
        bot.send_message(call.from_user.id, f"{Emoji.WARNING} حذف التعليق #{cid}؟", parse_mode="Markdown", reply_markup=mk)
    except Exception as e:
        logger.error(f"delete_comment error: {e}", exc_info=True)


def confirm_delete_comment(bot, call, admin_ids):
    try:
        if call.from_user.id not in admin_ids: return
        cid = int(call.data.split("::")[1])
        if CommentRepository.delete(cid):
            bot.answer_callback_query(call.id, f"{Emoji.SUCCESS} حُذف")
            bot.edit_message_text(f"🗑️ *حُذف #{cid}*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"confirm_delete error: {e}", exc_info=True)


def handle_delete_all_comments(bot, user_id, admin_ids):
    if user_id not in admin_ids: return
    stats = CommentRepository.get_stats()
    total = stats['total_comments'] if stats else 0
    mk = types.InlineKeyboardMarkup()
    mk.row(types.InlineKeyboardButton(f"{Emoji.SUCCESS} احذف الكل", callback_data="confirm_delete_all_comments"),
           types.InlineKeyboardButton(f"{Emoji.ERROR} إلغاء", callback_data="noop"))
    bot.send_message(user_id, f"{Emoji.WARNING} *حذف كل التعليقات* ({total})?\nلا يمكن التراجع!",
                     parse_mode="Markdown", reply_markup=mk)


def confirm_delete_all_comments(bot, call, admin_ids):
    if call.from_user.id not in admin_ids: return
    d = CommentRepository.delete_all()
    bot.answer_callback_query(call.id, f"{Emoji.SUCCESS} حُذف {d}")
    bot.edit_message_text(f"🗑️ *حُذف {d} تعليق*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")


def confirm_delete_user_comments(bot, call, admin_ids):
    if call.from_user.id not in admin_ids: return
    tuid = int(call.data.split("::")[1])
    d = CommentRepository.delete_by_user(tuid)
    bot.answer_callback_query(call.id, f"{Emoji.SUCCESS} حُذف {d}")
    bot.edit_message_text(f"🗑️ *حُذف {d} تعليق*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")


def confirm_delete_old_comments(bot, call, admin_ids):
    if call.from_user.id not in admin_ids: return
    days = int(call.data.split("::")[1])
    d = CommentRepository.delete_old(days)
    bot.answer_callback_query(call.id, f"{Emoji.SUCCESS} حُذف {d}")
    bot.edit_message_text(f"🗑️ *حُذف {d} تعليق أقدم من {days} يوم*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")


def handle_comments_stats(bot, user_id, admin_ids):
    if user_id not in admin_ids: return
    s = CommentRepository.get_stats()
    if not s:
        bot.send_message(user_id, f"{Emoji.ERROR} فشل جلب الإحصائيات")
        return
    bot.send_message(user_id,
        f"{Emoji.STATS} *إحصائيات التعليقات*\n\n"
        f"📝 الإجمالي: {s['total_comments']}\n"
        f"🔴 غير مقروءة: {s['unread_comments']}\n"
        f"✅ تم الرد: {s['replied_comments']}\n"
        f"👥 مستخدمين: {s['unique_users']}",
        parse_mode="Markdown")
