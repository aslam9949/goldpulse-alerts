# GoldPulse Alerts — Complete System Architecture

> **Version:** 1.0
> **Last Updated:** July 29, 2026
> **Runtime:** Python 3.11+ on Ubuntu VPS (systemd service)
> **Bot Token:** your_bot_token_here (see .env)
> **Chat ID:** your_chat_id_here (see .env)

---

## Table of Contents

1. [Overview](#overview)
2. [Directory Structure](#directory-structure)
3. [Architecture Diagram](#architecture-diagram)
4. [Module Details](#module-details)
5. [Data Flow](#data-flow)
6. [Database Schema](#database-schema)
7. [Configuration](#configuration)
8. [Scheduled Jobs](#scheduled-jobs)
9. [Bot Commands](#bot-commands)
10. [Alert Pipeline](#alert-pipeline)
11. [Price Fetching](#price-fetching)
12. [Calendar System](#calendar-system)
13. [Relevance Scoring](#relevance-scoring)
14. [Deduplication](#deduplication)
15. [Message Formatting](#message-formatting)
16. [Error Handling](#error-handling)
17. [Deployment](#deployment)

---

## Overview

GoldPulse Alerts is a **24/7 Telegram bot** for gold traders. It monitors news, economic events, and gold prices to deliver real-time intelligence.

**Key Features:**
- Real-time gold news alerts from 7+ RSS sources + GDELT
- US economic calendar tracking (FOMC, NFP, CPI, GDP)
- Gold price updates (USD + MCX/INR with India import duty markup)
- Morning/evening digest summaries
- Inline keyboard menu system
- Smart relevance scoring (1-10 scale)
- Fuzzy deduplication to avoid repeated alerts

**Tech Stack:**
- **Language:** Python 3.11+
- **Bot Framework:** aiogram 3.x (async)
- **Scheduler:** APScheduler (AsyncIOScheduler)
- **Database:** SQLite with WAL mode
- **HTTP:** aiohttp (async)
- **Parsing:** feedparser (RSS), rapidfuzz (fuzzy matching)

---

## Directory Structure

```
/root/goldpulse-alerts/
├── main.py                          # Entry point, app lifecycle
├── config/
│   └── settings.py                  # All configuration from .env
├── storage/
│   └── database.py                  # SQLite database wrapper
├── ingestion/
│   ├── price_fetcher.py             # Gold price (multi-source)
│   ├── rss_fetcher.py               # RSS feed parser
│   ├── gdelt_fetcher.py             # GDELT news API
│   └── calendar_fetcher.py          # Forex Factory calendar
├── processing/
│   ├── relevance.py                 # Gold relevance scoring
│   └── dedup.py                     # URL + fuzzy title dedup
├── alerts/
│   ├── news_alerts.py               # News alert pipeline
│   ├── calendar_alerts.py           # Calendar event alerts
│   └── digest.py                    # Morning/evening digests
├── bot/
│   ├── handlers.py                  # Telegram command handlers
│   └── formatter.py                 # Message formatting
├── utils/
│   └── logger.py                    # Logging setup
├── data/
│   └── goldpulse.db                 # SQLite database
├── logs/
│   ├── goldpulse.log                # Main log (5MB rotation)
│   └── goldpulse_errors.log         # Error log (2MB rotation)
├── .env                             # Environment variables
├── .dockerignore                    # Docker exclusions
├── Dockerfile                       # Docker build
├── docker-compose.yml               # Docker compose
├── requirements.txt                 # Python dependencies
├── COMMANDS.md                      # Quick command reference
└── GOLDPULSE_ARCHITECTURE.md        # This file
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Telegram API                              │
│                    (Long Polling via aiogram)                    │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     GoldPulseBot (handlers.py)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ /start   │ │ /menu    │ │ /price   │ │ /latest  │           │
│  │ /help    │ │ /digest  │ │/upcoming │ │/settings │           │
│  │ /health  │ │          │ │          │ │          │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
│  ┌──────────────────────────────────────────────────────┐       │
│  │ InlineKeyboard: Menu, Read Article, Back, Close      │       │
│  └──────────────────────────────────────────────────────┘       │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Alert Engines (alerts/)                        │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │
│  │  NewsAlertEngine │ │CalendarAlertEng.│ │  DigestEngine   │   │
│  │                  │ │                  │ │                  │   │
│  │ fetch→dedup→     │ │ sync→pre-alert→ │ │ morning→evening │   │
│  │ score→store→alert│ │ post-alert      │ │ digest          │   │
│  └────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘   │
└───────────┼─────────────────────┼─────────────────────┼───────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Processing (processing/)                       │
│  ┌─────────────────────────┐ ┌─────────────────────────┐        │
│  │    relevance.py         │ │      dedup.py           │        │
│  │                         │ │                         │        │
│  │ • Keyword matching      │ │ • URL hash (SHA-256)    │        │
│  │ • Source trust score    │ │ • Fuzzy title (92%)     │        │
│  │ • India/MCX boost       │ │ • rapidfuzz library     │        │
│  │ • Recency boost         │ │                         │        │
│  │ • 5-factor scoring      │ │                         │        │
│  └─────────────────────────┘ └─────────────────────────┘        │
└─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Ingestion (ingestion/)                         │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────┐│
│  │price_fetcher │ │ rss_fetcher  │ │gdelt_fetcher │ │calendar_ ││
│  │              │ │              │ │              │ │fetcher   ││
│  │ • India gold │ │ • 7 RSS feeds│ │ • 5 queries  │ │ • Forex  ││
│  │ • Yahoo Fin. │ │ • feedparser │ │ • Doc API    │ │ Factory  ││
│  │ • GoldAPI    │ │ • Async HTTP │ │ • Staggered  │ │ • 14 days││
│  │ • Google Fin │ │              │ │              │ │          ││
│  │ • ExchangeRT │ │              │ │              │ │          ││
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────┘│
└─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Storage (storage/)                             │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    database.py                           │    │
│  │                                                         │    │
│  │  Tables: articles, events, seen_urls, seen_titles,     │    │
│  │          user_settings                                  │    │
│  │                                                         │    │
│  │  Mode: WAL (Write-Ahead Logging)                        │    │
│  │  Thread-safe: threading.Lock for writes                 │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Module Details

### 1. main.py — Application Lifecycle

**Class:** `GoldPulseApp`

**Lifecycle:**
1. `init()` — Create all objects (database, fetchers, bot, engines, scheduler)
2. `start()` — Start scheduler, run initial fetch, begin bot polling
3. `shutdown()` — Graceful shutdown of all components

**Signal Handling:**
- SIGINT/SIGTERM → Sets `shutdown_event` → Clean shutdown
- Single shutdown execution (no double-close race)

**Key Components Initialized:**
```python
self.db = Database()
self.price_fetcher = PriceFetcher()
self.bot = GoldPulseBot(db=self.db, price_fetcher=self.price_fetcher)
self.news_engine = NewsAlertEngine(db, price_fetcher, bot)
self.calendar_engine = CalendarAlertEngine(db, price_fetcher, bot)
self.digest_engine = DigestEngine(db, price_fetcher, bot)
self.scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
```

---

### 2. config/settings.py — Configuration

All configuration loaded from `.env` with defaults:

```python
# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ADMIN_CHAT_IDS = [x.strip() for x in os.getenv("ADMIN_CHAT_IDS", "").split(",") if x.strip()]

# Alert Threshold (1-10)
ALERT_THRESHOLD = _int("ALERT_THRESHOLD", 5)  # Validated: 1-10

# Digest Schedule (IST)
MORNING_DIGEST_HOUR = _int("MORNING_DIGEST_HOUR", 8)  # Validated: 0-23
EVENING_DIGEST_HOUR = _int("EVENING_DIGEST_HOUR", 20)  # Validated: 0-23

# Poll Intervals (minutes)
RSS_POLL_INTERVAL_MINUTES = _int("RSS_POLL_INTERVAL_MINUTES", 15)  # Min: 1
CALENDAR_POLL_INTERVAL_MINUTES = _int("CALENDAR_POLL_INTERVAL_MINUTES", 30)
PRICE_REFRESH_MINUTES = _int("PRICE_REFRESH_MINUTES", 5)

# Calendar Look-ahead (days)
CALENDAR_LOOKAHEAD_DAYS = _int("CALENDAR_LOOKAHEAD_DAYS", 14)

# Gold Keywords (for relevance scoring)
GOLD_KEYWORDS_PRIMARY = [
    "gold", "xau", "gold price", "gold market", "gold etf",
    "bullion", "gold futures", "gold reserves", "central bank gold",
    "gold mining", "gold demand", "gold supply", "safe haven",
    "gold etf", "spdr gold", "gld etf",  # Note: "gold etf" appears once now
]

GOLD_KEYWORDS_SECONDARY = [
    "inflation", "interest rate", "federal reserve", "fed", "fomc",
    "dollar", "usd", "treasury", "bond yield", "war", "geopolitical",
    "recession", "economic crisis", "monetary policy", "rate hike",
    "rate cut", "powell", "central bank", "ism", "cpi", "nfp",
]

INDIA_KEYWORDS = [
    "india", "mcx", "inr", "rupee", "indian gold",
    "gold price india", "gold import", "gold duty",
]
```

**Validation:**
- `ALERT_THRESHOLD`: Must be 1-10
- `MORNING_DIGEST_HOUR` / `EVENING_DIGEST_HOUR`: Must be 0-23
- `RSS_POLL_INTERVAL_MINUTES`: Must be >= 1

---

### 3. storage/database.py — Database Layer

**Class:** `Database`

**Connection:**
- SQLite with WAL mode (Write-Ahead Logging)
- `check_same_thread=False` for async access
- `threading.Lock` for write operations
- Auto-reconnect on `None` connection

**Tables:**

```sql
-- Articles (news items)
CREATE TABLE articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE,
    title TEXT,
    summary TEXT,
    source TEXT,
    published_at TEXT,
    relevance_score REAL DEFAULT 0,
    gold_price REAL,
    tags TEXT,
    sent_as_alert INTEGER DEFAULT 0,
    sent_in_digest INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Economic calendar events
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    title TEXT,
    country TEXT,
    currency TEXT,
    impact TEXT,
    event_time TEXT,
    forecast TEXT,
    previous TEXT,
    actual TEXT,
    gold_implication TEXT,
    pre_alert_sent INTEGER DEFAULT 0,
    post_alert_sent INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- URL deduplication (SHA-256 hash)
CREATE TABLE seen_urls (
    url_hash TEXT PRIMARY KEY,
    url TEXT,
    seen_at TEXT DEFAULT (datetime('now'))
);

-- Title deduplication (normalized + fuzzy)
CREATE TABLE seen_titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_normalized TEXT,
    seen_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_seen_titles_at ON seen_titles(seen_at);

-- User settings (future)
CREATE TABLE user_settings (
    chat_id TEXT PRIMARY KEY,
    settings_json TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
```

**Key Methods:**
- `insert_article()` → Returns article ID (or None if duplicate)
- `mark_article_alerted(article_id)` → Sets sent_as_alert=1
- `get_recent_articles(hours, limit, min_score)` → For /latest
- `get_digest_articles(hours, limit)` → For digests
- `get_upcoming_events(hours_ahead)` → For /upcoming
- `check_and_mark_url_seen(url_hash, url)` → Atomic dedup
- `cleanup_old_data(days)` → Removes records older than N days

---

### 4. ingestion/price_fetcher.py — Gold Price Fetching

**Class:** `PriceFetcher`

**Data Class:**
```python
@dataclass
class GoldPrice:
    price_usd: float          # USD per troy ounce
    price_inr: float | None   # INR per 10 grams (with India markup)
    change_usd: float | None  # 24h change
    change_pct: float | None  # 24h change percentage
    source: str               # "India direct", "Yahoo Finance", etc.
    fetched_at: datetime

    def format_usd(self) -> str:  # "$3,325.40/oz"
    def format_inr(self) -> str:  # "₹1,45,558/10g"
    def format_change(self) -> str:  # "📈 +$15.20 (+0.46%)"
```

**Source Priority (fallback chain):**
1. **Indian gold price API** (with 15% import duty markup → `INDIA_MARKUP = 1.15`)
2. **Yahoo Finance direct API** (v8 endpoint)
3. **GoldAPI.io** (requires API key)
4. **Google Finance** (scraping)
5. **ExchangeRate-API** (USD→INR conversion)

**Key Features:**
- Session pooling with `asyncio.Lock`
- 5-minute cache (`PRICE_CACHE_SECONDS = 300`)
- Float validation with bounds checking (100-50000 USD)
- INR rate sanity check (50-150 range)
- Context manager support (`async with PriceFetcher() as f:`)

---

### 5. ingestion/rss_fetcher.py — RSS Feed Parser

**Class:** `RSSFetcher`

**Feeds (7 sources):**
```python
RSS_FEEDS = [
    ("Google News (Gold)", "https://news.google.com/rss/search?q=gold...", 6),
    ("Mining.com", "https://www.mining.com/feed/", 7),
    ("GoldSeiten English", "https://www.goldseiten.de/artikel/...", 7),
    ("Kitco Gold News", "https://www.kitco.com/feed/...", 8),
    ("Reuters Commodities", "https://www.reuters.com/arc/outboundfeeds/...", 9),
    ("Gold.org News", "https://www.gold.org/feed/...", 9),
    ("Moneycontrol Gold", "https://www.moneycontrol.com/rss/...", 7),
]
```

**Processing:**
1. Fetch all feeds concurrently with `asyncio.gather()`
2. Parse with `feedparser`
3. Extract: title, summary, url, published_at
4. Strip HTML tags
5. Filter: only `https://` URLs
6. Assign trust score from feed config

---

### 6. ingestion/gdelt_fetcher.py — GDELT News API

**Class:** `GDELTFetcher`

**Queries (5 staggered):**
```python
GDELT_QUERIES = [
    "gold",
    "gold price",
    "central bank gold",
    "gold reserves",
    "safe haven gold",
]
```

**Features:**
- Staggered requests (2s delay between queries) to avoid 429
- URL validation (https:// only)
- Date parsing with fallback

---

### 7. ingestion/calendar_fetcher.py — Economic Calendar

**Class:** `CalendarFetcher`

**Data Source:** Forex Factory public JSON feed
- `ff_calendar_thisweek.json` — This week's events
- `ff_calendar_nextweek.json` — Next week's events (when available)

**Look-ahead:** 14 days (configurable via `CALENDAR_LOOKAHEAD_DAYS`)

**Gold Impact Events (scored 1-10):**
```python
GOLD_IMPACT_EVENTS = {
    # Tier 1 — Biggest gold movers
    "Non-Farm Employment Change": 10,
    "Non-Farm Payrolls": 10,
    "FOMC Statement": 10,
    "FOMC Rate Decision": 10,
    "Federal Funds Rate": 10,
    "FOMC Press Conference": 9,
    "CPI": 9,
    "Consumer Price Index": 9,
    "Core CPI": 9,
    "PPI": 8,

    # Tier 2 — Significant gold movers
    "GDP": 8,
    "Unemployment Rate": 8,
    "Retail Sales": 7,
    "ISM Manufacturing PMI": 7,

    # Tier 3 — Moderate gold movers
    "ADP Non-Farm Employment Change": 6,
    "Initial Jobless Claims": 6,
    "Consumer Confidence": 6,

    # Fed speakers
    "Fed Chair": 8,
    "Powell": 9,
    "FOMC Member": 6,
}
```

**Filtering:**
- Must be USD-related (country/currency or title keywords)
- Must be "high impact" on Forex Factory
- Must match known gold-moving events

---

### 8. processing/relevance.py — Relevance Scoring

**Function:** `score_article(article, gold_price) -> float`

**5-Factor Scoring (1-10):**

| Factor | Points | Description |
|--------|--------|-------------|
| Primary keywords | 0-5 | "gold", "xau", "bullion" in title (2x weight) |
| Secondary keywords | 0-2 | "inflation", "fed", "cpi" in title/text |
| Source trust | 0-2 | Pre-configured per RSS feed (1-10 → 0-2) |
| India/MCX boost | 0-2 | "india", "mcx", "inr" keywords |
| Recency | 0-1 | <1hr=1.0, <3hr=0.7, <6hr=0.4, <12hr=0.2 |

**Word-Boundary Matching:**
```python
def _keyword_in_text(keyword: str, text: str) -> bool:
    """Match keyword as whole word/phrase using \b boundaries."""
    return bool(re.search(r"\b" + re.escape(keyword) + r"\b", text))
```

**Alert Threshold:** Score >= 5 triggers instant alert (configurable)

**Gold Angle Extraction:**
```python
def extract_gold_angle(title, summary) -> str | None:
    # Pattern matching for:
    # "gold surges" → "📈 Gold bullish"
    # "gold falls" → "📉 Gold bearish"
    # "safe haven" → "🛡️ Safe haven demand"
    # "central bank buying" → "🏦 Central bank buying"
    # etc.
```

---

### 9. processing/dedup.py — Deduplication

**Class:** `Deduplicator`

**Two-Level Dedup:**

1. **URL Hash (SHA-256):**
   ```python
   url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
   # Atomic check-and-mark in database
   ```

2. **Fuzzy Title (rapidfuzz):**
   ```python
   similarity = fuzz.ratio(normalized_title, seen_title)
   if similarity >= 92:  # 92% threshold
       return True  # Duplicate
   ```

**Title Normalization:**
```python
def normalize_title(title: str) -> str:
    title = title.lower().strip()
    title = re.sub(r'[^\w\s]', '', title)  # Remove punctuation
    title = re.sub(r'\s+', ' ', title)      # Collapse whitespace
    return title
```

**Performance:**
- Exact-match early exit before O(n) fuzzy loop
- 48-hour window for title comparison
- Index on `seen_titles.seen_at`

---

### 10. alerts/news_alerts.py — News Alert Pipeline

**Class:** `NewsAlertEngine`

**Pipeline:**
```
fetch_all_feeds() → dedup → score → store → alert (if threshold met)
```

**Detailed Flow:**
1. Fetch RSS + GDELT concurrently
2. Skip articles older than 72 hours
3. Check URL hash dedup (atomic)
4. Check fuzzy title dedup
5. Mark as seen (after successful insert)
6. Score with 5-factor algorithm
7. Store in database
8. If score >= threshold: format + send alert with inline button

**Stats Tracked:**
```python
stats = {
    "fetched": 0,
    "deduped": 0,
    "stored": 0,
    "alerted": 0,
    "skipped_old": 0,
}
```

---

### 11. alerts/calendar_alerts.py — Calendar Alerts

**Class:** `CalendarAlertEngine`

**Alert Types:**

1. **Pre-Event Alert** (2 hours before):
   - Checks every 10 minutes
   - Events happening within `PRE_ALERT_MINUTES = 120`
   - Includes gold implication context

2. **Post-Release Alert:**
   - Checks every 15 minutes
   - Re-fetches calendar to get actual values
   - Compares actual vs forecast for surprises

**Event Processing:**
```python
for event in events:
    try:
        message = format_calendar_alert(event, gold_price, alert_type)
        sent = await bot.broadcast(message)
        if sent > 0:
            db.mark_event_pre_alerted(event.event_id)
    except Exception as e:
        logger.error("Failed to alert event %s: %s", event.event_id, e)
```

---

### 12. alerts/digest.py — Digest Engine

**Class:** `DigestEngine`

**Digest Types:**

1. **Morning Digest** (8:00 AM IST):
   - Top news from last 12 hours
   - Upcoming events for next 24 hours
   - Current gold price

2. **Evening Digest** (8:00 PM IST):
   - Same format as morning
   - Afternoon/evening news focus

3. **On-Demand Digest** (`/digest` command):
   - Same content as scheduled digests
   - Marks articles as digested

**Message Format:**
```
📋 Morning Digest
Tuesday, July 29, 2026

📊 Gold: $4,106.90
📈 +$15.20 (+0.46%)
💱 MCX: ₹1,45,558/10g

📰 Top Gold News

🔴 1. Gold prices surge ahead of FOMC decision
   Reuters | Score: 8.5

🟡 2. India gold imports rise 15% in July
   Moneycontrol | Score: 6.2

📅 Upcoming Macro Events

⏰ Federal Funds Rate (FOMC)
   Jul 29, 11:30 PM IST

━━━━━━━━━━━━━━━━
GoldPulse | Stay sharp, trade smart 🥇
```

---

### 13. bot/handlers.py — Telegram Handlers

**Class:** `GoldPulseBot`

**Commands:**
| Command | Description |
|---------|-------------|
| `/start` | Welcome message + menu button |
| `/menu` | Inline keyboard menu |
| `/help` | List all commands |
| `/price` | Current gold price (USD + MCX) |
| `/latest` | Recent gold news |
| `/digest` | Today's digest |
| `/upcoming` | Upcoming macro events |
| `/settings` | Bot settings |
| `/health` | Bot health check |

**Authorization:**
```python
if str(message.chat.id) not in [TELEGRAM_CHAT_ID] + ADMIN_CHAT_IDS:
    return  # Silent rejection for unauthorized users
```

**Inline Keyboard Menu:**
```python
def build_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("📈 Latest Gold News", callback_data="menu_latest"),
         InlineKeyboardButton("💰 Gold Price", callback_data="menu_price")],
        [InlineKeyboardButton("📅 Macro Events", callback_data="menu_upcoming"),
         InlineKeyboardButton("📊 Digest", callback_data="menu_digest")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
         InlineKeyboardButton("✅ Health Check", callback_data="menu_health")],
        [InlineKeyboardButton("❓ Help", callback_data="menu_help")],
    ])
```

**Broadcast Methods:**
- `broadcast(text)` → Send to all configured chats
- `broadcast_with_button(text, button_text, button_url)` → With inline button

---

### 14. bot/formatter.py — Message Formatting

**Design:** Plain text (no Markdown) to avoid escaping issues

**Key Functions:**

```python
def clean_text(text: str | None) -> str:
    """Clean text for Telegram display."""
    # 1. HTML entity decoding
    # 2. HTML tag removal
    # 3. MarkdownV2 escape removal
    # 4. URL removal from text
    # 5. Domain name removal
    # 6. Whitespace normalization

def format_news_alert(article, score, gold_price, gold_angle) -> tuple[str, str | None]:
    """Format news alert. Returns (message, url) for inline button."""

def format_calendar_alert(event, gold_price, alert_type) -> str:
    """Format calendar event alert."""

def format_digest(title, articles, upcoming_events, gold_price) -> str:
    """Format morning/evening digest."""

def format_price_update(price) -> str:
    """Format gold price update."""

def format_health(stats, price) -> str:
    """Format health check."""

def is_article_too_old(published_at, max_age_hours=72) -> bool:
    """Check if article is too old to alert."""
```

**Constants:**
```python
SUMMARY_MAX_LEN = 250
DIGEST_TITLE_MAX_LEN = 80
URGENCY_HIGH_THRESHOLD = 9
URGENCY_NOTABLE_THRESHOLD = 7
```

**Score Indicators:**
```python
def _score_indicator(score: float) -> str:
    if score >= 8: return "🔴"
    if score >= 6: return "🟡"
    return "🟢"
```

---

### 15. utils/logger.py — Logging

**Setup:**
- Rotating file handler: 5MB, 5 backups
- Error log: 2MB, 3 backups
- Console handler: INFO level
- Format: `%(asctime)s | %(levelname)-8s | %(name)s | %(message)s`

---

## Data Flow

### News Alert Flow
```
1. APScheduler triggers news_cycle (every 15 min)
2. RSSFetcher.fetch_all_feeds() → 7 feeds concurrently
3. GDELTFetcher.fetch_gold_events() → 5 queries staggered
4. Combine all articles
5. For each article:
   a. Skip if older than 72 hours
   b. Check URL hash dedup (atomic)
   c. Check fuzzy title dedup (92% threshold)
   d. Mark as seen
   e. Score with 5-factor algorithm
   f. Store in database
   g. If score >= 5: format + send alert with inline button
6. Log stats: fetched, deduped, stored, alerted, skipped_old
```

### Calendar Flow
```
1. APScheduler triggers calendar_sync (every 30 min)
2. CalendarFetcher.fetch_events() → Forex Factory JSON
3. Filter: USD + high impact + gold-relevant
4. Store new events in database
5. Pre-alert check (every 10 min):
   - Events within 2 hours → send pre-alert
6. Post-alert check (every 15 min):
   - Re-fetch to get actual values
   - Send post-release alert if actual differs from forecast
```

### Digest Flow
```
1. APScheduler triggers at 8AM/8PM IST
2. Get top articles from last 12 hours
3. Get upcoming events for next 24 hours
4. Get current gold price
5. Format digest message
6. Send to all configured chats
7. Mark articles as digested
```

---

## Scheduled Jobs

| Job | Interval | Description |
|-----|----------|-------------|
| `news_cycle` | 15 min | Fetch RSS + GDELT, process, alert |
| `calendar_sync` | 30 min | Fetch Forex Factory calendar |
| `pre_alerts` | 10 min | Check for events 2 hours away |
| `post_alerts` | 15 min | Check for released event data |
| `morning_digest` | 8:00 AM IST | Send morning digest |
| `evening_digest` | 8:00 PM IST | Send evening digest |
| `cleanup` | 3:00 AM IST | Remove records older than 30 days |
| `price_refresh` | 5 min | Refresh gold price cache |

---

## Message Formatting Rules

**Plain Text (No Markdown):**
- No MarkdownV2 escaping issues
- Emojis for visual structure
- Inline keyboard buttons for links

**Message Structure (News Alert):**
```
🟢 UPDATE GOLD NEWS ALERT

📊 Gold: $4,106.90
📈 +$15.20 (+0.46%)
💱 MCX: ₹1,45,558/10g

📰 Gold prices fall ahead of Fed decision

Gold prices retreated as traders await the Federal Reserve's
policy announcement scheduled for Wednesday...

📡 Source: Reuters
🕐 Jul 28, 02:30 PM IST

Score: 6.5/10 | GoldPulse

[📖 Read full article]  ← Inline button
```

**Clean Text Function:**
- HTML entity decoding (`&amp;` → `&`)
- HTML tag stripping
- URL removal from body text
- Domain name removal (" - Reuters.com")
- Whitespace normalization

---

## Deployment

### Systemd Service
```ini
[Unit]
Description=GoldPulse Alerts - Gold Trading Intelligence Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/goldpulse-alerts
ExecStart=/root/goldpulse-alerts/venv/bin/python3 /root/goldpulse-alerts/main.py
Restart=always
RestartSec=10
EnvironmentFile=/root/goldpulse-alerts/.env
MemoryMax=512M

[Install]
WantedBy=multi-user.target
```

### Commands
```bash
# Service management
systemctl start goldpulse
systemctl stop goldpulse
systemctl restart goldpulse
systemctl status goldpulse

# Logs
journalctl -u goldpulse -f           # Live logs
journalctl -u goldpulse -n 50        # Last 50 lines
journalctl -u goldpulse --since today

# Manual run
source venv/bin/activate
python3 main.py
```

---

## Key Design Decisions

1. **Plain text messages** — No MarkdownV2 to avoid escaping issues
2. **Inline keyboard buttons** — Clean UX for article links and menu
3. **Word-boundary matching** — Prevents false positives ("war" ≠ "warehouse")
4. **Atomic dedup** — `check_and_mark_url_seen()` prevents TOCTOU races
5. **15% India markup** — Accurate MCX gold pricing for Indian traders
6. **Staggered GDELT** — 2s delays between queries to avoid 429
7. **72-hour article filter** — Skips old articles from RSS feeds
8. **14-day calendar look-ahead** — Catches FOMC, NFP, CPI events
9. **WAL mode** — Concurrent reads without locking
10. **Context managers** — Proper HTTP session cleanup

---

## Error Handling Strategy

1. **Every handler wrapped in try/except** — Bot never crashes
2. **Per-event isolation** — One failed alert doesn't abort the batch
3. **Graceful degradation** — If price fetch fails, continue without price
4. **Logged errors** — `exc_info=True` for full stack traces
5. **User-friendly messages** — Generic errors shown to users
6. **Retry with backoff** — For transient network failures

---

*This document describes the complete GoldPulse Alerts system architecture as of July 29, 2026.*
