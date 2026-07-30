"""
GoldPulse Alerts — Relevance Scoring
=======================================
Scores articles for gold relevance on a 1-10 scale.

Scoring logic:
1. Keyword match (primary gold keywords = high weight, secondary = medium)
2. Source trust score (from RSS feed config)
3. India/MCX boost (extra points for Indian gold context)
4. Recency boost (newer articles score slightly higher)
5. Geopolitical filter (only alert if clear gold angle)

The goal is HIGH PRECISION — we'd rather miss an alert than spam the user
with irrelevant noise. Gold traders need signal, not noise.
"""

import re
from datetime import datetime, timezone

from config.settings import (
    GOLD_KEYWORDS_PRIMARY,
    GOLD_KEYWORDS_SECONDARY,
    INDIA_KEYWORDS,
    ALERT_THRESHOLD,
)
from ingestion.rss_fetcher import Article
from utils.logger import get_logger

logger = get_logger("processing.relevance")


def _keyword_in_text(keyword: str, text: str) -> bool:
    """Check if keyword appears in text using word-boundary matching."""
    return bool(re.search(r"\b" + re.escape(keyword) + r"\b", text))


def score_article(
    article: Article,
    gold_price: float | None = None,
) -> float:
    """
    Score an article's relevance to gold trading (1-10).

    STRICT scoring — only high-signal gold news should score 7.5+.

    Scoring breakdown:
    - Primary keyword in TITLE:  0-5 points (mandatory for high scores)
    - Primary keyword in BODY:   0-2 points (supplementary only)
    - Secondary keyword match:   0-1.5 points (macro context)
    - Source trust:              0-1.5 points (high-quality sources only)
    - India/MCX boost:           0-1 point (Indian trader relevance)
    - Recency:                   0-0.5 points (newer = slightly more relevant)
    - Penalty:                  -2 points if NO primary keyword in title

    Args:
        article: The article to score
        gold_price: Current gold price (for context, not used in scoring yet)

    Returns:
        Score from 1.0 to 10.0
    """
    score = 0.0

    title_lower = (article.title or "").lower()
    summary_lower = (article.summary or "").lower()
    full_text = f"{title_lower} {summary_lower}"

    # ── 1. Primary keyword match — TITLE is mandatory for high scores ─
    primary_title_hits = 0
    primary_text_hits = 0
    for keyword in GOLD_KEYWORDS_PRIMARY:
        kw = keyword.lower()
        if _keyword_in_text(kw, title_lower):
            primary_title_hits += 1
        elif _keyword_in_text(kw, full_text):
            primary_text_hits += 1

    # Title match is the gatekeeper — no title match = cap at 6.0
    has_title_primary = primary_title_hits >= 1

    if primary_title_hits >= 3:
        score += 5.0
    elif primary_title_hits >= 2:
        score += 4.0
    elif primary_title_hits >= 1:
        score += 3.0
    elif primary_text_hits >= 3:
        score += 1.5
    elif primary_text_hits >= 1:
        score += 0.8

    # ── 2. Secondary keyword match (0-1.5 points) ────────────────────
    secondary_title_hits = 0
    secondary_text_hits = 0
    for keyword in GOLD_KEYWORDS_SECONDARY:
        kw = keyword.lower()
        if _keyword_in_text(kw, title_lower):
            secondary_title_hits += 1
        elif _keyword_in_text(kw, full_text):
            secondary_text_hits += 1

    if secondary_title_hits >= 2:
        score += 1.5
    elif secondary_title_hits >= 1:
        score += 0.8
    elif secondary_text_hits >= 2:
        score += 0.5
    elif secondary_text_hits >= 1:
        score += 0.2

    # ── 3. Source trust score (0-1.5 points) ──────────────────────────
    # Only high-trust sources (7+) get meaningful boost
    trust = article.trust_score
    if trust >= 9:
        score += 1.5
    elif trust >= 8:
        score += 1.0
    elif trust >= 7:
        score += 0.5
    # Low-trust sources get 0 — they need stronger keyword signals

    # ── 4. India/MCX boost (0-1 point) ───────────────────────────────
    india_title_hits = 0
    india_text_hits = 0
    for keyword in INDIA_KEYWORDS:
        kw = keyword.lower()
        if _keyword_in_text(kw, title_lower):
            india_title_hits += 1
        elif _keyword_in_text(kw, full_text):
            india_text_hits += 1

    if india_title_hits >= 1:
        score += 1.0
    elif india_text_hits >= 2:
        score += 0.5
    elif india_text_hits >= 1:
        score += 0.2

    # ── 5. Recency boost (0-0.5 points) ──────────────────────────────
    if article.published_at:
        pub = article.published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age_hours = (
            datetime.now(timezone.utc) - pub
        ).total_seconds() / 3600
        if age_hours < 1:
            score += 0.5
        elif age_hours < 3:
            score += 0.3
        elif age_hours < 6:
            score += 0.1

    # ── Penalty: no primary keyword in title ──────────────────────────
    # If "gold" / "xau" / "bullion" etc. isn't in the title,
    # the article is likely tangential — cap at 6.0
    if not has_title_primary:
        score = min(score, 6.0)

    # ── Clamp to 1-10 ────────────────────────────────────────────────
    final_score = max(1.0, min(10.0, score))

    logger.debug(
        "Scored '%s' = %.1f (primary=%d/%d, secondary=%d/%d, trust=%d, india=%d/%d, title_gate=%s)",
        article.title[:60],
        final_score,
        primary_title_hits,
        primary_text_hits,
        secondary_title_hits,
        secondary_text_hits,
        article.trust_score,
        india_title_hits,
        india_text_hits,
        has_title_primary,
    )

    return round(final_score, 1)


