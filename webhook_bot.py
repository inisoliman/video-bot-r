#!/usr/bin/env python3
# ==============================================================================
# ملف: webhook_bot.py (محدث للعمل مع Render)
# الوصف: البوت الرئيسي باستخدام webhook - محسن لـ Render
# ==============================================================================

import os
import json
import logging
import threading  # إضافة threading
from flask import Flask, request, jsonify, abort
import telebot
from telebot.types import Update

# استيراد الوحدات المخصصة
from db_manager import verify_and_repair_schema
from handlers import register_all_handlers
from state_manager import state_manager
from history_cleaner import start_history_cleanup

# --- إعداد نظام التسجيل ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- المتغيرات البيئية مع قيم افتراضية للاختبار ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL") 
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "default_secret")
APP_URL = os.getenv("APP_URL")

# Render يستخدم PORT بدلاً من WEBHOOK_PORT
PORT = int(os.getenv("PORT", "10000"))

# طباعة المتغيرات للتشخيص (بدون كشف القيم الحساسة)
logger.info(f"🔍 Environment Check:")
logger.info(f"BOT_TOKEN: {'✅ Set' if BOT_TOKEN else '❌ Missing'}")
logger.info(f"DATABASE_URL: {'✅ Set' if DATABASE_URL else '❌ Missing'}")
logger.info(f"CHANNEL_ID: {'✅ Set' if CHANNEL_ID else '❌ Missing'}")
logger.info(f"ADMIN_IDS: {'✅ Set' if ADMIN_IDS_STR else '❌ Missing'}")
logger.info(f"APP_URL: {'✅ Set' if APP_URL else '❌ Missing'}")
logger.info(f"PORT: {PORT}")

# التحقق من المتغيرات المطلوبة
missing_vars = []
if not BOT_TOKEN: missing_vars.append("BOT_TOKEN")
if not DATABASE_URL: missing_vars.append("DATABASE_URL")
if not CHANNEL_ID: missing_vars.append("CHANNEL_ID")
if not ADMIN_IDS_STR: missing_vars.append("ADMIN_IDS")
if not APP_URL: missing_vars.append("APP_URL")

if missing_vars:
    logger.critical(f"❌ MISSING ENVIRONMENT VARIABLES: {', '.join(missing_vars)}")
    logger.critical("📋 Required variables:")
    logger.critical("   BOT_TOKEN=your_bot_token")
    logger.critical("   DATABASE_URL=your_postgres_url")
    logger.critical("   CHANNEL_ID=-1001234567890")
    logger.critical("   ADMIN_IDS=123456789,987654321")
    logger.critical("   APP_URL=https://your-app.onrender.com")
    exit(1)

# التحقق من استخدام HTTPS
if APP_URL and not APP_URL.startswith('https://'):
    logger.critical("❌ APP_URL must use HTTPS for security!")
    logger.critical(f"   Current: {APP_URL}")
    logger.critical("   Required: https://your-app.onrender.com")
    exit(1)

try:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]
    logger.info(f"✅ ADMIN_IDS parsed: {len(ADMIN_IDS)} admins")
except ValueError as e:
    logger.critical(f"❌ ADMIN_IDS format error: {e}")
    exit(1)

# --- إعداد Flask والBot ---
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# --- إعداد Rate Limiting ---
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per day", "50 per hour"],
        storage_uri="memory://"
    )
    logger.info("✅ Rate limiting enabled")
except ImportError:
    logger.warning("⚠️ Flask-Limiter not installed. Rate limiting disabled.")
    limiter = None

# --- Routes ---
@app.route("/", methods=["GET"])
def health_check():
    # استثناء من rate limiting لأن Render يستخدمه للـ health checks
    return jsonify({
        "status": "healthy",
        "bot": "video-bot-webhook",
        "version": "2.0.0",
        "webhook_configured": bool(APP_URL)
    })

# استثناء health endpoint من rate limiting
if limiter:
    limiter.exempt(health_check)

@app.route("/health", methods=["GET"])
def health():
    try:
        from db_manager import get_db_connection
        with get_db_connection() as conn:
            if conn:
                db_status = "connected"
            else:
                db_status = "disconnected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return jsonify({
        "status": "ok",
        "database": db_status,
        "bot_token": "configured" if BOT_TOKEN else "missing",
        "webhook_configured": bool(APP_URL)
    })

