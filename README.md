# 🥇 GoldPulse Alerts

A lightweight, production-ready Telegram bot that delivers high-quality gold trading intelligence — news, economic events, and live prices — filtered for relevance and tailored for Indian (MCX) gold traders.

## Features

- **📰 Gold News Alerts** — Monitors 7+ RSS sources + GDELT for gold-relevant news
- **📅 Economic Calendar** — Tracks US high-impact events (NFP, CPI, FOMC) that move gold
- **💰 Live Gold Price** — XAU/USD + MCX (₹/10g) in every alert
- **📊 Relevance Scoring** — 1-10 scale, only high-quality items get instant alerts
- **🇮🇳 India/MCX Focus** — Extra relevance for Indian gold market news
- **📋 Daily Digest** — Morning + evening summaries of top news + upcoming events
- **🔇 Low Noise** — Smart filtering + cooldown system to avoid spam
- **🔄 24/7 Reliable** — Auto-retry, error handling, rotating logs

## Quick Start

### 1. Get a Telegram Bot Token

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the bot token (looks like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
4. **Get your Chat ID**: Send any message to your bot, then visit:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Look for `"chat":{"id":123456789}` — that's your Chat ID.

### 2. Install & Configure

```bash
# Clone the project
git clone <your-repo-url>
cd goldpulse-alerts

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your bot token and chat ID
nano .env
```

### 3. Run

```bash
python main.py
```

That's it! The bot will:
- Start polling Telegram for commands
- Fetch initial data (RSS, calendar, gold price)
- Begin monitoring on schedule

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | List all commands |
| `/price` | Current gold price (USD + MCX) |
| `/latest` | Recent gold news |
| `/digest` | Today's digest |
| `/upcoming` | Upcoming economic events |
| `/settings` | Current configuration |
| `/health` | Bot health status |

## Configuration

All settings are in `.env`:

```env
# Required
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Alert threshold (1-10, higher = stricter)
ALERT_THRESHOLD=7

# Digest times (IST)
MORNING_DIGEST_HOUR=8
EVENING_DIGEST_HOUR=20

# Poll intervals (minutes)
RSS_POLL_INTERVAL_MINUTES=15
CALENDAR_POLL_INTERVAL_MINUTES=30
```

See `.env.example` for all options.

## Deployment

### Option A: VPS with systemd (Recommended)

```bash
# On your VPS (Ubuntu/Debian)
sudo apt update && sudo apt install python3-pip python3-venv

# Clone and setup
git clone <repo>
cd goldpulse-alerts
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env

# Create systemd service
sudo nano /etc/systemd/system/goldpulse.service
```

Paste this content:

```ini
[Unit]
Description=GoldPulse Alerts Telegram Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/goldpulse-alerts
ExecStart=/path/to/goldpulse-alerts/venv/bin/python main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable goldpulse
sudo systemctl start goldpulse

# Check status
sudo systemctl status goldpulse
sudo journalctl -u goldpulse -f
```

### Option B: Docker

```bash
# Build and run
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

### Option C: Termux (Android — for testing)

```bash
pkg install python
pip install -r requirements.txt
cp .env.example .env
# Edit .env with nano
python main.py
```

## Architecture

```
goldpulse-alerts/
├── main.py                 # Entry point + scheduler
├── config/
│   └── settings.py         # All configuration from .env
├── storage/
│   └── database.py         # SQLite storage layer
├── ingestion/
│   ├── rss_fetcher.py      # RSS feed fetching
│   ├── gdelt_fetcher.py    # GDELT geopolitical events
│   ├── calendar_fetcher.py # Forex Factory economic calendar
│   └── price_fetcher.py    # Live gold price (yfinance)
├── processing/
│   ├── relevance.py        # Gold relevance scoring (1-10)
│   └── dedup.py            # URL + fuzzy title deduplication
├── alerts/
│   ├── news_alerts.py      # News alert pipeline
│   ├── calendar_alerts.py  # Pre/post event alerts
│   └── digest.py           # Daily digest generator
├── bot/
│   ├── handlers.py         # Telegram command handlers
│   └── formatter.py        # Message formatting
└── utils/
    └── logger.py           # Logging with rotation
```

## Data Sources

| Source | Type | Trust | What it covers |
|--------|------|-------|----------------|
| Google News (Gold) | RSS | 6/10 | Broad gold news |
| Mining.com | RSS | 8/10 | Mining industry |
| GoldSeiten | RSS | 8/10 | Gold market analysis |
| Kitco | RSS | 8/10 | Gold market news |
| Reuters | RSS | 9/10 | Mainstream financial |
| Gold.org | RSS | 9/10 | Official gold council |
| Moneycontrol | RSS | 7/10 | Indian market focus |
| GDELT | API | 6/10 | Geopolitical events |
| Forex Factory | JSON | 8/10 | Economic calendar |

## Scoring System

Articles are scored 1-10 based on:

| Factor | Weight | What it measures |
|--------|--------|------------------|
| Primary keywords | 0-3.5 | Direct gold mentions (gold, XAU, bullion...) |
| Secondary keywords | 0-1.5 | Related terms (inflation, Fed, safe haven...) |
| Source trust | 0-2 | Pre-configured per source |
| India/MCX boost | 0-1.5 | Indian market relevance |
| Recency | 0-1 | Newer articles score higher |

Items scoring ≥7 (configurable) trigger instant alerts. Lower-scoring items go to the digest.

## Economic Calendar

Tracked events (USD + High Impact only):

| Event | Gold Impact | Why |
|-------|-------------|-----|
| FOMC Rate Decision | 🔴 10/10 | #1 gold driver |
| Non-Farm Payrolls | 🔴 10/10 | Biggest monthly jobs data |
| CPI (Inflation) | 🔴 9/10 | Directly affects Fed policy |
| Fed Chair Speech | 🔴 9/10 | Forward guidance |
| GDP | 🟠 8/10 | Economic health |
| PPI | 🟠 8/10 | Pipeline inflation |
| ISM PMI | 🟡 7/10 | Manufacturing health |
| Retail Sales | 🟡 7/10 | Consumer spending |

**Pre-alerts**: Sent 2 hours before events.
**Post-alerts**: Sent when actual data is released.

## Troubleshooting

**Bot not responding?**
- Check `TELEGRAM_BOT_TOKEN` is correct
- Make sure you sent `/start` to the bot
- Check logs: `tail -f logs/goldpulse.log`

**No alerts coming?**
- Verify `TELEGRAM_CHAT_ID` is correct
- Lower `ALERT_THRESHOLD` (try 5) to see more items
- Check `/health` command

**Price not showing?**
- yfinance can be slow on first fetch — wait a few minutes
- Check internet connectivity
- Try setting `GOLD_PRICE_SOURCE=goldapi` with an API key

**Database issues?**
- Delete `data/goldpulse.db` to reset (loses history)
- Check disk space on VPS

## License

MIT — use freely for personal trading.

---

*Built for gold traders who value signal over noise.* 🥇
