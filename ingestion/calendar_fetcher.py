"""
GoldPulse Alerts — Economic Calendar Fetcher
===============================================
Fetches US economic events from Forex Factory public feed.

Design decisions:
- We only care about USD + High Impact events (the ones that move gold)
- Forex Factory's free JSON feed is the most reliable public source
- Events are scored by their typical gold impact (NFP, CPI, FOMC are king)
- We generate a unique event_id from title+date to avoid duplicates
- Gold implication is rule-based (simple but effective for trading context)
- Fetches events for the next 14-30 days (configurable) to catch FOMC, NFP, etc.
"""

import asyncio
import hashlib
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

import aiohttp

from utils.logger import get_logger

logger = get_logger("ingestion.calendar")

# ── Configuration ──────────────────────────────────────────────────────

# Look-ahead period for calendar events (days)
# Default: 14 days to catch FOMC, NFP, CPI, etc.
CALENDAR_LOOKAHEAD_DAYS = 14

# Forex Factory public JSON endpoints
# This week's events
FOREX_FACTORY_THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
# Next week's events
FOREX_FACTORY_NEXT_WEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

# High-impact events that significantly affect gold prices.
# Events not in this list are ignored even if they're "high impact" on FF.
# Score: 1-10 (10 = biggest gold mover)
GOLD_IMPACT_EVENTS: dict[str, int] = {
    # Tier 1 — Biggest gold movers
    "Non-Farm Employment Change": 10,
    "Non-Farm Payrolls": 10,
    "NFP": 10,
    "FOMC Statement": 10,
    "FOMC Rate Decision": 10,
    "Federal Funds Rate": 10,
    "FOMC Press Conference": 9,
    "FOMC Meeting": 10,
    "CPI": 9,
    "Consumer Price Index": 9,
    "Core CPI": 9,
    "PPI": 8,
    "Producer Price Index": 8,

    # Tier 2 — Significant gold movers
    "GDP": 8,
    "Gross Domestic Product": 8,
    "Unemployment Rate": 8,
    "Average Hourly Earnings": 7,
    "Retail Sales": 7,
    "Core Retail Sales": 7,
    "ISM Manufacturing PMI": 7,
    "ISM Services PMI": 7,

    # Tier 3 — Moderate gold movers
    "ADP Non-Farm Employment Change": 6,
    "Initial Jobless Claims": 6,
    "Continuing Jobless Claims": 5,
    "Durable Goods Orders": 6,
    "New Home Sales": 5,
    "Existing Home Sales": 5,
    "Consumer Confidence": 6,
    "Consumer Sentiment": 6,
    "Philadelphia Fed Index": 5,

    # Fed speakers (always relevant for gold)
    "Fed Chair": 8,
    "FOMC Member": 6,
    "Fed Speech": 5,
    "Fed Testimony": 7,
    "Powell": 9,

    # Tier 4 — Context events
    "Trade Balance": 4,
    "Current Account": 4,
    "Treasury Budget": 4,
    "10-Year Note Auction": 5,
    "30-Year Bond Auction": 4,
}

# Keywords that indicate an event is USD-related
USD_KEYWORDS = [
    "USD", "US ", "U.S.", "United States", "America",
    "Fed", "Federal", "Treasury", "FOMC", "CPI", "NFP",
    "Non-Farm", "Jobless", "Retail", "GDP", "ISM",
    "Powell", "Employment", "Housing",
]


@dataclass
class CalendarEvent:
    """Normalized economic calendar event."""
    event_id: str
    title: str
    country: str
    currency: str
    impact: str  # "high", "medium", "low"
    event_time: datetime
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None
    gold_impact_score: int = 0  # 0-10, how much this moves gold
    gold_implication: str = ""  # Human-readable gold context

    def __post_init__(self):
        if not self.event_id:
            # Generate stable ID from title + time
            key = f"{self.title}_{self.event_time.isoformat()}"
            self.event_id = hashlib.sha256(key.encode()).hexdigest()[:16]


