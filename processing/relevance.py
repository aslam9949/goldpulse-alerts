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
    GOLD_KEYWORD_CONCEPTS,
    GOLD_KEYWORDS_SECONDARY,
    INDIA_KEYWORDS,
    ALERT_THRESHOLD,
)
from ingestion.rss_fetcher import Article
from utils.logger import get_logger

logger = get_logger("processing.relevance")

# source_category tag for geopolitical-track articles (see ingestion)
GEOPOLITICAL_TAG = "geopolitical"

# ── Impact signals (independent of keyword repetition) ──────────────
# A "gold price" headline with no substance used to score the same as one
# with a real quantity, a named institution, or an escalation behind it.
# These patterns give importance a signal that repetition can't fake.
_QUANTITY_RE = re.compile(
    r"\b\d[\d,.]*\s*(tonnes?|tons?|ounces?|oz|grams?|kg|troy)\b"
    r"|\$\s?\d[\d,.]+\s*(k|m|b|bn)?\b"
    r"|\b\d[\d,.]*\s*%"
    r"|\b\d+\s*(bp|basis\s+points)\b"
)
_NAMED_INSTITUTION_RE = re.compile(
    r"\b(fed\b|federal\s+reserve|ecb\b|european\s+central\s+bank|boj\b|"
    r"bank\s+of\s+japan|rbi\b|reserve\s+bank\s+of\s+india|pboc\b|"
    r"people's\s+bank\s+of\s+china|boe\b|bank\s+of\s+england|"
    r"powell|lagarde|goldman\s+sachs|jpmorgan|ubs\b|world\s+bank|imf\b)"
)
_MONETARY_ACTION_RE = re.compile(
    r"\b(cuts?|hikes?|raises?|lowers?|reduces?)\s+(interest\s+rates?|rates?)\b"
    r"|\brate\s+(cut|hike|decision)\b"
)
# High-escalation terms: events that move gold as a safe haven.
_ESCALATION_HIGH_RE = re.compile(
    r"\b(war|invasion|nuclear|missile|airstrikes?|air\s+strikes?|sanctions?|"
    r"oil\s+embargo|embargo|coup|martial\s+law|warheads?|shelling|troops?|"
    r"military\s+(action|operation|buildup|strike))\b"
)
# Lower-escalation terms: present but less likely to be price-moving.
_ESCALATION_LOW_RE = re.compile(
    r"\b(ceasefire|tensions?|conflict|crisis|attack|border\s+clash|uprising)\b"
)


def _keyword_in_text(keyword: str, text: str) -> bool:
    """Check if keyword appears in text using word-boundary matching."""
    return bool(re.search(r"\b" + re.escape(keyword) + r"\b", text))


def _concept_hits(text: str) -> set[str]:
    """
    Distinct gold-concept buckets hit in ``text``.

    Longest keyword wins: a bare "gold" occurring inside "gold price" is
    attributed to the phrase, not counted as a second hit of its own
    bucket. Counting distinct buckets (not raw list entries) is what stops
    "gold price hits high as gold rally continues" from triple-scoring.
    """
    buckets: set[str] = set()
    covered: list[tuple[int, int]] = []

    flat = [
        (kw, bucket)
        for bucket, keywords in GOLD_KEYWORD_CONCEPTS.items()
        for kw in keywords
    ]
    flat.sort(key=lambda kv: len(kv[0]), reverse=True)

    for kw, bucket in flat:
        pattern = re.compile(r"\b" + re.escape(kw) + r"\b")
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(cs <= start and end <= ce for cs, ce in covered):
                continue  # inside an already-claimed longer phrase
            buckets.add(bucket)
            covered.append((start, end))
            break  # one occurrence per keyword is enough

    return buckets


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

    # source_category distinguishes the geopolitical ingestion track (S1)
    is_geopolitical = GEOPOLITICAL_TAG in article.tags

    # ── 1. Concept-bucket match — TITLE is mandatory for high scores ─
    title_concepts = _concept_hits(title_lower)
    text_concepts = _concept_hits(full_text)
    primary_title_hits = len(title_concepts)
    primary_text_hits = len(text_concepts)

    if primary_title_hits >= 3:
        score += 5.5
    elif primary_title_hits >= 2:
        score += 4.5
    elif primary_title_hits >= 1:
        score += 3.5
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

    # ── 6. Impact signal (independent of keyword repetition) ─────────
    # Gold-track: quantity / named institution / escalation add a bonus.
    # Geopolitical-track: the impact signal IS the score — these articles
    # rarely mention "gold", so they're exempt from the title gate below.
    if is_geopolitical:
        score += _geopolitical_impact(title_lower, full_text)
    else:
        score += _gold_impact(title_lower, full_text)

    # ── Gate: no primary concept in title → cap at 6.0 ───────────────
    # Geopolitical-track articles are exempt — their score comes from the
    # impact signal, not from saying the word "gold".
    if not is_geopolitical and primary_title_hits == 0:
        score = min(score, 6.0)

    # ── Clamp to 1-10 ────────────────────────────────────────────────
    final_score = max(1.0, min(10.0, score))

    logger.debug(
        "Scored '%s' = %.1f (concepts=%d/%d, secondary=%d/%d, trust=%d, india=%d/%d, geo=%s, title_gate=%s)",
        article.title[:60],
        final_score,
        primary_title_hits,
        primary_text_hits,
        secondary_title_hits,
        secondary_text_hits,
        article.trust_score,
        india_title_hits,
        india_text_hits,
        is_geopolitical,
        (not is_geopolitical and primary_title_hits == 0),
    )

    return round(final_score, 1)


def _gold_impact(title: str, full_text: str) -> float:
    """
    Impact bonus for gold-track articles (max 3.0).

    Rewards substance — a real tonnage figure, a named central bank, or
    an escalation — that bare keyword repetition can't fake.
    """
    bonus = 0.0
    if _QUANTITY_RE.search(full_text):
        bonus += 1.5
    if _NAMED_INSTITUTION_RE.search(title):
        bonus += 1.0
    if _ESCALATION_HIGH_RE.search(title) or _ESCALATION_LOW_RE.search(title):
        bonus += 0.5
    return bonus


def _geopolitical_impact(title: str, full_text: str) -> float:
    """
    Impact-driven score for geopolitical-track articles (max 7.5).

    These articles rarely mention "gold", so the escalation / central-bank
    / monetary-action signal IS their relevance. The on-track base keeps
    high-trust shock headlines from being mere digest fodder, while low
    trust sources still need a strong signal to clear the alert threshold.
    """
    bonus = 1.0  # on-track base — this track is high-precision by construction
    if _ESCALATION_HIGH_RE.search(title):
        bonus += 3.5
    elif _ESCALATION_HIGH_RE.search(full_text):
        bonus += 2.0

    if _ESCALATION_LOW_RE.search(title):
        bonus += 2.0
    elif _ESCALATION_LOW_RE.search(full_text):
        bonus += 1.0

    if _NAMED_INSTITUTION_RE.search(title):
        bonus += 1.5
    elif _NAMED_INSTITUTION_RE.search(full_text):
        bonus += 1.0

    if _MONETARY_ACTION_RE.search(full_text):
        bonus += 3.0

    if _QUANTITY_RE.search(full_text):
        bonus += 0.5

    return bonus


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
