"""
GoldPulse Alerts — Language Filter
====================================
Drops non-English articles early in the news pipeline.

Design decisions:
- Zero-dependency heuristic: no language-detection library. We detect
  predominance of non-Latin scripts (CJK, Arabic, Hebrew, Greek, Cyrillic,
  Devanagari, Thai, etc.) in the article title.
- Pure function, no I/O — trivially testable and cheap per article.
- Applied at a single choke point (alerts/news_alerts.py) so BOTH RSS and
  GDELT articles are filtered before dedup/scoring.
- Latin-script foreign languages (French, Spanish, German, ...) are NOT
  filtered here — they share the Latin script, and the English-keyword
  relevance scoring already keeps them from alerting.
"""

import re

from utils.logger import get_logger

logger = get_logger("processing.language")

# ── Non-Latin script ranges (Unicode blocks) ───────────────────────────
# Any letter/syllable in these blocks is non-Latin. The block list is
# chosen conservatively so accented Latin letters (é, ü, ñ — Latin-1
# Supplement / Latin Extended) are NOT treated as foreign, and so genuine
# English titles with a stray foreign character still pass.
_NON_LATIN_RE = re.compile(
    "["
    "Ͱ-Ͽ"  # Greek
    "Ѐ-ӿ"  # Cyrillic
    "԰-֏"  # Armenian
    "֐-׿"  # Hebrew
    "؀-ۿ"  # Arabic
    "ݐ-ݿ"  # Arabic Supplement
    "ࢠ-ࣿ"  # Arabic Extended-A
    "ऀ-ॿ"  # Devanagari
    "ঀ-৿"  # Bengali
    "਀-੿"  # Gurmukhi
    "઀-૿"  # Gujarati
    "଀-୿"  # Oriya
    "஀-௿"  # Tamil
    "ఀ-౿"  # Telugu
    "ಀ-೿"  # Kannada
    "ഀ-ൿ"  # Malayalam
    "඀-෿"  # Sinhala
    "฀-๿"  # Thai
    "຀-໿"  # Lao
    "ༀ-࿿"  # Tibetan
    "က-႟"  # Myanmar
    "Ⴀ-ჿ"  # Georgian
    "ក-៿"  # Khmer
    "ⴰ-⵿"  # Tifinagh
    "぀-ヿ"  # Hiragana + Katakana
    "㄀-ㄯ"  # Bopomofo
    "㐀-䶿"  # CJK Unified Ideographs Extension A
    "一-鿿"  # CJK Unified Ideographs
    "ꀀ-꒏"  # Yi Syllables
    "ꥠ-꥿"  # Hangul Jamo Extended-A
    "가-힯"  # Hangul Syllables
    "豈-﫿"  # CJK Compatibility Ideographs
    "ﭐ-﷿"  # Arabic Presentation Forms-A
    "ﹰ-﻿"  # Arabic Presentation Forms-B
    "]"
)

# Max non-Latin share of alphanumeric title characters before we drop the
# title. Empirically: purely non-Latin titles are 70-100% non-Latin, while
# English titles with a stray foreign character stay well under 10%.
NON_LATIN_MAX_RATIO = 0.35


def is_english_candidate(title: str) -> bool:
    """
    Heuristic filter: is this title predominantly in a Latin script?

    Titles whose non-Latin script characters exceed 35% of their
    alphanumeric characters are rejected. The check targets the title only
    (the highest-signal field) and is deliberately permissive — English
    titles with a few non-Latin characters pass.

    Args:
        title: Article title (may be empty or contain no letters).

    Returns:
        True if the title plausibly serves English readers (keep it),
        False if it is predominantly non-Latin script (drop it).
    """
    if not title:
        return True  # Empty title — let downstream logic decide

    non_latin = len(_NON_LATIN_RE.findall(title))
    if non_latin == 0:
        return True

    # Denominator: letters + digits across all scripts. Punctuation and
    # whitespace are neutral, so they don't dilute either side.
    alnum_count = sum(1 for ch in title if ch.isalnum())
    if alnum_count == 0:
        return True

    ratio = non_latin / alnum_count
    if ratio > NON_LATIN_MAX_RATIO:
        logger.debug(
            "Dropping non-English title (non-Latin ratio=%.0f%%): '%s'",
            ratio * 100,
            title[:80],
        )
        return False

    return True