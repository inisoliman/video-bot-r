#!/usr/bin/env python3
# ==============================================================================
# ملف: comment_handlers.py
# الوصف: معالجات نظام التعليقات الخاصة بين المستخدمين والأدمن
# ==============================================================================

import logging
from telebot import types
import db_manager as db

logger = logging.getLogger(__name__)

# ==============================================================================
# معالجات المستخدم
# ==============================================================================

def handle_add_comment(bot, call):
    """معالج لبدء إضافة تعليق على فيديو"""
    try:
        user_id = call.from_user.id
        video_id = int(call.data.split("::")[1])
        
        # حفظ حالة المستخدم
        db.set_user_state(user_id, "waiting_comment", {"video_id": video_id})
        
        bot.answer_callback_query(call.id)
        bot.send_message(
            user_id,
            "📝 *إضافة تعليق*\n\n"
            "الرجاء كتابة تعليقك أو استفسارك عن هذا الفيديو.\n"
            "سيتم إرساله للإدارة وسيتم الرد عليك في أقرب وقت.\n\n"
            "💡 _للإلغاء، اضغط /cancel_",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error in handle_add_comment: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ حدث خطأ، حاول مرة أخرى")

def process_comment_text(bot, message):
    """معالج لاستقبال نص التعليق من المستخدم"""
    try:
        user_id = message.from_user.id
        state = db.get_user_state(user_id)
        
        if not state or state['state'] != 'waiting_comment':
            return
        
        context = state.get('context', {})
        video_id = context.get('video_id')
        
        if not video_id:
            bot.send_message(user_id, "❌ حدث خطأ، الرجاء المحاولة مرة أخرى")
            db.clear_user_state(user_id)
            return
        
        # إضافة التعليق
        comment_text = message.text
        username = message.from_user.username or message.from_user.first_name or "مستخدم"
        
        comment_id = db.add_comment(video_id, user_id, username, comment_text)
        
        if comment_id:
            # مسح الحالة
            db.clear_user_state(user_id)
            
            # إرسال تأكيد للمستخدم
            bot.send_message(
                user_id,
                "✅ *تم إرسال تعليقك بنجاح!*\n\n"
                "سيتم مراجعته من قبل الإدارة والرد عليك في أقرب وقت.\n"
                "يمكنك متابعة تعليقاتك من خلال الأمر /my_comments",
                parse_mode="Markdown"
            )
            
            # إشعار الأدمن (اختياري - يمكن تفعيله لاحقاً)
            # notify_admins_new_comment(bot, comment_id, video_id, username, comment_text)
        else:
            bot.send_message(user_id, "❌ فشل إرسال التعليق، حاول مرة أخرى")
            
    except Exception as e:
        logger.error(f"Error in process_comment_text: {e}", exc_info=True)
        bot.send_message(message.from_user.id, "❌ حدث خطأ، حاول مرة أخرى")

def show_user_comments(bot, message, page=0):
    """عرض تعليقات المستخدم"""
    try:
        user_id = message.from_user.id
        comments, total = db.get_user_comments(user_id, page)
        
        if not comments:
            bot.send_message(
                user_id,
                "📭 *لا توجد تعليقات*\n\n"
                "لم تقم بإضافة أي تعليقات بعد.\n"
                "يمكنك إضافة تعليق على أي فيديو من خلال زر 'إضافة تعليق' 💬",
                parse_mode="Markdown"
            )
            return
        
        # عرض التعليقات
        for comment in comments:
            comment_text = (
                f"📹 *الفيديو:* {comment['video_caption'] or comment['video_name']}\n\n"
                f"💬 *تعليقك:*\n{comment['comment_text']}\n\n"
                f"📅 *التاريخ:* {comment['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
            )
            
            # إضافة الرد إذا كان موجوداً
            if comment['admin_reply']:
                comment_text += (
                    f"\n✅ *رد الإدارة:*\n{comment['admin_reply']}\n"
                    f"🕐 *تاريخ الرد:* {comment['replied_at'].strftime('%Y-%m-%d %H:%M')}"
                )
            else:
                comment_text += "\n⏳ *الحالة:* في انتظار الرد"
            
            bot.send_message(user_id, comment_text, parse_mode="Markdown")
        
        # أزرار التنقل
        if total > db.VIDEOS_PER_PAGE:
            markup = types.InlineKeyboardMarkup()
            buttons = []
            
            if page > 0:
                buttons.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"my_comments::{page-1}"))
            
            buttons.append(types.InlineKeyboardButton(f"📄 {page+1}/{(total-1)//db.VIDEOS_PER_PAGE + 1}", callback_data="noop"))
            
            if (page + 1) * db.VIDEOS_PER_PAGE < total:
                buttons.append(types.InlineKeyboardButton("➡️ التالي", callback_data=f"my_comments::{page+1}"))
            
            markup.row(*buttons)
            bot.send_message(user_id, "🔽 التنقل:", reply_markup=markup)
            
    except Exception as e:
        logger.error(f"Error in show_user_comments: {e}", exc_info=True)
        bot.send_message(message.from_user.id, "❌ حدث خطأ، حاول مرة أخرى")

