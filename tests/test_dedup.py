"""
Regression tests for deduplication.

Uses real near-duplicate title pairs observed in production logs (S6:
tests that would have caught the bugs found in the audit).
"""

import tempfile
from pathlib import Path

from processing.dedup import Deduplicator, normalize_title
from storage.database import Database


def _fresh_db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = Database(Path(tmp.name))
    db.connect()
    return db


def test_same_url_is_duplicate():
    db = _fresh_db()
    dedup = Deduplicator(db)

    dedup.mark_seen(
        "https://example.com/story/1",
        "Gold price hits record high as Fed signals cuts",
    )
    assert dedup.is_duplicate(
        "https://example.com/story/1",
        "Gold price hits record high as Fed signals cuts",
    )


def test_real_near_duplicate_title_pairs():
    """Pairs observed in production logs at >=92% similarity must dedupe."""
    pairs = [
        (
            "Gold, silver prices today, 5 August: Check retail rates of 24k",
            "gold silver prices today 4 august check retail rates of 24k",
        ),
        (
            "Gold extends gains on lower oil and softer dollar, markets await",
            "gold extends gains on lower oil and softer dollar markets await",
        ),
        (
            "Gold Price Today on August 04, 2026 - USA Today",
            "gold price today on august 03 2026 usa today",
        ),
        (
            "Current price of gold as of August 4, 2026 - Fortune",
            "current price of gold as of august 3 2026 fortune",
        ),
    ]

    db = _fresh_db()
    dedup = Deduplicator(db)

    for i, (t1, t2) in enumerate(pairs):
        dedup.mark_seen(f"https://example.com/a/{i}", t1)
        assert dedup.is_duplicate(f"https://example.com/b/{i}", t2), (
            f"expected duplicate: {t1!r} ~ {t2!r}"
        )


def test_distinct_titles_not_duplicate():
    db = _fresh_db()
    dedup = Deduplicator(db)

    dedup.mark_seen(
        "https://example.com/one",
        "Gold surges 5% as Fed cuts rates",
    )
    assert not dedup.is_duplicate(
        "https://example.com/two",
        "India's gold import duty may rise in upcoming budget",
    )


def test_normalize_title_strips_noise():
    assert normalize_title("Gold surges 2%!  —  WATCH") == "gold surges 2 watch"