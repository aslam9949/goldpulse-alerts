"""
GoldPulse Alerts — Main Entry Point
======================================
The main orchestrator that ties everything together:

1. Initialize database, price fetcher, and bot
2. Set up APScheduler for periodic tasks
3. Start the Telegram bot (long polling)
4. Run scheduled tasks: RSS, calendar, digests

Run with:
    python main.py

Environment:
    Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

import asyncio
import signal
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    RSS_POLL_INTERVAL_MINUTES,
    CALENDAR_POLL_INTERVAL_MINUTES,
    MORNING_DIGEST_HOUR,
    EVENING_DIGEST_HOUR,
)
from utils.logger import setup_logging, get_logger
from utils import error_counter
from storage.database import Database
from ingestion.price_fetcher import PriceFetcher
from bot.handlers import GoldPulseBot
from alerts.news_alerts import NewsAlertEngine
from alerts.calendar_alerts import CalendarAlertEngine
from alerts.digest import DigestEngine

# Initialize logging first
setup_logging()
logger = get_logger("main")

IST = ZoneInfo("Asia/Kolkata")


class GoldPulseApp:
    """
    Main application class that owns all components.

    Lifecycle:
    1. init() — create all objects
    2. start() — begin polling + scheduling
    3. shutdown() — clean up everything
    """

    def __init__(self):
        self.db: Database | None = None
        self.price_fetcher: PriceFetcher | None = None
        self.bot: GoldPulseBot | None = None
        self.news_engine: NewsAlertEngine | None = None
        self.calendar_engine: CalendarAlertEngine | None = None
        self.digest_engine: DigestEngine | None = None
        self.scheduler: AsyncIOScheduler | None = None
        self._running = False
        self.shutdown_event = asyncio.Event()
        self._polling_task: asyncio.Task | None = None

    async def init(self) -> None:
        """Initialize all components."""
        logger.info("=" * 50)
        logger.info("🥇 GoldPulse Alerts — Starting up")
        logger.info("=" * 50)

        # Validate config
        if not TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not set! Check your .env file.")
        if not TELEGRAM_CHAT_ID:
            raise RuntimeError("TELEGRAM_CHAT_ID not set! Check your .env file.")

        # Database
        self.db = Database()
        try:
            self.db.connect()
        except Exception as e:
            raise RuntimeError(f"Failed to connect to database: {e}") from e
        logger.info("Database initialized")

        # Price fetcher
        self.price_fetcher = PriceFetcher()
        # Pre-fetch gold price
        price = await self.price_fetcher.get_price()
        if price:
            logger.info("Initial gold price: %s", price.format_usd())
        else:
            logger.warning("Could not fetch initial gold price")

        # Telegram bot
        self.bot = GoldPulseBot(
            db=self.db,
            price_fetcher=self.price_fetcher,
        )
        logger.info("Telegram bot initialized")

        # Alert engines
        self.news_engine = NewsAlertEngine(
            db=self.db,
            price_fetcher=self.price_fetcher,
            bot=self.bot,
        )
        self.calendar_engine = CalendarAlertEngine(
            db=self.db,
            price_fetcher=self.price_fetcher,
            bot=self.bot,
        )
        self.digest_engine = DigestEngine(
            db=self.db,
            price_fetcher=self.price_fetcher,
            bot=self.bot,
        )
        logger.info("Alert engines initialized")

        # Scheduler
        self.scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
        self._setup_jobs()
        logger.info("Scheduler initialized")

    def _setup_jobs(self) -> None:
        """Configure all scheduled jobs."""
        if self.scheduler is None:
            raise RuntimeError("Scheduler must be initialized before setting up jobs")

        # ── News polling (every N minutes) ────────────────────────────
        self.scheduler.add_job(
            self._run_news_cycle,
            IntervalTrigger(minutes=RSS_POLL_INTERVAL_MINUTES),
            id="news_cycle",
            name="News RSS + GDELT fetch",
            replace_existing=True,
            misfire_grace_time=120,
        )

        # ── Calendar sync (every N minutes) ──────────────────────────
        self.scheduler.add_job(
            self._run_calendar_sync,
            IntervalTrigger(minutes=CALENDAR_POLL_INTERVAL_MINUTES),
            id="calendar_sync",
            name="Calendar events sync",
            replace_existing=True,
            misfire_grace_time=120,
        )

        # ── Pre-event alerts (every 10 minutes) ──────────────────────
        self.scheduler.add_job(
            self._run_pre_alerts,
            IntervalTrigger(minutes=10),
            id="pre_alerts",
            name="Pre-event alert check",
            replace_existing=True,
            misfire_grace_time=60,
        )

        # ── Post-event alerts (every 15 minutes) ─────────────────────
        self.scheduler.add_job(
            self._run_post_alerts,
            IntervalTrigger(minutes=15),
            id="post_alerts",
            name="Post-event alert check",
            replace_existing=True,
            misfire_grace_time=60,
        )

        # ── Morning digest (IST) ─────────────────────────────────────
        self.scheduler.add_job(
            self._run_morning_digest,
            CronTrigger(
                hour=MORNING_DIGEST_HOUR,
                minute=0,
                timezone="Asia/Kolkata",
            ),
            id="morning_digest",
            name="Morning digest",
            replace_existing=True,
        )

        # ── Evening digest (IST) ─────────────────────────────────────
        self.scheduler.add_job(
            self._run_evening_digest,
            CronTrigger(
                hour=EVENING_DIGEST_HOUR,
                minute=0,
                timezone="Asia/Kolkata",
            ),
            id="evening_digest",
            name="Evening digest",
            replace_existing=True,
        )

        # ── Database cleanup (daily at 3 AM IST) ─────────────────────
        self.scheduler.add_job(
            self._run_cleanup,
            CronTrigger(hour=3, minute=0, timezone="Asia/Kolkata"),
            id="cleanup",
            name="Database cleanup",
            replace_existing=True,
        )

        # ── Gold price refresh (every 5 minutes) ─────────────────────
        self.scheduler.add_job(
            self._refresh_price,
            IntervalTrigger(minutes=5),
            id="price_refresh",
            name="Gold price refresh",
            replace_existing=True,
        )

        logger.info("All scheduled jobs configured")

    # ── Job wrappers (with error handling) ────────────────────────────

    async def _run_news_cycle(self) -> None:
        """Wrapper for news cycle with error handling."""
        try:
            if self.news_engine:
                await self.news_engine.run_cycle()
        except Exception as e:
            logger.exception("News cycle job error: %s", e)
            error_counter.bump("main")

    async def _run_calendar_sync(self) -> None:
        """Wrapper for calendar sync with error handling."""
        try:
            if self.calendar_engine:
                await self.calendar_engine.sync_events()
        except Exception as e:
            logger.exception("Calendar sync job error: %s", e)
            error_counter.bump("main")

    async def _run_pre_alerts(self) -> None:
        """Wrapper for pre-event alerts with error handling."""
        try:
            if self.calendar_engine:
                await self.calendar_engine.check_pre_alerts()
        except Exception as e:
            logger.exception("Pre-alert job error: %s", e)
            error_counter.bump("main")

    async def _run_post_alerts(self) -> None:
        """Wrapper for post-event alerts with error handling."""
        try:
            if self.calendar_engine:
                await self.calendar_engine.check_post_alerts()
        except Exception as e:
            logger.exception("Post-alert job error: %s", e)
            error_counter.bump("main")

    async def _run_morning_digest(self) -> None:
        """Wrapper for morning digest with error handling."""
        try:
            if self.digest_engine:
                await self.digest_engine.send_morning_digest()
        except Exception as e:
            logger.exception("Morning digest job error: %s", e)
            error_counter.bump("main")

    async def _run_evening_digest(self) -> None:
        """Wrapper for evening digest with error handling."""
        try:
            if self.digest_engine:
                await self.digest_engine.send_evening_digest()
        except Exception as e:
            logger.exception("Evening digest job error: %s", e)
            error_counter.bump("main")

    async def _run_cleanup(self) -> None:
        """Wrapper for database cleanup with error handling."""
        try:
            if self.db:
                deleted = self.db.cleanup_old_data(days=30)
                logger.info("Cleanup: removed %d old records", deleted)
        except Exception as e:
            logger.exception("Cleanup job error: %s", e)
            error_counter.bump("main")

    async def _refresh_price(self) -> None:
        """Refresh gold price cache."""
        try:
            if self.price_fetcher:
                await self.price_fetcher.get_price(force_refresh=True)
        except Exception as e:
            logger.exception("Price refresh error: %s", e)
            error_counter.bump("main")

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start all services."""
        if self.bot is None:
            raise RuntimeError("Bot must be initialized before starting")
        if self.scheduler is None:
            raise RuntimeError("Scheduler must be initialized before starting")

        self._running = True

        # Start scheduler
        self.scheduler.start()
        logger.info("Scheduler started")

        # Run initial data fetch
        logger.info("Running initial data fetch...")
        await self._run_calendar_sync()
        await self._run_news_cycle()

        # Send startup notification
        try:
            from bot.formatter import format_health
            stats = self.db.get_stats() if self.db else {}
            price = await self.price_fetcher.get_price() if self.price_fetcher else None
            health_msg = format_health(stats, price)
            startup_msg = (
                "🥇 *GoldPulse Alerts Started!*\n\n"
                + health_msg
            )
            await self.bot.broadcast(startup_msg)
        except Exception as e:
            logger.exception("Could not send startup notification: %s", e)
            error_counter.bump("main")

        logger.info("GoldPulse is running! Press Ctrl+C to stop.")

        # Start bot polling as a background task
        self._polling_task = asyncio.create_task(self.bot.start_polling())

        # Block until shutdown signal is received
        await self.shutdown_event.wait()

    async def shutdown(self) -> None:
        """Gracefully shut down everything."""
        if not self._running:
            return
        logger.info("Shutting down GoldPulse...")
        self._running = False

        # Cancel bot polling task
        if hasattr(self, "_polling_task") and self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass

        # Stop scheduler
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=True)
            logger.info("Scheduler stopped")

        # Close engines
        try:
            if self.news_engine:
                await self.news_engine.close()
        except Exception as e:
            logger.exception("Error closing news engine: %s", e)
            error_counter.bump("main")

        try:
            if self.calendar_engine:
                await self.calendar_engine.close()
        except Exception as e:
            logger.exception("Error closing calendar engine: %s", e)
            error_counter.bump("main")

        # Stop bot
        try:
            if self.bot:
                await self.bot.stop()
        except Exception as e:
            logger.exception("Error stopping bot: %s", e)
            error_counter.bump("main")

        # Close price fetcher
        try:
            if self.price_fetcher:
                await self.price_fetcher.close()
        except Exception as e:
            logger.exception("Error closing price fetcher: %s", e)
            error_counter.bump("main")

        # Close database
        try:
            if self.db:
                self.db.close()
        except Exception as e:
            logger.exception("Error closing database: %s", e)
            error_counter.bump("main")

        logger.info("GoldPulse shutdown complete")


async def main():
    """Main async entry point."""
    app = GoldPulseApp()

    # Handle shutdown signals — set the event instead of calling shutdown() directly
    loop = asyncio.get_running_loop()

    def signal_handler():
        logger.info("Shutdown signal received")
        app.shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await app.init()
        await app.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        error_counter.bump("main")
    finally:
        app.shutdown_event.set()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
