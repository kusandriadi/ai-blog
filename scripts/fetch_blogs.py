#!/usr/bin/env python3
"""
AI Blog Aggregator - Fetches blog posts from multiple AI company blogs.
Runs every 6 hours via GitHub Actions (or OpenClaw).

Unique key strategy:
  id = "{source}:{slug}"
  - source: lowercase category name (claude, codex, deepseek, etc.)
  - slug: extracted from URL path (last meaningful segment)
  This prevents duplicates even when posts are refetched.
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

DATA_FILE = Path(__file__).parent.parent / "data" / "posts.json"
CUTOFF_DATE = "2025-01-01"

HEADERS = {
    "User-Agent": "AI-News-Aggregator/1.0 (https://github.com/kusandriadi/ai-blog)"
}


def slug_from_url(url: str) -> str:
    """Extract a slug from a URL path for use as part of the unique key."""
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1] if path else url


def load_existing() -> dict:
    """Load existing posts indexed by id."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            posts = json.load(f)
        return {p["id"]: p for p in posts}
    return {}


def save_posts(posts_map: dict):
    """Save posts sorted by date descending."""
    posts = sorted(posts_map.values(), key=lambda p: p["date"], reverse=True)
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(posts)} posts to {DATA_FILE}")


def add_post(posts_map: dict, source: str, title: str, date: str, url: str, description: str = ""):
    """Add a post if it's after cutoff and not a duplicate."""
    if date < CUTOFF_DATE:
        return
    slug = slug_from_url(url)
    post_id = f"{source}:{slug}"
    posts_map[post_id] = {
        "id": post_id,
        "title": title.strip(),
        "date": date,
        "url": url,
        "source": source,
        "description": description.strip(),
    }


# ---------------------------------------------------------------------------
# Fetchers for each source
# ---------------------------------------------------------------------------

