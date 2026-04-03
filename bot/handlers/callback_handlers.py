#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/handlers/callback_handlers.py
# الوصف: معالجات أزرار الـ Callback Query
# ==============================================================================

import logging
import threading

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.core.config import settings
from bot.database.repositories.video_repo import VideoRepository, VIDEOS_PER_PAGE
from bot.database.repositories.category_repo import CategoryRepository
from bot.database.repositories.settings_repo import SettingsRepository
from bot.database.repositories.user_repo import UserRepository
from bot.services.subscription_service import SubscriptionService
from bot.ui.messages import Messages
from bot.ui.keyboards import Keyboards, _build_tree
from bot.ui.emoji import Emoji
from bot.handlers import admin_handlers, comment_handlers
from bot.handlers.user_handlers import user_last_search
from bot.handlers.admin_callback import handle_admin_callback

logger = logging.getLogger(__name__)
DELIMITER = settings.CALLBACK_DELIMITER


def register(bot, admin_ids):
    @bot.callback_query_handler(func=lambda call: True)
    def callback_query(call):
        try:
            user_id = call.from_user.id
            data = call.data.split(DELIMITER)
            action = data[0]

            is_sub, unsub = SubscriptionService.check(bot, user_id)
            if action != "check_subscription" and not is_sub:
                try:
                    bot.edit_message_text(Messages.subscription_required(),
                        call.message.chat.id, call.message.message_id,
                        parse_mode="Markdown", reply_markup=Keyboards.subscription(unsub))
                except:
                    bot.send_message(call.message.chat.id, Messages.subscription_required(),
                        parse_mode="Markdown", reply_markup=Keyboards.subscription(unsub))
                return

            if action == "check_subscription":
                _handle_check_sub(bot, call, user_id)
            elif action == "fav":
                _handle_fav(bot, call, data, user_id)
            elif action in ["fav_page", "history_page"]:
                _handle_list_page(bot, call, data, action, user_id)
            elif action in ["search_type", "adv_filter", "adv_search", "search_scope"]:
                _handle_search(bot, call, data, action)
            elif action == "admin":
                if user_id not in admin_ids:
                    bot.answer_callback_query(call.id, "غير مصرح.", show_alert=True)
                    return
                handle_admin_callback(bot, call, data, admin_ids)
            elif action == "popular":
                _handle_popular(bot, call, data)
            elif action == "popular_page":
                _handle_popular_page(bot, call, data)
            elif action == "back_to_cats":
                kb = Keyboards.categories_tree("cat", add_back=True)
                bot.edit_message_text(f"{Emoji.FOLDER} *التصنيفات:*",
                    call.message.chat.id, call.message.message_id,
                    parse_mode="Markdown", reply_markup=kb)
            elif action == "back_to_main":
                bot.delete_message(call.message.chat.id, call.message.message_id)
                bot.send_message(call.message.chat.id, Messages.main_menu(),
                    parse_mode="Markdown", reply_markup=Keyboards.main_menu())
            elif action == "video":
                _handle_video(bot, call, data, user_id)
            elif action == "rate":
                _handle_rate(bot, call, data, user_id)
            elif action == "cat":
                _handle_cat(bot, call, data)
            elif action == "add_comment":
                comment_handlers.handle_add_comment(bot, call)
            elif action == "my_comments":
                p = int(data[1]) if len(data) > 1 else 0
                comment_handlers.show_user_comments(bot, call.message, p)
                bot.answer_callback_query(call.id)
            elif action in ["admin_comments", "admin_comments_unread"]:
                if user_id not in admin_ids:
                    bot.answer_callback_query(call.id, "⛔", show_alert=True)
                    return
                p = int(data[1]) if len(data) > 1 else 0
                comment_handlers.show_all_comments(bot, user_id, admin_ids, p,
                    unread_only=(action == "admin_comments_unread"))
                bot.answer_callback_query(call.id)
            elif action == "reply_comment" and user_id in admin_ids:
                comment_handlers.handle_reply_comment(bot, call, admin_ids)
            elif action == "mark_read" and user_id in admin_ids:
                comment_handlers.handle_mark_read(bot, call, admin_ids)
            elif action == "delete_comment" and user_id in admin_ids:
                comment_handlers.handle_delete_comment(bot, call, admin_ids)
            elif action == "confirm_delete_comment" and user_id in admin_ids:
                comment_handlers.confirm_delete_comment(bot, call, admin_ids)
            elif action == "confirm_delete_all_comments" and user_id in admin_ids:
                comment_handlers.confirm_delete_all_comments(bot, call, admin_ids)
            elif action == "confirm_delete_user_comments" and user_id in admin_ids:
                comment_handlers.confirm_delete_user_comments(bot, call, admin_ids)
            elif action == "confirm_delete_old_comments" and user_id in admin_ids:
                comment_handlers.confirm_delete_old_comments(bot, call, admin_ids)
            elif action == "noop":
                pass

        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"API error: {e}", exc_info=True)
            try:
                if "query is too old" not in str(e).lower():
                    bot.answer_callback_query(call.id, Messages.connection_error(), show_alert=True)
            except:
                pass
        except Exception as e:
            logger.error(f"Callback error: {e}", exc_info=True)
            try:
                bot.answer_callback_query(call.id, Messages.generic_error(), show_alert=True)
            except:
                pass