def should_alert(score: float) -> bool:
    """
    Determine if an article should trigger an instant alert.

    Args:
        score: Relevance score (1-10)

    Returns:
        True if score meets the alert threshold.
    """
    return score >= ALERT_THRESHOLD


def classify_event_importance(title: str) -> str:
    """
    Classify an event's importance for gold trading.

    Returns:
        "critical", "high", "medium", or "low"
    """
    title_lower = title.lower()

    critical_keywords = [
        r"\bfomc\b", r"\bfederal funds\b", r"\bnfp\b", r"\bnon-farm\b",
        r"\bcpi\b", r"\bconsumer price\b", r"\bpowell\b",
    ]
    high_keywords = [
        r"\bgdp\b", r"\bppi\b", r"\bunemployment\b", r"\bism\b",
        r"\bretail sales\b", r"\bfed chair\b", r"\bfed testimony\b",
    ]
    medium_keywords = [
        r"\bjobless claims\b", r"\bdurable goods\b", r"\bhousing\b",
        r"\bconsumer confidence\b", r"\bconsumer sentiment\b",
        r"\badp\b", r"\btrade balance\b",
    ]

    for kw in critical_keywords:
        if re.search(kw, title_lower):
            return "critical"
    for kw in high_keywords:
        if re.search(kw, title_lower):
            return "high"
    for kw in medium_keywords:
        if re.search(kw, title_lower):
            return "medium"

    return "low"


def extract_gold_angle(title: str, summary: str | None = None) -> str | None:
    """
    Try to extract the 'gold angle' from an article.

    This is a simple heuristic — looks for patterns like:
    - "gold surges on..."
    - "gold falls as..."
    - "safe haven demand..."
    - "central bank buying..."

    Returns:
        A short gold-angle description, or None if not found.
    """
    text = f"{title} {summary or ''}".lower()

    patterns = [
        (r"gold\s+(surges?|rises?|climbs?|gains?|jumps?|hits?|breaches?)", "📈 Gold bullish"),
        (r"gold\s+(falls?|drops?|slides?|plunges?|tumbles?|slips?)", "📉 Gold bearish"),
        (r"safe\s+haven", "🛡️ Safe haven demand"),
        (r"central\s+bank\s+(buy|purchas|accumul)", "🏦 Central bank buying"),
        (r"gold\s+(import|demand|consumption)\s+(rise|surge|jump|increas)", "📊 Gold demand rising"),
        (r"inflation\s+(rise|surge|hot|higher|elevat)", "🔥 Inflation pressure"),
        (r"fed\s+(cut|lower|dovish|pause|hold)", "🕊️ Fed dovish"),
        (r"fed\s+(hike|raise|hawkish|tighten)", "🦅 Fed hawkish"),
        (r"dollar\s+(weak|falls?|drops?|slides?)", "💵 Weak dollar (gold positive)"),
        (r"dollar\s+(strong|surges?|rises?|rally)", "💵 Strong dollar (gold negative)"),
        (r"\b(war|conflict|tension|sanctions|crisis)\b", "🌍 Geopolitical risk"),
        (r"recession|slowdown|economic\s+weak", "📉 Recession fear"),
    ]

    for pattern, label in patterns:
        if re.search(pattern, text):
            return label

    return None
