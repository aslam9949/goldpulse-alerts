"""
GoldPulse Alerts — Database Layer
===================================
SQLite storage for articles, events, and dedup tracking.

Design decisions:
- Single connection with WAL mode for concurrent reads (bot + scheduler)
- All writes go through this module (single source of truth)
- Timestamps stored as ISO 8601 strings (SQLite has no native datetime)
- Relevance scores stored so we can re-threshold without re-fetching
"""

import sqlite3
import threading
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from config.settings import DB_PATH
from utils.logger import get_logger

logger = get_logger("storage.database")

# Module-level constant: all table and index creation SQL.
# Extracted from _create_tables to keep the method body small and make the
# schema definition easy to find, diff, and review.
_CREATE_TABLES_SQL = """
    -- Stores ingested news articles
    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        summary TEXT,
        source TEXT,
        published_at TEXT,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
        relevance_score REAL DEFAULT 0,
        gold_price_at_fetch REAL,
        sent_as_alert INTEGER DEFAULT 0,
        sent_in_digest INTEGER DEFAULT 0,
        tags TEXT DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- Stores economic calendar events
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT UNIQUE,
        title TEXT NOT NULL,
        country TEXT,
        currency TEXT,
        impact TEXT,
        event_time TEXT NOT NULL,
        forecast TEXT,
        previous TEXT,
        actual TEXT,
        gold_implication TEXT,
        pre_alert_sent INTEGER DEFAULT 0,
        post_alert_sent INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- URL deduplication (separate from articles for speed)
    CREATE TABLE IF NOT EXISTS seen_urls (
        url_hash TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        seen_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- Title dedup (fuzzy match tracking)
    CREATE TABLE IF NOT EXISTS seen_titles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title_normalized TEXT NOT NULL,
        url TEXT NOT NULL,
        seen_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- User settings (future: multi-user support)
    CREATE TABLE IF NOT EXISTS user_settings (
        chat_id TEXT PRIMARY KEY,
        alert_threshold INTEGER DEFAULT 7,
        digest_enabled INTEGER DEFAULT 1,
        morning_hour INTEGER DEFAULT 8,
        evening_hour INTEGER DEFAULT 20,
        timezone TEXT DEFAULT 'Asia/Kolkata',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- Alert cooldowns: last-alert-time per topic cluster, so the same
    -- story/event can't spam the chat within ALERT_COOLDOWN_MINUTES.
    CREATE TABLE IF NOT EXISTS alert_cooldowns (
        topic TEXT PRIMARY KEY,
        last_alert_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- Indexes for common queries
    CREATE INDEX IF NOT EXISTS idx_articles_sent
        ON articles(sent_as_alert, relevance_score);
    CREATE INDEX IF NOT EXISTS idx_articles_published
        ON articles(published_at DESC);
    CREATE INDEX IF NOT EXISTS idx_events_time
        ON events(event_time);
    CREATE INDEX IF NOT EXISTS idx_events_alerts
        ON events(pre_alert_sent, post_alert_sent);
    CREATE INDEX IF NOT EXISTS idx_seen_urls_hash
        ON seen_urls(url_hash);
    CREATE INDEX IF NOT EXISTS idx_seen_titles_at
        ON seen_titles(seen_at);
"""

# Allowlist of tables cleanup_old_data may operate on, mapped to each
# table's real datetime column. The seen_* tables use `seen_at` (not
# `created_at`); using the right column per table is what makes cleanup
# actually run instead of throwing "no such column: created_at" and
# rolling back the whole sweep every night.
_CLEANUP_TABLE_COLUMNS: dict[str, str] = {
    "articles": "created_at",
    "seen_urls": "seen_at",
    "seen_titles": "seen_at",
    "alert_cooldowns": "last_alert_at",
}


