# state_manager.py

import threading
import time
import logging
import json
from datetime import datetime, timedelta
from app.database import get_pool

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
            'days_to_keep': 10,           # الأيام المحفوظة
            'max_records_per_user': 100,  # الحد الأقصى لكل مستخدم
            'cleanup_interval_hours': 24, # فترة التنظيف بالساعات
            'enabled': True               # تفعيل النظام
        }

        pool = get_pool()
        with pool.getconn() as conn:
            with conn.cursor() as cursor:
                for key in settings.keys():
                    cursor.execute(
                        "SELECT setting_value FROM bot_settings WHERE setting_key = %s",
                        (f'cleanup_{key}',)
                    )
                    result = cursor.fetchone()
                    if result:
                        try:
                            if key == 'enabled':
                                settings[key] = result[0].lower() == 'true'
                            elif key in ['days_to_keep', 'max_records_per_user', 'cleanup_interval_hours']:
                                settings[key] = int(result[0])
                        except (ValueError, AttributeError):
                            pass

        return settings

    def cleanup_old_history(self):
        """
        تنظيف سجل المشاهدة القديم
        """
        try:
            settings = self.get_cleanup_settings()
            if not settings['enabled']:
                logger.info("History cleanup is disabled")
                return 0

            cutoff_date = datetime.now() - timedelta(days=settings['days_to_keep'])

            pool = get_pool()
            with pool.getconn() as conn:
                with conn.cursor() as cursor:
                    # حذف السجلات القديمة
                    cursor.execute("""
                        DELETE FROM user_history
                        WHERE last_watched < %s
                    """, (cutoff_date,))

                    deleted_old = cursor.rowcount

                    # الحد من عدد السجلات لكل مستخدم
                    cursor.execute("""
                        DELETE FROM user_history
                        WHERE id IN (
                            SELECT id FROM (
                                SELECT id,
                                       ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY last_watched DESC) as rn
                                FROM user_history
                            ) t
                            WHERE t.rn > %s
                        )
                    """, (settings['max_records_per_user'],))

                    deleted_excess = cursor.rowcount

                    conn.commit()

                    total_deleted = deleted_old + deleted_excess
                    self.stats['total_cleaned'] += total_deleted
                    self.stats['last_cleanup'] = datetime.now()

                    logger.info(f"History cleanup completed: {total_deleted} records deleted ({deleted_old} old, {deleted_excess} excess)")

                    return total_deleted

        except Exception as e:
            logger.error(f"Error during history cleanup: {e}")
            self.stats['errors'] += 1
            return 0

    def start_cleanup_thread(self):
        """
        بدء thread التنظيف التلقائي
        """
        if self.is_running:
            logger.warning("Cleanup thread is already running")
            return

        self.is_running = True
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()
        logger.info("History cleanup thread started")

    def stop_cleanup_thread(self):
        """
        إيقاف thread التنظيف
        """
        self.is_running = False
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            self.cleanup_thread.join(timeout=5)
        logger.info("History cleanup thread stopped")

    def _cleanup_loop(self):
        """
        حلقة التنظيف التلقائي
        """
        while self.is_running:
            try:
                settings = self.get_cleanup_settings()
                if settings['enabled']:
                    self.cleanup_old_history()
                    self.stats['cleanup_count'] += 1

                # انتظار حتى الدورة التالية
                time.sleep(settings['cleanup_interval_hours'] * 3600)

            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
                time.sleep(3600)  # انتظار ساعة قبل المحاولة التالية

    def get_stats(self):
        """
        جلب إحصائيات التنظيف
        """
        return self.stats.copy()

# إنشاء instance عالمي
history_cleaner = HistoryCleanupManager()