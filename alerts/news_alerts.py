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
from processing.relevance import (
    score_article,
    should_alert,
    extract_gold_angle,
    _concept_hits,
)
from processing.dedup import Deduplicator
from processing.language_filter import is_english_candidate
from config.settings import ALERT_COOLDOWN_MINUTES
from storage.database import Database
from bot.formatter import format_news_alert, is_article_too_old
from utils.logger import get_logger

logger = get_logger("alerts.news")

# Maximum article age in hours for alerts (3 days)
MAX_ARTICLE_AGE_HOURS = 72


def _topic_key(article: Article) -> str:
    """
    Coarse topic cluster used for alert cooldown.

    Articles about the same angle/story (e.g. "📈 Gold bullish") share a
    key, so repeat alerts on the same topic are suppressed within
    ALERT_COOLDOWN_MINUTES. Falls back to the dominant gold concept bucket
    when no angle is detected.
    """
    angle = extract_gold_angle(article.title, article.summary)
    if angle:
        return f"news:{angle}"
    concepts = _concept_hits((article.title or "").lower())
    if concepts:
        return f"news:{min(concepts)}"
    return "news:other"


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
        stats = {
            "fetched": 0,
            "deduped": 0,
            "stored": 0,
            "alerted": 0,
            "filtered": 0,
            "skipped_old": 0,
            "cooldown_skipped": 0,
        }

        try:
            # 1. Fetch from all sources concurrently
            logger.info("Starting news fetch cycle...")
            # Two independent ingestion tracks:
            #  - gold-track: feeds/GDELT queries that require "gold"
            #  - geopolitical-track: world-news feeds + GDELT queries that do
            #    NOT require "gold" (wars, sanctions, central bank moves).
            #    Articles are tagged source_category="geopolitical".
            rss_task = self.rss_fetcher.fetch_all_feeds()
            geo_rss_task = self.rss_fetcher.fetch_geopolitical_feeds()
            gdelt_task = self.gdelt_fetcher.fetch_gold_events()
            geo_gdelt_task = self.gdelt_fetcher.fetch_geopolitical_events()
            rss_articles, geo_rss_articles, gdelt_articles, geo_gdelt_articles = (
                await asyncio.gather(
                    rss_task, geo_rss_task, gdelt_task, geo_gdelt_task,
                    return_exceptions=True,
                )
            )

            # Combine results, handling errors
            all_articles: list[Article] = []
            for name, result in (
                ("RSS", rss_articles),
                ("Geopolitical RSS", geo_rss_articles),
                ("GDELT", gdelt_articles),
                ("Geopolitical GDELT", geo_gdelt_articles),
            ):
                if isinstance(result, list):
                    all_articles.extend(result)
                else:
                    logger.error("%s fetch failed: %s", name, result)

            stats["fetched"] = len(all_articles)
            logger.info("Fetched %d total articles", len(all_articles))

            if not all_articles:
                return stats

            # 2. Get current gold price for context
            gold_price = await self.price_fetcher.get_price()
            price_usd = gold_price.price_usd if gold_price else None

            # 3. Process each article
            for article in all_articles:
                # Drop non-English articles EARLY — single choke point for both
                # RSS and GDELT, before dedup so foreign titles never churn
                # through the fuzzy matcher or pollute the seen-title store.
                if not is_english_candidate(article.title):
                    stats["filtered"] += 1
                    logger.info("Skipped non-English article: %s", article.title[:80])
                    continue

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
                        result = await self._send_article_alert(
                            article, score, gold_price
                        )
                        if result == "sent":
                            stats["alerted"] += 1
                            # Mark as alerted using the returned article ID
                            self.db.mark_article_alerted(stored)
                        elif result == "cooldown":
                            stats["cooldown_skipped"] += 1

            logger.info(
                "News cycle complete: fetched=%d, deduped=%d, stored=%d, alerted=%d, filtered=%d, skipped_old=%d, cooldown_skipped=%d",
                stats["fetched"],
                stats["deduped"],
                stats["stored"],
                stats["alerted"],
                stats["filtered"],
                stats["skipped_old"],
                stats["cooldown_skipped"],
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
            "sent" if the alert was sent, "cooldown" if suppressed by the
            topic cooldown, or "failed" if the send attempt failed.
        """
        try:
            # ── Cooldown: at most one alert per topic cluster per
            # ALERT_COOLDOWN_MINUTES — the same story/angle can't spam.
            topic = _topic_key(article)
            last_sent = self.db.get_last_alert_at(topic)
            if last_sent is not None:
                elapsed_min = (
                    datetime.now(timezone.utc) - last_sent
                ).total_seconds() / 60
                if elapsed_min < ALERT_COOLDOWN_MINUTES:
                    logger.info(
                        "Skipped alert for '%s' (topic '%s' on cooldown, last sent %.0f min ago)",
                        article.title[:60],
                        topic,
                        elapsed_min,
                    )
                    return "cooldown"

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
                self.db.mark_alert_sent(topic)
                logger.info(
                    "Alert sent (score=%.1f): %s",
                    score,
                    article.title[:80],
                )
            else:
                logger.warning("Failed to send alert: %s", article.title[:80])

            return "sent" if sent else "failed"

        except Exception as e:
            logger.error("Alert send error: %s", e, exc_info=True)
            return "failed"

    async def close(self) -> None:
        """Clean up resources."""
        await self.rss_fetcher.close()
        await self.gdelt_fetcher.close()