def _handle_check_sub(bot, call, user_id):
    is_sub, unsub = SubscriptionService.check(bot, user_id)
    if is_sub:
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        bot.send_message(call.message.chat.id, Messages.welcome(call.from_user.first_name),
            parse_mode="Markdown", reply_markup=Keyboards.main_menu())
    else:
        try:
            bot.edit_message_text(Messages.subscription_failed(),
                call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=Keyboards.subscription(unsub))
        except:
            bot.answer_callback_query(call.id, Messages.subscription_failed(), show_alert=True)


def _handle_fav(bot, call, data, user_id):
    _, act, vid = data
    vid = int(vid)
    if act == "remove":
        VideoRepository.remove_favorite(user_id, vid)
        text = Messages.fav_removed()
    else:
        VideoRepository.add_favorite(user_id, vid)
        text = Messages.fav_added()
    kb = Keyboards.video_actions(vid, user_id)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=kb)
    bot.answer_callback_query(call.id, text)


def _handle_list_page(bot, call, data, action, user_id):
    page = int(data[2])
    if action == "fav_page":
        videos, total = VideoRepository.get_favorites(user_id, page)
        title = Messages.favorites_header()
    else:
        videos, total = VideoRepository.get_history(user_id, page)
        title = Messages.history_header()
    if not videos:
        bot.edit_message_text("لا توجد المزيد.", call.message.chat.id, call.message.message_id)
        return
    kb = Keyboards.paginated(videos, total, page, action, "user_data")
    bot.edit_message_text(title, call.message.chat.id, call.message.message_id, reply_markup=kb)