# ==============================================================================
# معالجات الأدمن
# ==============================================================================

def show_all_comments(bot, message, admin_ids, page=0, unread_only=False):
    """عرض جميع التعليقات للأدمن"""
    try:
        user_id = message.from_user.id
        
        if user_id not in admin_ids:
            bot.send_message(user_id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        comments, total = db.get_all_comments(page, unread_only)
        
        filter_text = "غير المقروءة" if unread_only else "جميع"
        
        if not comments:
            bot.send_message(
                user_id,
                f"📭 *لا توجد تعليقات {filter_text}*",
                parse_mode="Markdown"
            )
            return
        
        # عرض عدد التعليقات غير المقروءة
        unread_count = db.get_unread_comments_count()
        header = f"📬 *التعليقات {filter_text}*\n🔔 غير المقروءة: {unread_count}\n\n"
        bot.send_message(user_id, header, parse_mode="Markdown")
        
        # عرض التعليقات
        for comment in comments:
            status_icon = "🔴" if not comment['is_read'] else "✅"
            
            comment_text = (
                f"{status_icon} *تعليق #{comment['id']}*\n\n"
                f"👤 *المستخدم:* @{comment['username']} (ID: {comment['user_id']})\n"
                f"📹 *الفيديو:* {comment['video_caption'] or comment['video_name']}\n\n"
                f"💬 *التعليق:*\n{comment['comment_text']}\n\n"
                f"📅 *التاريخ:* {comment['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
            )
            
            # إضافة الرد إذا كان موجوداً
            if comment['admin_reply']:
                comment_text += f"\n✅ *تم الرد:* {comment['admin_reply']}"
            
            # أزرار الإجراءات
            markup = types.InlineKeyboardMarkup()
            buttons = []
            
            if not comment['admin_reply']:
                buttons.append(types.InlineKeyboardButton("✍️ رد", callback_data=f"reply_comment::{comment['id']}"))
            
            if not comment['is_read']:
                buttons.append(types.InlineKeyboardButton("✓ تعليم كمقروء", callback_data=f"mark_read::{comment['id']}"))
            
            buttons.append(types.InlineKeyboardButton("🗑️ حذف", callback_data=f"delete_comment::{comment['id']}"))
            
            markup.row(*buttons)
            
            bot.send_message(user_id, comment_text, parse_mode="Markdown", reply_markup=markup)
        
        # أزرار التنقل والفلترة
        if total > db.VIDEOS_PER_PAGE or not unread_only:
            markup = types.InlineKeyboardMarkup()
            
            # أزرار التنقل
            nav_buttons = []
            if page > 0:
                callback = f"admin_comments_unread::{page-1}" if unread_only else f"admin_comments::{page-1}"
                nav_buttons.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=callback))
            
            nav_buttons.append(types.InlineKeyboardButton(f"📄 {page+1}/{(total-1)//db.VIDEOS_PER_PAGE + 1}", callback_data="noop"))
            
            if (page + 1) * db.VIDEOS_PER_PAGE < total:
                callback = f"admin_comments_unread::{page+1}" if unread_only else f"admin_comments::{page+1}"
                nav_buttons.append(types.InlineKeyboardButton("➡️ التالي", callback_data=callback))
            
            if nav_buttons:
                markup.row(*nav_buttons)
            
            # زر التبديل بين الكل وغير المقروءة
            filter_button = types.InlineKeyboardButton(
                "📋 عرض الكل" if unread_only else "🔔 غير المقروءة فقط",
                callback_data=f"admin_comments::0" if unread_only else f"admin_comments_unread::0"
            )
            markup.row(filter_button)
            
            bot.send_message(user_id, "🔽 الخيارات:", reply_markup=markup)
            
    except Exception as e:
        logger.error(f"Error in show_all_comments: {e}", exc_info=True)
        bot.send_message(message.from_user.id, "❌ حدث خطأ، حاول مرة أخرى")

