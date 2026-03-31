# Gunicorn configuration for Video Bot R

bind = "0.0.0.0:10000"
workers = 2
worker_class = "sync"
worker_connections = 1000
timeout = 30
keepalive = 2
max_requests = 1000
max_requests_jitter = 50

# Logging
loglevel = "info"
accesslog = "-"
errorlog = "-"

# Process naming
proc_name = "video_bot_r"

# Server mechanics
preload_app = True
pidfile = "/tmp/gunicorn.pid"
user = None
group = None
tmp_upload_dir = None

# SSL
keyfile = None
certfile = None

# Performance
worker_tmp_dir = "/dev/shm"