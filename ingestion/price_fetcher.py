"""
GoldPulse Alerts — Gold Price Fetcher
=======================================
Fetches live gold price from multiple sources with automatic fallback.

Sources (in priority order):
1. yfinance GC=F (COMEX Gold Futures — most accurate for live price)
2. Yahoo Finance direct API (fallback, no library needed)
3. Google Finance scrape
4. GoldAPI.io (if key provided)

Design decisions:
- yfinance library with GC=F symbol is the primary source for accuracy
- Direct HTTP to Yahoo Finance as fallback
- Multiple fallbacks ensure we almost always get a price
- Price is cached with a TTL to avoid hammering sources
- We fetch both XAU/USD and optionally XAU/INR for Indian traders
"""

import asyncio
import re
import json
from datetime import datetime, timezone
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

import aiohttp

from config.settings import (
    GOLD_PRICE_SOURCE,
    GOLDAPI_KEY,
    PRICE_REFRESH_MINUTES,
    INDIA_MARKUP,
)
from utils.logger import get_logger

logger = get_logger("ingestion.price")


def apply_india_pricing(price_usd: float, inr_rate: float | None) -> float | None:
    """
    India-calibrated INR price: USD spot x USD/INR x India markup.

    The markup (customs duty + GST + dealer premium) is set in
    config/settings.py (INDIA_MARKUP). Every fetch path must go through
    this function so a fallback source can never quietly drop the markup.

    Returns:
        float INR price, or None when the INR rate is unavailable.
    """
    if inr_rate is None or price_usd is None:
        return None
    return price_usd * inr_rate * INDIA_MARKUP


@dataclass(frozen=True)
class GoldPrice:
    """Immutable snapshot of gold price at a point in time."""
    price_usd: float
    price_inr: float | None  # None if INR fetch fails
    change_usd: float | None  # Change from previous close
    change_pct: float | None  # Percentage change
    source: str  # Source name
    fetched_at: datetime

    def format_usd(self) -> str:
        """Format USD price for display."""
        return f"${self.price_usd:,.2f}"

    def format_inr(self) -> str:
        """Format INR price for display (per 10g for MCX context)."""
        if self.price_inr is None:
            return "N/A"
        # MCX gold is per 10 grams, XAU is per troy ounce (31.1035g)
        per_10g = self.price_inr * (10 / 31.1035)
        return f"₹{per_10g:,.0f}/10g"

    def format_change(self) -> str:
        """Format price change with emoji."""
        if self.change_usd is None or self.change_pct is None:
            return ""
        arrow = "🟢" if self.change_usd >= 0 else "🔴"
        sign = "+" if self.change_usd >= 0 else ""
        return f"{arrow} {sign}{self.change_usd:.2f} ({sign}{self.change_pct:.2f}%)"


