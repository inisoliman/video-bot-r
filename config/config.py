
import os
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)

class Config:
    # --- Database Configuration ---
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        logger.critical("❌ FATAL: DATABASE_URL environment variable is not set.")
        exit(1)
    
    try:
        result = urlparse(DATABASE_URL)
        DB_CONFIG = {
            'user': result.username,
            'password': result.password,
            'host': result.hostname,
            'port': result.port,
            'dbname': result.path[1:]
        }
    except Exception as e:
        logger.critical(f"❌ FATAL: Could not parse DATABASE_URL. Error: {e}")
        exit(1)

    DB_POOL_MIN = int(os.getenv('DB_POOL_MIN', '2'))
    DB_POOL_MAX = int(os.getenv('DB_POOL_MAX', '20'))

    # --- Bot Configuration ---
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        logger.critical("❌ FATAL: BOT_TOKEN environment variable is not set.")
        exit(1)

    CHANNEL_ID = os.getenv("CHANNEL_ID")
    if not CHANNEL_ID:
        logger.critical("❌ FATAL: CHANNEL_ID environment variable is not set.")
        exit(1)
    
    try:
        CHANNEL_ID = int(CHANNEL_ID)
    except ValueError:
        logger.critical("❌ FATAL: CHANNEL_ID must be an integer.")
        exit(1)

    ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
    ADMIN_IDS = []
    if ADMIN_IDS_STR:
        try:
            ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]
        except ValueError as e:
            logger.critical(f"❌ FATAL: ADMIN_IDS format error: {e}. Must be comma-separated integers.")
            exit(1)

    # --- Webhook Configuration ---
    APP_URL = os.getenv("APP_URL")
    if not APP_URL:
        logger.critical("❌ FATAL: APP_URL environment variable is not set.")
        exit(1)
    if not APP_URL.startswith('https://'):
        logger.critical("❌ FATAL: APP_URL must use HTTPS for security!")
        exit(1)

    PORT = int(os.getenv("PORT", "10000"))
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "default_secret")

    # --- Bot Specific Settings ---
    VIDEOS_PER_PAGE = int(os.getenv('VIDEOS_PER_PAGE', '10'))
    CALLBACK_DELIMITER = "::"
    
    # --- Feature Flags ---
    ENABLE_COMMENTS = os.getenv('ENABLE_COMMENTS', 'True').lower() == 'true'
    ENABLE_RATINGS = os.getenv('ENABLE_RATINGS', 'True').lower() == 'true'
    ENABLE_FAVORITES = os.getenv('ENABLE_FAVORITES', 'True').lower() == 'true'
    ENABLE_HISTORY = os.getenv('ENABLE_HISTORY', 'True').lower() == 'true'

    # --- Cache Settings ---
    SEARCH_CACHE_TTL = int(os.getenv('SEARCH_CACHE_TTL', '60')) # seconds

    # --- Logging Configuration ---
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    @staticmethod
    def validate_config():
        # This method can be expanded to perform more comprehensive checks
        # For now, critical checks are done during variable loading
        logger.info("✅ Configuration loaded and validated.")

# Initialize logging based on config
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format=Config.LOG_FORMAT
)

# Ensure config is validated on import
Config.validate_config()
