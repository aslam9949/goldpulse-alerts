"""
GoldPulse Alerts — Calendar Alert Engine
===========================================
Handles US economic calendar alerts for gold traders.

Alert types:
1. Pre-event alert: Sent 1-2 hours before a high-impact event
   (gives traders time to prepare)
2. Post-release alert: Sent when actual data is available
   (highlights surprises that move gold)

Design decisions:
- Only USD + high-impact events (filtered by CalendarFetcher)
- Pre-alerts use a simple "within N minutes" check
- Post-alerts compare actual vs forecast to flag surprises
- Gold implication is included in every alert
"""

import asyncio
from datetime import datetime, timezone

from ingestion.calendar_fetcher import CalendarFetcher, CalendarEvent
from ingestion.price_fetcher import PriceFetcher
from storage.database import Database
from bot.formatter import format_calendar_alert
from processing.relevance import classify_event_importance
from utils.logger import get_logger

logger = get_logger("alerts.calendar")

# Minutes before event to send pre-alert
PRE_ALERT_MINUTES = 120  # 2 hours


class CalendarAlertEngine:
    """
    Processes economic calendar events and sends alerts.

    Two alert types:
    - Pre-event: "This event is coming in 2 hours"
    - Post-release: "Event data released — here's what happened"
    """

    def __init__(
        self,
        db: Database,
        price_fetcher: PriceFetcher,
        bot,  # GoldPulseBot instance
    ):
        self.db = db
        self.price_fetcher = price_fetcher
        self.bot = bot
        # Use configured look-ahead days (default 14)
        from config.settings import CALENDAR_LOOKAHEAD_DAYS
        self.fetcher = CalendarFetcher(look_ahead_days=CALENDAR_LOOKAHEAD_DAYS)

    async def sync_events(self) -> int:
        """
        Fetch calendar events from Forex Factory and store new ones.

        Returns:
            Number of new events stored.
        """
        try:
            logger.info("Syncing economic calendar...")
            events = await self.fetcher.fetch_events()

            new_count = 0
            for event in events:
                stored = self.db.insert_event(
                    event_id=event.event_id,
                    title=event.title,
                    country=event.country,
                    currency=event.currency,
                    impact=event.impact,
                    event_time=event.event_time.isoformat() if event.event_time else None,
                    forecast=event.forecast,
                    previous=event.previous,
                    actual=event.actual,
                    gold_implication=event.gold_implication,
                )
                if stored:
                    new_count += 1

            logger.info("Calendar sync: %d new events (total fetched: %d)", new_count, len(events))
            return new_count

        except Exception as e:
            logger.error("Calendar sync error: %s", e, exc_info=True)
            return 0

    async def check_pre_alerts(self) -> int:
        """
        Check for Tier-1 (critical) events that need pre-alerts.

        Only sends pre-alerts for critical events:
        - FOMC Statement / Rate Decision
        - Non-Farm Payrolls (NFP)
        - CPI / Core CPI
        - Powell speeches

        Lower-impact events are logged but NOT pushed as instant alerts.

        Returns:
            Number of pre-alerts sent.
        """
        try:
            events = self.db.get_unalerted_pre_events(minutes_before=PRE_ALERT_MINUTES)
            if not events:
                return 0

            gold_price = await self.price_fetcher.get_price()
            sent_count = 0

            for evt_data in events:
                try:
                    event = self._dict_to_event(evt_data)
                    if event is None:
                        continue

                    # ── Tier-1 gate: only pre-alert critical events ──
                    importance = classify_event_importance(event.title)
                    if importance != "critical":
                        logger.info(
                            "Skipping pre-alert (importance=%s): %s",
                            importance,
                            event.title,
                        )
                        # Mark as pre-alerted so we don't re-check
                        self.db.mark_event_pre_alerted(event.event_id)
                        continue

                    message = format_calendar_alert(
                        event=event,
                        gold_price=gold_price,
                        alert_type="pre",
                    )

                    sent = await self.bot.broadcast(message)
                    if sent > 0:
                        self.db.mark_event_pre_alerted(event.event_id)
                        sent_count += 1
                        logger.info(
                            "Pre-alert sent: %s (in %s min)",
                            event.title,
                            self._minutes_until(event.event_time),
                        )
                except Exception as e:
                    logger.error(
                        "Pre-alert error for event %s: %s",
                        evt_data.get("event_id", "unknown"),
                        e,
                        exc_info=True,
                    )

            return sent_count

        except Exception as e:
            logger.error("Pre-alert check error: %s", e, exc_info=True)
            return 0

    async def check_post_alerts(self) -> int:
        """
        Check for events that have released actual data.

        Looks for events that:
        1. Happened in the last 4 hours
        2. Don't have actual data yet (Forex Factory updates with delay)
        3. Haven't been post-alerted

        Note: Forex Factory often takes 15-60 minutes to post actuals.
        This is a limitation of the free data source.

        Returns:
            Number of post-alerts sent.
        """
        try:
            # Get events that happened recently but have no actual data
            # We check if actuals have appeared since last check
            events = self.db.get_recent_events_without_actual()
            if not events:
                return 0

            # Try to re-fetch calendar to get updated actuals
            fresh_events = await self.fetcher.fetch_events()
            actuals_found = 0

            for fresh in fresh_events:
                if fresh.actual:
                    updated = self.db.update_event_actual(
                        fresh.event_id, fresh.actual
                    )
                    if updated:
                        actuals_found += 1

            if actuals_found == 0:
                return 0

            # Now send post-alerts for events that have actuals
            gold_price = await self.price_fetcher.get_price()
            sent_count = 0

            # Re-query events that now have actuals but haven't been post-alerted
            rows = self.db.get_recent_events_with_actuals_not_post_alerted(hours=4)

            for row in rows:
                try:
                    event = self._dict_to_event(dict(row))
                    if event is None:
                        continue

                    message = format_calendar_alert(
                        event=event,
                        gold_price=gold_price,
                        alert_type="post",
                    )

                    sent = await self.bot.broadcast(message)
                    if sent > 0:
                        self.db.mark_event_post_alerted(event.event_id)
                        sent_count += 1
                        logger.info(
                            "Post-alert sent: %s (actual=%s)",
                            event.title,
                            event.actual,
                        )
                except Exception as e:
                    logger.error(
                        "Post-alert error for event %s: %s",
                        row.get("event_id", "unknown") if isinstance(row, dict) else "unknown",
                        e,
                        exc_info=True,
                    )

            return sent_count

        except Exception as e:
            logger.error("Post-alert check error: %s", e, exc_info=True)
            return 0

    def _dict_to_event(self, data: dict) -> CalendarEvent | None:
        """Convert a database row dict to a CalendarEvent."""
        try:
            event_time = data.get("event_time", "")
            if isinstance(event_time, str):
                dt = datetime.fromisoformat(event_time)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = event_time

            return CalendarEvent(
                event_id=data.get("event_id", ""),
                title=data.get("title", ""),
                country=data.get("country", "USD"),
                currency=data.get("currency", "USD"),
                impact=data.get("impact", "high"),
                event_time=dt,
                forecast=data.get("forecast"),
                previous=data.get("previous"),
                actual=data.get("actual"),
                gold_implication=data.get("gold_implication", ""),
                gold_impact_score=5,  # Default for stored events
            )
        except Exception as e:
            logger.error("Failed to convert event dict: %s", e)
            return None

    def _minutes_until(self, event_time: datetime) -> int:
        """Calculate minutes until an event."""
        now = datetime.now(timezone.utc)
        delta = event_time - now
        return max(0, int(delta.total_seconds() / 60))

    async def close(self) -> None:
        """Clean up resources."""
        await self.fetcher.close()
