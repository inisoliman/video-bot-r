#!/usr/bin/env python3
"""
سكريبت لفحص وتنظيف file_id غير الصالحة في قاعدة البيانات
"""

import os
import sys
import logging
from telebot import TeleBot

# إضافة المسار الحالي للـ path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_manager as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN not set")
    sys.exit(1)

bot = TeleBot(BOT_TOKEN)

def check_file_ids():
    """فحص جميع file_id في قاعدة البيانات"""
    
    logger.info("🔍 Fetching all videos with file_id...")
    
    sql = """
        SELECT id, file_id, caption, file_name
        FROM video_archive
        WHERE file_id IS NOT NULL
        ORDER BY id ASC
    """
    
    videos = db.execute_query(sql, fetch="all")
    
    if not videos:
        logger.info("✅ No videos found")
        return
    
    logger.info(f"📊 Found {len(videos)} videos with file_id")
    
    invalid_ids = []
    short_ids = []
    suspicious_ids = []
    
    for video in videos:
        file_id = video['file_id']
        video_id = video['id']
        
        # فحص الطول
        if len(file_id) < 20:
            short_ids.append({
                'id': video_id,
                'file_id': file_id,
                'length': len(file_id),
                'caption': video.get('caption', '')[:50]
            })
            continue
        
        # فحص البادئة (prefix)
        # file_id للفيديوهات عادة يبدأ بـ: BAA, CgAC, DQA, وغيرها
        # file_id للصور يبدأ بـ: AgAC
        # file_id للمستندات يبدأ بـ: BQA
        if file_id.startswith('AgAC'):
            suspicious_ids.append({
                'id': video_id,
                'file_id': file_id[:20] + '...',
                'type': 'صورة (AgAC)',
                'caption': video.get('caption', '')[:50]
            })
        elif file_id.startswith('BQA'):
            suspicious_ids.append({
                'id': video_id,
                'file_id': file_id[:20] + '...',
                'type': 'مستند (BQA)',
                'caption': video.get('caption', '')[:50]
            })
    
    # طباعة النتائج
    print("\n" + "="*80)
    print("📊 نتائج الفحص")
    print("="*80)
    
    print(f"\n✅ إجمالي الفيديوهات: {len(videos)}")
    print(f"⚠️ file_id قصيرة (< 20 حرف): {len(short_ids)}")
    print(f"🚨 file_id مشبوهة (ليست فيديوهات): {len(suspicious_ids)}")
    
    if short_ids:
        print("\n" + "-"*80)
        print("⚠️ file_id القصيرة:")
        print("-"*80)
        for item in short_ids[:10]:  # أول 10 فقط
            print(f"  ID: {item['id']}, Length: {item['length']}, Caption: {item['caption']}")
        if len(short_ids) > 10:
            print(f"  ... و {len(short_ids) - 10} أخرى")
    
    if suspicious_ids:
        print("\n" + "-"*80)
        print("🚨 file_id المشبوهة:")
        print("-"*80)
        for item in suspicious_ids[:10]:  # أول 10 فقط
            print(f"  ID: {item['id']}, Type: {item['type']}, Caption: {item['caption']}")
        if len(suspicious_ids) > 10:
            print(f"  ... و {len(suspicious_ids) - 10} أخرى")
    
    print("\n" + "="*80)
    
    # حفظ القائمة الكاملة في ملف
    if short_ids or suspicious_ids:
        with open('invalid_file_ids.txt', 'w', encoding='utf-8') as f:
            f.write("file_id القصيرة:\n")
            f.write("="*80 + "\n")
            for item in short_ids:
                f.write(f"ID: {item['id']}, Length: {item['length']}, Caption: {item['caption']}\n")
            
            f.write("\n\nfile_id المشبوهة:\n")
            f.write("="*80 + "\n")
            for item in suspicious_ids:
                f.write(f"ID: {item['id']}, Type: {item['type']}, Caption: {item['caption']}\n")
        
        logger.info("📄 تم حفظ القائمة الكاملة في: invalid_file_ids.txt")

def clean_invalid_file_ids(dry_run=True):
    """
    تنظيف file_id غير الصالحة
    
    Args:
        dry_run: إذا كان True، فقط عرض ما سيتم حذفه بدون تنفيذ
    """
    
    logger.info("🧹 Starting cleanup...")
    
    # حذف file_id القصيرة
    sql_short = """
        UPDATE video_archive
        SET file_id = NULL, thumbnail_file_id = NULL
        WHERE file_id IS NOT NULL AND LENGTH(file_id) < 20
    """
    
    # حذف file_id للصور والمستندات
    sql_suspicious = """
        UPDATE video_archive
        SET file_id = NULL, thumbnail_file_id = NULL
        WHERE file_id IS NOT NULL 
          AND (file_id LIKE 'AgAC%' OR file_id LIKE 'BQA%')
    """
    
    if dry_run:
        logger.info("🔍 DRY RUN - لن يتم تنفيذ أي تغييرات")
        
        # عد السجلات التي ستتأثر
        count_short = db.execute_query(
            "SELECT COUNT(*) as count FROM video_archive WHERE file_id IS NOT NULL AND LENGTH(file_id) < 20",
            fetch="one"
        )
        count_suspicious = db.execute_query(
            "SELECT COUNT(*) as count FROM video_archive WHERE file_id IS NOT NULL AND (file_id LIKE 'AgAC%' OR file_id LIKE 'BQA%')",
            fetch="one"
        )
        
        logger.info(f"  سيتم تنظيف {count_short['count']} سجل (file_id قصيرة)")
        logger.info(f"  سيتم تنظيف {count_suspicious['count']} سجل (file_id مشبوهة)")
        logger.info("\n💡 لتنفيذ التنظيف الفعلي، شغّل السكريبت مع --execute")
    else:
        logger.info("⚠️ تنفيذ التنظيف الفعلي...")
        
        # تنفيذ التنظيف
        db.execute_query(sql_short, commit=True)
        db.execute_query(sql_suspicious, commit=True)
        
        logger.info("✅ تم التنظيف بنجاح!")
        logger.info("💡 يمكنك الآن تشغيل /admin/fix_videos_professional لإعادة جلب file_id الصحيحة")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='فحص وتنظيف file_id غير الصالحة')
    parser.add_argument('--check', action='store_true', help='فحص file_id فقط')
    parser.add_argument('--clean', action='store_true', help='تنظيف file_id غير الصالحة (dry run)')
    parser.add_argument('--execute', action='store_true', help='تنفيذ التنظيف الفعلي')
    
    args = parser.parse_args()
    
    if args.check or (not args.clean and not args.execute):
        check_file_ids()
    
    if args.clean or args.execute:
        clean_invalid_file_ids(dry_run=not args.execute)
