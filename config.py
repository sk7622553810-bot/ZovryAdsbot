import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
ENV_TXT_FILE = PROJECT_ROOT / ".env.txt"
load_dotenv(dotenv_path=ENV_FILE, override=False)


def _int_env(name, default=0):
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _bool_env(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


# Telegram API credentials
API_ID = _int_env("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
LOGGER_BOT_TOKEN = os.getenv("LOGGER_BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Zovrybot").lstrip("@")
BOT_NAME = os.getenv("BOT_NAME", "ZovryAdsBot 🚀")
LOGGER_BOT_USERNAME = os.getenv("LOGGER_BOT_USERNAME", "Zovry2bot").lstrip("@")

# Admin settings
ADMIN_ID = _int_env("ADMIN_ID")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "ZovryChat").lstrip("@")
ADMIN_IDS = [
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip()
]
if ADMIN_ID > 0 and ADMIN_ID not in ADMIN_IDS:
    ADMIN_IDS.insert(0, ADMIN_ID)

# Image URLs
START_IMAGE = os.getenv("START_IMAGE", "https://files.catbox.moe/8jbbws.png")
BROADCAST_IMAGE = os.getenv("BROADCAST_IMAGE", START_IMAGE)
FORCE_JOIN_IMAGE = os.getenv("FORCE_JOIN_IMAGE", "https://files.catbox.moe/w2gc1t.png")

# Force-join settings
ENABLE_FORCE_JOIN = _bool_env("ENABLE_FORCE_JOIN", True)
MUST_JOIN_CHANNEL_ID = os.getenv("MUST_JOIN_CHANNEL_ID", "@ZovryAds")
MUSTJOIN_GROUP_ID = os.getenv("MUSTJOIN_GROUP_ID", "@ZovryChat")
MUST_JOIN_CHANNEL_URL = os.getenv("MUST_JOIN_CHANNEL_URL", "https://t.me/ZovryAds")
MUSTJOIN_GROUP_URL = os.getenv("MUSTJOIN_GROUP_URL", "https://t.me/ZovryChat")

# Operational Telegram chats
SETUP_GROUP_ID = _int_env("SETUP_GROUP_ID", -1003922143776)
TECH_LOG_CHANNEL_ID = _int_env("TECH_LOG_CHANNEL_ID", -1002945751584)
GROUP_ID = _int_env("GROUP_ID", -1003922143776)

# External links
PRIVACY_POLICY_URL = os.getenv("PRIVACY_POLICY_URL", "https://t.me/ZovryOrg")
SUPPORT_GROUP_URL = os.getenv("SUPPORT_GROUP_URL", "https://t.me/ZovryChat")
UPDATES_CHANNEL_URL = os.getenv("UPDATES_CHANNEL_URL", "https://t.me/ZovryAds")
ABOUT_BOT_URL = os.getenv("ABOUT_BOT_URL", "https://t.me/ZovryAds")
POWERED_BY_URL = os.getenv("POWERED_BY_URL", "https://t.me/ZovryOrg")
GUIDE_URL = os.getenv("GUIDE_URL", "https://t.me/ZovryAds")
PRIVATE_CHANNEL_INVITE = os.getenv("PRIVATE_CHANNEL_INVITE", "https://t.me/ZovryAds")

# Encryption and database configuration
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")
MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME = os.getenv("DB_NAME", "adsbot_db")

# Broadcast settings
DEFAULT_DELAY = 600
MIN_DELAY = 5
MAX_DELAY = 86400

# OTP settings
OTP_LENGTH = 5
OTP_EXPIRY = 300

# Logging configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "logs/ZovryAds.log")

# Feature toggles
ENABLE_OTP_VERIFICATION = _bool_env("ENABLE_OTP_VERIFICATION", True)
ENABLE_BROADCASTING = _bool_env("ENABLE_BROADCASTING", True)
ENABLE_ANALYTICS = _bool_env("ENABLE_ANALYTICS", True)

SUCCESS_MESSAGES = {
    "account_added": "Account added successfully!",
    "otp_sent": "OTP sent to your phone number!",
    "broadcast_started": "Broadcast started successfully!",
    "broadcast_completed": "Broadcast completed successfully!",
    "accounts_deleted": "All accounts deleted successfully!",
}

ERROR_MESSAGES = {
    "account_limit": "You've reached your account limit of 5! Get higher limit contact @ZovryChat",
    "invalid_phone": "Invalid phone number format! Use +1234567890",
    "otp_expired": "OTP has expired. Please restart hosting.",
    "invalid_otp": "Invalid OTP. Please try again.",
    "login_failed": "Failed to login to Telegram account!",
    "no_groups": "No groups found in your account!",
    "no_messages": "No messages found in Saved Messages!",
    "broadcast_limit": "Daily broadcast limit reached! Get higher limit contact @ZovryChat",
    "unauthorized": "You are not authorized to perform this action!",
    "force_join_required": "Join required channels to access this feature!",
}

SESSION_STORAGE_PATH = os.getenv("SESSION_STORAGE_PATH", "sessions/")


def validate_runtime_configuration():
    missing = []
    if API_ID <= 0:
        missing.append("TELEGRAM_API_ID")
    required_values = (
        ("TELEGRAM_API_HASH", API_HASH),
        ("TELEGRAM_BOT_TOKEN", BOT_TOKEN),
        ("LOGGER_BOT_TOKEN", LOGGER_BOT_TOKEN),
        ("MONGO_URI", MONGO_URI),
    )
    for name, value in required_values:
        if not value:
            missing.append(name)
    if not ADMIN_IDS:
        missing.append("ADMIN_ID or ADMIN_IDS")
    if missing:
        message = (
            "Missing required environment variables: "
            + ", ".join(missing)
            + f". Configure them in the process environment or {ENV_FILE}."
        )
        if not ENV_FILE.exists() and ENV_TXT_FILE.exists():
            message += f" A file named {ENV_TXT_FILE} was found; rename it to .env."
        raise RuntimeError(message)
