"""
GoldPulse Alerts — GDELT Fetcher
===================================
Fetches gold-relevant geopolitical events from GDELT Doc API.

Design decisions:
- GDELT Doc API is free and provides structured news data
- We query for gold-specific terms to find geopolitical events
  that could impact gold prices (wars, sanctions, central bank moves)
- Results are converted to the same Article format for unified processing
- We use the 'artlist' format for easy parsing
"""

import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass

import aiohttp

from config.settings import GDELT_BASE_URL, GDELT_QUERIES
from utils.logger import get_logger
from ingestion.rss_fetcher import Article

logger = get_logger("ingestion.gdelt")


class GDELTFetcher:
    """
    Fetches gold-relevant articles from GDELT Doc API.

    GDELT monitors worldwide news and provides structured access.
    We query it specifically for gold-related geopolitical events
    that traditional RSS feeds might miss.
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

    async def fetch_gold_events(self) -> list[Article]:
        """
        Fetch gold-relevant articles from GDELT.

        Staggers queries with delays to avoid rate limiting (429).
        GDELT is sensitive to concurrent requests.

        Returns:
            List of Article objects with gold-relevant geopolitical content.
        """
        seen_urls: set[str] = set()
        all_articles: list[Article] = []

        # Stagger requests to avoid 429 rate limits
        for i, query in enumerate(GDELT_QUERIES):
            if i > 0:
                await asyncio.sleep(2)  # 2 second delay between queries
            try:
                result = await self._query_gdelt(query)
                for article in result:
                    if article.url not in seen_urls:
                        seen_urls.add(article.url)
                        all_articles.append(article)
            except Exception as e:
                logger.error("GDELT query '%s' failed: %s", query, e)

        logger.info("GDELT: fetched %d unique gold-related articles", len(all_articles))
        return all_articles

    async def _query_gdelt(self, query: str) -> list[Article]:
        """
        Execute a single GDELT Doc API query.

        Args:
            query: Search query string

        Returns:
            List of Article objects from this query.
        """
        try:
            session = await self._get_session()
            params = {
                "query": query,
                "mode": "artlist",
                "maxrecords": 25,
                "format": "json",
                "sort": "DateDesc",
                "timespan": "24h",  # Last 24 hours
            }

            async with session.get(GDELT_BASE_URL, params=params) as response:
                if response.status != 200:
                    logger.warning(
                        "GDELT query '%s' returned HTTP %d", query, response.status
                    )
                    return []

                data = await response.json()

            articles = []
            for item in data.get("articles", []):
                article = self._parse_gdelt_article(item, query)
                if article:
                    articles.append(article)

            return articles

        except asyncio.TimeoutError:
            logger.warning("GDELT query '%s' timed out", query)
            return []
        except Exception as e:
            logger.error("GDELT query '%s' error: %s", query, e)
            return []

    def _parse_gdelt_article(self, item: dict, query: str) -> Article | None:
        """
        Parse a GDELT article into our Article format.

        Args:
            item: GDELT article dict
            query: The query that found this article

        Returns:
            Article or None if invalid
        """
        url = item.get("url", "")
        title = item.get("title", "")

        if not url or not title:
            return None

        # Validate URL scheme — reject anything that isn't HTTPS
        if not url.startswith("https://"):
            logger.warning("Rejected non-HTTPS URL: %s", url[:80])
            return None

        # Parse date
        date_str = item.get("seendate", "")
        published_at = None
        if date_str:
            try:
                # GDELT date format: 20240115T120000Z
                published_at = datetime.strptime(
                    date_str, "%Y%m%dT%H%M%SZ"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        # Extract domain as source
        source = item.get("domain", "GDELT")
        if source.startswith("www."):
            source = source[4:]

        # GDELT articles are geopolitical by nature — tag them
        tags = ["geopolitics", f"gdelt:{query}"]

        return Article(
            url=url,
            title=title.strip(),
            summary=None,  # GDELT socialimage is an image URL, not a text summary
            source=f"GDELT ({source})",
            trust_score=6,  # Moderate trust — GDELT aggregates, doesn't curate
            published_at=published_at,
            tags=tags,
        )
