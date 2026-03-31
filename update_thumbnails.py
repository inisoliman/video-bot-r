#!/usr/bin/env python3
"""
سكريبت لاستخراج وحفظ thumbnails للفيديوهات القديمة.

هذا السكريبت يُنفذ مرة واحدة فقط بعد إضافة ميزة inline query.
يقوم باستخراج thumbnail_file_id من الفيديوهات وحفظه في قاعدة البيانات.
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
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN not found in environment variables")
    sys.exit(1)

if not ADMIN_IDS:
    logger.error("❌ ADMIN_IDS not found in environment variables")
    sys.exit(1)

# إنشاء البوت
bot = telebot.TeleBot(BOT_TOKEN)

def extract_thumbnail_for_video(video):
    """
    استخراج thumbnail لفيديو واحد.
    
    Args:
        video: dict مع بيانات الفيديو
    
    Returns:
        thumbnail_file_id أو None
    """
    try:
        # إرسال الفيديو للأدمن الأول
        admin_id = ADMIN_IDS[0]
        
        logger.info(f"Extracting thumbnail for video {video['id']}: {video.get('caption', video.get('file_name'))}")
        
        # إرسال الفيديو
        sent_message = bot.send_video(
            chat_id=admin_id,
            video=video['file_id'],
            caption=f"🔄 استخراج thumbnail للفيديو #{video['id']}"
        )
        
        # استخراج thumbnail_file_id
        if sent_message.video and sent_message.video.thumb:
            thumbnail_id = sent_message.video.thumb.file_id
            logger.info(f"✅ Thumbnail extracted: {thumbnail_id[:20]}...")
            
            # حذف الرسالة
            try:
                bot.delete_message(admin_id, sent_message.message_id)
            except Exception as e:
                logger.warning(f"Could not delete message: {e}")
            
            return thumbnail_id
        else:
            logger.warning(f"⚠️ No thumbnail found for video {video['id']}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error extracting thumbnail for video {video['id']}: {e}")
        return None

def update_thumbnails_batch(batch_size=10, delay=2):
    """
    تحديث thumbnails لدفعة من الفيديوهات.
    
    Args:
        batch_size: عدد الفيديوهات في كل دفعة
        delay: التأخير بين كل فيديو (بالثواني)
    """
    logger.info("🚀 Starting thumbnail extraction...")
    
    # جلب الفيديوهات بدون thumbnail
    videos = db.get_videos_without_thumbnail(limit=batch_size)
    
    if not videos:
        logger.info("✅ All videos have thumbnails!")
        return 0
    
    logger.info(f"📊 Found {len(videos)} videos without thumbnails")
    
    success_count = 0
    failed_count = 0
    
    for i, video in enumerate(videos, 1):
        logger.info(f"Processing {i}/{len(videos)}...")
        
        # استخراج thumbnail
        thumbnail_id = extract_thumbnail_for_video(video)
        
        if thumbnail_id:
            # حفظ في قاعدة البيانات
            if db.update_video_thumbnail(video['id'], thumbnail_id):
                success_count += 1
                logger.info(f"✅ Updated video {video['id']}")
            else:
                failed_count += 1
                logger.error(f"❌ Failed to update database for video {video['id']}")
        else:
            failed_count += 1
        
        # تأخير لتجنب rate limiting
        if i < len(videos):
            time.sleep(delay)
    
    logger.info(f"\n📊 Summary:")
    logger.info(f"  ✅ Success: {success_count}")
    logger.info(f"  ❌ Failed: {failed_count}")
    logger.info(f"  📝 Total: {len(videos)}")
    
    return success_count

def main():
    """الدالة الرئيسية"""
    try:
        logger.info("="*50)
        logger.info("Thumbnail Extraction Script")
        logger.info("="*50)
        
        # التحقق من الاتصال بقاعدة البيانات
        logger.info("🔌 Connecting to database...")
        db.ensure_schema()
        logger.info("✅ Database connected")
        
        # تحديث thumbnails
        total_updated = 0
        max_iterations = 10  # حد أقصى 10 دفعات (200 فيديو)
        
        for iteration in range(max_iterations):
            logger.info(f"\n🔄 Batch {iteration + 1}/{max_iterations}")
            
            updated = update_thumbnails_batch(batch_size=20, delay=2)
            total_updated += updated
            
            if updated == 0:
                logger.info("✅ No more videos to process")
                break
            
            # تأخير بين الدفعات
            if iteration < max_iterations - 1:
                logger.info("⏳ Waiting 10 seconds before next batch...")
                time.sleep(10)
        
        logger.info(f"\n🎉 Completed! Total videos updated: {total_updated}")
        
    except KeyboardInterrupt:
        logger.info("\n⚠️ Script interrupted by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