def _handle_search(bot, call, data, action):
    qd = user_last_search.get(call.message.chat.id)
    if not qd or 'query' not in qd:
        bot.edit_message_text(Messages.search_no_results(""), call.message.chat.id, call.message.message_id)
        return
    q = qd['query']

    if action == "search_type":
        st = data[1]
        if st == "normal":
            kb = InlineKeyboardMarkup(row_width=1)
            cats = CategoryRepository.get_all()
            for c in _build_tree(cats):
                kb.add(InlineKeyboardButton(f"{Emoji.SEARCH} {c['name']}",
                    callback_data=f"search_scope{DELIMITER}{c['id']}{DELIMITER}0"))
            kb.add(InlineKeyboardButton(f"{Emoji.SEARCH} كل التصنيفات",
                callback_data=f"search_scope{DELIMITER}all{DELIMITER}0"))
            bot.edit_message_text(Messages.search_scope_select(q),
                call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=kb)
        elif st == "advanced":
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(InlineKeyboardButton("📺 الجودة", callback_data=f"adv_filter{DELIMITER}quality"),
                   InlineKeyboardButton("🗣️ الحالة", callback_data=f"adv_filter{DELIMITER}status"))
            kb.add(InlineKeyboardButton(f"{Emoji.BACK} رجوع", callback_data="back_to_main"))
            bot.edit_message_text(f"{Emoji.SETTINGS} اختر فلتر:", call.message.chat.id, call.message.message_id, reply_markup=kb)

    elif action == "adv_filter":
        ft = data[1]
        if ft == "quality":
            kb = InlineKeyboardMarkup(row_width=3)
            for qq in ["1080p", "720p", "480p", "360p"]:
                kb.add(InlineKeyboardButton(qq, callback_data=f"adv_search{DELIMITER}quality{DELIMITER}{qq}{DELIMITER}0"))
            kb.add(InlineKeyboardButton(f"{Emoji.BACK} رجوع", callback_data=f"search_type{DELIMITER}advanced"))
            bot.edit_message_text("📺 اختر الجودة:", call.message.chat.id, call.message.message_id, reply_markup=kb)
        elif ft == "status":
            kb = InlineKeyboardMarkup(row_width=2)
            for s in ["مترجم", "مدبلج"]:
                kb.add(InlineKeyboardButton(s, callback_data=f"adv_search{DELIMITER}status{DELIMITER}{s}{DELIMITER}0"))
            kb.add(InlineKeyboardButton(f"{Emoji.BACK} رجوع", callback_data=f"search_type{DELIMITER}advanced"))
            bot.edit_message_text("🗣️ اختر الحالة:", call.message.chat.id, call.message.message_id, reply_markup=kb)

    elif action == "adv_search":
        _, ft, fv, ps = data
        page = int(ps)
        kw = {'query': q, 'page': page}
        if ft == 'quality': kw['quality'] = fv
        elif ft == 'status': kw['status'] = fv
        videos, total = VideoRepository.search(**kw)
        if not videos:
            bot.edit_message_text(Messages.search_no_results(q), call.message.chat.id, call.message.message_id)
            return
        kb = Keyboards.paginated(videos, total, page, f"adv_search{DELIMITER}{ft}", fv)
        bot.edit_message_text(Messages.search_results(q), call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", reply_markup=kb)

    elif action == "search_scope":
        _, scope, ps = data
        page = int(ps)
        cat_id = int(scope) if scope != "all" else None
        videos, total = VideoRepository.search(query=q, page=page, category_id=cat_id)
        if not videos:
            bot.edit_message_text(Messages.search_no_results(q), call.message.chat.id, call.message.message_id)
            return
        kb = Keyboards.paginated(videos, total, page, "search_scope", scope)
        bot.edit_message_text(Messages.search_results(q), call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", reply_markup=kb)


def _handle_popular(bot, call, data):
    sub = data[1]
    pop = VideoRepository.get_popular()
    vids = pop.get(sub, [])
    title = Messages.popular_most_viewed() if sub == "most_viewed" else Messages.popular_highest_rated()
    if vids:
        kb = Keyboards.paginated(vids, len(vids), 0, "popular_page", sub)
        bot.edit_message_text(title, call.message.chat.id, call.message.message_id, reply_markup=kb)
    else:
        bot.edit_message_text(Messages.no_popular(), call.message.chat.id, call.message.message_id)


def _handle_popular_page(bot, call, data):
    sub, page = data[1], int(data[2])
    pop = VideoRepository.get_popular()
    vids = pop.get(sub, [])
    if vids:
        start = page * VIDEOS_PER_PAGE
        pv = vids[start:start + VIDEOS_PER_PAGE]
        kb = Keyboards.paginated(pv, len(vids), page, "popular_page", sub)
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=kb)


def _handle_video(bot, call, data, user_id):
    try:
        _, vid, mid, cid = data
        if not vid.isdigit() or not mid.isdigit():
            bot.answer_callback_query(call.id, "خطأ.", show_alert=True)
            return
        v, m, c = int(vid), int(mid), int(cid)
        VideoRepository.increment_views(v)
        VideoRepository.add_to_history(user_id, v)
        bot.copy_message(call.message.chat.id, c, m)
        kb = Keyboards.video_actions(v, user_id)
        bot.send_message(call.message.chat.id, Messages.rate_video(), reply_markup=kb)
    except telebot.apihelper.ApiTelegramException as e:
        if "message not found" in str(e).lower():
            bot.answer_callback_query(call.id, Messages.video_not_found(), show_alert=True)
        else:
            bot.answer_callback_query(call.id, Messages.connection_error(), show_alert=True)
    except Exception as e:
        logger.error(f"Video error: {e}", exc_info=True)
        bot.answer_callback_query(call.id, Messages.generic_error(), show_alert=True)


def _handle_rate(bot, call, data, user_id):
    _, vid, rat = data
    if not vid.isdigit() or not rat.isdigit():
        bot.answer_callback_query(call.id, "خطأ.", show_alert=True)
        return
    v, r = int(vid), int(rat)
    if not (1 <= r <= 5):
        bot.answer_callback_query(call.id, "التقييم 1-5.", show_alert=True)
        return
    if VideoRepository.add_rating(v, user_id, r):
        kb = Keyboards.video_actions(v, user_id)
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=kb)
        bot.answer_callback_query(call.id, Messages.rated(r))
    else:
        bot.answer_callback_query(call.id, Messages.generic_error())


def _handle_cat(bot, call, data):
    try:
        _, cid, ps = data
        if not cid.isdigit() or not ps.isdigit():
            bot.answer_callback_query(call.id, "خطأ.", show_alert=True)
            return
        cat_id, page = int(cid), int(ps)
        cat = CategoryRepository.get_by_id(cat_id)
        if not cat:
            bot.edit_message_text(f"{Emoji.ERROR} التصنيف غير موجود.", call.message.chat.id, call.message.message_id)
            return
        children = CategoryRepository.get_children(cat_id)
        videos, total = VideoRepository.get_by_category(cat_id, page)
        if not children and not videos:
            kb = Keyboards.combined([], [], 0, 0, cat_id)
            bot.edit_message_text(Messages.category_empty(cat['name']),
                call.message.chat.id, call.message.message_id, reply_markup=kb)
        else:
            parts = []
            if children: parts.append(f"{len(children)} قسم فرعي")
            if videos: parts.append(f"{total} فيديو")
            info = " • ".join(parts) if parts else "فارغ"
            kb = Keyboards.combined(children, videos, total, page, cat_id)
            bot.edit_message_text(Messages.category_header(cat['name'], info),
                call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        logger.error(f"Cat error: {e}", exc_info=True)
        bot.answer_callback_query(call.id, Messages.generic_error())