def handle_reply_comment(bot, call, admin_ids):
    """معالج لبدء الرد على تعليق"""
    try:
        user_id = call.from_user.id
        
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        comment_id = int(call.data.split("::")[1])
        
        # حفظ حالة الأدمن
        db.set_user_state(user_id, "replying_comment", {"comment_id": comment_id})
        
        bot.answer_callback_query(call.id)
        bot.send_message(
            user_id,
            f"✍️ *الرد على التعليق #{comment_id}*\n\n"
            "الرجاء كتابة ردك على هذا التعليق.\n"
            "سيتم إرساله للمستخدم مباشرة.\n\n"
            "💡 _للإلغاء، اضغط /cancel_",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error in handle_reply_comment: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ حدث خطأ، حاول مرة أخرى")

def process_reply_text(bot, message, admin_ids):
    """معالج لاستقبال نص الرد من الأدمن"""
    try:
        user_id = message.from_user.id
        
        if user_id not in admin_ids:
            return
        
        state = db.get_user_state(user_id)
        
        if not state or state['state'] != 'replying_comment':
            return
        
        context = state.get('context', {})
        comment_id = context.get('comment_id')
        
        if not comment_id:
            bot.send_message(user_id, "❌ حدث خطأ، الرجاء المحاولة مرة أخرى")
            db.clear_user_state(user_id)
            return
        
        # جلب بيانات التعليق
        comment = db.get_comment_by_id(comment_id)
        
        if not comment:
            bot.send_message(user_id, "❌ التعليق غير موجود")
            db.clear_user_state(user_id)
            return
        
        # حفظ الرد
        reply_text = message.text
        
        if db.reply_to_comment(comment_id, reply_text):
            # مسح الحالة
            db.clear_user_state(user_id)
            
            # إرسال تأكيد للأدمن
            bot.send_message(
                user_id,
                f"✅ *تم إرسال الرد بنجاح!*\n\n"
                f"تم إرسال ردك على التعليق #{comment_id}",
                parse_mode="Markdown"
            )
            
            # إرسال إشعار للمستخدم
            try:
                notification_text = (
                    f"📬 *رد جديد على تعليقك!*\n\n"
                    f"📹 *الفيديو:* {comment['video_caption'] or comment['video_name']}\n\n"
                    f"💬 *تعليقك:*\n{comment['comment_text']}\n\n"
                    f"✅ *رد الإدارة:*\n{reply_text}\n\n"
                    f"يمكنك مشاهدة جميع تعليقاتك من خلال /my_comments"
                )
                bot.send_message(comment['user_id'], notification_text, parse_mode="Markdown")
            except Exception as notify_error:
                logger.warning(f"Could not notify user {comment['user_id']}: {notify_error}")
        else:
            bot.send_message(user_id, "❌ فشل إرسال الرد، حاول مرة أخرى")
            
    except Exception as e:
        logger.error(f"Error in process_reply_text: {e}", exc_info=True)
        bot.send_message(message.from_user.id, "❌ حدث خطأ، حاول مرة أخرى")

def handle_mark_read(bot, call, admin_ids):
    """معالج لتعليم التعليق كمقروء"""
    try:
        user_id = call.from_user.id
        
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        comment_id = int(call.data.split("::")[1])
        
        if db.mark_comment_read(comment_id):
            bot.answer_callback_query(call.id, "✅ تم تعليم التعليق كمقروء")
            # تحديث الرسالة
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        else:
            bot.answer_callback_query(call.id, "❌ فشل تحديث التعليق")
            
    except Exception as e:
        logger.error(f"Error in handle_mark_read: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ حدث خطأ")

def handle_delete_comment(bot, call, admin_ids):
    """معالج لحذف تعليق"""
    try:
        user_id = call.from_user.id
        
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        comment_id = int(call.data.split("::")[1])
        
        # طلب تأكيد الحذف
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_delete_comment::{comment_id}"),
            types.InlineKeyboardButton("❌ إلغاء", callback_data="noop")
        )
        
        bot.answer_callback_query(call.id)
        bot.send_message(
            user_id,
            f"⚠️ *تأكيد الحذف*\n\nهل أنت متأكد من حذف التعليق #{comment_id}؟",
            parse_mode="Markdown",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Error in handle_delete_comment: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ حدث خطأ")

def confirm_delete_comment(bot, call, admin_ids):
    """تأكيد حذف التعليق"""
    try:
        user_id = call.from_user.id
        
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        comment_id = int(call.data.split("::")[1])
        
        if db.delete_comment(comment_id):
            bot.answer_callback_query(call.id, "✅ تم حذف التعليق")
            bot.edit_message_text(
                "🗑️ *تم حذف التعليق بنجاح*",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown"
            )
        else:
            bot.answer_callback_query(call.id, "❌ فشل حذف التعليق")
            
    except Exception as e:
        logger.error(f"Error in confirm_delete_comment: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ حدث خطأ")
