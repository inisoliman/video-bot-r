#!/usr/bin/env python3
# ==============================================================================
# ملف: db_audit.py 
# الوصف: أداة فحص وتوثيق بنية قاعدة البيانات الحالية
# الاستخدام: python db_audit.py > database_structure_report.txt
# ==============================================================================

import os
import psycopg2
from psycopg2.extras import DictCursor
from urllib.parse import urlparse
import json
from datetime import datetime
import logging

# إعداد نظام التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_db_config():
    """الحصول على إعدادات قاعدة البيانات من متغيرات البيئة"""
    try:
        DATABASE_URL = os.environ.get('DATABASE_URL')
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL not set.")
        
        result = urlparse(DATABASE_URL)
        return {
            'user': result.username,
            'password': result.password,
            'host': result.hostname,
            'port': result.port,
            'dbname': result.path[1:]
        }
    except Exception as e:
        logger.error(f"Could not parse DATABASE_URL: {e}")
        return None

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    config = get_db_config()
    if not config:
        return None
    
    try:
        return psycopg2.connect(**config)
    except psycopg2.OperationalError as e:
        logger.error(f"Database connection failed: {e}")
        return None

def audit_database_structure():
    """فحص شامل لبنية قاعدة البيانات الحالية"""
    conn = get_db_connection()
    if not conn:
        print("❌ فشل الاتصال بقاعدة البيانات")
        return None
    
    audit_report = {
        'timestamp': datetime.now().isoformat(),
        'database_info': {},
        'tables': {},
        'indexes': {},
        'constraints': {},
        'data_samples': {}
    }
    
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            # معلومات عامة عن قاعدة البيانات
            cur.execute("SELECT version()")
            audit_report['database_info']['version'] = cur.fetchone()[0]
            
            cur.execute("SELECT current_database()")
            audit_report['database_info']['database_name'] = cur.fetchone()[0]
            
            # جلب جميع الجداول
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            
            tables = [row[0] for row in cur.fetchall()]
            print(f"🔍 تم العثور على {len(tables)} جدول:")
            
            for table_name in tables:
                print(f"   📋 {table_name}")
                audit_report['tables'][table_name] = audit_table_structure(cur, table_name)
                audit_report['indexes'][table_name] = get_table_indexes(cur, table_name)
                audit_report['constraints'][table_name] = get_table_constraints(cur, table_name)
                audit_report['data_samples'][table_name] = get_sample_data(cur, table_name)
            
            # فحص العلاقات بين الجداول
            audit_report['foreign_keys'] = get_foreign_key_relationships(cur)
            
    except Exception as e:
        logger.error(f"خطأ أثناء فحص قاعدة البيانات: {e}")
        return None
    finally:
        conn.close()
    
    return audit_report

def audit_table_structure(cur, table_name):
    """فحص بنية جدول معين"""
    cur.execute("""
        SELECT 
            column_name,
            data_type,
            is_nullable,
            column_default,
            character_maximum_length,
            numeric_precision,
            numeric_scale
        FROM information_schema.columns 
        WHERE table_name = %s
        ORDER BY ordinal_position
    """, (table_name,))
    
    columns = {}
    for row in cur.fetchall():
        col_info = {
            'data_type': row['data_type'],
            'nullable': row['is_nullable'] == 'YES',
            'default': row['column_default'],
            'max_length': row['character_maximum_length'],
            'precision': row['numeric_precision'],
            'scale': row['numeric_scale']
        }
        columns[row['column_name']] = col_info
    
    # حساب عدد السجلات
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        row_count = cur.fetchone()[0]
    except:
        row_count = 0
    
    return {
        'columns': columns,
        'row_count': row_count
    }

def get_table_indexes(cur, table_name):
    """جلب الفهارس الخاصة بجدول معين"""
    cur.execute("""
        SELECT 
            indexname,
            indexdef
        FROM pg_indexes 
        WHERE tablename = %s
    """, (table_name,))
    
    indexes = {}
    for row in cur.fetchall():
        indexes[row['indexname']] = row['indexdef']
    
    return indexes

def get_table_constraints(cur, table_name):
    """جلب القيود الخاصة بجدول معين"""
    cur.execute("""
        SELECT 
            constraint_name,
            constraint_type
        FROM information_schema.table_constraints 
        WHERE table_name = %s
    """, (table_name,))
    
    constraints = {}
    for row in cur.fetchall():
        constraints[row['constraint_name']] = row['constraint_type']
    
    return constraints

def get_sample_data(cur, table_name, limit=3):
    """جلب عينة من البيانات (للفهم والتحليل)"""
    try:
        cur.execute(f"SELECT * FROM {table_name} LIMIT %s", (limit,))
        rows = cur.fetchall()
        
        # تحويل البيانات إلى تنسيق قابل للقراءة
        sample_data = []
        for row in rows:
            row_dict = {}
            for key, value in row.items():
                # تحويل البيانات الحساسة أو الكبيرة
                if isinstance(value, (int, float, bool)) or value is None:
                    row_dict[key] = value
                elif len(str(value)) > 100:
                    row_dict[key] = f"[DATA_LENGTH:{len(str(value))}]"
                else:
                    row_dict[key] = str(value)[:50] + "..." if len(str(value)) > 50 else str(value)
            sample_data.append(row_dict)
        
        return sample_data
    except Exception as e:
        return f"Error sampling data: {str(e)}"

