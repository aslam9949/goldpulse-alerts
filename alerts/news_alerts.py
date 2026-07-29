"""
GoldPulse Alerts — News Alert Engine
=======================================
Orchestrates the news alert pipeline:

1. Fetch articles from RSS + GDELT
2. Deduplicate against seen articles
3. Score for relevance
4. Store in database
5. Send instant alerts for high-scoring items

This is the main "glue" that connects ingestion → processing → alerts.
"""

import asyncio
from datetime import datetime, timezone

from ingestion.rss_fetcher import RSSFetcher, Article
from ingestion.gdelt_fetcher import GDELTFetcher
from ingestion.price_fetcher import PriceFetcher
from processing.relevance import score_article, should_alert, extract_gold_angle
from processing.dedup import Deduplicator
from storage.database import Database
from bot.formatter import format_news_alert, is_article_too_old
from utils.logger import get_logger

logger = get_logger("alerts.news")

# Maximum article age in hours for alerts (3 days)
MAX_ARTICLE_AGE_HOURS = 72


class NewsAlertEngine:
    """
    Processes news feeds and sends alerts for high-relevance articles.

    Pipeline:
    fetch → dedup → score → store → alert (if threshold met)
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
        self.rss_fetcher = RSSFetcher()
        self.gdelt_fetcher = GDELTFetcher()
        self.dedup = Deduplicator(db)

    async def run_cycle(self) -> dict[str, int]:
        """
        Run one complete news ingestion + alert cycle.

        Returns:
            Stats dict with counts: fetched, deduplicated, stored, alerted.
        """
        stats = {"fetched": 0, "deduped": 0, "stored": 0, "alerted": 0, "skipped_old": 0}

        try:
            # 1. Fetch from all sources concurrently
            logger.info("Starting news fetch cycle...")
            rss_task = self.rss_fetcher.fetch_all_feeds()
            gdelt_task = self.gdelt_fetcher.fetch_gold_events()
            rss_articles, gdelt_articles = await asyncio.gather(
                rss_task, gdelt_task, return_exceptions=True
            )

            # Combine results, handling errors
            all_articles: list[Article] = []
            if isinstance(rss_articles, list):
                all_articles.extend(rss_articles)
            else:
                logger.error("RSS fetch failed: %s", rss_articles)

            if isinstance(gdelt_articles, list):
                all_articles.extend(gdelt_articles)
            else:
                logger.error("GDELT fetch failed: %s", gdelt_articles)

            stats["fetched"] = len(all_articles)
            logger.info("Fetched %d total articles", len(all_articles))

            if not all_articles:
                return stats

            # 2. Get current gold price for context
            gold_price = await self.price_fetcher.get_price()
            price_usd = gold_price.price_usd if gold_price else None

            # 3. Process each article
            for article in all_articles:
                # Skip very old articles
                if is_article_too_old(article.published_at, MAX_ARTICLE_AGE_HOURS):
                    stats["skipped_old"] += 1
                    continue

                # Dedup check
                if self.dedup.is_duplicate(article.url, article.title):
                    stats["deduped"] += 1
                    continue

                # Score
                score = score_article(article, price_usd)

                # Store
                stored = self.db.insert_article(
                    url=article.url,
                    title=article.title,
                    summary=article.summary,
                    source=article.source,
                    published_at=(
                        article.published_at.isoformat()
                        if article.published_at
                        else None
                    ),
                    relevance_score=score,
                    gold_price=price_usd,
                    tags=article.tags,
                )

                # Mark as seen only AFTER successful store to avoid TOCTOU race
                self.dedup.mark_seen(article.url, article.title)

                if stored:
                    stats["stored"] += 1

                    # 4. Alert if high relevance
                    if should_alert(score):
                        sent = await self._send_article_alert(
                            article, score, gold_price
                        )
                        if sent:
                            stats["alerted"] += 1
                            # Mark as alerted using the returned article ID
                            self.db.mark_article_alerted(stored)

            logger.info(
                "News cycle complete: fetched=%d, deduped=%d, stored=%d, alerted=%d, skipped_old=%d",
                stats["fetched"],
                stats["deduped"],
                stats["stored"],
                stats["alerted"],
                stats["skipped_old"],
            )

        except Exception as e:
            logger.error("News cycle error: %s", e, exc_info=True)

        return stats

    async def _send_article_alert(
        self,
        article: Article,
        score: float,
        gold_price,
    ) -> bool:
        """
        Format and send a news alert with inline keyboard button for the link.

        Args:
            article: The article to alert on
            score: Relevance score
            gold_price: Current GoldPrice object

        Returns:
            True if alert was sent successfully.
        """
        try:
            # Extract gold angle for context
            gold_angle = extract_gold_angle(article.title, article.summary)

            # Format the alert message — returns (message_text, url)
            message, url = format_news_alert(
                article=article,
                score=score,
                gold_price=gold_price,
                gold_angle=gold_angle,
            )

            # Broadcast to all configured chats
            sent = await self.bot.broadcast_with_button(
                text=message,
                button_text="📖 Read full article",
                button_url=url,
            )

            if sent:
                logger.info(
                    "Alert sent (score=%.1f): %s",
                    score,
                    article.title[:80],
                )
            else:
                logger.warning("Failed to send alert: %s", article.title[:80])

            return sent > 0

        except Exception as e:
            logger.error("Alert send error: %s", e, exc_info=True)
            return False

    async def close(self) -> None:
        """Clean up resources."""
        await self.rss_fetcher.close()
        await self.gdelt_fetcher.close()
