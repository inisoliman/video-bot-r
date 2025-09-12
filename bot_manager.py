# bot_manager.py
# إدارة تشغيل البوت ومنع التداخل

import os
import time
import logging
import psutil
from pathlib import Path

logger = logging.getLogger(__name__)

class BotManager:
    def __init__(self, bot_name="video_bot"):
        self.bot_name = bot_name
        self.pid_file = Path(f"{bot_name}.pid")
        
    def is_bot_running(self):
        """التحقق من وجود نسخة أخرى من البوت"""
        if not self.pid_file.exists():
            return False
            
        try:
            with open(self.pid_file, 'r') as f:
                pid = int(f.read().strip())
                
            # التحقق من وجود العملية
            if psutil.pid_exists(pid):
                process = psutil.Process(pid)
                # التحقق من أن العملية هي فعلاً البوت
                if self.bot_name.lower() in ' '.join(process.cmdline()).lower():
                    return True
                    
        except (ValueError, psutil.NoSuchProcess, psutil.AccessDenied):
            pass
            
        # إزالة ملف PID إذا كانت العملية غير موجودة
        self.pid_file.unlink(missing_ok=True)
        return False
        
    def create_pid_file(self):
        """إنشاء ملف PID للبوت الحالي"""
        with open(self.pid_file, 'w') as f:
            f.write(str(os.getpid()))
            
    def cleanup_pid_file(self):
        """إزالة ملف PID عند إغلاق البوت"""
        self.pid_file.unlink(missing_ok=True)
        
    def start_bot_safely(self, bot_function):
        """تشغيل البوت بأمان مع منع التداخل"""
        if self.is_bot_running():
            logger.error("Bot is already running! Please stop the existing instance first.")
            return False
            
        try:
            self.create_pid_file()
            logger.info(f"Starting {self.bot_name}...")
            bot_function()
            
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
        except Exception as e:
            logger.error(f"Bot crashed: {e}")
        finally:
            self.cleanup_pid_file()
            
        return True
        
    def stop_existing_bot(self):
        """إيقاف البوت الموجود"""
        if not self.pid_file.exists():
            logger.info("No bot instance found.")
            return True
            
        try:
            with open(self.pid_file, 'r') as f:
                pid = int(f.read().strip())
                
            if psutil.pid_exists(pid):
                process = psutil.Process(pid)
                process.terminate()
                
                # انتظار إغلاق العملية
                for _ in range(10):
                    if not psutil.pid_exists(pid):
                        break
                    time.sleep(1)
                    
                # إجبار الإغلاق إذا لم تتوقف
                if psutil.pid_exists(pid):
                    process.kill()
                    
            self.cleanup_pid_file()
            logger.info("Existing bot instance stopped.")
            return True
            
        except Exception as e:
            logger.error(f"Error stopping existing bot: {e}")
            self.cleanup_pid_file()
            return False