#!/usr/bin/env python3
"""
AI Blog Aggregator - Fetches blog posts from multiple AI company blogs.
Runs every 6 hours via GitHub Actions (or OpenClaw).

Unique key strategy:
  id = "{source}:{slug}"
  - source: lowercase category name (claude, codex, deepseek, etc.)
  - slug: extracted from URL path (last meaningful segment)
  This prevents duplicates even when posts are refetched.

Sources using headless browser (Playwright):
  - DeepSeek, Cursor, Perplexity, xAI (JS-rendered pages)
Sources using requests + BeautifulSoup:
  - Anthropic, OpenAI Codex, Kimi, Qwen, OpenClaw, Ollama
Sources using RSS:
  - Gemma/DeepMind
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

# Playwright browser instance (lazy-loaded)
_browser = None
_playwright = None


def get_browser():
    """Lazy-load Playwright browser. Returns browser instance."""
    global _browser, _playwright
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
        print("  Playwright browser launched")
    return _browser


def close_browser():
    """Close Playwright browser if it was opened."""
    global _browser, _playwright
    if _browser:
        _browser.close()
        _browser = None
    if _playwright:
        _playwright.stop()
        _playwright = None


def fetch_with_playwright(url: str, wait_selector: str = None, scroll: bool = True, timeout: int = 30000) -> str:
    """Fetch a JS-rendered page using Playwright. Returns page HTML."""
    browser = get_browser()
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=timeout)
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=10000)
            except Exception:
                pass  # selector might not exist, continue anyway
        if scroll:
            # Scroll down to trigger lazy-loaded content
            for _ in range(5):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(800)
        html = page.content()
    finally:
        context.close()
    return html


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


def extract_body(url: str, use_playwright: bool = False) -> str:
    """Fetch a blog post URL and extract the main body text as clean HTML."""
    try:
        if use_playwright:
            html = fetch_with_playwright(url, scroll=False, timeout=20000)
        else:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "html.parser")

        # Remove unwanted elements
        for tag in soup.select("script, style, nav, header, footer, iframe, noscript, [class*='nav'], [class*='footer'], [class*='header'], [class*='sidebar'], [class*='cookie'], [class*='banner']"):
            tag.decompose()

        # Try common article selectors
        article = (
            soup.select_one("article") or
            soup.select_one("[class*='post-content']") or
            soup.select_one("[class*='article-content']") or
            soup.select_one("[class*='blog-content']") or
            soup.select_one("[class*='entry-content']") or
            soup.select_one("[class*='prose']") or
            soup.select_one("main") or
            soup.select_one("[role='main']")
        )

        if not article:
            return ""

        # Keep only text-relevant tags, convert to simple HTML
        allowed_tags = {"p", "h1", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "pre", "code", "strong", "em", "a", "br", "img"}
        for tag in article.find_all(True):
            if tag.name not in allowed_tags:
                tag.unwrap()

        # Clean up: remove empty elements, excess whitespace
        body = article.decode_contents().strip()
        # Collapse multiple blank lines
        body = re.sub(r'\n{3,}', '\n\n', body)
        return body[:50000]  # cap at 50KB per post

    except Exception as e:
        print(f"    Could not fetch body for {url}: {e}")
        return ""


# Which sources need Playwright for body fetching
PLAYWRIGHT_SOURCES = {"deepseek", "cursor", "perplexity", "xai"}


MAX_BODY_FETCH_PER_RUN = 30  # Limit to avoid GitHub Actions timeout


def fetch_bodies(posts_map: dict):
    """Fetch body content for posts that don't have it yet. Limited per run."""
    missing = [p for p in posts_map.values() if "body" not in p]
    if not missing:
        print("All posts already have body content.")
        return

    # Prioritize newest posts first
    missing.sort(key=lambda p: p["date"], reverse=True)
    batch = missing[:MAX_BODY_FETCH_PER_RUN]

    print(f"Fetching body for {len(batch)}/{len(missing)} posts (limit {MAX_BODY_FETCH_PER_RUN}/run)...")
    fetched = 0
    for i, post in enumerate(batch):
        use_pw = post["source"] in PLAYWRIGHT_SOURCES
        print(f"  [{i+1}/{len(batch)}] {post['source']}: {post['title'][:50]}...")
        body = extract_body(post["url"], use_playwright=use_pw)
        if body:
            post["body"] = body
            fetched += 1
        else:
            post["body"] = ""  # mark as attempted

    print(f"  Fetched body for {fetched}/{len(batch)} posts ({len(missing) - len(batch)} remaining)")


def clean_url(url: str) -> str:
    """Fix common URL issues like extra dots in domain."""
    # Fix trailing dot before path: perplexity.ai./hub -> perplexity.ai/hub
    url = re.sub(r'(\.\w+)\./', r'\1/', url)
    return url