# استثناء health endpoint من rate limiting
if limiter:
    limiter.exempt(health)

@app.route(f"/bot{BOT_TOKEN}", methods=["POST"])
def webhook():
    # تطبيق rate limiting يدوياً إذا كان متاحاً
    if limiter:
        try:
            limiter.check()
        except Exception:
            logger.warning(f"Rate limit exceeded from {request.remote_addr}")
            abort(429)  # Too Many Requests
    
    try:
        # التحقق من WEBHOOK_SECRET فقط إذا تم تعيينه بشكل مخصص
        # ملاحظة: Telegram قد لا يرسل secret_token في الطلبات القديمة
        if WEBHOOK_SECRET and WEBHOOK_SECRET != "default_secret":
            secret_token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
            # فقط نحذر إذا كان هناك secret مخصص ولم يتطابق
            # لكن لا نرفض الطلب لأن Telegram قد لا يرسله في بعض الحالات
            if secret_token and secret_token != WEBHOOK_SECRET:
                logger.warning(f"Webhook secret mismatch from {request.remote_addr}")
                # لا نستخدم abort هنا لتجنب رفض الطلبات الشرعية
        
        if request.content_type != 'application/json':
            logger.warning(f"Invalid content-type: {request.content_type}")
            abort(400)
        
        json_data = request.get_json()
        if not json_data:
            logger.warning("Empty JSON received")
            abort(400)
        
        update = Update.de_json(json_data)
        if not update:
            logger.warning("Invalid update object")
            abort(400)
        
        # معالجة التحديث
        process_update(update)
        
        return jsonify({"ok": True})
        
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({"error": "server_error"}), 500

def process_update(update):
    try:
        # معالجة حالة المستخدم أولاً
        if update.message and update.message.from_user:
            if state_manager.handle_message(update.message, bot):
                return
        
        # معالجة أنواع التحديثات المختلفة
        if update.message:
            bot.process_new_messages([update.message])
        elif update.callback_query:
            bot.process_new_callback_query([update.callback_query])
        elif update.inline_query:
            bot.process_new_inline_query([update.inline_query])
            
    except Exception as e:
        logger.error(f"Process update error: {e}", exc_info=True)

