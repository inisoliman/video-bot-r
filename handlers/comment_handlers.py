#!/usr/bin/env python3
# ==============================================================================
# ملف: comment_handlers.py
# الوصف: معالجات نظام التعليقات الخاصة بين المستخدمين والأدمن
# ==============================================================================

import logging
from telebot import types
import db_manager as db

logger = logging.getLogger(__name__)

# دالة لـ escape أحرف Markdown الخاصة
def markdown_escape(text):
    """Escape special characters for Markdown"""
    if not text:
        return ""
    text = str(text)
    # الأحرف الخاصة في Markdown
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, '\\' + char)
    return text

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
            "📝 *إضافة تعليق*\\n\\n"
            "الرجاء كتابة تعليقك أو استفسارك عن هذا الفيديو.\\n"
            "سيتم إرساله للإدارة وسيتم الرد عليك في أقرب وقت.\\n\\n"
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
                "✅ *تم إرسال تعليقك بنجاح!*\\n\\n"
                "سيتم مراجعته من قبل الإدارة والرد عليك في أقرب وقت.\\n"
                "يمكنك متابعة تعليقاتك من خلال الأمر /my\\_comments",
                parse_mode="Markdown"
            )
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
                "📭 *لا توجد تعليقات*\\n\\n"
                "لم تقم بإضافة أي تعليقات بعد.\\n"
                "يمكنك إضافة تعليق على أي فيديو من خلال زر 'إضافة تعليق' 💬",
                parse_mode="Markdown"
            )
            return
        
        # عرض التعليقات
        for comment in comments:
            video_title = markdown_escape(comment['video_caption'] or comment['video_name'])
            comment_text_escaped = markdown_escape(comment['comment_text'])
            
            comment_msg = (
                f"📹 *الفيديو:* {video_title}\n\n"
                f"💬 *تعليقك:*\n{comment_text_escaped}\n\n"
                f"📅 *التاريخ:* {comment['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
            )
            
            # إضافة الرد إذا كان موجوداً
            if comment['admin_reply']:
                admin_reply_escaped = markdown_escape(comment['admin_reply'])
                comment_msg += (
                    f"\n✅ *رد الإدارة:*\n{admin_reply_escaped}\n"
                    f"🕐 *تاريخ الرد:* {comment['replied_at'].strftime('%Y-%m-%d %H:%M')}"
                )
            else:
                comment_msg += "\n⏳ *الحالة:* في انتظار الرد"
            
            bot.send_message(user_id, comment_msg, parse_mode="Markdown")
        
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