class CalendarFetcher:
    """
    Fetches and filters US economic calendar events.

    Only returns USD events with high impact that are likely to
    affect gold prices. Each event gets a gold impact score and
    a plain-English implication note.

    Fetches events for the next 14-30 days to catch FOMC meetings,
    NFP releases, CPI data, and other scheduled events.
    """

    def __init__(self, look_ahead_days: int = CALENDAR_LOOKAHEAD_DAYS):
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        self._look_ahead_days = look_ahead_days

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy-init aiohttp session."""
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30),
                )
        return self._session

    async def close(self) -> None:
        """Clean up the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def fetch_events(self) -> list[CalendarEvent]:
        """
        Fetch economic calendar events for the next N days.

        Fetches both this week's and next week's events from Forex Factory
        to cover the configured look-ahead period (default 14 days).

        Returns:
            List of CalendarEvent objects filtered to USD + high-impact + gold-relevant.
        """
        all_events = []
        seen_ids = set()

        # Calculate date range
        now = datetime.now(timezone.utc)
        end_date = now + timedelta(days=self._look_ahead_days)

        # Fetch this week's events
        this_week_events = await self._fetch_from_url(FOREX_FACTORY_THIS_WEEK, "this week")
        for event in this_week_events:
            if event.event_id not in seen_ids:
                seen_ids.add(event.event_id)
                all_events.append(event)

        # Fetch next week's events (to cover 7-14 day range)
        if self._look_ahead_days > 7:
            next_week_events = await self._fetch_from_url(FOREX_FACTORY_NEXT_WEEK, "next week")
            for event in next_week_events:
                if event.event_id not in seen_ids:
                    seen_ids.add(event.event_id)
                    all_events.append(event)

        # Filter to date range
        filtered_events = []
        for event in all_events:
            # Only include events within our look-ahead window
            if now <= event.event_time <= end_date:
                filtered_events.append(event)
            else:
                logger.debug(
                    "Skipping event outside window: %s at %s",
                    event.title, event.event_time
                )

        # Sort by time
        filtered_events.sort(key=lambda e: e.event_time)
        logger.info(
            "Calendar: %d gold-relevant USD events in next %d days",
            len(filtered_events),
            self._look_ahead_days,
        )
        return filtered_events

    async def _fetch_from_url(self, url: str, period_name: str) -> list[CalendarEvent]:
        """
        Fetch events from a specific Forex Factory URL.

        Args:
            url: The Forex Factory JSON endpoint
            period_name: Human-readable name for logging (e.g., "this week")

        Returns:
            List of parsed and filtered CalendarEvent objects
        """
        try:
            session = await self._get_session()
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning(
                        "Forex Factory (%s) returned HTTP %d", period_name, response.status
                    )
                    return []
                data = await response.json()

        except asyncio.TimeoutError:
            logger.warning("Forex Factory fetch timed out (%s)", period_name)
            return []
        except Exception as e:
            logger.error("Forex Factory fetch error (%s): %s", period_name, e)
            return []

        events = []
        for item in data:
            event = self._parse_event(item)
            if event and self._is_gold_relevant(event):
                events.append(event)

        logger.info("Fetched %d gold-relevant events for %s", len(events), period_name)
        return events

    def _parse_event(self, item: dict) -> CalendarEvent | None:
        """
        Parse a Forex Factory event into our CalendarEvent format.

        Args:
            item: Raw event dict from FF JSON

        Returns:
            CalendarEvent or None if unparseable
        """
        title = item.get("title", "").strip()
        if not title:
            return None

        # Parse event time
        date_str = item.get("date", "")
        if not date_str:
            return None

        try:
            # FF format: "2024-01-15T13:30:00+00:00" or similar
            event_time = datetime.fromisoformat(date_str)
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            logger.debug("Failed to parse event date: %s", date_str)
            return None

        # Generate event_id
        key = f"{title}_{event_time.isoformat()}"
        event_id = hashlib.sha256(key.encode()).hexdigest()[:16]

        return CalendarEvent(
            event_id=event_id,
            title=title,
            country=item.get("country", "USD"),
            currency=item.get("currency", "USD"),
            impact=item.get("impact", "low").lower(),
            event_time=event_time,
            forecast=item.get("forecast", ""),
            previous=item.get("previous", ""),
            actual=item.get("actual"),
        )

    def _is_gold_relevant(self, event: CalendarEvent) -> bool:
        """
        Determine if an event is relevant to gold trading.

        Filter logic:
        1. Must be USD-related (country/currency or title keywords)
        2. Must be high impact
        3. Must match known gold-moving events OR have strong keywords

        This is deliberately strict to reduce noise.
        """
        # Must be USD-related
        title_lower = event.title.lower()
        is_usd = (
            event.currency.upper() == "USD"
            or event.country.upper() == "USD"
            or any(kw.lower() in title_lower for kw in USD_KEYWORDS)
        )
        if not is_usd:
            return False

        # Must be high impact
        if event.impact != "high":
            return False

        # Check against known gold-moving events
        gold_score = self._score_gold_impact(event.title)
        if gold_score > 0:
            event.gold_impact_score = gold_score
            event.gold_implication = self._generate_implication(event)
            return True

        # Fallback: if it's USD + high impact but not in our list,
        # still include it with a lower score
        event.gold_impact_score = 3
        event.gold_implication = f"High-impact USD event — monitor for gold reaction"
        return True

    def _score_gold_impact(self, title: str) -> int:
        """
        Score how much an event impacts gold (0-10).

        Checks the event title against our known gold-moving events.
        Partial matches are used (e.g., "CPI" matches "Core CPI m/m").
        """
        title_lower = title.lower()
        max_score = 0

        for event_name, score in GOLD_IMPACT_EVENTS.items():
            if event_name.lower() in title_lower:
                max_score = max(max_score, score)

        return max_score

    def _generate_implication(self, event: CalendarEvent) -> str:
        """
        Generate a plain-English note about what this event means for gold.

        This is rule-based (no LLM). Simple but useful for quick context.
        """
        title_lower = event.title.lower()

        # FOMC / Fed Rate
        if any(kw in title_lower for kw in ["fomc", "federal funds", "fed rate"]):
            return (
                "🏦 Fed rate decisions are the #1 driver of gold. "
                "Dovish (hold/cut) = gold bullish. Hawkish (hike) = gold bearish."
            )

        # Fed Chair / Powell
        if any(kw in title_lower for kw in ["powell", "fed chair", "fed testimony"]):
            return (
                "🎤 Fed Chair's words move markets instantly. "
                "Watch for hints on rate path. Dovish tone = gold up."
            )

        # NFP
        if any(kw in title_lower for kw in ["non-farm", "nfp", "employment change"]):
            return (
                "📊 NFP is the biggest monthly jobs report. "
                "Weak jobs = Fed may cut rates = gold bullish. Strong = bearish."
            )

        # CPI / Inflation
        if any(kw in title_lower for kw in ["cpi", "consumer price", "inflation"]):
            return (
                "📈 Inflation data directly impacts Fed policy expectations. "
                "Higher CPI = rates stay high = gold pressure. Lower CPI = gold rally."
            )

        # PPI
        if "ppi" in title_lower or "producer price" in title_lower:
            return (
                "🏭 PPI signals pipeline inflation. "
                "Higher PPI can pressure gold on rate fears."
            )

        # GDP
        if "gdp" in title_lower:
            return (
                "📉 GDP shows economic health. "
                "Weak GDP = safe haven buying = gold up. Strong = gold down."
            )

        # Unemployment
        if "unemployment" in title_lower:
            return (
                "👷 Rising unemployment = economic weakness = "
                "Fed may cut = gold bullish."
            )

        # Retail Sales
        if "retail sales" in title_lower:
            return (
                "🛒 Consumer spending indicator. "
                "Weak sales = economic slowdown = gold supportive."
            )

        # ISM PMI
        if "ism" in title_lower and "pmi" in title_lower:
            return (
                "🏭 Manufacturing/services health check. "
                "Below 50 = contraction = gold supportive."
            )

        # Jobless Claims
        if "jobless" in title_lower or "claims" in title_lower:
            return (
                "📋 Weekly jobless claims — rising claims = "
                "labor market weakening = gold supportive."
            )

        # Default for other high-impact
        return "⚡ High-impact USD event — can cause gold volatility. Watch closely."
