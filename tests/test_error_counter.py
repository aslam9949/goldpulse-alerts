"""
Regression tests for error counters and /health observability (S5).
"""

from utils import error_counter
from bot.formatter import format_health


def test_counter_bump_and_snapshot():
    error_counter.reset()
    error_counter.bump("ingestion.gdelt")
    error_counter.bump("ingestion.gdelt")
    error_counter.bump("alerts.news")

    snap = error_counter.snapshot()
    assert snap == {"alerts.news": 1, "ingestion.gdelt": 2}
    assert error_counter.total() == 3


def test_format_health_shows_errors():
    text = format_health(
        {"total_articles": 10, "alerts_sent": 2, "total_events": 4, "urls_seen": 5},
        None,
        error_counts={"ingestion.gdelt": 12},
    )
    assert "ingestion.gdelt: 12" in text


def test_format_health_no_errors():
    text = format_health(
        {"total_articles": 0, "alerts_sent": 0, "total_events": 0, "urls_seen": 0},
        None,
        error_counts=None,
    )
    assert "none" in text