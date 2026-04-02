# 🎬 Video Bot - Telegram Video Archive Bot

## 📋 Overview
A powerful Telegram bot for managing and organizing video content with webhook support for production deployment.

## 🚀 Deployment (Render.com)

### Required Environment Variables
```env
BOT_TOKEN=your_telegram_bot_token
DATABASE_URL=postgresql://user:password@host:port/database
CHANNEL_ID=-1001234567890
ADMIN_IDS=123456789,987654321
APP_URL=https://your-app-name.onrender.com
# or alternatively:
BASE_URL=https://your-app-name.onrender.com
```

### Render Settings
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `python webhook_bot.py`
- **Runtime**: Python 3.11.9 (specified in runtime.txt)

## 🔧 Features
- ✅ **Webhook Mode**: Fast, reliable webhook-based operation
- ✅ **Connection Pooling**: Optimized PostgreSQL connections
- ✅ **Auto-Indexing**: Performance indexes created at startup
- ✅ **Health Endpoints**: `/`, `/live`, `/ready` for monitoring
- ✅ **User Management**: Favorites, history, ratings
- ✅ **Category System**: Hierarchical video organization
- ✅ **Search**: Advanced text search with filters
- ✅ **Admin Panel**: Channel management and statistics

## 📡 Webhook Endpoints
- `GET /` - Health check
- `GET /live` - Liveness probe
- `GET /ready` - Readiness probe
- `POST /bot{TOKEN}` - Telegram webhook
- `GET|POST /set_webhook` - Setup webhook
- `GET /webhook_info` - Webhook status

## 🗄️ Database
Uses PostgreSQL with auto-migration and schema bootstrapping:
- **videoarchive**: Main video storage
- **categories**: Video categorization
- **botusers**: User management
- **userfavorites**: User favorites
- **userhistory**: View history
- **videoratings**: User ratings
- **botsettings**: Bot configuration
- **requiredchannels**: Subscription requirements

## 🔄 Setup Process
1. Set environment variables in Render dashboard
2. Deploy with webhook_bot.py as start command
3. Visit `/set_webhook` to activate webhook
4. Bot is ready!

## 📁 File Structure
```
├── webhook_bot.py          # Main webhook server (PRODUCTION)
├── db_manager.py          # Database operations
├── db_pool.py            # Connection pooling
├── handlers/             # Bot message handlers
├── state_manager.py      # User state management
├── utils.py             # Utility functions
├── requirements.txt     # Dependencies
├── runtime.txt         # Python version
└── legacy/             # Old files (reference only)
    ├── bot.py         # Original polling mode
    └── keep_alive.py  # Not needed for webhook
```

## ⚠️ Important Notes
- **Use webhook_bot.py only** for production deployment
- Files in `legacy/` are for reference and not used in webhook mode
- PostgreSQL indexes are created automatically at startup
- Connection pooling improves performance under load

## 🛠️ Development
For local development with polling mode, see files in `legacy/` directory.
For production, always use webhook mode with `webhook_bot.py`.

---
*Version: 2.0.0 - Webhook Mode*