def fetch_anthropic(posts_map: dict):
    """Fetch Claude/Anthropic blog posts by scraping the blog page."""
    print("Fetching: Anthropic (Claude)...")
    try:
        resp = requests.get("https://www.anthropic.com/blog", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select("a[href*='/blog/']"):
            href = a.get("href", "")
            if not href or href == "/blog" or href == "/blog/":
                continue
            url = href if href.startswith("http") else f"https://www.anthropic.com{href}"

            title_el = a.select_one("h3, h2, [class*='title'], [class*='heading']")
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            date_el = a.select_one("time, [class*='date']")
            date_str = ""
            if date_el:
                date_text = date_el.get("datetime", "") or date_el.get_text(strip=True)
                date_str = parse_date(date_text)

            desc_el = a.select_one("p, [class*='desc'], [class*='excerpt']")
            desc = desc_el.get_text(strip=True) if desc_el else ""

            if not date_str:
                date_str = "2026-01-01"  # placeholder

            add_post(posts_map, "claude", title, date_str, url, desc)

        print(f"  Found posts from Anthropic")
    except Exception as e:
        print(f"  Error fetching Anthropic: {e}")


def fetch_openai_codex(posts_map: dict):
    """Fetch OpenAI Codex blog posts."""
    print("Fetching: OpenAI Codex...")
    try:
        resp = requests.get("https://developers.openai.com/blog/topic/codex", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for article in soup.select("article, [class*='post'], [class*='blog']"):
            a = article.select_one("a[href*='/blog/']")
            if not a:
                continue
            href = a.get("href", "")
            url = href if href.startswith("http") else f"https://developers.openai.com{href}"

            title = a.get_text(strip=True)
            date_el = article.select_one("time, [class*='date']")
            date_str = parse_date(date_el.get_text(strip=True)) if date_el else ""

            desc_el = article.select_one("p")
            desc = desc_el.get_text(strip=True) if desc_el else ""

            if title and len(title) > 3:
                add_post(posts_map, "codex", title, date_str or "2025-01-01", url, desc)

        print(f"  Found posts from OpenAI Codex")
    except Exception as e:
        print(f"  Error fetching OpenAI Codex: {e}")


def fetch_deepseek(posts_map: dict):
    """Fetch DeepSeek blog posts."""
    print("Fetching: DeepSeek...")
    try:
        resp = requests.get("https://deepseek.ai/blog", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select("a[href*='/blog/']"):
            href = a.get("href", "")
            if not href or href == "/blog" or href == "/blog/":
                continue
            url = href if href.startswith("http") else f"https://deepseek.ai{href}"
            title = a.get_text(strip=True)
            if title and len(title) > 3:
                add_post(posts_map, "deepseek", title, "2025-01-01", url, "")

        print(f"  Found posts from DeepSeek")
    except Exception as e:
        print(f"  Error fetching DeepSeek: {e}")


def fetch_gemma(posts_map: dict):
    """Fetch Gemma/DeepMind posts via RSS feed."""
    print("Fetching: Gemma (DeepMind RSS)...")
    try:
        resp = requests.get("https://deepmind.google/blog/rss.xml", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        for item in root.iter("item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            pub_date = item.findtext("pubDate", "")
            desc = item.findtext("description", "").strip()

            date_str = parse_rss_date(pub_date)
            if title and link:
                # Filter for Gemma-related or include all DeepMind posts
                add_post(posts_map, "gemma", title, date_str, link, desc[:200])

        print(f"  Found posts from DeepMind RSS")
    except Exception as e:
        print(f"  Error fetching Gemma: {e}")


def fetch_kimi(posts_map: dict):
    """Fetch Kimi blog posts."""
    print("Fetching: Kimi...")
    try:
        resp = requests.get("https://www.kimi.com/blog/", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select("a[href*='/blog/']"):
            href = a.get("href", "")
            if not href or href == "/blog" or href == "/blog/":
                continue
            url = href if href.startswith("http") else f"https://www.kimi.com{href}"
            title = a.get_text(strip=True)

            date_el = a.find_parent().select_one("time, [class*='date']") if a.find_parent() else None
            date_str = parse_date(date_el.get_text(strip=True)) if date_el else ""

            if title and len(title) > 3:
                add_post(posts_map, "kimi", title, date_str or "2025-01-01", url, "")

        print(f"  Found posts from Kimi")
    except Exception as e:
        print(f"  Error fetching Kimi: {e}")


def fetch_qwen(posts_map: dict):
    """Fetch Qwen blog posts from GitHub Pages."""
    print("Fetching: Qwen...")
    try:
        resp = requests.get("https://qwenlm.github.io/blog/", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select("a[href*='/blog/']"):
            href = a.get("href", "")
            if not href or href == "/blog/" or href == "/blog":
                continue
            url = href if href.startswith("http") else f"https://qwenlm.github.io{href}"
            title = a.get_text(strip=True)

            date_el = a.find_parent().select_one("time, [class*='date'], .post-date") if a.find_parent() else None
            date_str = parse_date(date_el.get_text(strip=True)) if date_el else ""

            if title and len(title) > 3:
                add_post(posts_map, "qwen", title, date_str or "2025-01-01", url, "")

        print(f"  Found posts from Qwen")
    except Exception as e:
        print(f"  Error fetching Qwen: {e}")


def fetch_openclaw(posts_map: dict):
    """Fetch OpenClaw blog posts."""
    print("Fetching: OpenClaw...")
    try:
        resp = requests.get("https://openclaw.ai/blog", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select("a[href*='/blog/']"):
            href = a.get("href", "")
            if not href or href == "/blog" or href == "/blog/":
                continue
            url = href if href.startswith("http") else f"https://openclaw.ai{href}"
            title_el = a.select_one("h2, h3, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)

            date_el = a.select_one("time, [class*='date']")
            date_str = parse_date(date_el.get_text(strip=True)) if date_el else ""

            desc_el = a.select_one("p")
            desc = desc_el.get_text(strip=True) if desc_el else ""

            if title and len(title) > 3:
                add_post(posts_map, "openclaw", title, date_str or "2026-01-01", url, desc)

        print(f"  Found posts from OpenClaw")
    except Exception as e:
        print(f"  Error fetching OpenClaw: {e}")


def fetch_ollama(posts_map: dict):
    """Fetch Ollama blog posts."""
    print("Fetching: Ollama...")
    try:
        resp = requests.get("https://ollama.com/blog", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select("a[href*='/blog/']"):
            href = a.get("href", "")
            if not href or href == "/blog" or href == "/blog/":
                continue
            url = href if href.startswith("http") else f"https://ollama.com{href}"
            title_el = a.select_one("h2, h3, [class*='title'], span")
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)

            date_el = a.select_one("time, [class*='date']")
            date_str = parse_date(date_el.get_text(strip=True)) if date_el else ""

            if title and len(title) > 3:
                add_post(posts_map, "ollama", title, date_str or "2025-01-01", url, "")

        print(f"  Found posts from Ollama")
    except Exception as e:
        print(f"  Error fetching Ollama: {e}")


def fetch_cursor(posts_map: dict):
    """Fetch Cursor blog posts."""
    print("Fetching: Cursor...")
    try:
        resp = requests.get("https://cursor.com/blog", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select("a[href*='/blog/']"):
            href = a.get("href", "")
            if not href or href == "/blog" or href == "/blog/":
                continue
            url = href if href.startswith("http") else f"https://cursor.com{href}"
            title_el = a.select_one("h2, h3, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)

            date_el = a.select_one("time, [class*='date']")
            date_str = parse_date(date_el.get_text(strip=True)) if date_el else ""

            desc_el = a.select_one("p")
            desc = desc_el.get_text(strip=True) if desc_el else ""

            if title and len(title) > 3:
                add_post(posts_map, "cursor", title, date_str or "2026-01-01", url, desc)

        print(f"  Found posts from Cursor")
    except Exception as e:
        print(f"  Error fetching Cursor: {e}")


def fetch_perplexity(posts_map: dict):
    """Fetch Perplexity hub posts."""
    print("Fetching: Perplexity...")
    try:
        resp = requests.get("https://www.perplexity.ai/hub", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select("a[href*='/hub/']"):
            href = a.get("href", "")
            if not href or href == "/hub" or href == "/hub/":
                continue
            url = href if href.startswith("http") else f"https://www.perplexity.ai{href}"
            title = a.get_text(strip=True)

            if title and len(title) > 3:
                add_post(posts_map, "perplexity", title, "2025-01-01", url, "")

        print(f"  Found posts from Perplexity")
    except Exception as e:
        print(f"  Error fetching Perplexity: {e}")


def fetch_xai(posts_map: dict):
    """Fetch xAI news."""
    print("Fetching: xAI...")
    try:
        resp = requests.get("https://x.ai/news", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select("a[href*='/news/']"):
            href = a.get("href", "")
            if not href or href == "/news" or href == "/news/":
                continue
            url = href if href.startswith("http") else f"https://x.ai{href}"
            title = a.get_text(strip=True)

            date_el = a.find_parent().select_one("time, [class*='date']") if a.find_parent() else None
            date_str = parse_date(date_el.get_text(strip=True)) if date_el else ""

            if title and len(title) > 3:
                add_post(posts_map, "xai", title, date_str or "2025-01-01", url, "")

        print(f"  Found posts from xAI")
    except Exception as e:
        print(f"  Error fetching xAI: {e}")


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------

def parse_date(text: str) -> str:
    """Try to parse various date formats into YYYY-MM-DD."""
    if not text:
        return ""
    text = text.strip()

    # Already YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text

    formats = [
        "%B %d, %Y",      # January 15, 2025
        "%b %d, %Y",      # Jan 15, 2025
        "%d %B %Y",       # 15 January 2025
        "%d %b %Y",       # 15 Jan 2025
        "%Y/%m/%d",       # 2025/01/15
        "%m/%d/%Y",       # 01/15/2025
        "%B %Y",          # January 2025
        "%Y-%m-%dT%H:%M", # ISO partial
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Try ISO 8601
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass

    return ""


def parse_rss_date(text: str) -> str:
    """Parse RSS pubDate format: 'Mon, 06 May 2026 00:00:00 GMT'."""
    if not text:
        return ""
    formats = [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(text.strip(), fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"AI Blog Aggregator - {datetime.now().isoformat()}")
    print(f"Cutoff date: {CUTOFF_DATE}")
    print("=" * 50)

    posts_map = load_existing()
    existing_count = len(posts_map)
    print(f"Loaded {existing_count} existing posts\n")

    fetchers = [
        fetch_anthropic,
        fetch_openai_codex,
        fetch_deepseek,
        fetch_gemma,
        fetch_kimi,
        fetch_qwen,
        fetch_openclaw,
        fetch_ollama,
        fetch_cursor,
        fetch_perplexity,
        fetch_xai,
    ]

    for fetcher in fetchers:
        try:
            fetcher(posts_map)
        except Exception as e:
            print(f"  Unexpected error in {fetcher.__name__}: {e}")
        print()

    new_count = len(posts_map) - existing_count
    print("=" * 50)
    print(f"Total posts: {len(posts_map)} ({'+' if new_count >= 0 else ''}{new_count} new)")

    save_posts(posts_map)


if __name__ == "__main__":
    main()
