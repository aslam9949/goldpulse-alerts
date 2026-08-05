"""
Standalone unit test for processing/language_filter.py.

Run with:
    /root/goldpulse-alerts/venv/bin/python tests/test_language_filter.py

Uses plain asserts (no pytest dependency).
"""

import sys
from pathlib import Path

# Make project root importable regardless of CWD
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from processing.language_filter import is_english_candidate, NON_LATIN_MAX_RATIO


def test_non_english_titles_are_rejected():
    """Real non-Latin titles from the dedup logs + representative scripts."""
    rejected = [
        # Exact examples from the production logs
        "سعر الدولار اليوم .. مفاجأة جديدة في أسعار البنوك المصرية",  # Arabic
        "Χρυσός: Εκτόξευση πάνω από 2%",  # Greek
        "港股收评 ：恒指涨0 . 24 % 科指涨0 . 97 % 科网股活跃 黄金股、PCB概念股大涨",  # Chinese
        # Other non-Latin scripts
        "Золото подорожало до рекордного уровня",  # Russian (Cyrillic)
        "מחיר הזהב נוסק לשיא",  # Hebrew
        "金価格が急騰、過去最高値を更新",  # Japanese
        "금값 사상 최고치 경신",  # Korean
        "सोने की कीमत रिकॉर्ड ऊंचाई पर पहुंची",  # Hindi (Devanagari)
        "ราคาทองคำพุ่งแตะระดับสูงสุดเป็นประวัติการณ์",  # Thai
        "ঢাকার বাজারে সোনার দামে রেকর্ড",  # Bengali
    ]
    for title in rejected:
        assert not is_english_candidate(title), f"should reject: {title}"


def test_english_titles_are_kept():
    """Normal English gold headlines must survive."""
    kept = [
        "Gold price hits record high as Fed signals rate cuts",
        "Gold surges 2% as dollar weakens after weak jobs data",
        "Central banks bought a record 1,000 tonnes of gold in 2024",
        "Gold futures rise on safe-haven demand amid Middle East tensions",
        "MCX Gold futures open higher tracking global cues",
    ]
    for title in kept:
        assert is_english_candidate(title), f"should keep: {title}"


def test_english_with_few_foreign_chars_is_kept():
    """English titles with a sprinkling of non-Latin chars must survive."""
    kept = [
        "Gold price surges as 央行 acts to stabilize the market",
        "XAU/USD breaks out — 黄金 enters overbought zone",
        "Russian gold buying surges as Лондон sanctions bite",
    ]
    for title in kept:
        assert is_english_candidate(title), f"should keep: {title}"


def test_latin_script_foreign_languages_are_kept():
    """Accented Latin (French/Spanish/German) is NOT treated as foreign."""
    kept = [
        "L'or atteint un record historique",  # French
        "El oro sube tras los datos de inflación",  # Spanish
        "Goldpreis steigt auf Rekordhoch",  # German
        "Le prix de l'or dépasse les 3 000 $",  # French with accented chars
    ]
    for title in kept:
        assert is_english_candidate(title), f"should keep: {title}"


def test_edge_cases():
    """Empty / punctuation-only / digit-only titles fall through safely."""
    kept = [
        "",
        "!!!",
        "2024-08-05",
        "Gold price rockets 🚀🚀🚀",  # emoji are not non-Latin script
    ]
    for title in kept:
        assert is_english_candidate(title), f"should keep: {title}"


if __name__ == "__main__":
    test_non_english_titles_are_rejected()
    test_english_titles_are_kept()
    test_english_with_few_foreign_chars_is_kept()
    test_latin_script_foreign_languages_are_kept()
    test_edge_cases()
    print(f"All language_filter tests passed (NON_LATIN_MAX_RATIO={NON_LATIN_MAX_RATIO})")