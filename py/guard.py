import json
import os
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


def check_content_safety_sync(text: str) -> tuple:
    if not text or not _all_words:
        return True, []
    matched = []
    lower = text.lower()
    for word in _flat_zh:
        if word in lower:
            matched.append(word)
    for word in _flat_en:
        if word in lower:
            matched.append(word)
    for lang_words in _flat_other.values():
        for word in lang_words:
            if word in lower:
                matched.append(word)
    return len(matched) == 0, matched


async def check_content_safety(text: str) -> tuple:
    return await asyncio.to_thread(check_content_safety_sync, text)
