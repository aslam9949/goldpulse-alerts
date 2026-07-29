"""
GoldPulse Alerts — Message Formatter
=======================================
Formats alerts and digests for Telegram.

Design decisions:
- Uses plain text (no Markdown) to avoid escaping issues
- Emojis are used for visual structure
- Clean, readable messages without backslashes or raw URLs
- Includes gold price context in every alert
- Indian traders get both USD and INR/10g prices
- Inline keyboard buttons for article links (no raw URLs in text)
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re
import html

from ingestion.rss_fetcher import Article
from ingestion.price_fetcher import GoldPrice
from ingestion.calendar_fetcher import CalendarEvent

# IST timezone for Indian traders
IST = ZoneInfo("Asia/Kolkata")

# ── Named Constants ─────────────────────────────────────────────────
SUMMARY_MAX_LEN = 250
DIGEST_TITLE_MAX_LEN = 80
URGENCY_HIGH_THRESHOLD = 9
URGENCY_NOTABLE_THRESHOLD = 7

# Regex to match URLs in text
_URL_PATTERN = re.compile(
    r'https?://[^\s<>"\')\]]+|'  # http:// or https:// URLs
    r'www\.[^\s<>"\')\]]+'         # www. URLs
)

# Regex to match domain names (e.g., "moneycontrol.com", "reuters.com")
_DOMAIN_PATTERN = re.compile(
    r'\b(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|io|co|in|uk|de|fr|jp|cn|ru|br|au|ca)\b'
    r'(?:/[^\s]*)?'  # Optional path
)

# Common source suffixes to strip from titles
_SOURCE_SUFFIXES = [
    r'\s*[-–—|]\s*(?:Moneycontrol|Reuters|Kitco|Bloomberg|CNBC|Economic Times|'
    r'Hindu Business Line|LiveMint|Business Standard|NDTV|India Today|'
    r'Times of India|Hindustan Times|Zee Business|Aaj Tak|'
    r'Gold\.org|World Gold Council|GOLD\.de|GoldSeiten|'
    r'Mining\.com|ForexLive|FXStreet|Investing\.com)\s*\.?$',
    r'\s*[-–—|]\s*[A-Za-z]+\.com\s*\.?$',
    r'\s*[-–—|]\s*[A-Za-z]+\.in\s*\.?$',
    r'\s*[-–—|]\s*[A-Za-z]+\.org\s*\.?$',
]


def clean_text(text: str | None) -> str:
    """
    Clean text for Telegram display — remove garbage, fix encoding, strip HTML.

    This is the main cleaning function applied to ALL user-facing text.
    """
    if not text:
        return ""

    # Decode HTML entities (&amp; → &, &lt; → <, &nbsp; → space, etc.)
    text = html.unescape(text)

    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)

    # Remove MarkdownV2 escape characters that might be in raw source
    text = re.sub(r'\\([_*\[\]()~`>#+=|{}.!\\-])', r'\1', text)

    # Remove any remaining standalone backslashes (not part of \n etc.)
    text = re.sub(r'\\(?![\n\t])', '', text)

    # Remove URLs from text (we'll add a clean button separately)
    text = _URL_PATTERN.sub('', text)

    # Remove standalone domain names that got appended
    # (e.g., "Gold price rises - Moneycontrol.com" → "Gold price rises")
    text = _remove_source_domains(text)

    # Fix any remaining HTML entities that html.unescape might have missed
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = re.sub(r'&#\d+;', ' ', text)

    # Fix smart quotes and dashes that sometimes break
    text = text.replace('​', '')  # Zero-width space
    text = text.replace('‌', '')  # Zero-width non-joiner
    text = text.replace('‍', '')  # Zero-width joiner
    text = text.replace('﻿', '')  # BOM

    # Normalize whitespace (collapse multiple spaces/newlines)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _remove_source_domains(text: str) -> str:
    """
    Remove source domain names and source suffixes from text.

    Examples:
    - "Gold rises - Moneycontrol.com" → "Gold rises"
    - "Fed holds rates | Reuters" → "Fed holds rates"
    - "Gold price forecast via Kitco.com" → "Gold price forecast"
    """
    for pattern in _SOURCE_SUFFIXES:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    # Also remove trailing domain patterns like "- gold.org/news/123"
    text = re.sub(r'\s*[-–—|]\s*\S+\.(?:com|org|net|io|co|in)\S*\s*$', '', text)

    return text.strip()


def _truncate_summary(text: str, max_len: int = SUMMARY_MAX_LEN) -> str:
    """Truncate summary to max_len, ending at a sentence boundary if possible."""
    if len(text) <= max_len:
        return text

    # Try to cut at sentence boundary
    truncated = text[:max_len]
    last_period = truncated.rfind('. ')
    last_excl = truncated.rfind('! ')
    last_ques = truncated.rfind('? ')

    # Find the latest sentence boundary
    boundary = max(last_period, last_excl, last_ques)
    if boundary > max_len * 0.5:  # Only use if it's not too short
        return truncated[:boundary + 1]

    # Otherwise cut at word boundary
    last_space = truncated.rfind(' ')
    if last_space > 0:
        return truncated[:last_space] + "..."

    return truncated + "..."


def _format_time_ist(dt: datetime | None) -> str:
    """Format datetime in IST for display."""
    if dt is None:
        return "N/A"
    ist_time = dt.astimezone(IST)
    return ist_time.strftime("%b %d, %I:%M %p IST")


def _format_price_block(price: GoldPrice | None) -> str:
    """Format the gold price context block."""
    if price is None:
        return "📊 Gold Price: Updating..."

    lines = []
    lines.append(f"📊 Gold: {price.format_usd()}")

    change = price.format_change()
    if change:
        lines.append(change)

    if price.price_inr:
        lines.append(f"💱 MCX: {price.format_inr()}")

    return "\n".join(lines)


def _score_indicator(score: float) -> str:
    """Return a color indicator emoji based on score thresholds."""
    if score >= 8:
        return "🔴"
    elif score >= 6:
        return "🟡"
    return "🟢"


def format_news_alert(
    article: Article,
    score: float,
    gold_price: GoldPrice | None,
    gold_angle: str | None = None,
) -> tuple[str, str | None]:
    """
    Format a news article as a Telegram alert message.

    Returns:
        Tuple of (message_text, article_url).
        The article_url is returned separately so the caller can
        attach it as an inline keyboard button.
    """
    # Score indicator
    if score >= URGENCY_HIGH_THRESHOLD:
        urgency = "🔴 HIGH IMPACT"
    elif score >= URGENCY_NOTABLE_THRESHOLD:
        urgency = "🟡 NOTABLE"
    else:
        urgency = "🟢 UPDATE"

    lines = []

    # Header
    lines.append(f"{urgency} GOLD NEWS ALERT")
    lines.append("")

    # Gold price
    lines.append(_format_price_block(price=gold_price))
    lines.append("")

    # Title — clean it thoroughly
    title = clean_text(article.title) if article.title else "Untitled"
    # Remove any remaining domain patterns from title
    title = _DOMAIN_PATTERN.sub('', title).strip()
    # Clean up trailing dashes/pipes
    title = re.sub(r'\s*[-–—|]\s*$', '', title).strip()
    lines.append(f"📰 {title}")
    lines.append("")

    # Summary if available — truncated and cleaned
    if article.summary:
        summary = clean_text(article.summary)
        summary = _truncate_summary(summary)
        if summary:
            lines.append(summary)
            lines.append("")

    # Gold angle
    if gold_angle:
        lines.append(f"💡 Gold angle: {gold_angle}")
        lines.append("")

    # Source and time
    source = clean_text(article.source) if article.source else "Unknown"
    lines.append(f"📡 Source: {source}")
    if article.published_at:
        lines.append(f"🕐 {_format_time_ist(article.published_at)}")
    lines.append("")

    # Score
    lines.append(f"Score: {score}/10 | GoldPulse")

    message = "\n".join(lines)
    url = article.url if article.url else None

    return message, url


def format_calendar_alert(
    event: CalendarEvent,
    gold_price: GoldPrice | None,
    alert_type: str = "pre",
) -> str:
    """
    Format an economic calendar event as a Telegram alert.
    Uses plain text — no Markdown.
    """
    lines = []

    if alert_type == "pre":
        lines.append("⏰ UPCOMING EVENT ALERT")
    else:
        lines.append("📢 EVENT RELEASED")

    lines.append("")

    # Gold price
    lines.append(_format_price_block(price=gold_price))
    lines.append("")

    # Event title
    event_title = clean_text(event.title) if event.title else "Economic Event"
    lines.append(f"🔴 {event_title}")
    lines.append("")

    # Time
    lines.append(f"🕐 Time: {_format_time_ist(event.event_time)}")

    # Forecast / Previous / Actual
    if event.forecast:
        lines.append(f"📈 Forecast: {clean_text(event.forecast)}")
    if event.previous:
        lines.append(f"📊 Previous: {clean_text(event.previous)}")
    if event.actual:
        lines.append(f"✅ Actual: {clean_text(event.actual)}")

    lines.append("")

    # Gold implication
    if event.gold_implication:
        lines.append(f"💡 Gold impact: {clean_text(event.gold_implication)}")
        lines.append("")

    # Score
    impact_score = event.gold_impact_score if event.gold_impact_score is not None else "N/A"
    lines.append(f"Gold Impact Score: {impact_score}/10 | GoldPulse")

    return "\n".join(lines)


def format_digest(
    title: str,
    articles: list[dict],
    upcoming_events: list[dict],
    gold_price: GoldPrice | None,
) -> str:
    """
    Format a daily digest message.
    Uses plain text — no Markdown.
    """
    lines = []

    # Header
    lines.append(f"📋 {clean_text(title)}")
    lines.append(datetime.now(IST).strftime('%A, %B %d, %Y'))
    lines.append("")

    # Gold price
    lines.append(_format_price_block(price=gold_price))
    lines.append("")

    # Top news section
    if articles:
        lines.append("📰 Top Gold News")
        lines.append("")
        for i, art in enumerate(articles[:8], 1):
            score = art.get("relevance_score", 0)
            art_title = clean_text(art.get("title", "Untitled"))
            source = clean_text(art.get("source", "Unknown"))

            # Score indicator
            indicator = _score_indicator(score)

            # Truncate title for digest readability
            if len(art_title) > DIGEST_TITLE_MAX_LEN:
                art_title = art_title[:DIGEST_TITLE_MAX_LEN - 3] + "..."

            lines.append(f"{indicator} {i}. {art_title}")
            lines.append(f"   {source} | Score: {score}")
            lines.append("")
    else:
        lines.append("📰 No significant gold news in this period.")
        lines.append("")

    # Upcoming events
    if upcoming_events:
        lines.append("📅 Upcoming Macro Events")
        lines.append("")
        for evt in upcoming_events[:5]:
            evt_title = clean_text(evt.get("title", "Event"))
            evt_time = evt.get("event_time", "")

            # Parse time for display
            try:
                if isinstance(evt_time, str):
                    dt = datetime.fromisoformat(evt_time)
                else:
                    dt = evt_time
                time_str = _format_time_ist(dt)
            except (ValueError, TypeError):
                time_str = str(evt_time)

            lines.append(f"⏰ {evt_title}")
            lines.append(f"   {time_str}")
            lines.append("")
    else:
        lines.append("📅 No major events coming up.")
        lines.append("")

    # Footer
    lines.append("━━━━━━━━━━━━━━━━")
    lines.append("GoldPulse | Stay sharp, trade smart 🥇")

    return "\n".join(lines)


def format_price_update(price: GoldPrice | None) -> str:
    """Format a standalone price update message."""
    if price is None:
        return "⚠️ Unable to fetch gold price right now. Try again shortly."

    lines = []
    lines.append("🥇 Gold Price Update")
    lines.append("")
    lines.append(f"💰 XAU/USD: {price.format_usd()}")

    change = price.format_change()
    if change:
        lines.append(f"📊 {change}")

    if price.price_inr:
        lines.append(f"\n💱 MCX (10g): {price.format_inr()}")

    lines.append(f"\n🕐 {_format_time_ist(price.fetched_at)}")
    lines.append(f"\nSource: {clean_text(price.source)}")

    return "\n".join(lines)


def format_help() -> str:
    """Format the /help command response."""
    lines = [
        "🥇 GoldPulse",
        "Your gold trading intelligence bot",
        "",
        "📌 Commands:",
        "",
        "/menu - Open the menu (easy access to all features)",
        "/start - Start the bot",
        "/help - Show this help",
        "/price - Current gold price",
        "/latest - Recent gold news",
        "/digest - Today's digest",
        "/upcoming - Upcoming macro events",
        "/settings - Your settings",
        "/health - Bot health status",
        "",
        "💡 How it works:",
        "• Monitors 7+ gold news sources 24/7",
        "• Scores articles for gold relevance",
        "• Sends instant alerts for high-impact news",
        "• Tracks US economic events that move gold",
        "• Includes India/MCX context for Indian traders",
        "",
        "⚙️ Alert Threshold: Only high-relevance items",
        "push instantly. Lower-scoring items go to digest.",
        "",
        "Built for gold traders who value signal over noise. 🥇",
    ]
    return "\n".join(lines)


def format_health(stats: dict[str, int], price: GoldPrice | None) -> str:
    """Format a health check message."""
    lines = []
    lines.append("✅ GoldPulse Health Check")
    lines.append("")
    lines.append(f"📰 Articles stored: {stats.get('total_articles', 0)}")
    lines.append(f"🔔 Alerts sent: {stats.get('alerts_sent', 0)}")
    lines.append(f"📅 Events tracked: {stats.get('total_events', 0)}")
    lines.append(f"🔗 URLs deduped: {stats.get('urls_seen', 0)}")
    lines.append("")

    if price:
        lines.append(f"🥇 Gold price: {price.format_usd()} ({clean_text(price.source)})")
    else:
        lines.append("⚠️ Gold price: unavailable")

    lines.append(f"\n🕐 {_format_time_ist(datetime.now(IST))}")
    return "\n".join(lines)


def is_article_too_old(published_at: datetime | None, max_age_hours: int = 72) -> bool:
    """
    Check if an article is too old to be worth alerting on.

    Args:
        published_at: When the article was published
        max_age_hours: Maximum age in hours (default 72 = 3 days)

    Returns:
        True if the article is too old. Returns False if published_at
        is None (cannot determine age, so don't skip).
    """
    if published_at is None:
        return False  # Can't determine age, don't skip

    now = datetime.now(timezone.utc)
    age = now - published_at
    return age.total_seconds() > max_age_hours * 3600
