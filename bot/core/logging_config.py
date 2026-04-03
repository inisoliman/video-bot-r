#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/core/logging_config.py
# الوصف: نظام التسجيل المركزي (Structured Logging)
# ==============================================================================

import logging
import sys
import os
from datetime import datetime


class ColoredFormatter(logging.Formatter):
    """مُنسّق ملوّن يدعم مستويات مختلفة"""

    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[41m',   # Red background
    }
    RESET = '\033[0m'

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


def setup_logging(debug: bool = False) -> None:
    """
    إعداد نظام التسجيل المركزي.

    Args:
        debug: تفعيل وضع التصحيح (سجلات أكثر تفصيلاً)
    """
    log_level = logging.DEBUG if debug else logging.INFO

    # المنسّق
    log_format = '%(asctime)s │ %(levelname)-8s │ %(name)-25s │ %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    # المعالج (Console)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    # استخدام منسّق ملوّن إذا كان الخرج terminal
    if hasattr(sys.stdout, 'isatty') and sys.stdout.isatty():
        formatter = ColoredFormatter(log_format, datefmt=date_format)
    else:
        formatter = logging.Formatter(log_format, datefmt=date_format)

    handler.setFormatter(formatter)

    # إعداد المسجّل الجذري
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # إزالة المعالجات السابقة لتجنب التكرار
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # تقليل ضوضاء المكتبات الخارجية
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('telebot').setLevel(logging.WARNING)

    # رسالة بدء التشغيل
    logger = logging.getLogger(__name__)
    logger.info(f"📋 Logging initialized (level={logging.getLevelName(log_level)})")
