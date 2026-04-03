#!/usr/bin/env python3
# ==============================================================================
# ملف: migrate_database.py
# الوصف: سكربت لترحيل البيانات من الجداول القديمة وحذفها
# الاستخدام: python migrate_database.py
# ==============================================================================

import psycopg2
from psycopg2.extras import DictCursor
import os
from urllib.parse import urlparse
import logging

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# قراءة اتصال قاعدة البيانات
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print("❌ خطأ: يجب تعيين DATABASE_URL")
    print("   مثال: set DATABASE_URL=postgresql://user:password@host:port/database")
    exit(1)

result = urlparse(DATABASE_URL)
DB_CONFIG = {
    'user': result.username,
    'password': result.password,
    'host': result.hostname,
    'port': result.port,
    'dbname': result.path[1:]
}

# خريطة الجداول القديمة -> الجديدة
TABLE_MAPPINGS = {
    'videoarchive': {
        'new_table': 'video_archive',
        'column_mappings': {
            'messageid': 'message_id',
            'chatid': 'chat_id',
            'filename': 'file_name',
            'fileid': 'file_id',
            'categoryid': 'category_id',
            'viewcount': 'view_count',
            'uploaddate': 'upload_date',
            'groupingkey': 'grouping_key',
            # الأعمدة بنفس الاسم
            'id': 'id',
            'caption': 'caption',
            'metadata': 'metadata'
        }
    },
    'botusers': {
        'new_table': 'bot_users',
        'column_mappings': {
            'userid': 'user_id',
            'firstname': 'first_name',
            'joindate': 'join_date',
            'username': 'username'
        }
    },
    'userfavorites': {
        'new_table': 'user_favorites',
        'column_mappings': {
            'userid': 'user_id',
            'videoid': 'video_id',
            'dateadded': 'date_added',
            'id': 'id'
        }
    },
    'userhistory': {
        'new_table': 'user_history',
        'column_mappings': {
            'userid': 'user_id',
            'videoid': 'video_id',
            'lastwatched': 'last_watched',
            'id': 'id'
        }
    },
    'videoratings': {
        'new_table': 'video_ratings',
        'column_mappings': {
            'videoid': 'video_id',
            'userid': 'user_id',
            'rating': 'rating',
            'createdat': 'created_at',
            'id': 'id'
        }
    },
    'userstates': {
        'new_table': 'user_states',
        'column_mappings': {
            'userid': 'user_id',
            'state': 'state',
            'context': 'context',
            'lastupdate': 'last_update'
        }
    },
    'botsettings': {
        'new_table': 'bot_settings',
        'column_mappings': {
            'settingkey': 'setting_key',
            'settingvalue': 'setting_value'
        }
    },
    'requiredchannels': {
        'new_table': 'required_channels',
        'column_mappings': {
            'channelid': 'channel_id',
            'channelname': 'channel_name',
            'id': 'id'
        }
    }
}


def get_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    return psycopg2.connect(**DB_CONFIG)