class PriceFetcher:
    """
    Fetches and caches gold price from multiple sources.

    The price is refreshed every PRICE_REFRESH_MINUTES to balance
    freshness with API rate limits.
    """

    def __init__(self):
        self._cache: GoldPrice | None = None
        self._cache_time: datetime | None = None
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy-init aiohttp session."""
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=15),
                )
        return self._session

    async def close(self) -> None:
        """Clean up the HTTP session and thread pool."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._executor.shutdown(wait=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    @property
    def cached_price(self) -> GoldPrice | None:
        """Return cached price if it's still fresh."""
        if self._cache is None or self._cache_time is None:
            return None
        age = (datetime.now(timezone.utc) - self._cache_time).total_seconds()
        if age > PRICE_REFRESH_MINUTES * 60:
            return None  # Stale
        return self._cache

    async def get_price(self, force_refresh: bool = False) -> GoldPrice | None:
        """
        Get the current gold price. Uses cache if fresh.

        Args:
            force_refresh: Bypass cache and fetch fresh price.

        Returns:
            GoldPrice or None if all sources fail.
        """
        if not force_refresh:
            cached = self.cached_price
            if cached is not None:
                return cached

        async with self._lock:
            # Double-check after acquiring lock
            if not force_refresh:
                cached = self.cached_price
                if cached is not None:
                    return cached

            # Try sources in priority order
            # 1. Swissquote spot gold (XAU/USD) — most accurate spot price
            # 2. yfinance GC=F (COMEX futures, includes premium)
            # 3. Yahoo Finance direct API (fallback)
            # 4. GoldAPI.io (if key provided)
            # 5. Google Finance (scrape)
            # 6. ExchangeRate-API

            sources = [
                ("Swissquote (spot)", self._fetch_swissquote_spot),
                ("yfinance (GC=F)", self._fetch_yfinance_gold),
                ("Yahoo Finance", self._fetch_yahoo_direct),
                ("GoldAPI.io", self._fetch_goldapi) if GOLDAPI_KEY else None,
                ("Google Finance", self._fetch_google_finance),
                ("Exchange Rate API", self._fetch_exchangerate),
            ]
            sources = [s for s in sources if s is not None]

            for name, fetcher in sources:
                try:
                    price = await fetcher()
                    if price:
                        self._cache = price
                        self._cache_time = datetime.now(timezone.utc)
                        logger.info(
                            "Gold price updated: %s (%s)",
                            price.format_usd(),
                            price.source,
                        )
                        return price
                except Exception as e:
                    logger.debug("%s failed: %s", name, e)
                    continue

            logger.warning("Failed to fetch gold price from any source")
            return None

    async def _fetch_swissquote_spot(self) -> GoldPrice | None:
        """
        Fetch spot gold (XAU/USD) from Swissquote forex feed.

        This is the PRIMARY source — returns real spot gold price,
        NOT futures. Spot price matches what traders see on TradingView.

        Swissquote provides free forex feed data including XAU/USD.
        """
        try:
            session = await self._get_session()
            url = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"

            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.debug("Swissquote returned HTTP %d", resp.status)
                    return None
                data = await resp.json()

            if not data or not isinstance(data, list) or len(data) == 0:
                logger.debug("Swissquote returned empty data")
                return None

            # Extract bid/ask from premium profile
            spread_prices = data[0].get("spreadProfilePrices", [])
            if not spread_prices:
                logger.debug("Swissquote no spread prices")
                return None

            # Use first profile (premium) — bid and ask
            bid = float(spread_prices[0].get("bid", 0))
            ask = float(spread_prices[0].get("ask", 0))

            if bid <= 0 or ask <= 0:
                logger.debug("Swissquote invalid bid/ask: %s/%s", bid, ask)
                return None

            # Use mid-price as the spot price
            price_usd = (bid + ask) / 2.0

            # Sanity check
            if price_usd < 100 or price_usd > 50000:
                logger.debug("Swissquote price out of bounds: %.2f", price_usd)
                return None

            # Fetch INR rate and previous close for change calculation
            inr_rate = await self._fetch_inr_rate()
            price_inr = apply_india_pricing(price_usd, inr_rate)

            # For change calculation, use cached previous price if available
            change_usd = None
            change_pct = None
            if self._cache and self._cache.price_usd > 0:
                change_usd = price_usd - self._cache.price_usd
                change_pct = (change_usd / self._cache.price_usd) * 100

            return GoldPrice(
                price_usd=round(price_usd, 2),
                price_inr=round(price_inr, 2) if price_inr else None,
                change_usd=round(change_usd, 2) if change_usd is not None else None,
                change_pct=round(change_pct, 2) if change_pct is not None else None,
                source="Swissquote (spot)",
                fetched_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.debug("Swissquote spot fetch failed: %s", e)
            return None

    async def _fetch_yfinance_gold(self) -> GoldPrice | None:
        """
        Fetch gold price using yfinance library with GC=F (COMEX Gold Futures).

        Attempts to use yfinance for accurate futures price.
        Falls back to None if yfinance fails (Yahoo API changes frequently).

        yfinance is synchronous, so we run it in a thread pool executor.
        """
        try:
            import yfinance as yf

            def _fetch_sync():
                # Try yf.download first (more reliable than Ticker.history)
                data = yf.download("GC=F", period="2d", progress=False)
                if data.empty:
                    # Fallback to Ticker API
                    ticker = yf.Ticker("GC=F")
                    hist = ticker.history(period="2d")
                    if hist.empty:
                        return None
                    price_usd = float(hist["Close"].iloc[-1])
                    prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else None
                    return price_usd, prev_close

                price_usd = float(data["Close"].iloc[-1])
                prev_close = float(data["Close"].iloc[-2]) if len(data) >= 2 else None
                return price_usd, prev_close

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(self._executor, _fetch_sync)

            if result is None:
                logger.debug("yfinance GC=F returned no data")
                return None

            price_usd, prev_close = result

            # Sanity check
            if price_usd < 100 or price_usd > 50000:
                logger.debug("yfinance GC=F price out of bounds: %.2f", price_usd)
                return None

            # Calculate change
            change_usd = None
            change_pct = None
            if prev_close and prev_close > 0:
                change_usd = price_usd - prev_close
                change_pct = (change_usd / prev_close) * 100

            # Fetch INR rate for Indian price
            inr_rate = await self._fetch_inr_rate()
            price_inr = apply_india_pricing(price_usd, inr_rate)

            logger.info(
                "yfinance GC=F: $%.2f (prev: %s, change: %s)",
                price_usd,
                f"${prev_close:.2f}" if prev_close else "N/A",
                f"${change_usd:+.2f}" if change_usd is not None else "N/A",
            )

            return GoldPrice(
                price_usd=round(price_usd, 2),
                price_inr=round(price_inr, 2) if price_inr else None,
                change_usd=round(change_usd, 2) if change_usd is not None else None,
                change_pct=round(change_pct, 2) if change_pct is not None else None,
                source="yfinance (GC=F)",
                fetched_at=datetime.now(timezone.utc),
            )

        except ImportError:
            logger.debug("yfinance not installed, skipping")
            return None
        except Exception as e:
            logger.debug("yfinance GC=F fetch failed: %s", e)
            return None

    async def _fetch_yahoo_direct(self) -> GoldPrice | None:
        """
        Fetch gold price directly from Yahoo Finance API.

        This bypasses the yfinance library to avoid its rate-limiting issues.
        Uses Yahoo's v8 finance API endpoint directly.
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1d&range=2d"

            session = await self._get_session()
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    logger.debug("Yahoo Finance returned HTTP %d", resp.status)
                    return None
                data = await resp.json()

            result = data.get("chart", {}).get("result", [])
            if not result:
                return None

            quote = result[0]
            meta = quote.get("meta", {})

            price_usd = meta.get("regularMarketPrice") or meta.get("previousClose")
            prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")

            if price_usd is None:
                # Try from indicators
                indicators = quote.get("indicators", {}).get("quote", [{}])
                if indicators:
                    closes = indicators[0].get("close", [])
                    # Get last non-None close
                    for c in reversed(closes):
                        if c is not None:
                            price_usd = c
                            break

            if price_usd is None:
                return None

            try:
                price_usd = float(price_usd)
            except (ValueError, TypeError):
                logger.debug("Yahoo Finance returned non-numeric price: %s", price_usd)
                return None
            if price_usd < 100 or price_usd > 50000:
                logger.debug("Yahoo Finance price out of bounds: %.2f", price_usd)
                return None
            change_usd = None
            change_pct = None
            if prev_close:
                try:
                    prev_close = float(prev_close)
                except (ValueError, TypeError):
                    prev_close = None
            if prev_close and prev_close > 0:
                change_usd = price_usd - prev_close
                change_pct = (change_usd / prev_close) * 100

            inr_rate = await self._fetch_inr_rate()
            price_inr = apply_india_pricing(price_usd, inr_rate)

            return GoldPrice(
                price_usd=round(price_usd, 2),
                price_inr=round(price_inr, 2) if price_inr else None,
                change_usd=round(change_usd, 2) if change_usd is not None else None,
                change_pct=round(change_pct, 2) if change_pct is not None else None,
                source="Yahoo Finance",
                fetched_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.debug("Yahoo Finance direct fetch failed: %s", e)
            return None

    async def _fetch_goldapi(self) -> GoldPrice | None:
        """Fetch from GoldAPI.io (requires API key)."""
        if not GOLDAPI_KEY:
            return None

        try:
            headers = {"x-access-token": GOLDAPI_KEY}
            session = await self._get_session()
            async with session.get(
                "https://www.goldapi.io/api/XAU/USD",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.debug("GoldAPI returned HTTP %d", resp.status)
                    return None
                data = await resp.json()

            price_usd = data.get("price")
            if price_usd is None:
                return None

            try:
                price_usd = float(price_usd)
            except (ValueError, TypeError):
                logger.debug("GoldAPI returned non-numeric price: %s", price_usd)
                return None

            if price_usd < 100 or price_usd > 50000:
                logger.debug("GoldAPI price out of bounds: %.2f", price_usd)
                return None

            prev_close = data.get("prev_close_price")
            change_usd = None
            change_pct = None
            if prev_close:
                try:
                    prev_close = float(prev_close)
                except (ValueError, TypeError):
                    prev_close = None
            if prev_close and prev_close > 0:
                change_usd = price_usd - prev_close
                change_pct = (change_usd / prev_close) * 100

            inr_rate = await self._fetch_inr_rate()
            price_inr = apply_india_pricing(price_usd, inr_rate)

            return GoldPrice(
                price_usd=round(price_usd, 2),
                price_inr=round(price_inr, 2) if price_inr else None,
                change_usd=round(change_usd, 2) if change_usd is not None else None,
                change_pct=round(change_pct, 2) if change_pct is not None else None,
                source="GoldAPI",
                fetched_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.debug("GoldAPI fetch failed: %s", e)
            return None

    async def _fetch_google_finance(self) -> GoldPrice | None:
        """
        Fetch gold price from Google Finance.

        Scrapes the gold price from Google's finance page.
        This is a reliable fallback since Google Finance is always up.
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            url = "https://www.google.com/finance/quote/GC=COMEX"

            session = await self._get_session()
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()

            # Extract price from Google Finance page
            # Look for the main price element
            price_match = re.search(
                r'data-last-price="([\d,.]+)"',
                html
            )
            if not price_match:
                # Alternative pattern
                price_match = re.search(
                    r'class="[^"]*YMlKec[^"]*">\$?([\d,.]+)',
                    html
                )

            if not price_match:
                return None

            price_str = price_match.group(1).replace(",", "")
            try:
                price_usd = float(price_str)
            except (ValueError, TypeError):
                logger.debug("Google Finance returned non-numeric price: %s", price_str)
                return None

            if price_usd < 100 or price_usd > 50000:
                return None

            # Try to get change
            change_match = re.search(
                r'data-last-normal-market-change="([-\d,.]+)"',
                html
            )
            change_usd = None
            change_pct = None
            if change_match:
                try:
                    change_usd = float(change_match.group(1).replace(",", ""))
                except (ValueError, TypeError):
                    change_usd = None

            inr_rate = await self._fetch_inr_rate()
            price_inr = apply_india_pricing(price_usd, inr_rate)

            return GoldPrice(
                price_usd=round(price_usd, 2),
                price_inr=round(price_inr, 2) if price_inr else None,
                change_usd=round(change_usd, 2) if change_usd is not None else None,
                change_pct=round(change_pct, 2) if change_pct is not None else None,
                source="Google Finance",
                fetched_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.debug("Google Finance fetch failed: %s", e)
            return None

    async def _fetch_exchangerate(self) -> GoldPrice | None:
        """
        Fetch gold price from ExchangeRate-API (uses XAU/USD rate).

        This is a reliable free source that provides currency rates including XAU.
        """
        try:
            session = await self._get_session()
            async with session.get(
                "https://open.er-api.com/v6/latest/XAU",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            # The API returns 1 XAU = X USD
            rates = data.get("rates", {})
            usd_rate = rates.get("USD")

            if usd_rate is None:
                return None

            # If 1 XAU = 0.000X USD, we need to invert
            try:
                usd_rate = float(usd_rate)
            except (ValueError, TypeError):
                logger.debug("ExchangeRate-API returned non-numeric rate: %s", usd_rate)
                return None

            if usd_rate < 1:
                price_usd = 1.0 / usd_rate
            else:
                price_usd = usd_rate

            if price_usd < 100 or price_usd > 50000:  # Sanity check
                return None

            inr_rate = await self._fetch_inr_rate()
            price_inr = apply_india_pricing(price_usd, inr_rate)

            return GoldPrice(
                price_usd=round(price_usd, 2),
                price_inr=round(price_inr, 2) if price_inr else None,
                change_usd=None,
                change_pct=None,
                source="ExchangeRate-API",
                fetched_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.debug("ExchangeRate-API fetch failed: %s", e)
            return None

    async def _fetch_inr_rate(self) -> float | None:
        """Fetch USD/INR exchange rate for Indian price conversion."""
        try:
            session = await self._get_session()
            async with session.get(
                "https://open.er-api.com/v6/latest/USD",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                rate = data.get("rates", {}).get("INR")
                if rate:
                    try:
                        rate = float(rate)
                    except (ValueError, TypeError):
                        logger.debug("INR rate non-numeric: %s", rate)
                        return None
                    if not (50 < rate < 150):
                        logger.debug("INR rate out of bounds: %.2f", rate)
                        return None
                    logger.debug("USD/INR rate: %.2f", rate)
                return rate if rate else None
        except Exception as e:
            logger.debug("INR rate fetch failed (non-critical): %s", e)
            return None
