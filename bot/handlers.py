"""
GoldPulse Alerts — Telegram Bot Handlers
===========================================
Handles all Telegram commands and message sending.

Design decisions:
- Uses aiogram 3.x (async, modern, well-maintained)
- Commands are simple and focused on gold trading workflow
- Uses plain text (no Markdown) to avoid escaping issues
- Error handling wraps every handler (bot should never crash)
- Sending is done through a central function for consistency
- Inline keyboard buttons for article links and menu system
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
)
from aiogram.filters import Command

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ADMIN_CHAT_IDS
from storage.database import Database
from ingestion.price_fetcher import PriceFetcher
from bot.formatter import (
    format_news_alert,
    format_price_update,
    format_help,
    format_health,
    format_digest,
    format_calendar_alert,
    clean_text,
    _score_indicator,
)
from utils.logger import get_logger
from utils import error_counter

logger = get_logger("bot.handlers")

IST = ZoneInfo("Asia/Kolkata")


# ── Menu Keyboard Builders ─────────────────────────────────────────────

def build_main_menu() -> InlineKeyboardMarkup:
    """Build the main menu inline keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📈 Latest Gold News", callback_data="menu_latest"),
                InlineKeyboardButton(text="💰 Gold Price", callback_data="menu_price"),
            ],
            [
                InlineKeyboardButton(text="📅 Macro Events", callback_data="menu_upcoming"),
                InlineKeyboardButton(text="📊 Digest", callback_data="menu_digest"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Settings", callback_data="menu_settings"),
                InlineKeyboardButton(text="✅ Health Check", callback_data="menu_health"),
            ],
            [
                InlineKeyboardButton(text="❓ Help", callback_data="menu_help"),
            ],
        ]
    )


def build_back_menu() -> InlineKeyboardMarkup:
    """Build a keyboard with just a 'Back to Menu' button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="menu_main")],
        ]
    )


def build_close_menu() -> InlineKeyboardMarkup:
    """Build a keyboard with Back and Close buttons."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Back to Menu", callback_data="menu_main"),
                InlineKeyboardButton(text="✖️ Close", callback_data="menu_close"),
            ],
        ]
    )