def table_exists(cursor, table_name):
    """التحقق من وجود جدول"""
    cursor.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = %s
        )
    """, (table_name,))
    return cursor.fetchone()[0]


def get_table_count(cursor, table_name):
    """جلب عدد السجلات في جدول"""
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    return cursor.fetchone()[0]


def analyze_tables(cursor):
    """تحليل الجداول الموجودة"""
    print("\n" + "=" * 60)
    print("📊 تحليل الجداول")
    print("=" * 60)
    
    results = {}
    
    for old_table, mapping in TABLE_MAPPINGS.items():
        new_table = mapping['new_table']
        
        old_exists = table_exists(cursor, old_table)
        new_exists = table_exists(cursor, new_table)
        
        old_count = get_table_count(cursor, old_table) if old_exists else 0
        new_count = get_table_count(cursor, new_table) if new_exists else 0
        
        results[old_table] = {
            'old_exists': old_exists,
            'new_exists': new_exists,
            'old_count': old_count,
            'new_count': new_count,
            'new_table': new_table
        }
        
        status = "✅" if not old_exists else ("⚠️" if old_count > 0 else "🔵")
        print(f"\n{status} {old_table} -> {new_table}")
        print(f"   القديم: {'موجود' if old_exists else 'غير موجود'} ({old_count} سجل)")
        print(f"   الجديد: {'موجود' if new_exists else 'غير موجود'} ({new_count} سجل)")
        
        if old_exists and old_count > 0 and new_count >= old_count:
            print(f"   ℹ️  البيانات موجودة بالفعل في الجدول الجديد")
    
    return results


def migrate_table(cursor, old_table, mapping):
    """ترحيل البيانات من جدول قديم لجديد"""
    new_table = mapping['new_table']
    column_mappings = mapping['column_mappings']
    
    # جلب أعمدة الجدول القديم الموجودة فعلياً
    cursor.execute("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = %s
    """, (old_table,))
    existing_old_columns = [row[0] for row in cursor.fetchall()]
    
    # جلب أعمدة الجدول الجديد الموجودة فعلياً  
    cursor.execute("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = %s
    """, (new_table,))
    existing_new_columns = [row[0] for row in cursor.fetchall()]
    
    # بناء قائمة الأعمدة للنقل
    old_cols = []
    new_cols = []
    
    for old_col, new_col in column_mappings.items():
        if old_col in existing_old_columns and new_col in existing_new_columns:
            old_cols.append(old_col)
            new_cols.append(new_col)
    
    if not old_cols:
        logger.warning(f"  لا توجد أعمدة متطابقة للنقل من {old_table}")
        return 0
    
    # تحديد عمود المفتاح الأساسي للتعارض
    pk_column = 'user_id' if 'user_id' in new_cols else ('setting_key' if 'setting_key' in new_cols else 'id')
    
    # بناء جملة INSERT
    old_cols_str = ", ".join(old_cols)
    new_cols_str = ", ".join(new_cols)
    
    # استخدام ON CONFLICT DO NOTHING لتجنب التكرار
    if pk_column in new_cols:
        migrate_query = f"""
            INSERT INTO {new_table} ({new_cols_str})
            SELECT {old_cols_str} FROM {old_table}
            ON CONFLICT DO NOTHING
        """
    else:
        migrate_query = f"""
            INSERT INTO {new_table} ({new_cols_str})
            SELECT {old_cols_str} FROM {old_table}
            ON CONFLICT DO NOTHING
        """
    
    try:
        cursor.execute(migrate_query)
        migrated = cursor.rowcount
        logger.info(f"  ✅ تم ترحيل {migrated} سجل من {old_table} إلى {new_table}")
        return migrated
    except Exception as e:
        logger.error(f"  ❌ خطأ في ترحيل {old_table}: {e}")
        return 0


def drop_old_tables(cursor, tables_to_drop):
    """حذف الجداول القديمة"""
    print("\n" + "=" * 60)
    print("🗑️ حذف الجداول القديمة")
    print("=" * 60)
    
    for table in tables_to_drop:
        try:
            # حذف الـ foreign key constraints أولاً
            cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            logger.info(f"  ✅ تم حذف {table}")
        except Exception as e:
            logger.error(f"  ❌ خطأ في حذف {table}: {e}")


def cleanup_sequences(cursor):
    """تنظيف الـ sequences المعزولة"""
    print("\n" + "=" * 60)
    print("🧹 تنظيف الـ Sequences")
    print("=" * 60)
    
    # البحث عن sequences مرتبطة بالجداول القديمة
    old_sequences = [
        'videoarchive_id_seq',
        'userfavorites_id_seq', 
        'userhistory_id_seq',
        'videoratings_id_seq',
        'requiredchannels_id_seq'
    ]
    
    for seq in old_sequences:
        try:
            cursor.execute(f"DROP SEQUENCE IF EXISTS {seq} CASCADE")
            logger.info(f"  ✅ تم حذف {seq}")
        except Exception as e:
            logger.warning(f"  ⚠️ لم يتم العثور على {seq}")


def main():
    """الدالة الرئيسية"""
    print("\n" + "=" * 60)
    print("🔄 بدء عملية ترحيل قاعدة البيانات")
    print("=" * 60)
    print(f"📍 الاتصال بـ: {DB_CONFIG['host']}/{DB_CONFIG['dbname']}")
    
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        
        # 1. تحليل الجداول
        analysis = analyze_tables(cursor)
        
        # 2. سؤال المستخدم قبل المتابعة
        tables_to_migrate = []
        tables_to_drop = []
        
        for old_table, info in analysis.items():
            if info['old_exists']:
                if info['old_count'] > 0:
                    tables_to_migrate.append(old_table)
                tables_to_drop.append(old_table)
        
        if not tables_to_drop:
            print("\n✅ لا توجد جداول قديمة للحذف. قاعدة البيانات نظيفة!")
            return
        
        print("\n" + "=" * 60)
        print("⚠️ الجداول التالية سيتم التعامل معها:")
        print("=" * 60)
        
        if tables_to_migrate:
            print(f"\n📦 سيتم ترحيل البيانات من: {', '.join(tables_to_migrate)}")
        print(f"🗑️ سيتم حذف: {', '.join(tables_to_drop)}")
        
        confirm = input("\n❓ هل تريد المتابعة؟ (نعم/yes/y للمتابعة): ").strip().lower()
        
        if confirm not in ['نعم', 'yes', 'y']:
            print("❌ تم الإلغاء")
            return
        
        # 3. ترحيل البيانات
        if tables_to_migrate:
            print("\n" + "=" * 60)
            print("📦 ترحيل البيانات")
            print("=" * 60)
            
            for old_table in tables_to_migrate:
                migrate_table(cursor, old_table, TABLE_MAPPINGS[old_table])
            
            conn.commit()
        
        # 4. حذف الجداول القديمة
        drop_old_tables(cursor, tables_to_drop)
        conn.commit()
        
        # 5. تنظيف الـ sequences
        cleanup_sequences(cursor)
        conn.commit()
        
        # 6. التحقق النهائي
        print("\n" + "=" * 60)
        print("✅ اكتملت عملية الترحيل!")
        print("=" * 60)
        
        # عرض الإحصائيات النهائية
        cursor.execute("SELECT COUNT(*) FROM video_archive")
        videos = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM bot_users")
        users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM categories")
        categories = cursor.fetchone()[0]
        
        print(f"\n📊 الإحصائيات النهائية:")
        print(f"   🎬 الفيديوهات: {videos}")
        print(f"   👥 المستخدمون: {users}")
        print(f"   📂 التصنيفات: {categories}")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ خطأ عام: {e}")
        raise


if __name__ == "__main__":
    main()
