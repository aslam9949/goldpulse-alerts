"""
Regression tests for relevance scoring.

These guard the S2 fix: keyword matching counts CONCEPT buckets, not raw
list entries, and an impact signal independent of keyword repetition keeps
low-information headlines from reaching the alert ceiling.
"""

from datetime import datetime, timezone

from ingestion.rss_fetcher import Article
from processing.relevance import (
    score_article,
    _concept_hits,
    should_alert,
)


def _article(title, trust=8, tags=None):
    """Build a fresh, recent Article for scoring."""
    return Article(
        url="https://example.com/" + str(abs(hash(title))),
        title=title,
        summary=None,
        source="Test",
        trust_score=trust,
        published_at=datetime.now(timezone.utc),
        tags=tags or [],
    )


def test_overlapping_keywords_count_once():
    """
    'gold price hits high as gold rally continues' used to score 3 raw
    keyword hits (gold, gold price, gold rally) and instantly max the
    title bucket. It is ONE concept (gold price action) and must count
    as a single bucket.
    """
    title = "gold price hits high as gold rally continues"
    assert _concept_hits(title) == {"gold_price_action"}


def test_generic_repetition_does_not_alert():
    """A low-information headline must not clear the alert threshold."""
    score = score_article(_article("gold price hits high as gold rally continues"))
    assert score < 6.0
    assert not should_alert(score)


def test_substantive_news_alerts():
    """A real quantity + named institution is what should clear the bar."""
    score = score_article(
        _article("Gold prices surge 5% as Fed signals rate cuts", trust=9)
    )
    assert score >= 6.0
    assert should_alert(score)


def test_geopolitical_article_not_hit_by_title_gate():
    """
    Geopolitical-track articles rarely mention 'gold'; the title-keyword
    gate (cap at 6.0) must NOT apply to them, or fix #1's articles can
    never alert.
    """
    score = score_article(
        _article("Israel strikes Iranian nuclear sites", trust=9, tags=["geopolitical"])
    )
    assert score >= 6.0
    assert should_alert(score)


def test_low_trust_geopolitical_does_not_alert():
    """Low-trust sources still need a strong signal — no spam from aggregators."""
    score = score_article(
        _article("Tensions rise in Middle East region", trust=6, tags=["geopolitical"])
    )
    assert score < 6.0
    assert not should_alert(score)