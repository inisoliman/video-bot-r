# ==============================================================================
# ملف: history_cleaner.py
# الوصف: نظام متطور لتنظيف سجل المشاهدة التلقائي
# التاريخ: 2025-10-05
# المطور: تحسين شامل لإدارة قاعدة البيانات
# ==============================================================================

import threading
import time
import logging
import json
from datetime import datetime, timedelta
from db_manager import execute_query, get_db_connection

# إعداد المسجل
logger = logging.getLogger(__name__)

class HistoryCleanupManager:
    """
    مدير نظام تنظيف سجل المشاهدة المتطور
    """
    
    def __init__(self):
        self.is_running = False
        self.cleanup_thread = None
        self.stats = {
            'last_cleanup': None,
            'total_cleaned': 0,
            'cleanup_count': 0,
            'errors': 0
        }
        
    def get_cleanup_settings(self):
        """
        جلب إعدادات التنظيف من قاعدة البيانات
        """
        settings = {
            'days_to_keep': 15,           # الأيام المحفوظة
            'max_records_per_user': 100,  # الحد الأقصى لكل مستخدم
            'cleanup_interval_hours': 24, # فترة التنظيف بالساعات
            'enabled': True               # تفعيل النظام
        }
        
        # جلب الإعدادات من قاعدة البيانات
        for key in settings.keys():
            result = execute_query(
                "SELECT setting_value FROM bot_settings WHERE setting_key = %s",
                (f'cleanup_{key}',),
                fetch="one"
            )
            if result:
                try:
                    if key == 'enabled':
                        settings[key] = result['setting_value'].lower() == 'true'
                    else:
                        settings[key] = int(result['setting_value'])
                except (ValueError, KeyError):
                    logger.warning(f"Invalid setting value for cleanup_{key}, using default")
        
        return settings
    
    def update_cleanup_setting(self, key, value):
        """
        تحديث إعداد التنظيف في قاعدة البيانات
        """
        return execute_query(
            "INSERT INTO bot_settings (setting_key, setting_value) VALUES (%s, %s) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
            (f'cleanup_{key}', str(value)),
            commit=True
        )
    
    def cleanup_old_history(self, days_to_keep=15):
        """
        حذف سجل المشاهدة الأقدم من العدد المحدد من الأيام
        """
        try:
            query = """
                DELETE FROM user_history 
                WHERE last_watched < CURRENT_TIMESTAMP - INTERVAL '%s days'
                RETURNING id
            """
            
            deleted_records = execute_query(query % days_to_keep, fetch="all", commit=True)
            deleted_count = len(deleted_records) if deleted_records else 0
            
            logger.info(f"Cleaned up {deleted_count} old history records (older than {days_to_keep} days)")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error in cleanup_old_history: {e}", exc_info=True)
            self.stats['errors'] += 1
            return 0
    
    def cleanup_excess_user_records(self, max_records_per_user=100):
        """
        الاحتفاظ بآخر عدد محدد من السجلات لكل مستخدم
        """
        try:
            # جلب المستخدمين الذين لديهم سجلات زائدة
            users_query = """
                SELECT user_id, COUNT(*) as record_count
                FROM user_history
                GROUP BY user_id
                HAVING COUNT(*) > %s
            """
            
            users_with_excess = execute_query(users_query, (max_records_per_user,), fetch="all")
            total_deleted = 0
            
            for user in users_with_excess:
                user_id = user['user_id']
                excess_count = user['record_count'] - max_records_per_user
                
                # حذف السجلات الزائدة (الأقدم)
                cleanup_query = """
                    DELETE FROM user_history 
                    WHERE user_id = %s AND id IN (
                        SELECT id FROM user_history 
                        WHERE user_id = %s 
                        ORDER BY last_watched ASC 
                        LIMIT %s
                    )
                    RETURNING id
                """
                
                deleted_records = execute_query(
                    cleanup_query, 
                    (user_id, user_id, excess_count), 
                    fetch="all", 
                    commit=True
                )
                
                deleted_count = len(deleted_records) if deleted_records else 0
                total_deleted += deleted_count
                
                logger.debug(f"Cleaned {deleted_count} excess records for user {user_id}")
            
            logger.info(f"Cleaned up {total_deleted} excess user records (max {max_records_per_user} per user)")
            return total_deleted
            
        except Exception as e:
            logger.error(f"Error in cleanup_excess_user_records: {e}", exc_info=True)
            self.stats['errors'] += 1
            return 0
    
    def cleanup_inactive_users_history(self, inactive_days=30):
        """
        حذف سجل المشاهدة للمستخدمين غير النشطين لفترة طويلة
        """
        try:
            query = """
                DELETE FROM user_history 
                WHERE user_id IN (
                    SELECT DISTINCT h.user_id 
                    FROM user_history h
                    LEFT JOIN bot_users u ON h.user_id = u.user_id
                    WHERE u.user_id IS NULL 
                    OR (
                        SELECT MAX(last_watched) 
                        FROM user_history h2 
                        WHERE h2.user_id = h.user_id
                    ) < CURRENT_TIMESTAMP - INTERVAL '%s days'
                )
                RETURNING id
            """
            
            deleted_records = execute_query(query % inactive_days, fetch="all", commit=True)
            deleted_count = len(deleted_records) if deleted_records else 0
            
            logger.info(f"Cleaned up {deleted_count} history records for inactive users (inactive for {inactive_days}+ days)")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error in cleanup_inactive_users_history: {e}", exc_info=True)
            self.stats['errors'] += 1
            return 0
    
    def get_cleanup_statistics(self):
        """
        جلب إحصائيات التنظيف والحالة الحالية
        """
        try:
            # إحصائيات عامة
            total_history_records = execute_query(
                "SELECT COUNT(*) as count FROM user_history",
                fetch="one"
            )
            
            # إحصائيات تفصيلية
            user_stats = execute_query("""
                SELECT 
                    COUNT(DISTINCT user_id) as unique_users,
                    AVG(records_per_user) as avg_records_per_user,
                    MAX(records_per_user) as max_records_per_user,
                    MIN(records_per_user) as min_records_per_user
                FROM (
                    SELECT user_id, COUNT(*) as records_per_user
                    FROM user_history
                    GROUP BY user_id
                ) user_counts
            """, fetch="one")
            
            # السجلات القديمة
            old_records = execute_query(
                "SELECT COUNT(*) as count FROM user_history WHERE last_watched < CURRENT_TIMESTAMP - INTERVAL '15 days'",
                fetch="one"
            )
            
            return {
                'total_records': total_history_records['count'] if total_history_records else 0,
                'unique_users': user_stats['unique_users'] if user_stats else 0,
                'avg_records_per_user': float(user_stats['avg_records_per_user']) if user_stats and user_stats['avg_records_per_user'] else 0,
                'max_records_per_user': user_stats['max_records_per_user'] if user_stats else 0,
                'min_records_per_user': user_stats['min_records_per_user'] if user_stats else 0,
                'old_records_count': old_records['count'] if old_records else 0,
                'cleanup_stats': self.stats.copy()
            }
            
        except Exception as e:
            logger.error(f"Error getting cleanup statistics: {e}", exc_info=True)
            return {'error': str(e)}
    
    def perform_full_cleanup(self):
        """
        تنفيذ تنظيف شامل
        """
        settings = self.get_cleanup_settings()
        
        if not settings['enabled']:
            logger.info("History cleanup is disabled")
            return {'status': 'disabled'}
        
        logger.info("Starting full history cleanup...")
        start_time = datetime.now()
        
        # إحصائيات ما قبل التنظيف
        pre_stats = self.get_cleanup_statistics()
        
        # تنفيذ التنظيف
        deleted_old = self.cleanup_old_history(settings['days_to_keep'])
        deleted_excess = self.cleanup_excess_user_records(settings['max_records_per_user'])
        deleted_inactive = self.cleanup_inactive_users_history(30)  # 30 يوم للمستخدمين غير النشطين
        
        total_deleted = deleted_old + deleted_excess + deleted_inactive
        
        # تحديث الإحصائيات
        self.stats['last_cleanup'] = start_time.isoformat()
        self.stats['total_cleaned'] += total_deleted
        self.stats['cleanup_count'] += 1
        
        # إحصائيات ما بعد التنظيف
        post_stats = self.get_cleanup_statistics()
        
        duration = (datetime.now() - start_time).total_seconds()
        
        result = {
            'status': 'completed',
            'duration_seconds': duration,
            'deleted_records': {
                'old_records': deleted_old,
                'excess_records': deleted_excess,
                'inactive_users': deleted_inactive,
                'total': total_deleted
            },
            'before_cleanup': pre_stats['total_records'],
            'after_cleanup': post_stats['total_records'],
            'space_saved_percent': ((pre_stats['total_records'] - post_stats['total_records']) / pre_stats['total_records'] * 100) if pre_stats['total_records'] > 0 else 0
        }
        
        logger.info(f"History cleanup completed: {total_deleted} records deleted in {duration:.2f}s")
        return result
    
    def start_scheduled_cleanup(self):
        """
        بدء التنظيف المجدول
        """
        if self.is_running:
            logger.warning("Cleanup scheduler is already running")
            return False
        
        def cleanup_worker():
            logger.info("History cleanup scheduler started")
            
            while self.is_running:
                try:
                    settings = self.get_cleanup_settings()
                    
                    if settings['enabled']:
                        # تنفيذ التنظيف
                        result = self.perform_full_cleanup()
                        logger.info(f"Scheduled cleanup result: {result['status']}")
                    
                    # انتظار الفترة المحددة
                    sleep_seconds = settings['cleanup_interval_hours'] * 3600
                    
                    # النوم مع إمكانية الإيقاف
                    for _ in range(sleep_seconds):
                        if not self.is_running:
                            break
                        time.sleep(1)
                        
                except Exception as e:
                    logger.error(f"Error in cleanup worker: {e}", exc_info=True)
                    self.stats['errors'] += 1
                    time.sleep(300)  # انتظار 5 دقائق عند حدوث خطأ
            
            logger.info("History cleanup scheduler stopped")
        
        self.is_running = True
        self.cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
        self.cleanup_thread.start()
        
        logger.info("History cleanup scheduler started successfully")
        return True
    
    def stop_scheduled_cleanup(self):
        """
        إيقاف التنظيف المجدول
        """
        if not self.is_running:
            return False
        
        self.is_running = False
        
        # انتظار انتهاء الخيط
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            self.cleanup_thread.join(timeout=5)
        
        logger.info("History cleanup scheduler stopped")
        return True
    
    def get_status(self):
        """
        جلب حالة نظام التنظيف
        """
        settings = self.get_cleanup_settings()
        stats = self.get_cleanup_statistics()
        
        return {
            'enabled': settings['enabled'],
            'is_running': self.is_running,
            'settings': settings,
            'statistics': stats,
            'thread_alive': self.cleanup_thread.is_alive() if self.cleanup_thread else False
        }

# إنشاء مثيل عام
history_manager = HistoryCleanupManager()

# دوال مساعدة للاستخدام السريع
def start_history_cleanup():
    """بدء نظام التنظيف التلقائي"""
    return history_manager.start_scheduled_cleanup()

def stop_history_cleanup():
    """إيقاف نظام التنظيف التلقائي"""
    return history_manager.stop_scheduled_cleanup()

def manual_cleanup():
    """تنفيذ تنظيف يدوي"""
    return history_manager.perform_full_cleanup()

def get_cleanup_status():
    """جلب حالة نظام التنظيف"""
    return history_manager.get_status()

def update_cleanup_settings(**kwargs):
    """تحديث إعدادات التنظيف"""
    for key, value in kwargs.items():
        history_manager.update_cleanup_setting(key, value)
    return True