def add_post(posts_map: dict, source: str, title: str, date: str, url: str, description: str = ""):
    """Add a post if it's after cutoff and not a duplicate."""
    if date < CUTOFF_DATE:
        return
    url = clean_url(url)
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
# Fetchers using requests + BeautifulSoup
# ---------------------------------------------------------------------------

def fetch_anthropic(posts_map: dict):
    """Fetch Claude posts from 3 sources: claude.com/blog, anthropic.com/news, anthropic.com/research."""
    print("Fetching: Anthropic (Claude)...")
    try:
        sources = [
            ("https://claude.com/blog", "/blog/", "https://claude.com"),
            ("https://www.anthropic.com/news", "/news/", "https://www.anthropic.com"),
            ("https://www.anthropic.com/research", "/research/", "https://www.anthropic.com"),
        ]
        for base_url, link_pattern, domain in sources:
            print(f"  Scraping {base_url}...")
            resp = requests.get(base_url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            for a in soup.select(f"a[href*='{link_pattern}']"):
                href = a.get("href", "")
                if not href or href in (link_pattern, link_pattern.rstrip("/")):
                    continue
                # Skip team/topic pages
                if "/team/" in href or "/topic/" in href:
                    continue
                url = href if href.startswith("http") else f"{domain}{href}"

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
                    date_str = "2026-01-01"

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
            # Strip HTML from description
            if desc:
                desc = BeautifulSoup(desc, "html.parser").get_text(strip=True)[:200]

            date_str = parse_rss_date(pub_date)
            if title and link:
                add_post(posts_map, "gemma", title, date_str, link, desc)

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
            if not href or href in ("/blog", "/blog/"):
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
    """Fetch Qwen blog posts from GitHub Pages (all pages)."""
    print("Fetching: Qwen...")
    try:
        page_num = 1
        while page_num <= 10:  # safety limit
            url_page = "https://qwenlm.github.io/blog/" if page_num == 1 else f"https://qwenlm.github.io/blog/page/{page_num}/"
            resp = requests.get(url_page, headers=HEADERS, timeout=30)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            found = 0
            for a in soup.select("a[href*='/blog/']"):
                href = a.get("href", "")
                if not href or href in ("/blog/", "/blog") or "/page/" in href:
                    continue
                url = href if href.startswith("http") else f"https://qwenlm.github.io{href}"
                title = a.get_text(strip=True)

                date_el = a.find_parent().select_one("time, [class*='date'], .post-date") if a.find_parent() else None
                date_str = parse_date(date_el.get_text(strip=True)) if date_el else ""

                if title and len(title) > 3:
                    add_post(posts_map, "qwen", title, date_str or "2025-01-01", url, "")
                    found += 1

            if found == 0:
                break
            page_num += 1

        print(f"  Found posts from Qwen ({page_num - 1} pages)")
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
            if not href or href in ("/blog", "/blog/"):
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
            if not href or href in ("/blog", "/blog/"):
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


# ---------------------------------------------------------------------------
# Fetchers using Playwright (JS-rendered pages)
# ---------------------------------------------------------------------------

def fetch_deepseek(posts_map: dict):
    """Fetch DeepSeek blog posts using headless browser."""
    print("Fetching: DeepSeek (Playwright)...")
    try:
        html = fetch_with_playwright("https://deepseek.ai/blog", wait_selector="a[href*='/blog/']")
        soup = BeautifulSoup(html, "html.parser")

        for a in soup.select("a[href*='/blog/']"):
            href = a.get("href", "")
            if not href or href in ("/blog", "/blog/"):
                continue
            url = href if href.startswith("http") else f"https://deepseek.ai{href}"
            title_el = a.select_one("h2, h3, [class*='title'], [class*='heading'], span")
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            # Clean up title - remove extra whitespace
            title = " ".join(title.split())

            date_el = a.select_one("time, [class*='date'], [class*='time']")
            if not date_el:
                parent = a.find_parent()
                if parent:
                    date_el = parent.select_one("time, [class*='date'], [class*='time']")
            date_str = ""
            if date_el:
                date_text = date_el.get("datetime", "") or date_el.get_text(strip=True)
                date_str = parse_date(date_text)

            desc_el = a.select_one("p, [class*='desc'], [class*='summary']")
            desc = desc_el.get_text(strip=True) if desc_el else ""

            if title and len(title) > 3:
                add_post(posts_map, "deepseek", title, date_str or "2025-01-01", url, desc)

        print(f"  Found posts from DeepSeek")
    except Exception as e:
        print(f"  Error fetching DeepSeek: {e}")


def fetch_cursor(posts_map: dict):
    """Fetch Cursor blog posts using headless browser."""
    print("Fetching: Cursor (Playwright)...")
    try:
        html = fetch_with_playwright("https://cursor.com/blog", wait_selector="a[href*='/blog/']", scroll=True)
        soup = BeautifulSoup(html, "html.parser")

        for a in soup.select("a[href*='/blog/']"):
            href = a.get("href", "")
            if not href or href in ("/blog", "/blog/"):
                continue
            url = href if href.startswith("http") else f"https://cursor.com{href}"
            title_el = a.select_one("h2, h3, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            title = " ".join(title.split())

            date_el = a.select_one("time, [class*='date']")
            if not date_el:
                parent = a.find_parent()
                if parent:
                    date_el = parent.select_one("time, [class*='date']")
            date_str = ""
            if date_el:
                date_text = date_el.get("datetime", "") or date_el.get_text(strip=True)
                date_str = parse_date(date_text)

            desc_el = a.select_one("p, [class*='desc']")
            desc = desc_el.get_text(strip=True) if desc_el else ""

            if title and len(title) > 3:
                add_post(posts_map, "cursor", title, date_str or "2026-01-01", url, desc)

        print(f"  Found posts from Cursor")
    except Exception as e:
        print(f"  Error fetching Cursor: {e}")


def fetch_perplexity(posts_map: dict):
    """Fetch Perplexity hub posts using headless browser."""
    print("Fetching: Perplexity (Playwright)...")
    try:
        html = fetch_with_playwright("https://www.perplexity.ai/hub", wait_selector="a[href*='/hub/']", scroll=True)
        soup = BeautifulSoup(html, "html.parser")

        for a in soup.select("a[href*='/hub/']"):
            href = a.get("href", "")
            if not href or href in ("/hub", "/hub/"):
                continue
            url = href if href.startswith("http") else f"https://www.perplexity.ai{href}"
            title_el = a.select_one("h2, h3, [class*='title'], [class*='heading']")
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            title = " ".join(title.split())

            date_el = a.select_one("time, [class*='date']")
            if not date_el:
                parent = a.find_parent()
                if parent:
                    date_el = parent.select_one("time, [class*='date']")
            date_str = ""
            if date_el:
                date_text = date_el.get("datetime", "") or date_el.get_text(strip=True)
                date_str = parse_date(date_text)

            desc_el = a.select_one("p, [class*='desc']")
            desc = desc_el.get_text(strip=True) if desc_el else ""

            if title and len(title) > 3:
                add_post(posts_map, "perplexity", title, date_str or "2025-01-01", url, desc)

        print(f"  Found posts from Perplexity")
    except Exception as e:
        print(f"  Error fetching Perplexity: {e}")


def fetch_xai(posts_map: dict):
    """Fetch xAI news using headless browser."""
    print("Fetching: xAI (Playwright)...")
    try:
        html = fetch_with_playwright("https://x.ai/news", wait_selector="a[href*='/news/']", scroll=True)
        soup = BeautifulSoup(html, "html.parser")

        for a in soup.select("a[href*='/news/']"):
            href = a.get("href", "")
            if not href or href in ("/news", "/news/"):
                continue
            url = href if href.startswith("http") else f"https://x.ai{href}"
            title_el = a.select_one("h2, h3, [class*='title'], [class*='heading']")
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            title = " ".join(title.split())

            date_el = a.select_one("time, [class*='date']")
            if not date_el:
                parent = a.find_parent()
                if parent:
                    date_el = parent.select_one("time, [class*='date']")
            date_str = ""
            if date_el:
                date_text = date_el.get("datetime", "") or date_el.get_text(strip=True)
                date_str = parse_date(date_text)

            desc_el = a.select_one("p, [class*='desc']")
            desc = desc_el.get_text(strip=True) if desc_el else ""

            if title and len(title) > 3:
                add_post(posts_map, "xai", title, date_str or "2025-01-01", url, desc)

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

    # --- Standard fetchers (requests + BS4) ---
    standard_fetchers = [
        fetch_anthropic,
        fetch_openai_codex,
        fetch_gemma,
        fetch_kimi,
        fetch_qwen,
        fetch_openclaw,
        fetch_ollama,
    ]

    for fetcher in standard_fetchers:
        try:
            fetcher(posts_map)
        except Exception as e:
            print(f"  Unexpected error in {fetcher.__name__}: {e}")
        print()

    # --- Playwright fetchers (JS-rendered pages) ---
    playwright_fetchers = [
        fetch_deepseek,
        fetch_cursor,
        fetch_perplexity,
        fetch_xai,
    ]

    print("--- Headless browser fetchers ---\n")
    for fetcher in playwright_fetchers:
        try:
            fetcher(posts_map)
        except Exception as e:
            print(f"  Unexpected error in {fetcher.__name__}: {e}")
        print()

    # --- Fetch body content for new posts ---
    print("--- Fetching post bodies ---\n")
    fetch_bodies(posts_map)

    close_browser()

    new_count = len(posts_map) - existing_count
    print("=" * 50)
    print(f"Total posts: {len(posts_map)} ({'+' if new_count >= 0 else ''}{new_count} new)")

    save_posts(posts_map)


if __name__ == "__main__":
    main()