class Database:
    """Thread-safe SQLite database wrapper for GoldPulse."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        # Guards _conn creation and all write operations for thread safety.
        self._lock = threading.Lock()

    def connect(self) -> None:
        """Open connection and enable WAL mode for better concurrency."""
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # Assign _conn before _create_tables so the property doesn't recurse.
        self._conn = conn
        self._create_tables()
        logger.info("Database connected: %s", self.db_path)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Database connection closed")

    @property
    def conn(self) -> sqlite3.Connection:
        """Get the active connection, connecting if needed (lock-protected)."""
        if self._conn is None:
            with self._lock:
                # Double-check: another thread may have connected while we waited.
                if self._conn is None:
                    self.connect()
        assert self._conn is not None
        return self._conn

    # -- Table Creation ---------------------------------------------------

    def _create_tables(self) -> None:
        """Create all tables if they don't exist."""
        self.conn.executescript(_CREATE_TABLES_SQL)
        self.conn.commit()
        logger.debug("Database tables initialized")

    # -- Article Operations -----------------------------------------------

    def insert_article(
        self,
        url: str,
        title: str,
        summary: str | None,
        source: str,
        published_at: str | None,
        relevance_score: float = 0.0,
        gold_price: float | None = None,
        tags: list[str] | None = None,
    ) -> int | None:
        """
        Insert a new article. Returns article ID if inserted, None if duplicate.
        """
        try:
            cursor = self.conn.execute(
                """INSERT INTO articles
                   (url, title, summary, source, published_at,
                    relevance_score, gold_price_at_fetch, tags)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    url, title, summary, source, published_at,
                    relevance_score, gold_price,
                    json.dumps(tags or []),
                ),
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint" in str(exc):
                # URL already exists -- not an error, just a duplicate
                return None
            logger.exception("IntegrityError inserting article: %s", title[:80])
            return None

    def mark_article_alerted(self, article_id: int) -> None:
        """Mark an article as sent in an instant alert."""
        try:
            self.conn.execute(
                "UPDATE articles SET sent_as_alert = 1 WHERE id = ?",
                (article_id,),
            )
            self.conn.commit()
        except sqlite3.Error:
            logger.exception(
                "Failed to mark article %d as alerted", article_id
            )
            self.conn.rollback()

    def mark_article_digested(self, article_id: int) -> None:
        """Mark an article as included in a digest."""
        try:
            self.conn.execute(
                "UPDATE articles SET sent_in_digest = 1 WHERE id = ?",
                (article_id,),
            )
            self.conn.commit()
        except sqlite3.Error:
            logger.exception(
                "Failed to mark article %d as digested", article_id
            )
            self.conn.rollback()

    def get_recent_articles(
        self, hours: int = 24, limit: int = 50, min_score: float = 0.0
    ) -> list[dict[str, Any]]:
        """Get recent articles, ordered by relevance."""
        try:
            cutoff = datetime.now(timezone.utc).isoformat()
            rows = self.conn.execute(
                """SELECT * FROM articles
                   WHERE relevance_score >= ?
                     AND datetime(created_at) >= datetime(?, '-' || ? || ' hours')
                   ORDER BY relevance_score DESC, published_at DESC
                   LIMIT ?""",
                (min_score, cutoff, hours, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to get recent articles")
            return []

    def get_unsent_high_score_articles(
        self, threshold: float, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get high-relevance articles not yet sent as alerts."""
        try:
            rows = self.conn.execute(
                """SELECT * FROM articles
                   WHERE sent_as_alert = 0
                     AND relevance_score >= ?
                   ORDER BY relevance_score DESC
                   LIMIT ?""",
                (threshold, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to get unsent high score articles")
            return []

    def get_digest_articles(
        self, hours: int = 12, limit: int = 15
    ) -> list[dict[str, Any]]:
        """Get top articles for digest (not yet in a digest)."""
        try:
            cutoff = datetime.now(timezone.utc).isoformat()
            rows = self.conn.execute(
                """SELECT * FROM articles
                   WHERE sent_in_digest = 0
                     AND datetime(created_at) >= datetime(?, '-' || ? || ' hours')
                   ORDER BY relevance_score DESC, published_at DESC
                   LIMIT ?""",
                (cutoff, hours, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to get digest articles")
            return []

    # -- Event Operations -------------------------------------------------

    def insert_event(
        self,
        event_id: str,
        title: str,
        country: str,
        currency: str,
        impact: str,
        event_time: str,
        forecast: str | None = None,
        previous: str | None = None,
        actual: str | None = None,
        gold_implication: str | None = None,
    ) -> bool:
        """Insert a calendar event. Returns True if new, False if exists."""
        try:
            self.conn.execute(
                """INSERT INTO events
                   (event_id, title, country, currency, impact,
                    event_time, forecast, previous, actual, gold_implication)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id, title, country, currency, impact,
                    event_time, forecast, previous, actual, gold_implication,
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def update_event_actual(
        self, event_id: str, actual: str
    ) -> bool:
        """Update an event with the actual value when released."""
        try:
            cursor = self.conn.execute(
                "UPDATE events SET actual = ? WHERE event_id = ? AND actual IS NULL",
                (actual, event_id),
            )
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error:
            logger.exception(
                "Failed to update event actual for %s", event_id
            )
            self.conn.rollback()
            return False

    def mark_event_pre_alerted(self, event_id: str) -> None:
        """Mark that the pre-event alert was sent."""
        try:
            self.conn.execute(
                "UPDATE events SET pre_alert_sent = 1 WHERE event_id = ?",
                (event_id,),
            )
            self.conn.commit()
        except sqlite3.Error:
            logger.exception(
                "Failed to mark event %s as pre-alerted", event_id
            )
            self.conn.rollback()

    def mark_event_post_alerted(self, event_id: str) -> None:
        """Mark that the post-event alert was sent."""
        try:
            self.conn.execute(
                "UPDATE events SET post_alert_sent = 1 WHERE event_id = ?",
                (event_id,),
            )
            self.conn.commit()
        except sqlite3.Error:
            logger.exception(
                "Failed to mark event %s as post-alerted", event_id
            )
            self.conn.rollback()

    def get_upcoming_events(
        self, hours_ahead: int = 24
    ) -> list[dict[str, Any]]:
        """Get upcoming high-impact USD events."""
        try:
            now = datetime.now(timezone.utc)
            future = now + timedelta(hours=hours_ahead)
            now_str = now.isoformat()
            future_str = future.isoformat()
            rows = self.conn.execute(
                """SELECT * FROM events
                   WHERE datetime(event_time) > datetime(?)
                     AND datetime(event_time) <= datetime(?)
                   ORDER BY event_time ASC""",
                (now_str, future_str),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to get upcoming events")
            return []

    def get_unalerted_pre_events(
        self, minutes_before: int = 120
    ) -> list[dict[str, Any]]:
        """Get events that need pre-alerts (within N minutes, not yet alerted)."""
        try:
            now = datetime.now(timezone.utc)
            future = now + timedelta(minutes=minutes_before)
            now_str = now.isoformat()
            future_str = future.isoformat()
            rows = self.conn.execute(
                """SELECT * FROM events
                   WHERE pre_alert_sent = 0
                     AND datetime(event_time) > datetime(?)
                     AND datetime(event_time) <= datetime(?)
                   ORDER BY event_time ASC""",
                (now_str, future_str),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to get unalerted pre events")
            return []

    def get_recent_events_without_actual(self) -> list[dict[str, Any]]:
        """Get events that happened but don't have actual values yet."""
        try:
            now = datetime.now(timezone.utc)
            past = now - timedelta(hours=4)
            now_str = now.isoformat()
            past_str = past.isoformat()
            rows = self.conn.execute(
                """SELECT * FROM events
                   WHERE actual IS NULL
                     AND datetime(event_time) <= datetime(?)
                     AND datetime(event_time) >= datetime(?)
                   ORDER BY event_time DESC""",
                (now_str, past_str),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to get recent events without actual")
            return []

    def get_recent_events_with_actuals_not_post_alerted(
        self, hours: int = 4
    ) -> list[dict[str, Any]]:
        """Get recent events that have actual data but haven't been post-alerted."""
        try:
            now = datetime.now(timezone.utc)
            past = now - timedelta(hours=hours)
            past_str = past.isoformat()
            rows = self.conn.execute(
                """SELECT * FROM events
                   WHERE actual IS NOT NULL
                     AND post_alert_sent = 0
                     AND datetime(event_time) >= datetime(?)
                   ORDER BY event_time DESC""",
                (past_str,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception(
                "Failed to get recent events with actuals not post alerted"
            )
            return []

    # -- URL Dedup --------------------------------------------------------

    def is_url_seen(self, url_hash: str) -> bool:
        """Check if a URL hash has been seen before."""
        try:
            row = self.conn.execute(
                "SELECT 1 FROM seen_urls WHERE url_hash = ?",
                (url_hash,),
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            logger.exception("Failed to check if URL is seen: %s", url_hash)
            return False

    def mark_url_seen(self, url_hash: str, url: str) -> None:
        """Record a URL hash as seen."""
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO seen_urls (url_hash, url) VALUES (?, ?)",
                (url_hash, url),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass

    def check_and_mark_url_seen(self, url_hash: str, url: str) -> bool:
        """Atomically check whether a URL hash was seen and mark it if not.

        Returns True if the URL was already seen, False if it is new.
        This eliminates the TOCTOU race between ``is_url_seen`` and
        ``mark_url_seen`` that exists when they are called separately.
        """
        try:
            self.conn.execute(
                "INSERT INTO seen_urls (url_hash, url) VALUES (?, ?)",
                (url_hash, url),
            )
            self.conn.commit()
            return False  # inserted => not previously seen
        except sqlite3.IntegrityError:
            return True  # duplicate => already seen

    # -- Title Dedup ------------------------------------------------------

    def get_recent_titles(self, hours: int = 48) -> list[str]:
        """Get normalized titles from the last N hours for fuzzy matching."""
        try:
            cutoff = datetime.now(timezone.utc).isoformat()
            rows = self.conn.execute(
                """SELECT title_normalized FROM seen_titles
                   WHERE datetime(seen_at) >= datetime(?, '-' || ? || ' hours')""",
                (cutoff, hours),
            ).fetchall()
            return [r["title_normalized"] for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to get recent titles")
            return []

    def mark_title_seen(self, title_normalized: str, url: str) -> None:
        """Record a normalized title as seen."""
        try:
            self.conn.execute(
                "INSERT INTO seen_titles (title_normalized, url) VALUES (?, ?)",
                (title_normalized, url),
            )
            self.conn.commit()
        except sqlite3.Error:
            logger.exception(
                "Failed to mark title as seen: %s", title_normalized[:80]
            )
            self.conn.rollback()

    # -- Stats ------------------------------------------------------------

    def get_stats(self) -> dict[str, int]:
        """Get quick stats for health checks."""
        try:
            stats: dict[str, int] = {}
            stats["total_articles"] = self.conn.execute(
                "SELECT COUNT(*) FROM articles"
            ).fetchone()[0]
            stats["alerts_sent"] = self.conn.execute(
                "SELECT COUNT(*) FROM articles WHERE sent_as_alert = 1"
            ).fetchone()[0]
            stats["total_events"] = self.conn.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
            stats["urls_seen"] = self.conn.execute(
                "SELECT COUNT(*) FROM seen_urls"
            ).fetchone()[0]
            return stats
        except sqlite3.Error:
            logger.exception("Failed to retrieve database stats")
            return {
                "total_articles": -1,
                "alerts_sent": -1,
                "total_events": -1,
                "urls_seen": -1,
            }

    # -- Alert Cooldown Operations ----------------------------------------
    # Topic-cluster cooldown: at most one alert per topic per
    # ALERT_COOLDOWN_MINUTES, so the same story/event can't spam the chat.

    def get_last_alert_at(self, topic: str) -> datetime | None:
        """
        Timestamp of the last alert sent for a topic cluster.

        Returns:
            Aware datetime, or None if no alert has been sent for this topic.
        """
        try:
            row = self.conn.execute(
                "SELECT last_alert_at FROM alert_cooldowns WHERE topic = ?",
                (topic,),
            ).fetchone()
            if not row:
                return None
            value = row["last_alert_at"]
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except (sqlite3.Error, ValueError):
            logger.exception("Failed to read cooldown for topic '%s'", topic)
            return None

    def mark_alert_sent(self, topic: str) -> None:
        """Record that an alert was just sent for a topic cluster (now)."""
        try:
            self.conn.execute(
                """INSERT INTO alert_cooldowns (topic, last_alert_at)
                   VALUES (?, ?)
                   ON CONFLICT(topic)
                   DO UPDATE SET last_alert_at = excluded.last_alert_at""",
                (topic, datetime.now(timezone.utc).isoformat()),
            )
            self.conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to record alert sent for topic '%s'", topic)
            self.conn.rollback()

    # -- Cleanup ----------------------------------------------------------

    def cleanup_old_data(self, days: int = 30) -> int:
        """Remove data older than N days. Returns count of deleted rows."""
        cutoff = datetime.now(timezone.utc).isoformat()
        total = 0

        try:
            for table, col in _CLEANUP_TABLE_COLUMNS.items():
                # Table and column names come only from the allowlist dict,
                # so f-string interpolation is safe here -- no user input.
                cursor = self.conn.execute(
                    f"DELETE FROM {table} WHERE datetime({col}) < datetime(?, '-' || ? || ' days')",
                    (cutoff, days),
                )
                total += cursor.rowcount

            self.conn.commit()
            if total > 0:
                logger.info("Cleaned up %d old records (>%d days)", total, days)
            return total
        except sqlite3.Error:
            logger.exception("Failed to clean up old data (>%d days)", days)
            self.conn.rollback()
            return 0
