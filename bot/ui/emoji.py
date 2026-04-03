#!/usr/bin/env python3
# ==============================================================================
# ملف: bot/ui/emoji.py
# الوصف: نظام Emoji موحد لضمان تناسق التصميم
# ==============================================================================


class Emoji:
    """نظام Emoji موحد ومهيكل"""

    # --- Navigation ---
    BACK = "🔙"
    NEXT = "▶️"
    PREV = "◀️"
    HOME = "🏠"
    UP = "⬆️"

    # --- Status ---
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    INFO = "ℹ️"
    LOADING = "⏳"
    FIRE = "🔥"
    SPARKLE = "✨"

    # --- Content ---
    VIDEO = "🎬"
    FOLDER = "📂"
    SUBFOLDER = "📁"
    SEARCH = "🔍"
    STAR = "⭐"
    HEART = "❤️"
    CLOCK = "🕐"
    TROPHY = "🏆"
    DICE = "🎲"
    POPCORN = "🍿"

    # --- Users ---
    USER = "👤"
    USERS = "👥"
    ADMIN = "🛡️"
    BOT = "🤖"

    # --- Actions ---
    ADD = "➕"
    REMOVE = "➖"
    DELETE = "🗑️"
    EDIT = "✏️"
    MOVE = "➡️"
    BROADCAST = "📢"
    STATS = "📊"
    SETTINGS = "⚙️"
    REFRESH = "🔄"
    COMMENT = "💬"
    CHANNELS = "📋"

    # --- Rating Stars ---
    STARS = {
        1: "⭐",
        2: "⭐⭐",
        3: "⭐⭐⭐",
        4: "⭐⭐⭐⭐",
        5: "⭐⭐⭐⭐⭐"
    }

    # --- Separators ---
    LINE = "━━━━━━━━━━━━━━━━━━━━"
    LINE_SHORT = "━━━━━━━━━"
    DOT = "•"

    @staticmethod
    def rating_bar(rating: float, max_rating: int = 5) -> str:
        """شريط تقييم مرئي"""
        filled = round(rating)
        return "★" * filled + "☆" * (max_rating - filled)

    @staticmethod
    def number_emoji(num: int) -> str:
        """تحويل رقم إلى Emoji رقمي"""
        emojis = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        return emojis[num] if 0 <= num <= 10 else str(num)
