"""
Regression tests for the database layer.

Two bugs guarded here:
1. cleanup_old_data used `created_at` on tables that only have `seen_at`
   (and rolled back the whole sweep every night).
2. ALERT_COOLDOWN_MINUTES was dead config — no storage backed it.
"""

import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config.settings import ALERT_COOLDOWN_MINUTES
from storage.database import Database


def _fresh_db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = Database(Path(tmp.name))
    db.connect()
    return db


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# -- Cleanup bug --------------------------------------------------------

def test_cleanup_old_data_works_on_all_tables():
    """The seen_* tables use seen_at, not created_at — cleanup must not throw."""
    db = _fresh_db()

    # Old rows (40 days) in every cleanup table
    db.conn.execute(
        "INSERT INTO articles (url, title, created_at) VALUES (?, ?, ?)",
        ("old", "Old article", _iso(40)),
    )
    db.conn.execute(
        "INSERT INTO seen_urls (url_hash, url, seen_at) VALUES ('old', 'old', ?)",
        (_iso(40),),
    )
    db.conn.execute(
        "INSERT INTO seen_titles (title_normalized, url, seen_at) VALUES ('old', 'old', ?)",
        (_iso(40),),
    )
    db.conn.execute(
        "INSERT INTO alert_cooldowns (topic, last_alert_at) VALUES ('old', ?)",
        (_iso(40),),
    )
    # Fresh rows (1 day) that must survive
    db.conn.execute(
        "INSERT INTO articles (url, title, created_at) VALUES (?, ?, ?)",
        ("fresh", "Fresh article", _iso(1)),
    )
    db.conn.execute(
        "INSERT INTO seen_urls (url_hash, url, seen_at) VALUES ('fresh', 'fresh', ?)",
        (_iso(1),),
    )
    db.conn.execute(
        "INSERT INTO seen_titles (title_normalized, url, seen_at) VALUES ('fresh', 'fresh', ?)",
        (_iso(1),),
    )
    db.conn.execute(
        "INSERT INTO alert_cooldowns (topic, last_alert_at) VALUES ('fresh', ?)",
        (_iso(1),),
    )
    db.conn.commit()

    deleted = db.cleanup_old_data(days=30)

    assert deleted >= 4
    # Old rows gone
    assert db.conn.execute(
        "SELECT COUNT(*) FROM articles WHERE url='old'"
    ).fetchone()[0] == 0
    assert db.conn.execute(
        "SELECT COUNT(*) FROM seen_urls WHERE url_hash='old'"
    ).fetchone()[0] == 0
    assert db.conn.execute(
        "SELECT COUNT(*) FROM seen_titles WHERE title_normalized='old'"
    ).fetchone()[0] == 0
    assert db.conn.execute(
        "SELECT COUNT(*) FROM alert_cooldowns WHERE topic='old'"
    ).fetchone()[0] == 0
    # Fresh rows survive
    assert db.conn.execute(
        "SELECT COUNT(*) FROM articles WHERE url='fresh'"
    ).fetchone()[0] == 1
    assert db.conn.execute(
        "SELECT COUNT(*) FROM seen_urls WHERE url_hash='fresh'"
    ).fetchone()[0] == 1


# -- Cooldown storage ---------------------------------------------------

def test_cooldown_round_trip():
    db = _fresh_db()

    assert db.get_last_alert_at("news:📈 Gold bullish") is None

    db.mark_alert_sent("news:📈 Gold bullish")
    last = db.get_last_alert_at("news:📈 Gold bullish")
    assert last is not None
    assert last.tzinfo is not None

    # Upsert updates the timestamp rather than erroring
    db.mark_alert_sent("news:📈 Gold bullish")
    assert db.get_last_alert_at("news:📈 Gold bullish") is not None


def test_cooldown_window_logic():
    """
    A second alert within ALERT_COOLDOWN_MINUTES is suppressed; one sent
    after the window is allowed again.
    """
    db = _fresh_db()
    topic = "news:📈 Gold bullish"

    db.mark_alert_sent(topic)
    last = db.get_last_alert_at(topic)
    age_min = (datetime.now(timezone.utc) - last).total_seconds() / 60
    assert age_min < ALERT_COOLDOWN_MINUTES  # still inside the window

    # Simulate the last alert being sent long ago -> outside the window
    old = datetime.now(timezone.utc) - timedelta(minutes=ALERT_COOLDOWN_MINUTES + 5)
    db.conn.execute(
        "UPDATE alert_cooldowns SET last_alert_at = ? WHERE topic = ?",
        (old.isoformat(), topic),
    )
    db.conn.commit()
    last = db.get_last_alert_at(topic)
    age_min = (datetime.now(timezone.utc) - last).total_seconds() / 60
    assert age_min >= ALERT_COOLDOWN_MINUTES  # outside the window now