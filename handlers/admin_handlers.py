# handlers/admin_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import re
import time
from telebot.apihelper import ApiTelegramException # [إصلاح] لإصدار الخطأ 403

from db_manager import (
    add_category, get_all_user_ids, add_required_channel, remove_required_channel,
    get_required_channels, get_subscriber_count, get_bot_stats, get_popular_videos,
    delete_videos_by_ids, get_video_by_id, delete_bot_user, # [تعديل] يجب استيراد الدالة المضافة هنا
    delete_category_and_contents, move_videos_from_category, delete_category_by_id, 
    get_categories_tree, set_active_category_id, # [تعديل] استيراد الدوال المطلوبة للأدمن
    get_child_categories, move_video_to_category # [إصلاح] إضافة الدالتين المفقودتين
)
from .helpers import admin_steps, create_categories_keyboard, CALLBACK_DELIMITER

logger = logging.getLogger(__name__)

# --- Top-level functions for callbacks and next_step_handlers ---

def handle_rich_broadcast(message, bot):
    if check_cancel(message, bot): return
    user_ids = get_all_user_ids()
    sent_count, failed_count, removed_count = 0, 0, 0 # [تعديل] إضافة عداد الحذف
    bot.send_message(message.chat.id, f"بدء إرسال الرسالة إلى {len(user_ids)} مشترك...")
    
    for user_id in user_ids:
        try:
            # استخدام copy_message لتمرير الرسائل النصية الغنية (Rich messages)
            bot.copy_message(user_id, message.chat.id, message.message_id)
            sent_count += 1
        except ApiTelegramException as e:
            if 'bot was blocked by the user' in e.description:
                # [إصلاح] تنظيف قاعدة البيانات تلقائياً
                delete_bot_user(user_id)
                removed_count += 1
                logger.warning(f"Failed to send broadcast to {user_id}: Bot was blocked. User deleted.")
            else:
                failed_count += 1
                logger.warning(f"Failed to send broadcast to {user_id}: {e}")
        except Exception as e:
            failed_count += 1
            logger.error(f"Unexpected error broadcasting to {user_id}: {e}")
            
        time.sleep(0.1)
    
    bot.send_message(message.chat.id, 
                     f"✅ اكتمل البث!\n\n- رسائل ناجحة: {sent_count}\n- رسائل فاشلة (لم يتم إرسالها): {failed_count}\n- مشتركين محذوفين (لأنهم حظروا البوت): {removed_count}")


def handle_add_new_category(message, bot):
    if check_cancel(message, bot): return
    category_name = message.text.strip()
    step_data = admin_steps.pop(message.chat.id, {})
    parent_id = step_data.get("parent_id")
    success, result = add_category(category_name, parent_id=parent_id)
    if success:
        bot.reply_to(message, f"✅ تم إنشاء التصنيف الجديد بنجاح: \"{category_name}\".")
    else:
        # [إصلاح] يجب أن تكون رسالة الخطأ واضحة
        bot.reply_to(message, f"❌ خطأ في إنشاء التصنيف: {result[1] if isinstance(result, tuple) else result}")
        
def handle_add_channel_step1(message, bot):
    if check_cancel(message, bot): return
    channel_id = message.text.strip()
    admin_steps[message.chat.id] = {"channel_id": channel_id}
    msg = bot.send_message(message.chat.id, "الآن أرسل اسم القناة (مثال: قناة الأفلام). (أو /cancel)")
    bot.register_next_step_handler(msg, handle_add_channel_step2, bot)

