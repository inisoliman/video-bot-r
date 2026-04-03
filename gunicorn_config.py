# ==============================================================================
# ملف: gunicorn_config.py
# الوصف: إعدادات Gunicorn للإنتاج
# ==============================================================================

import os
import multiprocessing

# إعدادات الخادم
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = min(4, multiprocessing.cpu_count() * 2 + 1)
worker_class = "sync"
worker_connections = 1000
timeout = 60
keepalive = 5
max_requests = 1000
max_requests_jitter = 100

# إعدادات التسجيل
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# إعدادات الأمان
limit_request_line = 4096
limit_request_fields = 100
limit_request_field_size = 8190

# إعدادات الأداء
preload_app = True
enable_stdio_inheritance = True

# معالج التشغيل
def on_starting(server):
    server.log.info("Gunicorn server is starting...")

def on_reload(server):
    server.log.info("Gunicorn server is reloading...")

def worker_int(worker):
    worker.log.info(f"Worker {worker.pid} received INT signal")

def pre_fork(server, worker):
    server.log.info(f"Worker {worker.pid} spawned")

def post_fork(server, worker):
    server.log.info(f"Worker {worker.pid} spawned")

def when_ready(server):
    server.log.info("Gunicorn server is ready. Spawning workers")

def worker_abort(worker):
    worker.log.info(f"Worker {worker.pid} received SIGABRT signal")
