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
import json
from datetime import datetime, timezone
from dataclasses import dataclass

import aiohttp

from config.settings import (
    GDELT_BASE_URL,
    GDELT_QUERIES,
    GDELT_GEOPOLITICAL_QUERIES,
)
from utils.logger import get_logger
from utils import error_counter
from ingestion.rss_fetcher import Article

logger = get_logger("ingestion.gdelt")

# GDELT Doc API is rate limited to roughly 5 requests/min per IP.
# Space queries out and back off when the API reports HTTP 429.
GDELT_QUERY_DELAY_SECONDS = 3
MAX_GDELT_RETRIES = 3


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
        return await self._fetch_queries(GDELT_QUERIES, tags=[])

    async def fetch_geopolitical_events(self) -> list[Article]:
        """
        Fetch geopolitical/macro-shock articles from GDELT.

        These queries do NOT require "gold" — they target wars, sanctions,
        central bank moves, rate decisions, etc. that move gold as a safe
        haven. Articles are tagged source_category="geopolitical" so the
        relevance scorer routes them through the impact-based rubric rather
        than the title-keyword gate.

        Returns:
            List of Article objects tagged ["geopolitical", ...].
        """
        return await self._fetch_queries(
            GDELT_GEOPOLITICAL_QUERIES, tags=["geopolitical"]
        )

    async def _fetch_queries(self, queries: list[str], tags: list[str]) -> list[Article]:
        """
        Run a list of GDELT queries sequentially, dedup by URL, tag results.

        Args:
            queries: GDELT query strings to run, in order.
            tags: Extra source_category tags to attach to each article.

        Returns:
            Unique Article objects across all queries.
        """
        seen_urls: set[str] = set()
        all_articles: list[Article] = []

        # Stagger requests to avoid 429 rate limits
        for i, query in enumerate(queries):
            if i > 0:
                await asyncio.sleep(GDELT_QUERY_DELAY_SECONDS)
            try:
                result = await self._query_gdelt(query, tags)
                for article in result:
                    if article.url not in seen_urls:
                        seen_urls.add(article.url)
                        all_articles.append(article)
            except Exception as e:  # unexpected — surface it
                logger.exception("GDELT query '%s' unexpected error: %s", query, e)
                error_counter.bump("ingestion.gdelt")

        logger.info("GDELT: fetched %d unique articles", len(all_articles))
        return all_articles

    async def _query_gdelt(self, query: str, tags: list[str] | None = None) -> list[Article]:
        """
        Execute a single GDELT Doc API query.

        Retries on HTTP 429 via :meth:`_get_gdelt_with_retry`, and skips
        responses with malformed JSON bodies instead of crashing the cycle.

        Args:
            query: Search query string

        Returns:
            List of Article objects from this query, or [] when the query
            ultimately failed or returned unparseable data.
        """
        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": 25,
            "format": "json",
            "sort": "DateDesc",
            "timespan": "24h",  # Last 24 hours
        }

        text = await self._get_gdelt_with_retry(query, params)
        if text is None:
            return []

        # GDELT occasionally returns a malformed JSON body even on HTTP 200 —
        # skip that response instead of failing the whole fetch cycle.
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("GDELT query '%s' returned malformed JSON, skipping", query)
            return []

        if not isinstance(data, dict):
            logger.warning(
                "GDELT query '%s' returned unexpected JSON shape, skipping", query
            )
            return []

        articles = []
        for item in data.get("articles", []):
            article = self._parse_gdelt_article(item, query, tags)
            if article:
                articles.append(article)

        return articles

    async def _get_gdelt_with_retry(self, query: str, params: dict) -> str | None:
        """
        GET the GDELT Doc API, retrying on HTTP 429 with backoff.

        On rate limiting, honors the ``Retry-After`` header when present,
        otherwise backs off exponentially (2s, 4s, 8s), capped at
        ``MAX_GDELT_RETRIES`` attempts.

        Args:
            query: Search query string (for logging)
            params: GDELT Doc API query parameters

        Returns:
            Response body text on success, or None when the request
            ultimately failed or was rate limited past the retry cap.
        """
        for attempt in range(MAX_GDELT_RETRIES):
            try:
                session = await self._get_session()
                async with session.get(GDELT_BASE_URL, params=params) as response:
                    if response.status == 429:
                        if attempt >= MAX_GDELT_RETRIES - 1:
                            logger.warning(
                                "GDELT query '%s' still rate limited after %d retries, skipping",
                                query,
                                MAX_GDELT_RETRIES,
                            )
                            return None

                        delay = 2 ** (attempt + 1)  # 2s, 4s, 8s
                        retry_after = response.headers.get("Retry-After")
                        if retry_after:
                            try:
                                delay = max(int(retry_after), 1)
                            except ValueError:
                                pass  # fall back to exponential backoff

                        logger.warning(
                            "GDELT query '%s' rate limited (HTTP 429), retrying in %ds "
                            "(retry %d/%d)",
                            query,
                            delay,
                            attempt + 1,
                            MAX_GDELT_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        continue

                    if response.status != 200:
                        logger.warning(
                            "GDELT query '%s' returned HTTP %d", query, response.status
                        )
                        return None

                    return await response.text()

            except asyncio.TimeoutError:
                logger.warning("GDELT query '%s' timed out", query)
                return None
            except aiohttp.ClientError as exc:
                logger.warning("GDELT query '%s' request failed: %s", query, exc)
                return None

        return None

    def _parse_gdelt_article(
        self, item: dict, query: str, tags: list[str] | None = None
    ) -> Article | None:
        """
        Parse a GDELT article into our Article format.

        Args:
            item: GDELT article dict
            query: The query that found this article
            tags: Extra source_category tags (e.g. ["geopolitical"])

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

        # GDELT articles are geopolitical by nature — tag them.
        # Extra source_category tags (e.g. ["geopolitical"]) are prepended so
        # the scorer can route this article through the impact rubric.
        combined_tags = list(tags or []) + ["geopolitics", f"gdelt:{query}"]

        return Article(
            url=url,
            title=title.strip(),
            summary=None,  # GDELT socialimage is an image URL, not a text summary
            source=f"GDELT ({source})",
            trust_score=6,  # Moderate trust — GDELT aggregates, doesn't curate
            published_at=published_at,
            tags=combined_tags,
        )
