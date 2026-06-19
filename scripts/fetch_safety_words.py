#!/usr/bin/env python3
"""
Fetch and build the content safety word list from upstream open-source repositories.
Run this script before packaging (CI/CD) or when you need to update the word lists.

Sources:
  - Chinese: konsheng/Sensitive-lexicon (MIT)
  - English:  coffee-and-fun/google-profanity-words (MIT)
  - Multi:    LDNOOBW/List-of-Dirty-Naughty-Obscene... (CC-BY-4.0)

Output: config/safety_words.json
"""
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
import tempfile
import shutil
import zipfile
from pathlib import Path

# --- Config ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = PROJECT_ROOT / "config" / "safety_words.json"
TEMP_DIR = Path(tempfile.mkdtemp(prefix="safety_words_"))

# Sources to fetch (raw text URLs, one word per line)
SOURCES = {
    "zh": [
        # konsheng/Sensitive-lexicon — TrChat JSON format
        ("https://raw.githubusercontent.com/konsheng/Sensitive-lexicon/main/ThirdPartyCompatibleFormats/TrChat/SensitiveLexicon.json", "json_entries"),
    ],
    "en": [
        ("https://raw.githubusercontent.com/coffee-and-fun/google-profanity-words/master/data/en.txt", "lines"),
    ],
    "multi_lines": [
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/en", "en"),
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/zh", "zh"),
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/ja", "ja"),
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/ko", "ko"),
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/fr", "fr"),
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/de", "de"),
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/ru", "ru"),
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/th", "th"),
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/es", "es"),
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/pt", "pt"),
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/it", "it"),
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/ar", "ar"),
    ],
    "de_vi": [
        ("https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/vi", "vi"),
    ],
}


def fetch_url(url: str) -> str:
    """Download a URL and return text content."""
    req = urllib.request.Request(url, headers={"User-Agent": "super-agent-party-build/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        print(f"  WARN: HTTP {e.code} fetching {url}")
        return ""
    except Exception as e:
        print(f"  WARN: {e} fetching {url}")
        return ""


def fetch_konsheng_vocabulary() -> set:
    """Download konsheng's Sensitive-lexicon zip and extract all .txt files from
    Vocabulary/ and Organized/ directories."""
    words = set()
    zip_url = "https://github.com/konsheng/Sensitive-lexicon/archive/refs/heads/main.zip"
    zip_path = TEMP_DIR / "konsheng.zip"
    try:
        req = urllib.request.Request(zip_url, headers={"User-Agent": "super-agent-party-build/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(zip_path, "wb") as f:
                f.write(resp.read())
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name_lower = info.filename.lower()
                if not name_lower.endswith(".txt"):
                    continue
                if "/vocabulary/" not in name_lower and "/organized/" not in name_lower:
                    continue
                try:
                    text = zf.read(info).decode("utf-8", errors="ignore")
                except Exception:
                    text = zf.read(info).decode("gbk", errors="ignore")
                for line in text.splitlines():
                    w = line.strip().lower()
                    if w and len(w) >= 2 and not w.startswith("#") and not w.startswith("//"):
                        words.add(w)
        shutil.rmtree(zip_path.parent, ignore_errors=True)
    except Exception as e:
        print(f"  WARN: failed to fetch konsheng vocabulary: {e}")
    return words


def main():
    print("Fetching content safety word lists...")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    all_words: dict = {}

    # --- Chinese (konsheng) ---
    print("\n[1/4] Chinese: konsheng/Sensitive-lexicon...")
    zh_words = fetch_konsheng_vocabulary()
    print(f"  -> {len(zh_words):,} words")

    # --- Also fetch TrChat JSON for Chinese ---
    for url, lang in SOURCES["zh"]:
        text = fetch_url(url)
        if lang == "json_entries" and text:
            try:
                data = json.loads(text)
                for entry in data:
                    if isinstance(entry, dict):
                        w = entry.get("originalWord", "")
                        if w:
                            zh_words.add(w.lower())
                    elif isinstance(entry, str) and len(entry) >= 2:
                        zh_words.add(entry.lower())
            except json.JSONDecodeError:
                pass
    print(f"  -> {len(zh_words):,} total (with TrChat)")

    # --- English ---
    print("\n[2/4] English: google-profanity-words...")
    en_words = set()
    for url, lang in SOURCES["en"]:
        text = fetch_url(url)
        for line in text.splitlines():
            w = line.strip().lower()
            if w and len(w) >= 2:
                en_words.add(w)
    print(f"  -> {len(en_words):,} words")

    # --- Multi-language (LDNOOBW) ---
    print("\n[3/4] Multi-language: LDNOOBW (Shutterstock)...")
    multi = {}
    for url, lang in SOURCES["multi_lines"]:
        text = fetch_url(url)
        if not text:
            continue
        words = set()
        for line in text.splitlines():
            w = line.strip().lower()
            if w:
                words.add(w)
        multi[lang] = sorted(words)
        print(f"  {lang}: {len(multi[lang]):,} words")
    # Merge LDNOOBW English and Chinese into main pools
    if "en" in multi:
        en_words |= set(multi["en"])
        del multi["en"]
    if "zh" in multi:
        zh_words |= set(multi["zh"])
        del multi["zh"]

    # Vietnamese
    for url, lang in SOURCES["de_vi"]:
        text = fetch_url(url)
        if text:
            words = set(line.strip().lower() for line in text.splitlines() if line.strip())
            multi[lang] = sorted(words)
            print(f"  {lang}: {len(multi[lang]):,} words")

    # --- Save ---
    print("\n[4/4] Saving...")
    result = {"zh": sorted(zh_words), "en": sorted(en_words)}
    result.update(multi)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in result.values())
    size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"\nDone! {total:,} words across {len(result)} languages")
    print(f"Saved: {OUTPUT_FILE} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
