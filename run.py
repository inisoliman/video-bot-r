#!/usr/bin/env python3
# ==============================================================================
# ملف: run.py (Entry Point for Render.com)
# الوصف: نقطة البداية لتشغيل التطبيق على Render.com
# ==============================================================================

import sys
import os

# إضافة الجذر إلى المسار لجعل app package
sys.path.insert(0, os.path.dirname(__file__))

# استيراد التطبيق
from app.webhook import app

# تشغيل التطبيق (للاختبار المحلي فقط)
if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 10000)),
        debug=False
    )