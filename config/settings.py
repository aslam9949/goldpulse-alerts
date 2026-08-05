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

# India markup applied to every INR price path: customs duty + GST +
# dealer premium. This is an ACTIVE decision — recalibrate against real
# MCX/retail rates when India's budget changes duties.
# Last checked: 2026-08-05 (spot ~$4,157/oz vs ~₹14.2-14.6k/gram retail ≈ 12-15%)
INDIA_MARKUP: float = _float("INDIA_MARKUP", 1.15)

# ── Alert Thresholds ─────────────────────────────────────────────────
# Items scoring below this are stored but NOT pushed as instant alerts.
# Range: 1–10. Higher = stricter (fewer but higher-quality alerts).
# 6.0 = high-signal gold news only (no noise, but catches important moves)
# Lower to 5 for more alerts, raise to 7+ for ultra-strict.
ALERT_THRESHOLD: float = _float("ALERT_THRESHOLD", 6.0)
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

# ── Geopolitical / World News Feeds ─────────────────────────────────
# General world-news feeds that cover shocks (wars, sanctions, central
# bank moves, rate decisions) WITHOUT requiring "gold" in the query.
# Articles land with source_category="geopolitical" and are scored with a
# different rubric (see processing/relevance.py) — a headline like
# "Israel strikes Iranian nuclear sites" moves gold as a safe haven even
# though it never mentions the metal.
GEOPOLITICAL_RSS_FEEDS: list[tuple[str, str, int]] = [
    (
        "Reuters World",
        "https://news.google.com/rss/search?q=site:reuters.com+world&hl=en-IN&gl=IN&ceid=IN:en",
        9,
    ),
    (
        "AP World News",
        "https://news.google.com/rss/search?q=site:apnews.com+world&hl=en-IN&gl=IN&ceid=IN:en",
        8,
    ),
    (
        "Google News (Geopolitics)",
        "https://news.google.com/rss/search?q=war+OR+sanctions+OR+invasion+OR+ceasefire&hl=en-IN&gl=IN&ceid=IN:en",
        6,
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

# GDELT queries for geopolitical/macro shocks that move gold WITHOUT the
# word "gold" in the query. These get their own ingestion track and are
# tagged source_category="geopolitical" so the scorer can route them
# through the impact-based rubric instead of the title-keyword gate.
GDELT_GEOPOLITICAL_QUERIES: list[str] = [
    "war",
    "military conflict",
    "sanctions",
    "central bank policy",
    "interest rate decision",
    "nuclear",
    "invasion",
    "ceasefire",
    "oil embargo",
]

# ── Cooldown ─────────────────────────────────────────────────────────
# Minimum minutes between alerts for the same topic cluster
ALERT_COOLDOWN_MINUTES: int = _int("ALERT_COOLDOWN_MINUTES", 30)

# ── Gold Keyword Concepts (used for relevance scoring) ───────────────
# Grouped into CONCEPT buckets. Each bucket counts at most ONE hit per
# article, so overlapping phrases ("gold", "gold price", "gold rally")
# can't stack into a score driven by the author repeating the same idea.
# A title that hits multiple DISTINCT buckets is genuinely multi-faceted.
GOLD_KEYWORD_CONCEPTS: dict[str, list[str]] = {
    "gold_general": [
        "gold", "xau", "bullion", "troy ounce", "spot gold",
        "comex gold", "lbma", "paper gold", "gold futures",
    ],
    "gold_price_action": [
        "gold price", "gold rally", "gold surges", "gold falls",
        "gold rises", "gold drops", "gold hits", "gold breaches",
        "gold plunge", "gold climbs", "gold gains", "gold extends",
        "gold slides", "gold tumbles", "gold jumps",
    ],
    "gold_flows": [
        "gold etf", "spdr gold", "gld etf", "gold buying",
        "gold selling", "gold demand", "gold supply", "gold import",
        "gold export", "gold reserves", "central bank gold",
    ],
    "gold_india_market": [
        "mcx gold", "gold mcx", "gold rate", "gold rate india",
        "gold import duty", "sgb", "sovereign gold bond",
        "digital gold", "gold coins", "gold bars",
    ],
    "gold_mining": [
        "gold mining", "gold mine", "gold miner",
    ],
}

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
