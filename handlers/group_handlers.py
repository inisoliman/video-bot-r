# handlers/group_handlers.py

import telebot
from telebot import types
import logging
import threading

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
                # رسالة الترحيب
                welcome_text = (
                    "👋 *مرحباً! تم إضافتي للجروب*\n\n"
                    "🔍 *للبحث السريع عن الفيديوهات:*\n"
                    f"اكتب: `@{bot_info.username} كلمة البحث`\n\n"
                    "📝 *مثال:*\n"
                    f"`@{bot_info.username} أكشن`\n"
                    f"`@{bot_info.username} كوميدي`\n\n"
                    "💡 *نصيحة:* يمكنك البحث في أي محادثة بدون إضافتي كعضو!\n\n"
                    "⚡ هذه الرسالة ستُحذف تلقائياً بعد 30 دقيقة"
                )
                
                sent_message = bot.send_message(
                    message.chat.id,
                    welcome_text,
                    parse_mode="Markdown"
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
            
            help_text = (
                "🔍 *كيفية البحث عن الفيديوهات*\n\n"
                "📱 *في أي محادثة (جروب أو خاص):*\n"
                f"1️⃣ اكتب: `@{bot_info.username}`\n"
                "2️⃣ اكتب كلمة البحث بعدها\n"
                "3️⃣ اختر الفيديو من النتائج\n"
                "4️⃣ الفيديو يُرسل مباشرة!\n\n"
                "💡 *أمثلة عملية:*\n"
                f"• `@{bot_info.username} أكشن`\n"
                f"• `@{bot_info.username} كوميدي`\n"
                f"• `@{bot_info.username} رعب`\n"
                f"• `@{bot_info.username}` (بدون كلمة = الأكثر مشاهدة)\n\n"
                "✨ *ميزات البحث:*\n"
                "⭐ يبحث في العنوان والوصف\n"
                "📂 يبحث في التصنيفات\n"
                "🎯 مرتب حسب الشعبية والتقييم\n"
                "⚡ نتائج فورية أثناء الكتابة\n\n"
                "❓ *هل أحتاج إضافة البوت للجروب؟*\n"
                "لا! يمكنك البحث في أي محادثة بدون إضافتي كعضو"
            )
            
            sent_message = bot.send_message(
                message.chat.id,
                help_text,
                parse_mode="Markdown"
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
