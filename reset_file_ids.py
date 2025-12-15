#!/usr/bin/env python3
"""
سكريبت لإعادة تعيين file_id من القناة الأصلية
هذا السكريبت يحذف file_id الحالية (التي قد تكون من forward_message)
ويعيد جلبها من الرسائل الأصلية في القناة
"""

import os
import sys
import logging

# إضافة المسار الحالي للـ path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_manager as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def reset_file_ids():
    """حذف جميع file_id لإعادة جلبها من القناة"""
    
    logger.info("🔄 Resetting all file_id to NULL...")
    
    sql = """
        UPDATE video_archive
        SET file_id = NULL, thumbnail_file_id = NULL
        WHERE file_id IS NOT NULL
    """
    
    try:
        db.execute_query(sql, commit=True)
        logger.info("✅ All file_id reset successfully!")
        logger.info("💡 الآن شغّل /admin/fix_videos_professional لإعادة جلب file_id الأصلية")
    except Exception as e:
        logger.error(f"❌ Error resetting file_id: {e}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='إعادة تعيين file_id')
    parser.add_argument('--confirm', action='store_true', help='تأكيد إعادة التعيين')
    
    args = parser.parse_args()
    
    if not args.confirm:
        print("⚠️ هذا السكريبت سيحذف جميع file_id من قاعدة البيانات!")
        print("💡 للتأكيد، شغّل السكريبت مع --confirm")
        print("\nمثال:")
        print("  python reset_file_ids.py --confirm")
    else:
        reset_file_ids()