@app.route("/set_webhook", methods=["POST", "GET"])
def set_webhook():
    try:
        webhook_url = f"{APP_URL}/bot{BOT_TOKEN}"
        
        # حذف webhook القديم
        bot.remove_webhook()
        logger.info("🗑️ Old webhook removed")
        
        # تعيين webhook جديد
        webhook_params = {
            'url': webhook_url,
            'max_connections': 40,
            'drop_pending_updates': True,
            'allowed_updates': ["message", "callback_query", "inline_query"]
        }
        
        # إضافة secret_token فقط إذا تم تعيينه بشكل مخصص
        if WEBHOOK_SECRET and WEBHOOK_SECRET != "default_secret":
            webhook_params['secret_token'] = WEBHOOK_SECRET
            logger.info("🔐 Webhook secret token configured")
        else:
            logger.warning("⚠️ Using webhook without secret token")
        
        result = bot.set_webhook(**webhook_params)
        
        if result:
            logger.info(f"✅ Webhook set: {webhook_url}")
            return jsonify({
                "status": "success", 
                "webhook": webhook_url
            })
        else:
            logger.error("❌ Failed to set webhook")
            return jsonify({
                "status": "failed",
                "error": "Could not set webhook"
            }), 500
            
    except Exception as e:
        logger.error(f"Set webhook error: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route("/admin/update_thumbnails", methods=["GET", "POST"])
def admin_update_thumbnails():
    """
    مسار للأدمن لتحديث thumbnails للفيديوهات القديمة.
    يعمل بدون الحاجة لـ shell access.
    """
    try:
        import threading
        import db_manager as db
        
        # التحقق من وجود admin_id في الطلب
        admin_id = request.args.get('admin_id') or request.form.get('admin_id')
        
        if not admin_id:
            return jsonify({
                "status": "error",
                "message": "Missing admin_id parameter"
            }), 400
        
        try:
            admin_id = int(admin_id)
        except ValueError:
            return jsonify({
                "status": "error",
                "message": "Invalid admin_id"
            }), 400
        
        # التحقق من أن المستخدم admin
        if admin_id not in ADMIN_IDS:
            return jsonify({
                "status": "error",
                "message": "Unauthorized"
            }), 403
        
        def update_thumbnails_background():
            """تحديث thumbnails في الخلفية"""
            try:
                logger.info("🚀 Starting thumbnail extraction in background...")
                
                total_updated = 0
                batch_size = 20  # دفعات أكبر قليلاً
                max_iterations = 100  # زيادة الحد الأقصى لـ 100 دفعة (2000 فيديو)
                
                for iteration in range(max_iterations):
                    videos = db.get_videos_without_thumbnail(limit=batch_size)
                    
                    if not videos:
                        logger.info("✅ No more videos to process")
                        break
                    
                    for video in videos:
                        try:
                            # التحقق من صحة file_id
                            if not video.get('file_id'):
                                logger.warning(f"Video {video['id']} has no file_id, skipping")
                                continue
                            
                            # إرسال الفيديو للأدمن
                            sent_message = bot.send_video(
                                chat_id=admin_id,
                                video=video['file_id'],
                                caption=f"🔄 استخراج thumbnail #{video['id']}"
                            )
                            
                            # استخراج thumbnail
                            if sent_message.video and sent_message.video.thumb:
                                thumbnail_id = sent_message.video.thumb.file_id
                                
                                # حفظ في قاعدة البيانات
                                if db.update_video_thumbnail(video['id'], thumbnail_id):
                                    total_updated += 1
                                    logger.info(f"✅ Updated video {video['id']}")
                                
                                # حذف الرسالة
                                try:
                                    bot.delete_message(admin_id, sent_message.message_id)
                                except:
                                    pass
                            
                            import time
                            time.sleep(1)  # تأخير بسيط
                            
                        except Exception as e:
                            logger.error(f"Error updating video {video['id']}: {e}")
                            # متابعة مع الفيديو التالي
                            continue
                    
                    import time
                    time.sleep(5)  # تأخير بين الدفعات
                
                # إرسال رسالة للأدمن بالنتيجة
                bot.send_message(
                    admin_id,
                    f"✅ *تم تحديث Thumbnails*\n\n"
                    f"📊 عدد الفيديوهات: {total_updated}\n"
                    f"🎉 العملية مكتملة!",
                    parse_mode="Markdown"
                )
                
                logger.info(f"🎉 Thumbnail extraction completed! Total: {total_updated}")
                
            except Exception as e:
                logger.error(f"Error in background thumbnail update: {e}", exc_info=True)
                try:
                    bot.send_message(
                        admin_id,
                        f"❌ حدث خطأ أثناء تحديث Thumbnails:\n{str(e)}"
                    )
                except:
                    pass
        
        # تشغيل في thread منفصل
        thread = threading.Thread(target=update_thumbnails_background, daemon=True)
        thread.start()
        
        return jsonify({
            "status": "success",
            "message": "Thumbnail update started in background. You will receive a message when complete."
        })
        
    except Exception as e:
        logger.error(f"Admin update thumbnails error: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route("/admin/extract_channel_thumbnails", methods=["GET", "POST"])
def admin_extract_channel_thumbnails():
    """
    استخراج thumbnails من القناة للفيديوهات القديمة.
    يعمل بدون shell access.
    """
    try:
        import threading
        import db_manager as db
        
        # التحقق من admin_id
        admin_id = request.args.get('admin_id') or request.form.get('admin_id')
        
        if not admin_id:
            return jsonify({
                "status": "error",
                "message": "Missing admin_id parameter"
            }), 400
        
        try:
            admin_id = int(admin_id)
        except ValueError:
            return jsonify({
                "status": "error",
                "message": "Invalid admin_id"
            }), 400
        
        if admin_id not in ADMIN_IDS:
            return jsonify({
                "status": "error",
                "message": "Unauthorized"
            }), 403
        
        def extract_thumbnails_background():
            """استخراج thumbnails من القناة في الخلفية"""
            try:
                logger.info("🚀 Starting channel thumbnail extraction...")
                
                # جلب الفيديوهات بدون thumbnails
                videos = db.get_videos_without_thumbnail(limit=5000)  # زيادة الحد لمعالجة جميع الفيديوهات بسرعة
                
                if not videos:
                    bot.send_message(
                        admin_id,
                        "✅ جميع الفيديوهات لديها thumbnails بالفعل!"
                    )
                    return
                
                bot.send_message(
                    admin_id,
                    f"🔄 بدء استخراج thumbnails لـ {len(videos)} فيديو..."
                )
                
                total_updated = 0
                failed_count = 0
                
                for video in videos:
                    try:
                        if not video.get('message_id') or not video.get('chat_id'):
                            failed_count += 1
                            continue
                        
                        # نسخ الرسالة للأدمن لاستخراج thumbnail
                        # لا يمكن forward على نفس القناة!
                        message = bot.copy_message(
                            chat_id=admin_id,  # إرسال للأدمن
                            from_chat_id=video['chat_id'],
                            message_id=video['message_id']
                        )
                        
                        # الآن نحتاج جلب الرسالة المنسوخة للحصول على thumbnail
                        # لكن copy_message لا يعيد message object كامل!
                        # الحل: نستخدم file_id الموجود ونرسله للأدمن
                        
                        if video.get('file_id'):
                            # إرسال الفيديو للأدمن باستخدام file_id
                            sent = bot.send_video(
                                chat_id=admin_id,
                                video=video['file_id'],
                                caption=f"🔄 استخراج thumbnail #{video['id']}"
                            )
                            
                            # استخراج thumbnail
                            if sent.video and sent.video.thumb:
                                thumbnail_id = sent.video.thumb.file_id
                                
                                if db.update_video_thumbnail(video['id'], thumbnail_id):
                                    total_updated += 1
                                    logger.info(f"✅ Updated video {video['id']}")
                                else:
                                    failed_count += 1
                            else:
                                logger.warning(f"No thumbnail in sent message for video {video['id']}")
                                failed_count += 1
                            
                            # حذف الرسالة
                            try:
                                bot.delete_message(admin_id, sent.message_id)
                            except:
                                pass
                        else:
                            logger.warning(f"Video {video['id']} has no file_id")
                            failed_count += 1
                        
                        import time
                        time.sleep(0.5)
                        
                    except Exception as e:
                        logger.error(f"Error extracting thumbnail for video {video['id']}: {e}")
                        failed_count += 1
                        continue
                
                # إرسال النتيجة
                bot.send_message(
                    admin_id,
                    f"✅ *اكتمل استخراج Thumbnails!*\n\n"
                    f"📊 الإحصائيات:\n"
                    f"• نجح: {total_updated}\n"
                    f"• فشل: {failed_count}\n"
                    f"• المجموع: {len(videos)}",
                    parse_mode="Markdown"
                )
                
                logger.info(f"🎉 Channel thumbnail extraction completed! Success: {total_updated}, Failed: {failed_count}")
                
            except Exception as e:
                logger.error(f"Error in channel thumbnail extraction: {e}", exc_info=True)
                try:
                    bot.send_message(
                        admin_id,
                        f"❌ حدث خطأ أثناء استخراج Thumbnails:\n{str(e)}"
                    )
                except:
                    pass
        
        # تشغيل في thread منفصل
        thread = threading.Thread(target=extract_thumbnails_background, daemon=True)
        thread.start()
        
        return jsonify({
            "status": "success",
            "message": "Channel thumbnail extraction started. You will receive a message when complete."
        })
        
    except Exception as e:
        logger.error(f"Admin extract channel thumbnails error: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/admin/fix_videos_professional', methods=['GET'])
def admin_fix_videos_professional():
    """الحل الاحترافي: جلب file_id و thumbnail من القناة"""
    try:
        import db_manager as db
        
        admin_id = request.args.get('admin_id')
        
        if not admin_id or int(admin_id) not in ADMIN_IDS:
            return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
        admin_id = int(admin_id)
        
        def fix_videos_background():
            """إصلاح الفيديوهات في الخلفية"""
            try:
                logger.info("🚀 Starting professional video fix...")
                
                sql = """
                    SELECT id, message_id, chat_id, file_id, thumbnail_file_id
                    FROM video_archive
                    WHERE message_id IS NOT NULL 
                      AND chat_id IS NOT NULL
                      AND (file_id IS NULL OR thumbnail_file_id IS NULL)
                    ORDER BY id ASC
                    LIMIT 100
                """
                videos = db.execute_query(sql, fetch="all")
                
                if not videos:
                    bot.send_message(admin_id, "✅ جميع الفيديوهات لديها file_id و thumbnail بالفعل!")
                    return
                
                total_updated = 0
                failed_count = 0
                
                for video in videos:
                    try:
                        # دائماً نحتاج إعادة جلب file_id من القناة لأن الموجود قد يكون Document
                        # وليس Video، لذلك نتجاهل file_id الموجود ونجلب الصحيح
                        try:
                            forwarded = bot.forward_message(
                                chat_id=admin_id,
                                from_chat_id=video['chat_id'],
                                message_id=video['message_id']
                            )
                            
                            if forwarded.video:
                                new_file_id = forwarded.video.file_id
                                new_thumbnail_id = forwarded.video.thumb.file_id if forwarded.video.thumb else None
                                
                                # تحديث file_id و thumbnail (حتى لو كان thumbnail = NULL)
                                update_sql = """
                                    UPDATE video_archive
                                    SET file_id = %s,
                                        thumbnail_file_id = %s
                                    WHERE id = %s
                                """
                                db.execute_query(update_sql, (new_file_id, new_thumbnail_id, video['id']), commit=True)
                                total_updated += 1
                                
                                if new_thumbnail_id:
                                    logger.info(f"✅ Updated file_id + thumbnail for video {video['id']}")
                                else:
                                    logger.info(f"✅ Updated file_id (no thumbnail) for video {video['id']}")
                            else:
                                failed_count += 1
                                logger.warning(f"⚠️ No video in forwarded message for video {video['id']}")
                            
                            # حذف الرسالة المُعاد توجيهها
                            try:
                                bot.delete_message(admin_id, forwarded.message_id)
                            except:
                                pass
                        except Exception as e:
                            logger.error(f"Error forwarding video {video['id']}: {e}")
                            failed_count += 1
                        
                        import time
                        time.sleep(0.3)
                        
                    except Exception as e:
                        logger.error(f"Error fixing video {video['id']}: {e}")
                        failed_count += 1
                        continue
                
                bot.send_message(
                    admin_id,
                    f"✅ *اكتمل الإصلاح الاحترافي!*\n\n"
                    f"📊 الإحصائيات:\n"
                    f"• نجح: {total_updated}\n"
                    f"• فشل: {failed_count}\n"
                    f"• المجموع: {len(videos)}\n\n"
                    f"💡 شغّل المسار مرة أخرى لإصلاح المزيد",
                    parse_mode='Markdown'
                )
                
            except Exception as e:
                logger.error(f"Error in fix videos background: {e}", exc_info=True)
                try:
                    bot.send_message(admin_id, f"❌ حدث خطأ أثناء الإصلاح:\n{str(e)}")
                except:
                    pass
        
        thread = threading.Thread(target=fix_videos_background, daemon=True)
        thread.start()
        
        return jsonify({
            "status": "success",
            "message": "Professional video fix started. You will receive a message when complete."
        })
        
    except Exception as e:
        logger.error(f"Admin fix videos error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
@app.route("/admin/optimize_db", methods=["GET", "POST"])
def admin_optimize_db():
    """
    تحسين قاعدة البيانات عبر الويب: إنشاء الفهارس.
    """
    try:
        import threading
        # import db_optimizer dynamically to avoid circular imports or early init
        import db_optimizer

        admin_id = request.args.get('admin_id') or request.form.get('admin_id')

        if not admin_id:
            return jsonify({
                "status": "error",
                "message": "Missing admin_id parameter"
            }), 400

        try:
            admin_id = int(admin_id)
        except ValueError:
            return jsonify({
                "status": "error",
                "message": "Invalid admin_id"
            }), 400

        if admin_id not in ADMIN_IDS:
            return jsonify({
                "status": "error",
                "message": "Unauthorized"
            }), 403

        def optimize_background():
            """تنفيذ التحسين في الخلفية"""
            try:
                logger.info("🚀 Starting database optimization from web...")
                
                bot.send_message(
                    admin_id,
                    "🔧 *بدء عملية تحسين قاعدة البيانات*...\n"
                    "سيتم إنشاء الفهارس لزيادة سرعة البحث.\n"
                    "قد تستغرق العملية دقيقة أو دقيقتين.",
                    parse_mode="Markdown"
                )
                
                result = db_optimizer.main()
                
                if result:
                    msg = "✅ *تم تحسين قاعدة البيانات بنجاح!*\nأصبح البحث الآن أسرع."
                else:
                    msg = "⚠️ *انتهت العملية مع بعض الملاحظات*\nراجع السجلات للتفاصيل."
                    
                bot.send_message(admin_id, msg, parse_mode="Markdown")
                logger.info(f"🎉 API Optimization completed! Result: {result}")
                
            except Exception as e:
                logger.error(f"Error in background optimization: {e}", exc_info=True)
                try:
                    bot.send_message(
                        admin_id,
                        f"❌ حدث خطأ أثناء تحسين القاعدة:\n{str(e)}"
                    )
                except:
                    pass

        # تشغيل في thread منفصل
        thread = threading.Thread(target=optimize_background, daemon=True)
        thread.start()

        return jsonify({
            "status": "success",
            "message": "Optimization started in background. You will receive a message when complete."
        })

    except Exception as e:
        logger.error(f"Admin optimize db error: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route("/admin/migrate_database", methods=["GET", "POST"])
def admin_migrate_database():
    """
    ترحيل قاعدة البيانات عبر الويب: ترحيل البيانات من الجداول القديمة وحذفها.
    الاستخدام: https://your-bot.onrender.com/admin/migrate_database?admin_id=YOUR_ID
    """
    try:
        import threading
        import psycopg2
        from psycopg2.extras import DictCursor
        
        admin_id = request.args.get('admin_id') or request.form.get('admin_id')
        
        if not admin_id:
            return jsonify({
                "status": "error",
                "message": "Missing admin_id parameter. Usage: /admin/migrate_database?admin_id=YOUR_ID"
            }), 400
        
        try:
            admin_id = int(admin_id)
        except ValueError:
            return jsonify({
                "status": "error",
                "message": "Invalid admin_id"
            }), 400
        
        if admin_id not in ADMIN_IDS:
            return jsonify({
                "status": "error",
                "message": "Unauthorized"
            }), 403
        
        # خريطة الجداول القديمة -> الجديدة
        TABLE_MAPPINGS = {
            'videoarchive': 'video_archive',
            'botusers': 'bot_users',
            'userfavorites': 'user_favorites',
            'userhistory': 'user_history',
            'videoratings': 'video_ratings',
            'userstates': 'user_states',
            'botsettings': 'bot_settings',
            'requiredchannels': 'required_channels'
        }
        
        def table_exists(cursor, table_name):
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' AND table_name = %s
                )
            """, (table_name,))
            return cursor.fetchone()[0]
        
        def get_table_count(cursor, table_name):
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                return cursor.fetchone()[0]
            except:
                return 0
        
        def migrate_background():
            """تنفيذ الترحيل في الخلفية"""
            try:
                from urllib.parse import urlparse
                
                result = urlparse(DATABASE_URL)
                db_config = {
                    'user': result.username,
                    'password': result.password,
                    'host': result.hostname,
                    'port': result.port,
                    'dbname': result.path[1:]
                }
                
                conn = psycopg2.connect(**db_config)
                cursor = conn.cursor(cursor_factory=DictCursor)
                
                bot.send_message(
                    admin_id,
                    "🔄 *بدء عملية ترحيل قاعدة البيانات*...\n"
                    "سيتم تحليل الجداول وترحيل البيانات.",
                    parse_mode="Markdown"
                )
                
                # تحليل الجداول
                analysis_text = "📊 *تحليل الجداول:*\n\n"
                tables_to_drop = []
                
                for old_table, new_table in TABLE_MAPPINGS.items():
                    old_exists = table_exists(cursor, old_table)
                    new_exists = table_exists(cursor, new_table)
                    
                    old_count = get_table_count(cursor, old_table) if old_exists else 0
                    new_count = get_table_count(cursor, new_table) if new_exists else 0
                    
                    if old_exists:
                        tables_to_drop.append(old_table)
                        status = "⚠️" if old_count > 0 else "🔵"
                        analysis_text += f"{status} `{old_table}` ({old_count}) → `{new_table}` ({new_count})\n"
                    else:
                        analysis_text += f"✅ `{old_table}` (غير موجود)\n"
                
                bot.send_message(admin_id, analysis_text, parse_mode="Markdown")
                
                if not tables_to_drop:
                    bot.send_message(
                        admin_id,
                        "✅ *قاعدة البيانات نظيفة!*\n"
                        "لا توجد جداول قديمة للحذف.",
                        parse_mode="Markdown"
                    )
                    cursor.close()
                    conn.close()
                    return
                
                # حذف الجداول القديمة
                deleted_count = 0
                for table in tables_to_drop:
                    try:
                        cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
                        deleted_count += 1
                        logger.info(f"✅ Dropped table: {table}")
                    except Exception as e:
                        logger.error(f"Error dropping {table}: {e}")
                
                conn.commit()
                
                # تنظيف sequences
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
                    except:
                        pass
                
                conn.commit()
                
                # النتيجة النهائية
                bot.send_message(
                    admin_id,
                    f"✅ *اكتمل الترحيل بنجاح!*\n\n"
                    f"🗑️ تم حذف {deleted_count} جدول قديم\n"
                    f"🧹 تم تنظيف الـ sequences",
                    parse_mode="Markdown"
                )
                
                cursor.close()
                conn.close()
                logger.info("🎉 Database migration completed!")
                
            except Exception as e:
                logger.error(f"Migration error: {e}", exc_info=True)
                try:
                    bot.send_message(
                        admin_id,
                        f"❌ حدث خطأ أثناء الترحيل:\n`{str(e)}`",
                        parse_mode="Markdown"
                    )
                except:
                    pass
        
        # تشغيل في thread منفصل
        thread = threading.Thread(target=migrate_background, daemon=True)
        thread.start()
        
        return jsonify({
            "status": "success",
            "message": "Database migration started. You will receive a Telegram message with results."
        })
        
    except Exception as e:
        logger.error(f"Admin migrate database error: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
@app.route("/webhook_info", methods=["GET"])
def webhook_info():
    try:
        info = bot.get_webhook_info()
        return jsonify({
            "url": info.url,
            "pending_updates": info.pending_update_count,
            "last_error": info.last_error_message,
            "max_connections": info.max_connections
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/thumbnail/<file_id>", methods=["GET"])
def get_thumbnail(file_id):
    """
    مسار لعرض الصور المصغرة.
    يقوم بإعادة توجيه الطلب إلى رابط تيليجرام المباشر.
    """
    try:
        # جلب مسار الملف من تيليجرام
        file_info = bot.get_file(file_id)
        
        # رابط الملف المباشر (صالح لمدة ساعة)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        
        # إعادة توجيه (302)
        from flask import redirect
        return redirect(file_url)
        
    except Exception as e:
        logger.error(f"Thumbnail fetch error for {file_id}: {e}")
        # صورة افتراضية أو خطأ 404
        abort(404)

# --- تهيئة البوت ---
def init_bot():
    logger.info("🤖 Initializing bot...")
    
    try:
        # فحص قاعدة البيانات
        verify_and_repair_schema()
        logger.info("✅ Database schema OK")
    except Exception as e:
        logger.error(f"❌ Database error: {e}")
        return False
    
    try:
        # تسجيل معالجات البوت
        register_all_handlers(bot, CHANNEL_ID, ADMIN_IDS)
        logger.info("✅ Bot handlers registered")
    except Exception as e:
        logger.error(f"❌ Handlers error: {e}")
        return False
    
    try:
        # إعداد webhook
        webhook_url = f"{APP_URL}/bot{BOT_TOKEN}"
        bot.remove_webhook()
        
        webhook_params = {
            'url': webhook_url,
            'max_connections': 40,
            'drop_pending_updates': True,
            'allowed_updates': ["message", "callback_query", "inline_query"]
        }
        
        # إضافة secret_token فقط إذا تم تعيينه بشكل مخصص
        # ملاحظة: لا نضيفه إذا كان القيمة الافتراضية لتجنب مشاكل التوافق
        if WEBHOOK_SECRET and WEBHOOK_SECRET != "default_secret":
            webhook_params['secret_token'] = WEBHOOK_SECRET
            logger.info("🔐 Webhook secret token configured")
        else:
            logger.warning("⚠️ Using webhook without secret token (less secure)")
        
        result = bot.set_webhook(**webhook_params)
        
        if result:
            logger.info(f"✅ Webhook set: {webhook_url}")
        else:
            logger.warning("⚠️ Webhook setup failed")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        # لا نتوقف هنا، سيتم إعداده لاحقاً
    
    logger.info("🚀 Bot initialization completed!")
    
    # بدء تنظيف السجل
    start_history_cleanup()
    
    return True

# --- تشغيل التطبيق ---
if __name__ == "__main__":
    logger.info("🔥 Starting Video Bot Webhook Server...")
    
    if not init_bot():
        logger.critical("💥 Bot initialization failed")
        exit(1)
    
    # تشغيل Flask على المنفذ الصحيح لـ Render
    logger.info(f"🌐 Starting Flask server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