def get_foreign_key_relationships(cur):
    """جلب العلاقات بين الجداول"""
    cur.execute("""
        SELECT
            tc.table_name,
            kcu.column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
          AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name
          AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
    """)
    
    foreign_keys = []
    for row in cur.fetchall():
        foreign_keys.append({
            'table': row['table_name'],
            'column': row['column_name'],
            'references_table': row['foreign_table_name'],
            'references_column': row['foreign_column_name']
        })
    
    return foreign_keys

def print_detailed_report(audit_report):
    """طباعة تقرير مفصل عن بنية قاعدة البيانات"""
    print("\n" + "="*80)
    print("📊 تقرير شامل عن بنية قاعدة البيانات")
    print("="*80)
    
    print(f"\n🕐 وقت الفحص: {audit_report['timestamp']}")
    print(f"🗄️  قاعدة البيانات: {audit_report['database_info']['database_name']}")
    print(f"🔧 الإصدار: {audit_report['database_info']['version']}")
    
    print(f"\n📋 إجمالي الجداول: {len(audit_report['tables'])}")
    
    # تفاصيل كل جدول
    for table_name, table_info in audit_report['tables'].items():
        print(f"\n" + "-"*60)
        print(f"📌 جدول: {table_name}")
        print(f"📊 عدد السجلات: {table_info['row_count']:,}")
        print(f"🏛️  عدد الأعمدة: {len(table_info['columns'])}")
        
        # تفاصيل الأعمدة
        print("\n   الأعمدة:")
        for col_name, col_info in table_info['columns'].items():
            nullable = "NULL" if col_info['nullable'] else "NOT NULL"
            default = f" DEFAULT {col_info['default']}" if col_info['default'] else ""
            print(f"   • {col_name}: {col_info['data_type']} {nullable}{default}")
        
        # الفهارس
        if audit_report['indexes'][table_name]:
            print("\n   الفهارس:")
            for idx_name, idx_def in audit_report['indexes'][table_name].items():
                print(f"   • {idx_name}")
        
        # القيود
        if audit_report['constraints'][table_name]:
            print("\n   القيود:")
            for const_name, const_type in audit_report['constraints'][table_name].items():
                print(f"   • {const_name}: {const_type}")
        
        # عينة من البيانات (إذا كانت متوفرة)
        if audit_report['data_samples'][table_name] and isinstance(audit_report['data_samples'][table_name], list):
            print("\n   عينة من البيانات:")
            for i, sample in enumerate(audit_report['data_samples'][table_name][:2], 1):
                print(f"   {i}. {sample}")
    
    # العلاقات بين الجداول
    if audit_report['foreign_keys']:
        print(f"\n" + "-"*60)
        print("🔗 العلاقات بين الجداول:")
        for fk in audit_report['foreign_keys']:
            print(f"   • {fk['table']}.{fk['column']} → {fk['references_table']}.{fk['references_column']}")

def generate_schema_code(audit_report):
    """توليد كود Python يعكس البنية الحالية"""
    print(f"\n" + "="*80)
    print("🐍 كود Python للبنية الحالية:")
    print("="*80)
    
    print("\nCURRENT_DATABASE_SCHEMA = {")
    for table_name, table_info in audit_report['tables'].items():
        print(f"    '{table_name}': {{")
        for col_name, col_info in table_info['columns'].items():
            data_type = col_info['data_type'].upper()
            
            # تحويل أنواع البيانات إلى تعريفات SQL
            if data_type == 'INTEGER' and col_name.endswith('id') and not col_info['nullable']:
                if col_name == 'id':
                    definition = 'SERIAL PRIMARY KEY'
                else:
                    definition = 'INTEGER'
            elif data_type == 'BIGINT':
                definition = 'BIGINT'
            elif data_type == 'TEXT':
                definition = 'TEXT'
            elif data_type == 'JSONB':
                definition = 'JSONB'
            elif data_type == 'TIMESTAMP WITHOUT TIME ZONE':
                if col_info['default'] and 'CURRENT_TIMESTAMP' in str(col_info['default']):
                    definition = 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
                else:
                    definition = 'TIMESTAMP'
            elif data_type == 'BOOLEAN':
                if col_info['default']:
                    definition = f"BOOLEAN DEFAULT {col_info['default']}"
                else:
                    definition = 'BOOLEAN'
            else:
                definition = data_type
            
            if not col_info['nullable'] and 'PRIMARY KEY' not in definition:
                definition += ' NOT NULL'
            
            print(f"        '{col_name}': '{definition}',")
        print(f"    }},")
    print("}")

def main():
    """الدالة الرئيسية"""
    print("🚀 بدء فحص قاعدة البيانات...")
    
    # فحص قاعدة البيانات
    audit_report = audit_database_structure()
    
    if not audit_report:
        print("❌ فشل في فحص قاعدة البيانات")
        return
    
    # طباعة التقرير المفصل
    print_detailed_report(audit_report)
    
    # توليد كود البنية
    generate_schema_code(audit_report)
    
    # حفظ التقرير في ملف JSON
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = f"database_audit_{timestamp}.json"
    
    try:
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(audit_report, f, indent=2, ensure_ascii=False, default=str)
        print(f"\n💾 تم حفظ التقرير الكامل في: {report_file}")
    except Exception as e:
        print(f"⚠️  لم يتم حفظ التقرير: {e}")
    
    print("\n✅ تم الانتهاء من فحص قاعدة البيانات بنجاح!")

if __name__ == "__main__":
    main()