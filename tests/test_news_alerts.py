"""
Regression tests for the news alert engine.

Guards the S3 cooldown behavior: a second alert on the same topic cluster
within ALERT_COOLDOWN_MINUTES is suppressed at the engine level.
"""

import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from alerts.news_alerts import NewsAlertEngine, _topic_key
from config.settings import ALERT_COOLDOWN_MINUTES
from ingestion.rss_fetcher import Article
from storage.database import Database


class _FakeBot:
    def __init__(self):
        self.sent: list[str] = []

    async def broadcast_with_button(self, text, button_text, button_url):
        self.sent.append(text)
        return 1


class _FakePrice:
    async def get_price(self):
        return None


def _fresh_db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = Database(Path(tmp.name))
    db.connect()
    return db


def _article(title: str) -> Article:
    return Article(
        url="https://example.com/" + str(abs(hash(title))),
        title=title,
        summary=None,
        source="Test",
        trust_score=9,
        published_at=datetime.now(timezone.utc),
    )


def test_topic_key_clusters_same_story():
    # Both titles detect the same gold angle -> same cooldown cluster.
    a1 = _article("Gold surges 5% as Fed cuts rates")
    a2 = _article("Gold climbs to record high on weak dollar")
    assert _topic_key(a1) == _topic_key(a2) == "news:📈 Gold bullish"


async def _run_engine_test():
    db = _fresh_db()
    bot = _FakeBot()
    engine = NewsAlertEngine(db, _FakePrice(), bot)

    article = _article("Gold surges 5% as Fed cuts rates")

    # First alert on the cluster -> sent
    result1 = await engine._send_article_alert(article, 8.0, None)
    assert result1 == "sent"
    assert len(bot.sent) == 1

    # Second alert on the same cluster within the window -> suppressed
    result2 = await engine._send_article_alert(
        _article("Gold climbs to record high on weak dollar"), 8.0, None
    )
    assert result2 == "cooldown"
    assert len(bot.sent) == 1

    # After the cooldown window elapses -> allowed again
    topic = _topic_key(article)
    old = datetime.now(timezone.utc) - timedelta(minutes=ALERT_COOLDOWN_MINUTES + 1)
    db.conn.execute(
        "UPDATE alert_cooldowns SET last_alert_at = ? WHERE topic = ?",
        (old.isoformat(), topic),
    )
    db.conn.commit()

    result3 = await engine._send_article_alert(article, 8.0, None)
    assert result3 == "sent"
    assert len(bot.sent) == 2


def test_cooldown_suppresses_and_releases():
    import asyncio

    asyncio.run(_run_engine_test())