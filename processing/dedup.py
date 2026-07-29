"""
GoldPulse Alerts — Deduplication
==================================
Prevents duplicate alerts using two strategies:

1. URL hash dedup — exact URL match (fast, O(1) lookup)
2. Fuzzy title dedup — catches near-duplicate articles from different
   sources reporting the same news (e.g., Reuters and Kitco covering
   the same gold price move)

Design decisions:
- We use rapidfuzz for fuzzy matching (much faster than difflib for our use case)
- Title normalization: lowercase, strip punctuation, collapse whitespace
- Similarity threshold of 85% catches most duplicates without false positives
- We check against the last 48 hours of titles (not the full database)
"""

import re
import hashlib

from rapidfuzz import fuzz

from storage.database import Database
from utils.logger import get_logger

logger = get_logger("processing.dedup")

# Similarity threshold for fuzzy title matching (0-100)
# 92% is the sweet spot — catches rewrites of the same story
# without flagging genuinely different articles about the same topic.
# 85% was too aggressive and ate different articles (e.g., different
# stock recommendations from the same broker).
TITLE_SIMILARITY_THRESHOLD = 92


def normalize_title(title: str) -> str:
    """
    Normalize a title for fuzzy comparison.

    Steps:
    1. Lowercase
    2. Remove all non-alphanumeric except spaces
    3. Collapse multiple spaces
    4. Strip leading/trailing whitespace

    This ensures "Gold surges 2%!" and "gold surges 2%"
    are treated as the same title.
    """
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def compute_url_hash(url: str) -> str:
    """
    Compute a short hash of a URL for dedup storage.

    We use SHA-256 truncated to 16 chars — collision risk is
    negligible for our scale (thousands of articles, not millions).
    """
    return hashlib.sha256(url.encode()).hexdigest()[:16]


class Deduplicator:
    """
    Handles article deduplication using URL and title matching.

    Usage:
        dedup = Deduplicator(db)
        if dedup.is_duplicate(article.url, article.title):
            skip  # Already seen
        else:
            dedup.mark_seen(article.url, article.title)
            process(article)
    """

    def __init__(self, db: Database):
        self.db = db

    def is_duplicate(self, url: str, title: str) -> bool:
        """
        Check if an article is a duplicate.

        Checks:
        1. Exact URL hash match (fast path)
        2. Fuzzy title match against recent articles (slow path)

        Args:
            url: Article URL
            title: Article title

        Returns:
            True if this is a duplicate.
        """
        if url is None or title is None:
            return False

        # Fast path: URL hash
        url_hash = compute_url_hash(url)
        if self.db.is_url_seen(url_hash):
            logger.debug("Dedup: URL already seen: %s", url[:80])
            return True

        # Slow path: Fuzzy title match
        normalized = normalize_title(title)
        recent_titles = self.db.get_recent_titles(hours=48)

        # Exact-match early exit before O(n) fuzzy loop
        if normalized in recent_titles:
            logger.info("Dedup: Exact title match: '%s'", title[:60])
            return True

        for seen_title in recent_titles:
            similarity = fuzz.ratio(normalized, seen_title)
            if similarity >= TITLE_SIMILARITY_THRESHOLD:
                logger.info(
                    "Dedup: Title match (%.0f%%): '%s' ~ '%s'",
                    similarity,
                    title[:60],
                    seen_title[:60],
                )
                return True

        return False

    def mark_seen(self, url: str, title: str) -> None:
        """
        Mark an article as seen for future dedup checks.

        Records both the URL hash and the normalized title.
        """
        if url is None or title is None:
            return

        url_hash = compute_url_hash(url)
        self.db.mark_url_seen(url_hash, url)

        normalized = normalize_title(title)
        self.db.mark_title_seen(normalized, url)

        logger.debug("Dedup: Marked seen: %s", url[:80])
