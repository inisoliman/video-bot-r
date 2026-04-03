#!/usr/bin/env python3
"""
سكريبت لاستخراج thumbnails من الفيديوهات الموجودة في القناة.

هذا الحل يعمل لأن:
1. الفيديوهات موجودة في القناة مع صورها المصغرة
2. نجلب الرسائل مباشرة من القناة
3. نستخرج thumbnail_file_id من message.video.thumb
4. نحفظه في قاعدة البيانات
"""

import os
import sys
import time
import logging
import telebot

# إضافة المسار الحالي للـ path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_manager as db

# إعداد logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# قراءة المتغيرات من البيئة
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN not found in environment variables")
    sys.exit(1)

if not CHANNEL_ID:
    logger.error("❌ CHANNEL_ID not found in environment variables")
    sys.exit(1)

# إنشاء البوت
bot = telebot.TeleBot(BOT_TOKEN)

def extract_thumbnails_from_channel():
    """
    استخراج thumbnails من القناة للفيديوهات التي لا تملك thumbnail_file_id.
    """
    try:
        logger.info("🚀 Starting thumbnail extraction from channel...")
        
        # جلب الفيديوهات بدون thumbnail
        videos = db.get_videos_without_thumbnail(limit=1000)  # جلب حتى 1000 فيديو
        
        if not videos:
            logger.info("✅ All videos already have thumbnails!")
            return 0
        
        logger.info(f"📊 Found {len(videos)} videos without thumbnails")
        
        total_updated = 0
        failed_count = 0
        
        for i, video in enumerate(videos, 1):
            try:
                logger.info(f"Processing {i}/{len(videos)}: Video ID {video['id']}, Message ID {video['message_id']}")
                
                # التحقق من وجود message_id و chat_id
                if not video.get('message_id') or not video.get('chat_id'):
                    logger.warning(f"⚠️ Video {video['id']} missing message_id or chat_id, skipping")
                    failed_count += 1
                    continue
                
                # جلب الرسالة من القناة
                try:
                    message = bot.forward_message(
                        chat_id=video['chat_id'],  # نفس القناة
                        from_chat_id=video['chat_id'],
                        message_id=video['message_id']
                    )
                    
                    # حذف الرسالة المعاد توجيهها فوراً
                    try:
                        bot.delete_message(video['chat_id'], message.message_id)
                    except:
                        pass
                    
                except Exception as e:
                    # إذا فشل forward، نجرب get message مباشرة
                    logger.info(f"Forward failed, trying direct fetch for video {video['id']}")
                    # للأسف، لا يوجد get_message في Telegram Bot API
                    # نتخطى هذا الفيديو
                    logger.warning(f"⚠️ Cannot fetch message {video['message_id']} for video {video['id']}")
                    failed_count += 1
                    continue
                
                # استخراج thumbnail
                if message.video and message.video.thumb:
                    thumbnail_id = message.video.thumb.file_id
                    
                    # حفظ في قاعدة البيانات
                    if db.update_video_thumbnail(video['id'], thumbnail_id):
                        total_updated += 1
                        logger.info(f"✅ Updated video {video['id']} ({total_updated}/{len(videos)})")
                    else:
                        failed_count += 1
                        logger.error(f"❌ Failed to update database for video {video['id']}")
                else:
                    logger.warning(f"⚠️ No thumbnail found in message for video {video['id']}")
                    failed_count += 1
                
                # تأخير بسيط لتجنب rate limiting
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"❌ Error processing video {video['id']}: {e}")
                failed_count += 1
                continue
        
        logger.info(f"\n📊 Summary:")
        logger.info(f"  ✅ Success: {total_updated}")
        logger.info(f"  ❌ Failed: {failed_count}")
        logger.info(f"  📝 Total: {len(videos)}")
        
        return total_updated
        
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        return 0

def main():
    """الدالة الرئيسية"""
    try:
        logger.info("="*50)
        logger.info("Channel Thumbnail Extraction Script")
        logger.info("="*50)
        
        # التحقق من الاتصال بقاعدة البيانات
        logger.info("🔌 Connecting to database...")
        db.ensure_schema()
        logger.info("✅ Database connected")
        
        # استخراج thumbnails
        total = extract_thumbnails_from_channel()
        
        logger.info(f"\n🎉 Completed! Total videos updated: {total}")
        
    except KeyboardInterrupt:
        logger.info("\n⚠️ Script interrupted by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
