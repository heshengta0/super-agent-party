import json
import os
import re
import asyncio

SAFETY_WORDS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "safety_words.json")

_all_words: dict = {}
_flat_zh: set = set()
_flat_en: set = set()
_flat_other: dict = {}

def load_safety_words():
    global _all_words, _flat_zh, _flat_en, _flat_other
    if not os.path.exists(SAFETY_WORDS_FILE):
        print("[guard] safety_words.json not found. Content safety filter is disabled.")
        print("[guard] Run: python scripts/fetch_safety_words.py")
        return
    try:
        with open(SAFETY_WORDS_FILE, "r", encoding="utf-8") as f:
            _all_words = json.load(f)
    except Exception as e:
        print(f"[guard] Failed to load safety words: {e}")
        _all_words = {}
    _flat_zh = set(_all_words.get("zh", []))
    _flat_en = set(_all_words.get("en", []))
    _flat_other = {k: set(v) for k, v in _all_words.items() if k not in ("zh", "en")}


def _contains_cjk(word: str) -> bool:
    """Check if word contains any CJK character."""
    return any('\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf' for c in word)

def _cjk_char_count(word: str) -> int:
    """Count the number of CJK characters in a word."""
    return sum(1 for c in word if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf')

def _is_url_like(word: str) -> bool:
    """Check if word looks like a URL/domain fragment (contains ., /, :, ?, etc.)."""
    return any(c in word for c in '.:/?=&%#@\\')

def _match_word(word: str, text: str, min_cjk_chars: int = 2) -> bool:
    """Match a safety word against text.
    CJK words: use substring matching (skip entries with < min_cjk_chars CJK characters to avoid false positives).
    URL-like words (containing ., /, :, etc.): use substring matching.
    Other ASCII words: use word boundary (\b) matching to avoid false positives.
    """
    if _contains_cjk(word):
        if _cjk_char_count(word) < min_cjk_chars:
            return False
        return word in text
    if _is_url_like(word):
        return word in text
    # ASCII word: use \b boundary to match only whole words
    try:
        return bool(re.search(r'\b' + re.escape(word) + r'\b', text))
    except re.error:
        return word in text

def check_content_safety_sync(text: str, min_cjk_chars: int = 2) -> tuple:
    if not text or not _all_words:
        return True, []
    matched = []
    lower = text.lower()
    for word in _flat_zh:
        if _match_word(word.lower(), lower, min_cjk_chars):
            matched.append(word)
    for word in _flat_en:
        if _match_word(word.lower(), lower, min_cjk_chars):
            matched.append(word)
    for lang_words in _flat_other.values():
        for word in lang_words:
            if _match_word(word.lower(), lower, min_cjk_chars):
                matched.append(word)
    return len(matched) == 0, matched


async def check_content_safety(text: str, min_cjk_chars: int = 2) -> tuple:
    return await asyncio.to_thread(check_content_safety_sync, text, min_cjk_chars)