def handle_add_channel_step2(message, bot):
    if check_cancel(message, bot): return
    channel_name = message.text.strip()
    channel_id = admin_steps.pop(message.chat.id, {}).get("channel_id")
    if not channel_id: return
    
    # [تعديل] استخدام دالة إضافة القناة
    if add_required_channel(channel_id, channel_name):
        bot.send_message(message.chat.id, f"✅ تم إضافة القناة \"{channel_name}\" (ID: `{channel_id}`).", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ حدث خطأ أثناء إضافة القناة. تأكد من أن المعرف صحيح.")

def handle_remove_channel_step(message, bot):
    if check_cancel(message, bot): return
    channel_id = message.text.strip()
    if remove_required_channel(channel_id):
        bot.send_message(message.chat.id, f"✅ تم إزالة القناة (ID: `{channel_id}`) من القنوات المطلوبة.", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ حدث خطأ أو القناة غير موجودة.")

def handle_list_channels(message, bot):
    channels = get_required_channels()
    if channels:
        response = "📋 *القنوات المطلوبة:*\n" + "\n".join([f"- {ch['channel_name']} (ID: `{ch['channel_id']}`)" for ch in channels])
        bot.send_message(message.chat.id, response, parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "لا توجد قنوات مطلوبة حالياً.")

# [تحسين] دعم حذف أكثر من فيديو
def handle_delete_by_ids_input(message, bot):
    if check_cancel(message, bot): return
    try:
        video_ids_str = re.split(r'[,\s\n]+', message.text.strip())
        video_ids = [int(num) for num in video_ids_str if num.isdigit()]
        if not video_ids:
            msg = bot.reply_to(message, "لم يتم إدخال أرقام صحيحة. حاول مرة أخرى أو أرسل /cancel.")
            bot.register_next_step_handler(msg, handle_delete_by_ids_input, bot)
            return
        deleted_count = delete_videos_by_ids(video_ids)
        bot.reply_to(message, f"✅ تم حذف {deleted_count} فيديو بنجاح من أصل {len(video_ids)} مطلوب.")
    except Exception as e:
        logger.error(f"Error in handle_delete_by_ids_input: {e}", exc_info=True)
        bot.reply_to(message, "حدث خطأ. تأكد من إدخال أرقام فقط مفصولة بمسافات أو فواصل.")

# [تحسين] دعم نقل أكثر من فيديو
def handle_move_by_id_input(message):
    """معالج إدخال أرقام الفيديوهات للنقل"""
    try:
        user_id = message.from_user.id
        text = message.text.strip()
        
        # استخراج أرقام الفيديوهات (دعم الفواصل والمسافات)
        import re
        video_ids = re.split(r'[,\s]+', text)
        video_ids = [vid.strip() for vid in video_ids if vid.strip().isdigit()]
        
        if not video_ids:
            bot.reply_to(message, "❌ لم يتم إدخال أرقام فيديو صحيحة.\nمثال: 123 أو 123, 456, 789")
            return
        
        # التحقق من وجود الفيديوهات
        valid_videos = []
        invalid_ids = []
        
        for vid in video_ids:
            video = get_video_by_id(int(vid))
            if video:
                valid_videos.append(video)
            else:
                invalid_ids.append(vid)
        
        if not valid_videos:
            bot.reply_to(message, f"❌ لم يتم العثور على أي فيديو من الأرقام المدخلة.")
            return
        
        # حفظ معلومات الفيديوهات في الحالة
        video_ids_str = ",".join([str(v['id']) for v in valid_videos])
        state_manager.set_state(user_id, 'awaiting_move_category', {
            'video_ids': video_ids_str,
            'videos_info': valid_videos
        })
        
        # عرض التصنيفات
        categories = get_all_categories()
        if not categories:
            bot.reply_to(message, "❌ لا توجد تصنيفات متاحة.")
            return
        
        keyboard = InlineKeyboardMarkup(row_width=2)
        for cat in categories:
            # لا نعرض التصنيفات الفرعية هنا، فقط الرئيسية
            if cat.get('parent_id') is None:
                keyboard.add(InlineKeyboardButton(
                    text=f"📁 {cat['name']}",
                    callback_data=f"movecat:{video_ids_str}:{cat['id']}"
                ))
        
        keyboard.add(InlineKeyboardButton("❌ إلغاء", callback_data="cancel_move"))
        
        # رسالة تفصيلية
        video_names = "\n".join([f"• {v['title']}" for v in valid_videos[:5]])
        if len(valid_videos) > 5:
            video_names += f"\n... و{len(valid_videos) - 5} فيديو آخر"
        
        msg = f"📋 **الفيديوهات المراد نقلها ({len(valid_videos)}):**\n{video_names}\n\n"
        
        if invalid_ids:
            msg += f"⚠️ أرقام غير موجودة: {', '.join(invalid_ids)}\n\n"
        
        msg += "اختر التصنيف الجديد:"
        
        bot.reply_to(message, msg, reply_markup=keyboard, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error in handle_move_by_id_input: {e}", exc_info=True)
        bot.reply_to(message, "❌ حدث خطأ غير متوقع.")

        # [جديد] دعم أكثر من رقم فيديو
        video_ids_str = re.split(r'[,\s\n]+', message.text.strip())
        video_ids = [int(num) for num in video_ids_str if num.isdigit()]
        
        if not video_ids:
            msg = bot.reply_to(message, "لم يتم إدخال أرقام صحيحة. حاول مرة أخرى أو أرسل /cancel.")
            bot.register_next_step_handler(msg, handle_move_by_id_input, bot)
            return
            
        # التحقق من وجود الفيديوهات
        valid_videos = []
        invalid_ids = []
        
        for video_id in video_ids:
            video = get_video_by_id(video_id)
            if video:
                valid_videos.append((video_id, video))
            else:
                invalid_ids.append(video_id)
        
        if not valid_videos:
            msg = bot.reply_to(message, f"لا توجد فيديوهات صحيحة بهذه الأرقام: {', '.join(map(str, invalid_ids))}\nحاول مرة أخرى أو أرسل /cancel.")
            bot.register_next_step_handler(msg, handle_move_by_id_input, bot)
            return
        
        # إنشاء لوحة التصنيفات
        all_categories = get_categories_tree()
        if not all_categories:
            bot.reply_to(message, "لا توجد تصنيفات لنقل الفيديوهات إليها.")
            return
            
        move_keyboard = InlineKeyboardMarkup(row_width=1)
        
        # إضافة التصنيفات الرئيسية
        for cat in all_categories:
            video_ids_str = ','.join(str(vid) for vid, _ in valid_videos)
            move_keyboard.add(InlineKeyboardButton(
                f"📁 {cat['name']}", 
                callback_data=f"admin::move_multiple_confirm::{video_ids_str}::{cat['id']}"
            ))
            
            # إضافة التصنيفات الفرعية
            child_cats = get_child_categories(cat['id'])
            for child in child_cats:
                move_keyboard.add(InlineKeyboardButton(
                    f"  └── {child['name']}", 
                    callback_data=f"admin::move_multiple_confirm::{video_ids_str}::{child['id']}"
                ))
        
        # رسالة التأكيد
        video_list = '\n'.join([f"- رقم {vid}: {video.get('caption', 'بدون عنوان')[:50]}..." for vid, video in valid_videos])
        if invalid_ids:
            video_list += f"\n\n❌ أرقام غير صحيحة: {', '.join(map(str, invalid_ids))}"
            
        bot.reply_to(message, 
            f"اختر التصنيف الجديد لنقل الفيديوهات التالية:\n\n{video_list}\n\n📁 اختر التصنيف:", 
            reply_markup=move_keyboard
        )
        
    except ValueError:
        msg = bot.reply_to(message, "الرجاء إدخال أرقام صحيحة فقط. حاول مرة أخرى أو أرسل /cancel.")
        bot.register_next_step_handler(msg, handle_move_by_id_input, bot)
    except Exception as e:
        logger.error(f"Error in handle_move_by_id_input: {e}", exc_info=True)
        bot.reply_to(message, "حدث خطأ غير متوقع.")

def check_cancel(message, bot):
    if message.text == "/cancel":
        if message.chat.id in admin_steps:
            del admin_steps[message.chat.id]
        bot.send_message(message.chat.id, "تم إلغاء العملية.")
        return True
    return False

# --- Handler Registration ---
def register(bot, admin_ids):

    def check_admin(func):
        def wrapper(message):
            if message.from_user.id in admin_ids:
                return func(message)
            else:
                bot.reply_to(message, "ليس لديك صلاحية الوصول إلى هذا الأمر.")
        return wrapper

    def generate_admin_panel():
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(InlineKeyboardButton("➕ إضافة تصنيف", callback_data="admin::add_new_cat"),
                     InlineKeyboardButton("🗑️ حذف تصنيف", callback_data="admin::delete_category_select"))
        keyboard.add(InlineKeyboardButton("➡️ نقل فيديو/فيديوهات بالرقم", callback_data="admin::move_video_by_id"),
                     InlineKeyboardButton("❌ حذف فيديوهات بالأرقام", callback_data="admin::delete_videos_by_ids"))
        keyboard.add(InlineKeyboardButton("🔘 تعيين التصنيف النشط", callback_data="admin::set_active"),
                     InlineKeyboardButton("🔄 تحديث بيانات الفيديوهات القديمة", callback_data="admin::update_metadata"))
        keyboard.add(InlineKeyboardButton("➕ إضافة قناة اشتراك", callback_data="admin::add_channel"),
                     InlineKeyboardButton("➖ إزالة قناة اشتراك", callback_data="admin::remove_channel"))
        keyboard.add(InlineKeyboardButton("📋 عرض القنوات", callback_data="admin::list_channels"))
        keyboard.add(InlineKeyboardButton("📢 بث رسالة", callback_data="admin::broadcast"),
                     InlineKeyboardButton("📊 الإحصائيات", callback_data="admin::stats"),
                     InlineKeyboardButton("👤 عدد المشتركين", callback_data="admin::sub_count"))
        return keyboard

    @bot.message_handler(commands=["admin"])
    @check_admin
    def admin_panel(message):
        bot.send_message(message.chat.id, "أهلاً بك في لوحة تحكم الآدمن. اختر أحد الخيارات:", reply_markup=generate_admin_panel())

    @bot.message_handler(commands=["cancel"])
    @check_admin
    def cancel_step(message):
        if message.chat.id in admin_steps:
            del admin_steps[message.chat.id]
            bot.send_message(message.chat.id, "✅ تم إلغاء العملية الحالية بنجاح.")
        else:
            bot.send_message(message.chat.id, "لا توجد عملية لإلغائها.")
