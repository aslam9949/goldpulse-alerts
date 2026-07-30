"""
GoldPulse Alerts — Configuration
=================================
All settings are loaded from environment variables with sensible defaults.
Uses python-dotenv to load from .env file in project root.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (where main.py lives)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _int(name: str, default: int) -> int:
    """Safely parse an int from env, falling back to default."""
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _float(name: str, default: float) -> float:
    """Safely parse a float from env, falling back to default."""
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _validate_range(name: str, value: int | float, min_val: int | float, max_val: int | float) -> None:
    """Raise ValueError if value is outside the allowed range."""
    if not (min_val <= value <= max_val):
        raise ValueError(
            f"{name} must be between {min_val} and {max_val}, got {value}"
        )


# ── Telegram ──────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# Admin chat IDs (comma-separated) — used for /admin commands
_admin_raw = os.getenv("ADMIN_CHAT_IDS", "")
ADMIN_CHAT_IDS: list[str] = [x.strip() for x in _admin_raw.split(",") if x.strip()]

# ── Gold Price ────────────────────────────────────────────────────────
# "yfinance" (free, no key) or "goldapi" (needs GOLDAPI_KEY)
GOLD_PRICE_SOURCE: str = os.getenv("GOLD_PRICE_SOURCE", "yfinance")
GOLDAPI_KEY: str = os.getenv("GOLDAPI_KEY", "")

# ── Alert Thresholds ─────────────────────────────────────────────────
# Items scoring below this are stored but NOT pushed as instant alerts.
# Range: 1–10. Higher = stricter (fewer but higher-quality alerts).
# 7.5 = only high-signal gold news triggers instant alerts.
# Lower to 5-6 if you want more alerts (more noise).
ALERT_THRESHOLD: float = _float("ALERT_THRESHOLD", 7.5)
_validate_range("ALERT_THRESHOLD", ALERT_THRESHOLD, 1, 10)

# ── Digest Schedule (IST) ────────────────────────────────────────────
MORNING_DIGEST_HOUR: int = _int("MORNING_DIGEST_HOUR", 8)
_validate_range("MORNING_DIGEST_HOUR", MORNING_DIGEST_HOUR, 0, 23)
EVENING_DIGEST_HOUR: int = _int("EVENING_DIGEST_HOUR", 20)
_validate_range("EVENING_DIGEST_HOUR", EVENING_DIGEST_HOUR, 0, 23)

# ── Poll Intervals (minutes) ─────────────────────────────────────────
RSS_POLL_INTERVAL_MINUTES: int = _int("RSS_POLL_INTERVAL_MINUTES", 15)
_validate_range("RSS_POLL_INTERVAL_MINUTES", RSS_POLL_INTERVAL_MINUTES, 1, 1440)
CALENDAR_POLL_INTERVAL_MINUTES: int = _int("CALENDAR_POLL_INTERVAL_MINUTES", 30)
_validate_range("CALENDAR_POLL_INTERVAL_MINUTES", CALENDAR_POLL_INTERVAL_MINUTES, 1, 1440)
PRICE_REFRESH_MINUTES: int = _int("PRICE_REFRESH_MINUTES", 5)
_validate_range("PRICE_REFRESH_MINUTES", PRICE_REFRESH_MINUTES, 1, 1440)

# ── Calendar Look-ahead (days) ────────────────────────────────────────
# How far ahead to look for economic events (default: 14 days)
# This ensures FOMC meetings, NFP, CPI, etc. are captured even if 7-14 days away
CALENDAR_LOOKAHEAD_DAYS: int = _int("CALENDAR_LOOKAHEAD_DAYS", 14)

# ── Logging ──────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR: Path = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── Database ─────────────────────────────────────────────────────────
DB_PATH: Path = PROJECT_ROOT / "data" / "goldpulse.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── RSS Feed Sources ─────────────────────────────────────────────────
# Each entry: (name, url, trust_score)
# trust_score: 1-10, higher = more trusted for gold content
RSS_FEEDS: list[tuple[str, str, int]] = [
    (
        "Google News (Gold)",
        "https://news.google.com/rss/search?q=gold+price+OR+XAU+OR+gold+market&hl=en-IN&gl=IN&ceid=IN:en",
        6,
    ),
    (
        "Mining.com",
        "https://www.mining.com/feed/",
        8,
    ),
    (
        "GoldSeiten English",
        "https://www.goldseiten.de/rss/news_english.xml",
        8,
    ),
    (
        "Kitco Gold News",
        "https://news.google.com/rss/search?q=site:kitco.com+gold&hl=en-IN&gl=IN&ceid=IN:en",
        8,
    ),
    (
        "Reuters Commodities",
        "https://news.google.com/rss/search?q=site:reuters.com+gold+commodity&hl=en-IN&gl=IN&ceid=IN:en",
        9,
    ),
    (
        "Gold.org News",
        "https://news.google.com/rss/search?q=site:gold.org+gold&hl=en-IN&gl=IN&ceid=IN:en",
        9,
    ),
    (
        "Moneycontrol Gold",
        "https://news.google.com/rss/search?q=site:moneycontrol.com+gold&hl=en-IN&gl=IN&ceid=IN:en",
        7,
    ),
]

# ── GDELT Settings ───────────────────────────────────────────────────
GDELT_BASE_URL: str = "https://api.gdeltproject.org/api/v2/doc/doc"
# GDELT queries for gold-relevant geopolitical events
GDELT_QUERIES: list[str] = [
    "gold",
    "gold price",
    "central bank gold",
    "gold reserves",
    "safe haven gold",
]

# ── Cooldown ─────────────────────────────────────────────────────────
# Minimum minutes between alerts for the same topic cluster
ALERT_COOLDOWN_MINUTES: int = _int("ALERT_COOLDOWN_MINUTES", 30)

# ── Gold Keywords (used for relevance scoring) ───────────────────────
GOLD_KEYWORDS_PRIMARY: list[str] = [
    "gold", "xau", "gold price", "gold futures", "gold etf",
    "gold mining", "bullion", "gold bars", "gold coins",
    "gold reserves", "gold buying", "gold selling",
    "gold demand", "gold supply", "gold import", "gold export",
    "gold rate", "gold market", "gold rally", "gold plunge",
    "gold surges", "gold falls", "gold rises", "gold drops",
    "gold hits", "gold breaches", "gold ounce", "troy ounce",
    "comex gold", "lbma", "spot gold", "paper gold",
    "digital gold", "sovereign gold bond", "sgb",
    "spdr gold", "gld etf",
    "mcx gold", "gold mcx",
]

GOLD_KEYWORDS_SECONDARY: list[str] = [
    "safe haven", "inflation hedge", "central bank",
    "federal reserve", "fed rate", "interest rate",
    "us dollar", "dollar index", "dxy",
    "treasury yield", "bond yield",
    "geopolitical", "war", "conflict", "sanctions",
    "recession", "economic uncertainty",
    "quantitative easing", "qe", "taper",
    "goldilocks", "de-dollarization",
    "brics gold", "rbi gold", "reserve bank gold",
]

# India/MCX-specific keywords — extra boost for Indian traders
INDIA_KEYWORDS: list[str] = [
    "india", "indian", "mumbai", "delhi",
    "mcx", "ncdex", "sebi",
    "rbi", "reserve bank of india",
    "rupee", "inr", "indian rupee",
    "gold import india", "gold import duty",
    "gold price india", "gold rate india",
    "akshaya tritiy", "dhanteras", "diwali gold",
    "ibja", "india bullion",
    "gst gold", "gold gst",
]