class GoldPulseBot:
    """
    Telegram bot for GoldPulse Alerts.

    Manages the bot instance, dispatcher, and provides methods
    for sending alerts and handling commands.
    """

    def __init__(
        self,
        db: Database,
        price_fetcher: PriceFetcher,
    ):
        self.db = db
        self.price_fetcher = price_fetcher

        # Initialize bot and dispatcher
        from aiogram.client.default import DefaultBotProperties
        from aiogram.types import LinkPreviewOptions
        self.bot = Bot(
            token=TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(
                link_preview=LinkPreviewOptions(is_disabled=True),
            ),
        )
        self.dp = Dispatcher()
        self.router = Router()

        # Register handlers
        self._register_handlers()
        self._register_callbacks()
        self.dp.include_router(self.router)

    def _register_handlers(self) -> None:
        """Register all command handlers."""

        @self.router.message(Command("start"))
        async def cmd_start(message: Message):
            if str(message.chat.id) not in [TELEGRAM_CHAT_ID] + ADMIN_CHAT_IDS:
                return
            try:
                await message.answer(
                    "🥇 Welcome to GoldPulse Alerts!\n\n"
                    "Your 24/7 gold trading intelligence bot. "
                    "I monitor news, economic events, and gold prices "
                    "to keep you informed.\n\n"
                    "Tap the button below to get started!",
                    reply_markup=build_main_menu(),
                )
            except Exception as e:
                logger.exception("Start command error: %s", e)
                error_counter.bump("bot.handlers")
                await message.answer("⚠️ Welcome! Type /help to see available commands.")

        @self.router.message(Command("menu"))
        async def cmd_menu(message: Message):
            if str(message.chat.id) not in [TELEGRAM_CHAT_ID] + ADMIN_CHAT_IDS:
                return
            try:
                await message.answer(
                    "🥇 GoldPulse Menu\n\n"
                    "Choose an option below:",
                    reply_markup=build_main_menu(),
                )
            except Exception as e:
                logger.exception("Menu command error: %s", e)
                error_counter.bump("bot.handlers")
                await message.answer("⚠️ Failed to load menu. Try again later.")

        @self.router.message(Command("help"))
        async def cmd_help(message: Message):
            if str(message.chat.id) not in [TELEGRAM_CHAT_ID] + ADMIN_CHAT_IDS:
                return
            try:
                await message.answer(format_help(), reply_markup=build_back_menu())
            except Exception as e:
                logger.exception("Help command error: %s", e)
                error_counter.bump("bot.handlers")
                await message.answer("⚠️ Failed to load help. Try again later.")

        @self.router.message(Command("price"))
        async def cmd_price(message: Message):
            if str(message.chat.id) not in [TELEGRAM_CHAT_ID] + ADMIN_CHAT_IDS:
                return
            try:
                price = await self.price_fetcher.get_price()
                await message.answer(format_price_update(price), reply_markup=build_back_menu())
            except Exception as e:
                logger.exception("Price command error: %s", e)
                error_counter.bump("bot.handlers")
                await message.answer("⚠️ Failed to fetch gold price. Try again later.")

        @self.router.message(Command("latest"))
        async def cmd_latest(message: Message):
            if str(message.chat.id) not in [TELEGRAM_CHAT_ID] + ADMIN_CHAT_IDS:
                return
            try:
                text = await self._build_latest_text()
                await message.answer(text, reply_markup=build_back_menu())
            except Exception as e:
                logger.exception("Latest command error: %s", e)
                error_counter.bump("bot.handlers")
                await message.answer("⚠️ Failed to fetch news. Try again later.")

        @self.router.message(Command("digest"))
        async def cmd_digest(message: Message):
            if str(message.chat.id) not in [TELEGRAM_CHAT_ID] + ADMIN_CHAT_IDS:
                return
            try:
                text = await self._build_digest_text()
                await message.answer(text, reply_markup=build_back_menu())
            except Exception as e:
                logger.exception("Digest command error: %s", e)
                error_counter.bump("bot.handlers")
                await message.answer("⚠️ Failed to generate digest. Try again later.")

        @self.router.message(Command("upcoming"))
        async def cmd_upcoming(message: Message):
            if str(message.chat.id) not in [TELEGRAM_CHAT_ID] + ADMIN_CHAT_IDS:
                return
            try:
                text = await self._build_upcoming_text()
                await message.answer(text, reply_markup=build_back_menu())
            except Exception as e:
                logger.exception("Upcoming command error: %s", e)
                error_counter.bump("bot.handlers")
                await message.answer("⚠️ Failed to fetch events. Try again later.")

        @self.router.message(Command("settings"))
        async def cmd_settings(message: Message):
            if str(message.chat.id) not in [TELEGRAM_CHAT_ID] + ADMIN_CHAT_IDS:
                return
            try:
                text = self._build_settings_text()
                await message.answer(text, reply_markup=build_back_menu())
            except Exception as e:
                logger.exception("Settings command error: %s", e)
                error_counter.bump("bot.handlers")
                await message.answer("⚠️ Failed to load settings. Try again later.")

        @self.router.message(Command("health"))
        async def cmd_health(message: Message):
            if str(message.chat.id) not in [TELEGRAM_CHAT_ID] + ADMIN_CHAT_IDS:
                return
            try:
                stats = self.db.get_stats()
                price = await self.price_fetcher.get_price()
                await message.answer(
                    format_health(stats, price, error_counter.snapshot()),
                    reply_markup=build_back_menu(),
                )
            except Exception as e:
                logger.exception("Health check error: %s", e)
                error_counter.bump("bot.handlers")
                await message.answer("⚠️ Health check failed. Please try again later.")

    def _register_callbacks(self) -> None:
        """Register all callback query handlers for menu buttons."""

        @self.router.callback_query(F.data == "menu_main")
        async def cb_menu_main(callback: CallbackQuery):
            """Handle 'Back to Menu' button."""
            try:
                await callback.message.edit_text(
                    "🥇 GoldPulse Menu\n\n"
                    "Choose an option below:",
                    reply_markup=build_main_menu(),
                )
                await callback.answer()
            except Exception as e:
                logger.exception("Menu callback error: %s", e)
                error_counter.bump("bot.handlers")
                await callback.answer("⚠️ Error loading menu", show_alert=True)

        @self.router.callback_query(F.data == "menu_close")
        async def cb_menu_close(callback: CallbackQuery):
            """Handle 'Close' button — delete the message."""
            try:
                await callback.message.delete()
                await callback.answer()
            except Exception as e:
                logger.exception("Close callback error: %s", e)
                error_counter.bump("bot.handlers")
                await callback.answer()

        @self.router.callback_query(F.data == "menu_latest")
        async def cb_menu_latest(callback: CallbackQuery):
            """Handle 'Latest Gold News' button."""
            try:
                text = await self._build_latest_text()
                await callback.message.edit_text(text, reply_markup=build_close_menu())
                await callback.answer()
            except Exception as e:
                logger.exception("Latest callback error: %s", e)
                error_counter.bump("bot.handlers")
                await callback.answer("⚠️ Error fetching news", show_alert=True)

        @self.router.callback_query(F.data == "menu_price")
        async def cb_menu_price(callback: CallbackQuery):
            """Handle 'Gold Price' button."""
            try:
                price = await self.price_fetcher.get_price()
                text = format_price_update(price)
                await callback.message.edit_text(text, reply_markup=build_close_menu())
                await callback.answer()
            except Exception as e:
                logger.exception("Price callback error: %s", e)
                error_counter.bump("bot.handlers")
                await callback.answer("⚠️ Error fetching price", show_alert=True)

        @self.router.callback_query(F.data == "menu_upcoming")
        async def cb_menu_upcoming(callback: CallbackQuery):
            """Handle 'Macro Events' button."""
            try:
                text = await self._build_upcoming_text()
                await callback.message.edit_text(text, reply_markup=build_close_menu())
                await callback.answer()
            except Exception as e:
                logger.exception("Upcoming callback error: %s", e)
                error_counter.bump("bot.handlers")
                await callback.answer("⚠️ Error fetching events", show_alert=True)

        @self.router.callback_query(F.data == "menu_digest")
        async def cb_menu_digest(callback: CallbackQuery):
            """Handle 'Digest' button."""
            try:
                text = await self._build_digest_text()
                await callback.message.edit_text(text, reply_markup=build_close_menu())
                await callback.answer()
            except Exception as e:
                logger.exception("Digest callback error: %s", e)
                error_counter.bump("bot.handlers")
                await callback.answer("⚠️ Error generating digest", show_alert=True)

        @self.router.callback_query(F.data == "menu_settings")
        async def cb_menu_settings(callback: CallbackQuery):
            """Handle 'Settings' button."""
            try:
                text = self._build_settings_text()
                await callback.message.edit_text(text, reply_markup=build_close_menu())
                await callback.answer()
            except Exception as e:
                logger.exception("Settings callback error: %s", e)
                error_counter.bump("bot.handlers")
                await callback.answer("⚠️ Error loading settings", show_alert=True)

        @self.router.callback_query(F.data == "menu_health")
        async def cb_menu_health(callback: CallbackQuery):
            """Handle 'Health Check' button."""
            try:
                stats = self.db.get_stats()
                price = await self.price_fetcher.get_price()
                text = format_health(stats, price, error_counter.snapshot())
                await callback.message.edit_text(text, reply_markup=build_close_menu())
                await callback.answer()
            except Exception as e:
                logger.exception("Health callback error: %s", e)
                error_counter.bump("bot.handlers")
                await callback.answer("⚠️ Error checking health", show_alert=True)

        @self.router.callback_query(F.data == "menu_help")
        async def cb_menu_help(callback: CallbackQuery):
            """Handle 'Help' button."""
            try:
                text = format_help()
                await callback.message.edit_text(text, reply_markup=build_close_menu())
                await callback.answer()
            except Exception as e:
                logger.exception("Help callback error: %s", e)
                error_counter.bump("bot.handlers")
                await callback.answer("⚠️ Error loading help", show_alert=True)

    # ── Text Builders ──────────────────────────────────────────────────

    async def _build_latest_text(self) -> str:
        """Build the 'Latest Gold News' text."""
        articles = self.db.get_recent_articles(hours=6, limit=10, min_score=3.0)
        if not articles:
            return "📰 No recent gold news found. I'm watching!"

        price = await self.price_fetcher.get_price()
        lines = ["📰 Recent Gold News\n"]

        for i, art in enumerate(articles[:10], 1):
            score = art.get("relevance_score", 0)
            title = clean_text(art.get("title", "Untitled"))
            source = clean_text(art.get("source", ""))

            indicator = _score_indicator(score)

            lines.append(f"{indicator} {i}. {title}")
            lines.append(f"   {source} | Score: {score}")
            lines.append("")

        if price:
            lines.append(f"🥇 Gold: {price.format_usd()}")

        return "\n".join(lines)

    async def _build_upcoming_text(self) -> str:
        """Build the 'Upcoming Events' text."""
        from config.settings import CALENDAR_LOOKAHEAD_DAYS
        # Convert days to hours for the database query
        hours_ahead = CALENDAR_LOOKAHEAD_DAYS * 24
        events = self.db.get_upcoming_events(hours_ahead=hours_ahead)
        if not events:
            return f"📅 No major high-impact USD events in the next {CALENDAR_LOOKAHEAD_DAYS} days."

        lines = ["📅 Upcoming Gold-Moving Events\n"]
        for evt in events[:10]:
            title = clean_text(evt.get("title", "Event"))
            evt_time = evt.get("event_time", "")

            try:
                if isinstance(evt_time, str):
                    dt = datetime.fromisoformat(evt_time)
                else:
                    dt = evt_time
                time_str = dt.astimezone(IST).strftime("%b %d, %I:%M %p IST")
            except (ValueError, TypeError):
                time_str = str(evt_time)

            lines.append(f"⏰ {title}")
            lines.append(f"   {time_str}")
            lines.append("")

        return "\n".join(lines)

    async def _build_digest_text(self) -> str:
        """Build the 'Digest' text."""
        articles = self.db.get_digest_articles(hours=12, limit=10)
        upcoming = self.db.get_upcoming_events(hours_ahead=24)
        price = await self.price_fetcher.get_price()

        now_ist = datetime.now(IST)
        time_label = "Morning" if now_ist.hour < 14 else "Evening"

        return format_digest(
            title=f"{time_label} Digest",
            articles=articles,
            upcoming_events=upcoming,
            gold_price=price,
        )

    @staticmethod
    def _build_settings_text() -> str:
        """Build the 'Settings' text."""
        from config.settings import ALERT_THRESHOLD, MORNING_DIGEST_HOUR, EVENING_DIGEST_HOUR
        return (
            "⚙️ Current Settings\n\n"
            f"🔔 Alert threshold: {ALERT_THRESHOLD}/10\n"
            f"🌅 Morning digest: {MORNING_DIGEST_HOUR}:00 IST\n"
            f"🌆 Evening digest: {EVENING_DIGEST_HOUR}:00 IST\n\n"
            "Edit .env to change settings. Per-user settings coming soon."
        )

    # ── Alert & Broadcast ──────────────────────────────────────────────

    @staticmethod
    def _get_broadcast_targets() -> list[str]:
        """Return deduplicated list of chat IDs to broadcast to."""
        targets = [TELEGRAM_CHAT_ID]
        from config.settings import ADMIN_CHAT_IDS
        for admin_id in ADMIN_CHAT_IDS:
            if admin_id not in targets:
                targets.append(admin_id)
        return targets

    async def send_alert(
        self,
        chat_id: str,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        """
        Send a formatted alert to a Telegram chat.
        """
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
            )
            return True
        except Exception as e:
            logger.exception("Failed to send alert to %s: %s", chat_id, e)
            error_counter.bump("bot.handlers")
            return False

    async def broadcast(self, text: str) -> int:
        """
        Send an alert to all configured chat IDs.
        """
        targets = self._get_broadcast_targets()

        success = 0
        for chat_id in targets:
            if await self.send_alert(chat_id, text):
                success += 1
        return success

    async def broadcast_with_button(
        self,
        text: str,
        button_text: str,
        button_url: str | None,
    ) -> int:
        """
        Send an alert with an inline keyboard button.
        """
        reply_markup = None
        if button_url:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=button_text, url=button_url)]
                ]
            )

        targets = self._get_broadcast_targets()

        success = 0
        for chat_id in targets:
            if await self.send_alert(chat_id, text, reply_markup=reply_markup):
                success += 1
        return success

    async def register_commands(self) -> None:
        """Register bot commands with Telegram (shows blue Menu button)."""
        try:
            commands = [
                BotCommand(command="menu", description="📋 Open the menu"),
                BotCommand(command="price", description="💰 Current gold price"),
                BotCommand(command="latest", description="📈 Recent gold news"),
                BotCommand(command="digest", description="📊 Today's digest"),
                BotCommand(command="upcoming", description="📅 Macro events"),
                BotCommand(command="settings", description="⚙️ Bot settings"),
                BotCommand(command="health", description="✅ Health check"),
                BotCommand(command="help", description="❓ Help"),
            ]
            await self.bot.set_my_commands(commands)
            logger.info("Bot commands registered with Telegram")
        except Exception as e:
            logger.exception("Failed to register commands: %s", e)
            error_counter.bump("bot.handlers")

    async def start_polling(self) -> None:
        """Start the bot with long polling."""
        logger.info("Starting GoldPulse bot...")
        try:
            # Register commands first
            await self.register_commands()

            await self.bot.delete_webhook(drop_pending_updates=True)
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.exception("Bot polling error: %s", e)
            error_counter.bump("bot.handlers")
            raise

    async def stop(self) -> None:
        """Gracefully stop the bot."""
        logger.info("Stopping GoldPulse bot...")
        try:
            await self.dp.stop_polling()
            await self.bot.session.close()
        except Exception as e:
            logger.exception("Bot stop error: %s", e)
            error_counter.bump("bot.handlers")
