> **BrainSync Context Pumper** 🧠
> Dynamically loaded for active file: `handlers\__init__.py` (Domain: **Generic Logic**)

### 📐 Generic Logic Conventions & Fixes
- **[what-changed] what-changed in user_handlers.py**: -                                         bot.delete_message(admin_id, sent_message.message_id)
+                                         bot.delete_message(admin_id, forwarded.message_id)

📌 IDE AST Context: Modified symbols likely include [logger, register]
- **[problem-fix] problem-fix in user_handlers.py**: -                                         bot.delete_message(admin_id, forwarded.message_id)
+                                         bot.delete_message(admin_id, sent_message.message_id)
-                                     except:
+                                     except Exception:

📌 IDE AST Context: Modified symbols likely include [logger, register]
- **[convention] Updated schema ApiTelegramException — confirmed 3x**: -                     try:
+                     bot.copy_message(call.message.chat.id, chat_id_int, message_id_int)
-                         bot.copy_message(call.message.chat.id, chat_id_int, message_id_int)
+                     
-                     except telebot.apihelper.ApiTelegramException as copy_error:
+                     # إضافة لوحة التقييم
-                         logger.warning(f"Failed to copy message for video {video_id_int}: {copy_error}. Trying fallback with file_id.")
+                     rating_keyboard = helpers.create_video_action_keyboard(video_id_int, user_id)
-                         # Fallback: إرسال الفيديو باستخدام file_id
+                     bot.send_message(call.message.chat.id, "⭐ قيم هذا الفيديو:", reply_markup=rating_keyboard)
-                         video_data = get_video_by_id(video_id_int)
+                     
-                         if video_data and video_data.get('file_id'):
+                 except telebot.apihelper.ApiTelegramException as e:
-                             try:
+                     logger.error(f"Telegram API error handling video {video_id}: {e}", exc_info=True)
-                                 bot.send_video(
+                     if "message not found" in str(e).lower():
-                                     call.message.chat.id,
+                         bot.answer_callback_query(call.id, "❌ الفيديو غير متاح حالياً. ربما تم حذفه من القناة.", show_alert=True)
-                                     video_data['file_id'],
+                     elif "chat not found" in str(e).lower():
-                                     caption=video_data.get('title', 'فيديو')
+                         bot.answer_callback_query(call.id, "❌ القناة غير متاحة حالياً.", show_alert=True)
-                                 )
+                     else:
-                                 logger.info(f"Successfully sent video {video_id_int} using file_id fallback.")
+                         bot.answer_callback_query(ca
… [diff truncated]

📌 IDE AST Context: Modified symbols likely include [logger, register]
- **[convention] Patched security issue Args — prevents XSS injection attacks — confirmed 8x**: - from stream_utils import (
+ 
-     build_hostinger_watch_url, build_hostinger_download_url,
+ logger = logging.getLogger(__name__)
-     encode_stream_token
+ 
- )
+ # القواميس المشتركة لتخزين حالة المستخدمين
- 
+ admin_steps = {}
- logger = logging.getLogger(__name__)
+ user_last_search = {}
- # القواميس المشتركة لتخزين حالة المستخدمين
+ 
- admin_steps = {}
+ # ============================================
- user_last_search = {}
+ # 🌟 دالة جديدة: بناء شجرة التصنيفات الهرمية
- 
+ # ============================================
- 
+ def build_category_tree(categories):
- # ============================================
+     """
- # 🌟 دالة جديدة: بناء شجرة التصنيفات الهرمية
+     تنظم التصنيفات بشكل شجري هرمي مع إضافة رموز وإيموجي
- # ============================================
+     
- def build_category_tree(categories):
+     Args:
-     """
+         categories: قائمة جميع التصنيفات من قاعدة البيانات
-     تنظم التصنيفات بشكل شجري هرمي مع إضافة رموز وإيموجي
+     
-     
+     Returns:
-     Args:
+         list: قائمة منظمة بشكل شجري مع الرموز والإيموجي
-         categories: قائمة جميع التصنيفات من قاعدة البيانات
+     """
-     
+     tree = []
-     Returns:
+     cats_by_parent = {}
-         list: قائمة منظمة بشكل شجري مع الرموز والإيموجي
+     
-     """
+     # تجميع التصنيفات حسب parent_id
-     tree = []
+     for cat in categories:
-     cats_by_parent = {}
+         parent_id = cat.get('parent_id')
-     
+         if parent_id not in cats_by_parent:
-     # تجميع التصنيفات حسب parent_id
+             cats_by_parent[parent_id] = []
-     for cat in categories:
+         cats_by_parent[parent_id].append(cat)
-         parent_id = cat.get('parent_id')
+     
-         if parent_id not in cats_by_parent:
+     def insert_cats(parent_id, prefix="", level=0):
-             cats_by_parent[parent_id] = []
+         """دالة مساعدة لإدراج التصنيفات بشكل متداخل"""
-         cats_by_parent[parent_id].append(cat)
+         children = cats_by_parent.get(parent_i
… [diff truncated]

📌 IDE AST Context: Modified symbols likely include [logger, admin_steps, user_last_search, build_category_tree, check_subscription]
- **[convention] what-changed in group_handlers.py — confirmed 3x**: File updated (external): handlers/group_handlers.py

Content summary (117 lines):
# handlers/group_handlers.py

import telebot
from telebot import types
import logging
import threading

logger = logging.getLogger(__name__)

def register(bot, admin_ids):
    """تسجيل معالجات الجروبات"""
    
    @bot.message_handler(content_types=['new_chat_members'])
    def welcome_new_member(message):
        """
        رسالة ترحيب عند إضافة البوت لجروب جديد.
        تظهر فقط في الجروبات، وتُحذف تلقائياً بعد 30 دقيقة.
        """
        try:
            # التحقق من أن ا
- **[problem-fix] problem-fix in admin_handlers.py**: File updated (external): handlers/admin_handlers.py

Content summary (227 lines):
# handlers/admin_handlers.py

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import re
import time
from telebot.apihelper import ApiTelegramException

from db_manager import (
    add_category, get_all_user_ids, add_required_channel, remove_required_channel,
    get_required_channels, get_subscriber_count, get_bot_stats, get_popular_videos,
    delete_videos_by_ids, get_video_by_id, delete_bot_user,
    delete_category_and_contents, move_videos
- **[convention] what-changed in comment_handlers.py — confirmed 3x**: File updated (external): handlers/comment_handlers.py

Content summary (651 lines):
#!/usr/bin/env python3
# ==============================================================================
# ملف: comment_handlers.py
# الوصف: معالجات نظام التعليقات الخاصة بين المستخدمين والأدمن
# ==============================================================================

import logging
from telebot import types
import db_manager as db

logger = logging.getLogger(__name__)

# دالة لـ escape أحرف Markdown الخاصة
def markdown_escape(text):
    """Escape special characters for Markd
- **[convention] what-changed in inline_handlers.py — confirmed 3x**: File updated (external): handlers/inline_handlers.py

Content summary (222 lines):
# handlers/inline_handlers.py

import telebot
from telebot.types import (
    InlineQueryResultCachedVideo,
    InlineQueryResultCachedDocument,
    InlineQueryResultArticle,
    InputTextMessageContent
)
import logging
import os

import db_manager as db

logger = logging.getLogger(__name__)

def register(bot):
    """تسجيل معالج inline query"""
    
    @bot.inline_handler(lambda query: True)
    def handle_inline_query(inline_query):
        """
        معالج الـ inline q
- **[what-changed] 🟢 Edited c:\Users\HiMa\AppData\Local\Programs\Python\Python313\Lib\logging\__init__.py (52 changes, 13min)**: Active editing session on c:\Users\HiMa\AppData\Local\Programs\Python\Python313\Lib\logging\__init__.py.
52 content changes over 13 minutes.
- **[problem-fix] problem-fix in webhook_bot.py**: -                             except:
+                             except Exception:

📌 IDE AST Context: Modified symbols likely include [logger, BOT_TOKEN, DATABASE_URL, CHANNEL_ID, ADMIN_IDS_STR]
- **[problem-fix] problem-fix in webhook_bot.py**: -                 except:
+                 except Exception:

📌 IDE AST Context: Modified symbols likely include [logger, BOT_TOKEN, DATABASE_URL, CHANNEL_ID, ADMIN_IDS_STR]
- **[what-changed] Replaced auth Feature — enables runtime feature toggling without redeployment**: - PUBLIC_STREAM_PAGE_URL = os.environ.get('PUBLIC_STREAM_PAGE_URL', 'https://orsozox.com/vstream')
+ 
- LINK_SIGNING_[REDACTED] or BOT_TOKEN
+ # ==============================================================================
- 
+ # Feature Flags
- # Feature Flags
+ ENABLE_COMMENTS = os.environ.get('ENABLE_COMMENTS', 'true').lower() == 'true'
- # ==============================================================================
+ ENABLE_RATINGS = os.environ.get('ENABLE_RATINGS', 'true').lower() == 'true'
- ENABLE_COMMENTS = os.environ.get('ENABLE_COMMENTS', 'true').lower() == 'true'
+ ENABLE_FAVORITES = os.environ.get('ENABLE_FAVORITES', 'true').lower() == 'true'
- ENABLE_RATINGS = os.environ.get('ENABLE_RATINGS', 'true').lower() == 'true'
+ ENABLE_HISTORY = os.environ.get('ENABLE_HISTORY', 'true').lower() == 'true'
- ENABLE_FAVORITES = os.environ.get('ENABLE_FAVORITES', 'true').lower() == 'true'
+ 
- ENABLE_HISTORY = os.environ.get('ENABLE_HISTORY', 'true').lower() == 'true'
+ # ==============================================================================
- 
+ # Validation
- # Validation
+ def validate_config():
- # ==============================================================================
+     """التحقق من صحة الإعدادات الضرورية"""
- def validate_config():
+     errors = []
-     """التحقق من صحة الإعدادات الضرورية"""
+     
-     errors = []
+     if not BOT_TOKEN:
-     
+         errors.append("BOT_TOKEN is missing")
-     if not BOT_TOKEN:
+     
-         errors.append("BOT_TOKEN is missing")
+     if not DATABASE_URL:
-     
+         errors.append("DATABASE_URL is missing")
-     if not DATABASE_URL:
+     
-         errors.append("DATABASE_URL is missing")
+     if not CHANNEL_ID:
-     
+         errors.append("CHANNEL_ID is missing")
-     if not CHANNEL_ID:
+     
-         errors.append("CHANNEL_ID is missing")
+     if not ADMIN_IDS:
-     
+         errors.append("AD
… [diff truncated]

📌 IDE AST Context: Modified symbols likely include [DATABASE_URL, result, DB_CONFIG, DB_POOL_MIN, DB_POOL_MAX]
- **[what-changed] Replaced auth Flask — evolves the database schema to support new requirements**: - import requests
+ from flask import Flask, request, jsonify, abort
- from flask import Flask, request, jsonify, abort, Response, render_template_string
+ import telebot
- import telebot
+ from telebot.types import Update
- from telebot.types import Update
+ 
- 
+ # استيراد الوحدات المخصصة
- # استيراد الوحدات المخصصة
+ from db_manager import verify_and_repair_schema
- from db_manager import verify_and_repair_schema
+ from handlers import register_all_handlers
- from handlers import register_all_handlers
+ from state_manager import state_manager
- from state_manager import state_manager
+ from history_cleaner import start_history_cleanup
- from history_cleaner import start_history_cleanup
+ 
- from stream_utils import (
+ # --- إعداد نظام التسجيل ---
-     decode_stream_token, encode_stream_token,
+ logging.basicConfig(
-     build_render_stream_url, build_render_download_url,
+     level=logging.INFO,
-     build_render_watch_url, build_hostinger_watch_url,
+     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
-     build_hostinger_download_url
+ )
- )
+ logger = logging.getLogger(__name__)
- # --- إعداد نظام التسجيل ---
+ # --- المتغيرات البيئية مع قيم افتراضية للاختبار ---
- logging.basicConfig(
+ BOT_TOKEN = os.getenv("BOT_TOKEN")
-     level=logging.INFO,
+ DATABASE_URL = os.getenv("DATABASE_URL") 
-     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
+ CHANNEL_ID = os.getenv("CHANNEL_ID")
- )
+ ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
- logger = logging.getLogger(__name__)
+ WEBHOOK_[REDACTED] "default_secret")
- 
+ APP_URL = os.getenv("APP_URL")
- # --- المتغيرات البيئية مع قيم افتراضية للاختبار ---
+ 
- BOT_TOKEN = os.getenv("BOT_TOKEN")
+ # Render يستخدم PORT بدلاً من WEBHOOK_PORT
- DATABASE_URL = os.getenv("DATABASE_URL") 
+ PORT = int(os.getenv("PORT", "10000"))
- CHANNEL_ID = os.getenv("CHANNEL_ID")
+ 
- ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
+ # طباعة المتغيرات للتشخيص (بدون كشف القيم الحساسة)

… [diff truncated]

📌 IDE AST Context: Modified symbols likely include [logger, BOT_TOKEN, DATABASE_URL, CHANNEL_ID, ADMIN_IDS_STR]
- **[discovery] discovery in bot_manager.py**: File updated (external): bot_manager.py

Content summary (100 lines):
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
            
- **[what-changed] what-changed in db_compat_patch.py**: File updated (external): db_compat_patch.py

Content summary (31 lines):
#!/usr/bin/env python3
# ==============================================================================
# ملف: db_compat_patch.py
# الوصف: طبقة توافق - يُستخدَم لاستيراد الدوال المشتركة من db_manager فقط
# ملاحظة: هذا الملف لا يحتوي على أي دوال مكررة - كل شيء يُستورَد من db_manager
# ==============================================================================

"""
طبقة توافق لضمان عدم تضارب الاستيرادات.
جميع الدوال يتم استيرادها مباشرة من db_manager لتجنب التكرار.
"""

# استيراد الدوال المشترك
