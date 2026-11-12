# handlers/group_handlers.py

import telebot
from telebot import types
import logging
import threading
from html import escape as html_escape

from .user_handlers import build_group_library_markup

logger = logging.getLogger(__name__)

def register(bot, admin_ids):
    """تسجيل معالجات الجروبات"""
    
    @bot.message_handler(content_types=['new_chat_members'])
    def welcome_new_member(message):
        """
        رسالة ترحيب عند إضافة البوت لجروب جديد.
        تظهر فقط في الجروبات، وتُحذف تلقائياً بعد 30 دقيقة.
        """
        try:
            # التحقق من أن البوت هو العضو الجديد
            bot_info = bot.get_me()
            new_members = message.new_chat_members
            
            bot_added = any(member.id == bot_info.id for member in new_members)
            
            if bot_added:
                # اسم البوت قد يحوي & أو < أو > أو ": يُهرَّب قبل وضعه في HTML
                bot_username = html_escape(str(getattr(bot_info, "username", "") or ""))
                # رسالة الترحيب
                welcome_text = (
                    "👋 <b>مرحباً! تم إضافتي للجروب</b>\n\n"
                    "🔍 <b>للبحث السريع عن الفيديوهات:</b>\n"
                    f"اكتب: <code>@{bot_username} كلمة البحث</code>\n\n"
                    "📝 <b>مثال:</b>\n"
                    f"<code>@{bot_username} أكشن</code>\n"
                    f"<code>@{bot_username} كوميدي</code>\n\n"
                    "💡 <b>نصيحة:</b> يمكنك البحث في أي محادثة بدون إضافتي كعضو!\n\n"
                    "⚡ هذه الرسالة ستُحذف تلقائياً بعد 30 دقيقة"
                )
                # زر web_app لا يعمل في الجروبات: رابط التطبيق المصغّر الرئيسي بزر URL
                # عادي، ويُترك الزر كلياً إذا لم يمكن بناء رابط صالح
                group_markup = build_group_library_markup()
                if group_markup is not None:
                    welcome_text += (
                        "\n\n📱 <b>مكتبة الفيديوهات:</b>\n"
                        "اضغط الزر أدناه لفتح المكتبة داخل هذا الجروب."
                    )
                    sent_message = bot.send_message(
                        message.chat.id,
                        welcome_text,
                        parse_mode="HTML",
                        reply_markup=group_markup
                    )
                else:
                    sent_message = bot.send_message(
                        message.chat.id,
                        welcome_text,
                        parse_mode="HTML"
                    )
                
                # حذف الرسالة بعد 30 دقيقة
                def delete_message_later():
                    try:
                        import time
                        time.sleep(1800)  # 30 دقيقة
                        bot.delete_message(message.chat.id, sent_message.message_id)
                        logger.info(f"Deleted welcome message in group {message.chat.id}")
                    except Exception as e:
                        logger.error(f"Could not delete welcome message: {e}")
                
                # تشغيل في thread منفصل
                threading.Thread(target=delete_message_later, daemon=True).start()
                
                logger.info(f"Bot added to group {message.chat.id} ({message.chat.title})")
                
        except Exception as e:
            logger.error(f"Error in welcome_new_member: {e}", exc_info=True)
    
    @bot.message_handler(commands=['search_help'])
    def handle_search_help(message):
        """
        شرح مفصل لكيفية استخدام البحث.
        يعمل في الجروبات والمحادثات الخاصة.
        """
        try:
            bot_info = bot.get_me()
            is_group = message.chat.type in ['group', 'supergroup']
            bot_username = html_escape(str(getattr(bot_info, "username", "") or ""))
            
            help_text = (
                "🔍 <b>كيفية البحث عن الفيديوهات</b>\n\n"
                "📱 <b>في أي محادثة (جروب أو خاص):</b>\n"
                f"1️⃣ اكتب: <code>@{bot_username}</code>\n"
                "2️⃣ اكتب كلمة البحث بعدها\n"
                "3️⃣ اختر الفيديو من النتائج\n"
                "4️⃣ الفيديو يُرسل مباشرة!\n\n"
                "💡 <b>أمثلة عملية:</b>\n"
                f"• <code>@{bot_username} أكشن</code>\n"
                f"• <code>@{bot_username} كوميدي</code>\n"
                f"• <code>@{bot_username} رعب</code>\n"
                f"• <code>@{bot_username}</code> (بدون كلمة = الأكثر مشاهدة)\n\n"
                "✨ <b>ميزات البحث:</b>\n"
                "⭐ يبحث في العنوان والوصف\n"
                "📂 يبحث في التصنيفات\n"
                "🎯 مرتب حسب الشعبية والتقييم\n"
                "⚡ نتائج فورية أثناء الكتابة\n\n"
                "❓ <b>هل أحتاج إضافة البوت للجروب؟</b>\n"
                "لا! يمكنك البحث في أي محادثة بدون إضافتي كعضو"
            )
            
            sent_message = bot.send_message(
                message.chat.id,
                help_text,
                parse_mode="HTML"
            )
            
            # حذف الرسالة بعد 30 دقيقة في الجروبات فقط
            if is_group:
                def delete_help_later():
                    try:
                        import time
                        time.sleep(1800)  # 30 دقيقة
                        bot.delete_message(message.chat.id, sent_message.message_id)
                        # حذف رسالة الأمر أيضاً
                        bot.delete_message(message.chat.id, message.message_id)
                        logger.info(f"Deleted help message in group {message.chat.id}")
                    except Exception as e:
                        logger.error(f"Could not delete help message: {e}")
                
                threading.Thread(target=delete_help_later, daemon=True).start()
            
        except Exception as e:
            logger.error(f"Error in handle_search_help: {e}", exc_info=True)
