"""
GoldPulse Alerts — RSS Feed Fetcher
=====================================
Fetches and parses RSS feeds from gold-related news sources.

Design decisions:
- feedparser is sync, so we fetch HTTP with aiohttp (async) and parse in executor
- Each feed has a trust_score that feeds into relevance scoring
- We normalize all entries into a consistent Article dataclass
- Deduplication happens downstream (in processing/dedup.py)
"""

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Optional
import time

import aiohttp
import feedparser

from config.settings import RSS_FEEDS, GEOPOLITICAL_RSS_FEEDS
from utils.logger import get_logger
from utils import error_counter

logger = get_logger("ingestion.rss")


@dataclass
class Article:
    """Normalized article from any RSS source."""
    url: str
    title: str
    summary: str | None
    source: str
    trust_score: int  # 1-10, from feed config
    published_at: datetime | None = None
    url_hash: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.url_hash:
            self.url_hash = hashlib.sha256(self.url.encode()).hexdigest()[:16]


class RSSFetcher:
    """
    Fetches articles from configured RSS feeds.

    Fetches all feeds concurrently and returns normalized Article objects.
    Failed feeds are logged but don't block other feeds.
    """

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy-init aiohttp session."""
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={
                        "User-Agent": "GoldPulse/1.0 (Gold Trading Alert Bot)",
                    },
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

    async def fetch_all_feeds(self) -> list[Article]:
        """
        Fetch all configured gold RSS feeds concurrently.

        Returns:
            List of Article objects from all feeds, sorted by published_at.
        """
        return await self._fetch_feed_list(RSS_FEEDS, tags=[])

    async def fetch_geopolitical_feeds(self) -> list[Article]:
        """
        Fetch the geopolitical/world-news feeds (source_category=geopolitical).

        These feeds do NOT require "gold" in the query — they surface shocks
        (wars, sanctions, central bank moves) that move gold as a safe haven.
        Articles are tagged so the relevance scorer routes them through the
        impact-based rubric instead of the title-keyword gate.

        Returns:
            List of Article objects, tagged source_category=geopolitical.
        """
        return await self._fetch_feed_list(GEOPOLITICAL_RSS_FEEDS, tags=["geopolitical"])

    async def _fetch_feed_list(
        self, feeds: list[tuple[str, str, int]], tags: list[str]
    ) -> list[Article]:
        """Fetch a list of (name, url, trust) feeds concurrently, tagging results."""
        tasks = [
            self._fetch_single_feed(name, url, trust, tags)
            for name, url, trust in feeds
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_articles: list[Article] = []
        for i, result in enumerate(results):
            feed_name = feeds[i][0]
            if isinstance(result, Exception):
                logger.error("Feed '%s' failed: %s", feed_name, result)
            elif result:
                all_articles.extend(result)
                logger.info(
                    "Feed '%s': fetched %d articles", feed_name, len(result)
                )

        # Sort by published time (newest first), articles without time go last
        all_articles.sort(
            key=lambda a: a.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        logger.info("Total articles fetched: %d", len(all_articles))
        return all_articles

    async def _fetch_single_feed(
        self, name: str, url: str, trust_score: int, tags: list[str] | None = None
    ) -> list[Article]:
        """
        Fetch and parse a single RSS feed.

        Args:
            name: Human-readable feed name
            url: RSS feed URL
            trust_score: Trust level for this source (1-10)
            tags: Tags to attach to each article (e.g. source_category)

        Returns:
            List of parsed Article objects
        """
        try:
            session = await self._get_session()
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning(
                        "Feed '%s' returned HTTP %d", name, response.status
                    )
                    return []
                content = await response.text()

            # feedparser is CPU-bound, run in executor
            loop = asyncio.get_running_loop()
            feed = await loop.run_in_executor(None, feedparser.parse, content)

            articles = []
            for entry in feed.entries:
                article = self._parse_entry(entry, name, trust_score, tags)
                if article:
                    articles.append(article)

            return articles

        except asyncio.TimeoutError:
            logger.warning("Feed '%s' timed out", name)
            return []
        except aiohttp.ClientError as e:
            logger.warning("Feed '%s' network error: %s", name, e)
            return []
        except Exception as e:  # unexpected — surface it
            logger.exception("Feed '%s' unexpected error: %s", name, e)
            error_counter.bump("ingestion.rss")
            return []

    def _parse_entry(
        self, entry: dict, source: str, trust_score: int, tags: list[str] | None = None
    ) -> Article | None:
        """
        Parse a single RSS entry into an Article.

        Args:
            entry: feedparser entry dict
            source: Source name
            trust_score: Trust level

        Returns:
            Article or None if entry is invalid
        """
        url = getattr(entry, "link", None) or entry.get("link", "")
        title = getattr(entry, "title", None) or entry.get("title", "")

        if not url or not title:
            return None

        # Validate URL scheme — reject anything that isn't HTTPS
        if not url.startswith("https://"):
            logger.warning("Rejected non-HTTPS URL: %s", url[:80])
            return None

        # Extract summary, strip HTML tags
        summary = getattr(entry, "summary", None) or entry.get("summary", "")
        if summary:
            # Basic HTML stripping
            summary = re.sub(r"<[^>]+>", "", summary).strip()
            # Truncate long summaries
            if len(summary) > 500:
                summary = summary[:497] + "..."

        # Parse published date
        published_at = self._parse_date(entry)

        return Article(
            url=url,
            title=title.strip(),
            summary=summary or None,
            source=source,
            trust_score=trust_score,
            published_at=published_at,
            tags=tags or [],
        )

    def _parse_date(self, entry: dict) -> datetime | None:
        """Parse entry date into a timezone-aware datetime."""
        # feedparser provides parsed struct_time
        date_tuple = getattr(entry, "published_parsed", None)
        if date_tuple:
            try:
                from calendar import timegm
                ts = timegm(date_tuple)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except (ValueError, OverflowError):
                pass

        # Fallback: try raw string parsing
        raw_date = getattr(entry, "published", None) or entry.get("published")
        if raw_date:
            try:
                return parsedate_to_datetime(raw_date)
            except (ValueError, TypeError):
                pass

        return None
