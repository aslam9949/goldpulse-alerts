"""
GoldPulse Alerts — Digest Engine
===================================
Generates and sends daily digest messages.

Design decisions:
- Two digests per day: morning (8 AM IST) and evening (8 PM IST)
- Morning digest: recap of overnight news + upcoming events for the day
- Evening digest: day's top news + next day's preview
- Uses the formatter for consistent message styling
- Articles marked as "digested" so they don't repeat
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from storage.database import Database
from ingestion.price_fetcher import PriceFetcher
from bot.formatter import format_digest
from utils.logger import get_logger
from utils import error_counter

logger = get_logger("alerts.digest")

IST = ZoneInfo("Asia/Kolkata")


class DigestEngine:
    """
    Generates and sends daily gold trading digests.

    Two types:
    - Morning digest (8 AM IST): overnight recap + today's events
    - Evening digest (8 PM IST): day summary + tomorrow preview
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

    async def send_morning_digest(self) -> bool:
        """
        Send the morning digest.

        Includes:
        - Top news from the last 12 hours (overnight)
        - Upcoming events for today
        - Current gold price
        """
        try:
            logger.info("Generating morning digest...")

            # Get top articles from overnight
            articles = self.db.get_digest_articles(hours=12, limit=10)

            # Get today's upcoming events
            upcoming = self.db.get_upcoming_events(hours_ahead=24)

            # Get gold price
            gold_price = await self.price_fetcher.get_price()

            # Format digest
            digest_text = format_digest(
                title="🌅 Morning Gold Digest",
                articles=articles,
                upcoming_events=upcoming,
                gold_price=gold_price,
            )

            # Send
            sent = await self.bot.broadcast(digest_text)

            if sent:
                # Mark articles as digested (individual try/except so a
                # crash mid-loop doesn't leave articles unmarked silently)
                for art in articles:
                    try:
                        self.db.mark_article_digested(art["id"])
                    except Exception as mark_err:
                        logger.exception(
                            "Failed to mark article %s as digested: %s",
                            art["id"],
                            mark_err,
                        )
                        error_counter.bump("alerts.digest")
                logger.info(
                    "Morning digest sent (%d articles, %d events)",
                    len(articles),
                    len(upcoming),
                )
                return True
            else:
                logger.warning("Morning digest failed to send")
                return False

        except Exception as e:
            logger.exception("Morning digest error: %s", e)
            error_counter.bump("alerts.digest")
            return False

    async def send_evening_digest(self) -> bool:
        """
        Send the evening digest.

        Includes:
        - Top news from today
        - Events that happened today
        - Preview of tomorrow's events
        """
        try:
            logger.info("Generating evening digest...")

            # Get today's top articles
            articles = self.db.get_digest_articles(hours=14, limit=10)

            # Get tomorrow's events
            upcoming = self.db.get_upcoming_events(hours_ahead=48)

            # Get gold price
            gold_price = await self.price_fetcher.get_price()

            # Format digest
            digest_text = format_digest(
                title="🌆 Evening Gold Digest",
                articles=articles,
                upcoming_events=upcoming,
                gold_price=gold_price,
            )

            # Send
            sent = await self.bot.broadcast(digest_text)

            if sent:
                # Mark articles as digested (individual try/except so a
                # crash mid-loop doesn't leave articles unmarked silently)
                for art in articles:
                    try:
                        self.db.mark_article_digested(art["id"])
                    except Exception as mark_err:
                        logger.exception(
                            "Failed to mark article %s as digested: %s",
                            art["id"],
                            mark_err,
                        )
                        error_counter.bump("alerts.digest")
                logger.info(
                    "Evening digest sent (%d articles, %d events)",
                    len(articles),
                    len(upcoming),
                )
                return True
            else:
                logger.warning("Evening digest failed to send")
                return False

        except Exception as e:
            logger.exception("Evening digest error: %s", e)
            error_counter.bump("alerts.digest")
            return False

    async def send_on_demand_digest(self) -> bool:
        """
        Send a digest on demand (triggered by /digest command).

        Same as morning/evening but with a neutral title.
        """
        try:
            articles = self.db.get_digest_articles(hours=12, limit=10)
            upcoming = self.db.get_upcoming_events(hours_ahead=24)
            gold_price = await self.price_fetcher.get_price()

            now_ist = datetime.now(IST)
            time_label = "Morning" if now_ist.hour < 14 else "Evening"

            digest_text = format_digest(
                title=f"📋 {time_label} Digest",
                articles=articles,
                upcoming_events=upcoming,
                gold_price=gold_price,
            )

            sent = await self.bot.broadcast(digest_text)

            if sent:
                # Mark articles as digested (individual try/except so a
                # crash mid-loop doesn't leave articles unmarked silently)
                for art in articles:
                    try:
                        self.db.mark_article_digested(art["id"])
                    except Exception as mark_err:
                        logger.exception(
                            "Failed to mark article %s as digested: %s",
                            art["id"],
                            mark_err,
                        )
                        error_counter.bump("alerts.digest")
                return True
            else:
                return False

        except Exception as e:
            logger.exception("On-demand digest error: %s", e)
            error_counter.bump("alerts.digest")
            return False