def show_all_comments(bot, user_id, admin_ids, page=0, unread_only=False):
    """عرض جميع التعليقات للأدمن"""
    try:
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
        header = f"📬 *التعليقات {filter_text}*\\n🔔 غير المقروءة: {unread_count}\\n\\n"
        bot.send_message(user_id, header, parse_mode="Markdown")
        
        # عرض التعليقات
        for comment in comments:
            status_icon = "🔴" if not comment['is_read'] else "✅"
            
            # Escape جميع النصوص
            username = markdown_escape(comment['username'])
            video_title = markdown_escape(comment['video_caption'] or comment['video_name'])
            comment_text_escaped = markdown_escape(comment['comment_text'])
            
            comment_msg = (
                f"{status_icon} *تعليق #{comment['id']}*\n\n"
                f"👤 *المستخدم:* @{username} (ID: {comment['user_id']})\n"
                f"📹 *الفيديو:* {video_title}\n\n"
                f"💬 *التعليق:*\n{comment_text_escaped}\n\n"
                f"📅 *التاريخ:* {comment['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
            )
            
            # إضافة الرد إذا كان موجوداً
            if comment['admin_reply']:
                admin_reply_escaped = markdown_escape(comment['admin_reply'])
                comment_msg += f"\n✅ *تم الرد:* {admin_reply_escaped}"
            
            # أزرار الإجراءات
            markup = types.InlineKeyboardMarkup()
            buttons = []
            
            if not comment['admin_reply']:
                buttons.append(types.InlineKeyboardButton("✍️ رد", callback_data=f"reply_comment::{comment['id']}"))
            
            if not comment['is_read']:
                buttons.append(types.InlineKeyboardButton("✓ تعليم كمقروء", callback_data=f"mark_read::{comment['id']}"))
            
            buttons.append(types.InlineKeyboardButton("🗑️ حذف", callback_data=f"delete_comment::{comment['id']}"))
            
            markup.row(*buttons)
            
            bot.send_message(user_id, comment_msg, parse_mode="Markdown", reply_markup=markup)
        
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
            f"✍️ *الرد على التعليق #{comment_id}*\\n\\n"
            "الرجاء كتابة ردك على هذا التعليق.\\n"
            "سيتم إرساله للمستخدم مباشرة.\\n\\n"
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
                f"✅ *تم إرسال الرد بنجاح!*\\n\\n"
                f"تم إرسال ردك على التعليق #{comment_id}",
                parse_mode="Markdown"
            )
            
            # إرسال إشعار للمستخدم
            try:
                video_title = markdown_escape(comment['video_caption'] or comment['video_name'])
                comment_text_escaped = markdown_escape(comment['comment_text'])
                reply_escaped = markdown_escape(reply_text)
                
                notification_text = (
                    f"📬 *رد جديد على تعليقك!*\n\n"
                    f"📹 *الفيديو:* {video_title}\n\n"
                    f"💬 *تعليقك:*\n{comment_text_escaped}\n\n"
                    f"✅ *رد الإدارة:*\n{reply_escaped}\n\n"
                    f"يمكنك مشاهدة جميع تعليقاتك من خلال /my\\_comments"
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
            f"⚠️ *تأكيد الحذف*\\n\\nهل أنت متأكد من حذف التعليق #{comment_id}؟",
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

# ==============================================================================
# معالجات الحذف الجماعي (للأدمن فقط)
# ==============================================================================

def handle_delete_all_comments(bot, user_id, admin_ids):
    """حذف جميع التعليقات"""
    try:
        if user_id not in admin_ids:
            bot.send_message(user_id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        # طلب تأكيد
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ نعم، احذف الكل", callback_data="confirm_delete_all_comments"),
            types.InlineKeyboardButton("❌ إلغاء", callback_data="noop")
        )
        
        stats = db.get_comments_stats()
        total = stats['total_comments'] if stats else 0
        
        bot.send_message(
            user_id,
            f"⚠️ *تحذير!*\n\n"
            f"أنت على وشك حذف *جميع التعليقات* ({total} تعليق)\n"
            f"هذا الإجراء لا يمكن التراجع عنه!\n\n"
            f"هل أنت متأكد؟",
            parse_mode="Markdown",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Error in handle_delete_all_comments: {e}", exc_info=True)
        bot.send_message(user_id, "❌ حدث خطأ")

def confirm_delete_all_comments(bot, call, admin_ids):
    """تأكيد حذف جميع التعليقات"""
    try:
        user_id = call.from_user.id
        
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        deleted_count = db.delete_all_comments()
        
        bot.answer_callback_query(call.id, f"✅ تم حذف {deleted_count} تعليق")
        bot.edit_message_text(
            f"🗑️ *تم حذف جميع التعليقات*\n\n"
            f"عدد التعليقات المحذوفة: {deleted_count}",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error in confirm_delete_all_comments: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ حدث خطأ")

def handle_delete_user_comments(bot, user_id, admin_ids, target_user_id_str=None):
    """حذف تعليقات مستخدم معين"""
    try:
        if user_id not in admin_ids:
            bot.send_message(user_id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        # استخراج user_id من الأمر (إذا تم تمريره)
        if target_user_id_str:
            parts = target_user_id_str.split()
        else:
            bot.send_message(
                user_id,
                "❌ *الاستخدام الصحيح:*\n"
                "`/delete_user_comments <user_id>`\n\n"
                "مثال: `/delete_user_comments 123456789`",
                parse_mode="Markdown"
            )
            return
        
        if len(parts) < 2:
            bot.reply_to(
                message,
                "❌ *الاستخدام الصحيح:*\n"
                "`/delete_user_comments <user_id>`\n\n"
                "مثال: `/delete_user_comments 123456789`",
                parse_mode="Markdown"
            )
            return
        
        try:
            target_user_id = int(parts[1])
        except ValueError:
            bot.reply_to(message, "❌ رقم المستخدم غير صحيح")
            return
        
        # طلب تأكيد
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_delete_user_comments::{target_user_id}"),
            types.InlineKeyboardButton("❌ إلغاء", callback_data="noop")
        )
        
        bot.send_message(
            user_id,
            f"⚠️ *تأكيد الحذف*\n\n"
            f"هل أنت متأكد من حذف جميع تعليقات المستخدم `{target_user_id}`؟",
            parse_mode="Markdown",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Error in handle_delete_user_comments: {e}", exc_info=True)
        bot.send_message(user_id, "❌ حدث خطأ")

def confirm_delete_user_comments(bot, call, admin_ids):
    """تأكيد حذف تعليقات مستخدم"""
    try:
        user_id = call.from_user.id
        
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        target_user_id = int(call.data.split("::")[1])
        deleted_count = db.delete_user_comments(target_user_id)
        
        bot.answer_callback_query(call.id, f"✅ تم حذف {deleted_count} تعليق")
        bot.edit_message_text(
            f"🗑️ *تم حذف تعليقات المستخدم*\n\n"
            f"المستخدم: `{target_user_id}`\n"
            f"عدد التعليقات المحذوفة: {deleted_count}",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error in confirm_delete_user_comments: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ حدث خطأ")

def handle_delete_old_comments(bot, user_id, admin_ids, days_str=None):
    """حذف التعليقات القديمة"""
    try:
        if user_id not in admin_ids:
            bot.send_message(user_id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        # استخراج عدد الأيام من الأمر (افتراضي 30)
        days = 30
        if days_str:
            parts = days_str.split()
            if len(parts) >= 2:
                try:
                    days = int(parts[1])
                except ValueError:
                    bot.send_message(user_id, "❌ عدد الأيام غير صحيح")
                    return
        
        # طلب تأكيد
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_delete_old_comments::{days}"),
            types.InlineKeyboardButton("❌ إلغاء", callback_data="noop")
        )
        
        bot.send_message(
            user_id,
            f"⚠️ *تأكيد الحذف*\n\n"
            f"هل أنت متأكد من حذف التعليقات الأقدم من *{days} يوم*؟",
            parse_mode="Markdown",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Error in handle_delete_old_comments: {e}", exc_info=True)
        bot.send_message(user_id, "❌ حدث خطأ")

def confirm_delete_old_comments(bot, call, admin_ids):
    """تأكيد حذف التعليقات القديمة"""
    try:
        user_id = call.from_user.id
        
        if user_id not in admin_ids:
            bot.answer_callback_query(call.id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        days = int(call.data.split("::")[1])
        deleted_count = db.delete_old_comments(days)
        
        bot.answer_callback_query(call.id, f"✅ تم حذف {deleted_count} تعليق")
        bot.edit_message_text(
            f"🗑️ *تم حذف التعليقات القديمة*\n\n"
            f"الأقدم من: {days} يوم\n"
            f"عدد التعليقات المحذوفة: {deleted_count}",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error in confirm_delete_old_comments: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ حدث خطأ")

def handle_comments_stats(bot, user_id, admin_ids):
    """عرض إحصائيات التعليقات"""
    try:
        if user_id not in admin_ids:
            bot.send_message(user_id, "⛔ هذا الأمر للإدارة فقط")
            return
        
        stats = db.get_comments_stats()
        
        if not stats:
            bot.send_message(user_id, "❌ فشل جلب الإحصائيات")
            return
        
        stats_text = (
            f"📊 *إحصائيات التعليقات*\n\n"
            f"📝 إجمالي التعليقات: {stats['total_comments']}\n"
            f"🔴 غير المقروءة: {stats['unread_comments']}\n"
            f"✅ تم الرد عليها: {stats['replied_comments']}\n"
            f"👥 عدد المستخدمين: {stats['unique_users']}"
        )
        
        bot.send_message(user_id, stats_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in handle_comments_stats: {e}", exc_info=True)
        bot.send_message(user_id, "❌ حدث خطأ")
