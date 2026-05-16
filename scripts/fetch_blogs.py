#!/usr/bin/env python3
"""
AI Blog Aggregator - Fetches blog posts from multiple AI company blogs.
Runs every 6 hours via GitHub Actions (or OpenClaw).

Unique key strategy:
  id = "{source}:{slug}"
  - source: lowercase category name (claude, codex, gemma, etc.)
  - slug: extracted from URL path (last meaningful segment)

Date extraction strategy:
  1. Parse date from the listing card (per-source selectors).
  2. If missing, fetch the article page and look for meta tags, <time>,
     [datetime] attributes, or "Month D, YYYY" text near the top.
  3. If still missing, the post is skipped.

URL filtering:
  Each source has a strict URL pattern (URL_PATTERNS). Posts whose URLs
  don't match are not added, and pre-existing entries that no longer
  match are pruned from the index.
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
FALLBACK_DATES = {"2025-01-01", "2026-01-01"}  # legacy fallback markers — re-fetch

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

# Strict per-source URL patterns. Posts whose URLs don't match are filtered.
URL_PATTERNS = {
    "claude": re.compile(
        r"^https?://(?:www\.)?(?:claude\.com/blog|anthropic\.com/(?:news|research))/(?!category/|topic/|team/)[^/?#]+/?$"
    ),
    "openai": re.compile(
        r"^https?://developers\.openai\.com/blog/(?!topic/|category/)[^/?#]+/?$"
    ),
    "gemma": re.compile(r"^https?://deepmind\.google/.+$"),
    "kimi": re.compile(r"^https?://(?:www\.)?kimi\.com/blog/[^/?#]+/?$"),
    "qwen": re.compile(r"^https?://(?:www\.)?qwen\.ai/blog\?id=[^&#]+$"),
    "openclaw": re.compile(r"^https?://openclaw\.ai/blog/[^/?#]+/?$"),
    "ollama": re.compile(r"^https?://ollama\.com/blog/[^/?#]+/?$"),
    "cursor": re.compile(
        r"^https?://(?:www\.)?cursor\.com/blog/(?!topic/|category/)[^/?#]+/?$"
    ),
    "perplexity": re.compile(
        r"^https?://(?:www\.)?perplexity\.ai/hub/blog/[^/?#]+/?$"
    ),
    "xai": re.compile(r"^https?://x\.ai/news/[^/?#]+/?$"),
}

PLAYWRIGHT_SOURCES = {"perplexity", "xai", "qwen"}

# Playwright browser (lazy-loaded)
_browser = None
_playwright = None

# Per-run cache for article-page date lookups
_article_date_cache = {}


def get_browser():
    global _browser, _playwright
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
        print("  Playwright browser launched")
    return _browser


def close_browser():
    global _browser, _playwright
    if _browser:
        _browser.close()
        _browser = None
    if _playwright:
        _playwright.stop()
        _playwright = None


def fetch_with_playwright(url: str, wait_selector: str = None, scroll: bool = True, timeout: int = 30000) -> str:
    browser = get_browser()
    context = browser.new_context(user_agent=HEADERS["User-Agent"])
    page = context.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=timeout)
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=10000)
            except Exception:
                pass
        if scroll:
            for _ in range(5):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(800)
        html = page.content()
    finally:
        context.close()
    return html


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_MONTHS_RE = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DATE_RE = re.compile(rf"\b{_MONTHS_RE}\.?\s+\d{{1,2}},?\s+20\d{{2}}\b")
_ISO_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def parse_date(text: str) -> str:
    """Try to parse a date string into YYYY-MM-DD; return '' if it can't."""
    if not text:
        return ""
    text = text.strip()

    m = _ISO_PREFIX_RE.match(text)
    if m:
        return m.group(1)

    # Try to extract "Month D, YYYY" from inside a longer string
    m = _DATE_RE.search(text)
    if m:
        text_to_parse = m.group(0).replace(",", "").replace(".", "")
        # normalize multiple spaces
        text_to_parse = re.sub(r"\s+", " ", text_to_parse)
    else:
        text_to_parse = text

    formats = [
        "%B %d %Y",       # March 30 2026
        "%b %d %Y",       # Mar 30 2026
        "%B %d, %Y",
        "%b %d, %Y",
        "%b. %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y/%m/%d",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%B %Y",
        "%Y-%m-%dT%H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text_to_parse, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    return ""


def parse_rss_date(text: str) -> str:
    if not text:
        return ""
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def fetch_article_date(url: str) -> str:
    """Fetch the article page and extract its publication date. Cached per run."""
    if url in _article_date_cache:
        return _article_date_cache[url]
    date_str = ""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            # 1. Meta tags
            for attr, val in [
                ("property", "article:published_time"),
                ("property", "og:published_time"),
                ("property", "og:article:published_time"),
                ("name", "publish-date"),
                ("name", "publication-date"),
                ("name", "date"),
                ("name", "pubdate"),
                ("name", "DC.date.issued"),
                ("itemprop", "datePublished"),
                ("property", "article:modified_time"),
            ]:
                tag = soup.find("meta", attrs={attr: val})
                if tag and tag.get("content"):
                    date_str = parse_date(tag["content"])
                    if date_str:
                        break
            # 2. <time> elements with datetime
            if not date_str:
                for t in soup.find_all("time"):
                    dt = t.get("datetime", "") or t.get_text(strip=True)
                    date_str = parse_date(dt)
                    if date_str:
                        break
            # 3. any element with [datetime] attribute
            if not date_str:
                for el in soup.find_all(attrs={"datetime": True}):
                    date_str = parse_date(el.get("datetime", ""))
                    if date_str:
                        break
            # 4. Regex scan of body for "Month D, YYYY"
            if not date_str:
                text = soup.get_text(" ", strip=True)[:8000]
                m = _DATE_RE.search(text)
                if m:
                    date_str = parse_date(m.group(0))
    except Exception as e:
        print(f"    article date fetch failed: {url}: {e}")
    _article_date_cache[url] = date_str
    return date_str


# ---------------------------------------------------------------------------
# Storage and post-add helpers
# ---------------------------------------------------------------------------

def slug_from_url(url: str) -> str:
    """Extract a slug for the post id. Prefers the `?id=` query param
    (used by qwen.ai/blog?id=…), then the last path segment."""
    from urllib.parse import parse_qs
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "id" in qs and qs["id"]:
        return qs["id"][0]
    path = parsed.path.rstrip("/")
    return path.split("/")[-1] if path else url


def clean_url(url: str) -> str:
    url = re.sub(r"(\.\w+)\./", r"\1/", url)
    return url.rstrip("/")


def load_existing() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            posts = json.load(f)
        return {p["id"]: p for p in posts}
    return {}


def save_posts(posts_map: dict):
    posts = sorted(posts_map.values(), key=lambda p: p["date"], reverse=True)
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(posts)} posts to {DATA_FILE}")


def add_post(posts_map: dict, source: str, title: str, date: str, url: str, description: str = ""):
    """Add a post if it matches the source URL pattern and is after cutoff."""
    url = clean_url(url)
    pattern = URL_PATTERNS.get(source)
    if pattern and not pattern.match(url):
        return
    title = " ".join((title or "").split())
    if not title or len(title) < 3:
        return
    # Trust listing date if present; otherwise look it up on the article page.
    if not date:
        date = fetch_article_date(url)
    if not date or date < CUTOFF_DATE:
        return
    slug = slug_from_url(url)
    post_id = f"{source}:{slug}"
    # Preserve existing description if we don't have a better one
    existing = posts_map.get(post_id)
    if not description and existing:
        description = existing.get("description", "")
    posts_map[post_id] = {
        "id": post_id,
        "title": title,
        "date": date,
        "url": url,
        "source": source,
        "description": (description or "").strip(),
    }


def migrate_source(posts_map: dict, old: str, new: str):
    """Rename a source key in-place (id and source field)."""
    moved = 0
    for post_id in list(posts_map.keys()):
        p = posts_map[post_id]
        if p["source"] == old:
            del posts_map[post_id]
            p["source"] = new
            p["id"] = f"{new}:{post_id.split(':', 1)[1]}"
            posts_map[p["id"]] = p
            moved += 1
    if moved:
        print(f"Migrated {moved} entries: {old} -> {new}")


def cleanup_stale(posts_map: dict):
    """Remove entries whose source was dropped, or whose URL no longer matches
    its source's pattern."""
    removed = []
    for post_id, p in list(posts_map.items()):
        if p["source"] not in URL_PATTERNS:
            removed.append(post_id)
            del posts_map[post_id]
            continue
        pattern = URL_PATTERNS[p["source"]]
        if not pattern.match(p["url"]):
            removed.append(post_id)
            del posts_map[post_id]
    if removed:
        print(f"Pruned {len(removed)} stale entries (source dropped or URL no longer matches)")


def cleanup_titles(posts_map: dict):
    """Clean up Perplexity-style concatenated titles ('TitleMay 6, 2026Category')
    in pre-existing entries so users see clean text even before the next CI run."""
    fixed = 0
    for p in posts_map.values():
        if p["source"] == "perplexity":
            cleaned = _clean_perplexity_title(p["title"])
            if cleaned and cleaned != p["title"]:
                p["title"] = cleaned
                fixed += 1
    if fixed:
        print(f"Cleaned up {fixed} concatenated Perplexity titles")


def refetch_fallback_dates(posts_map: dict):
    """Re-fetch dates for entries saved with legacy fallback markers. Drop any
    post whose real date turns out to be before the cutoff."""
    targets = [p for p in posts_map.values() if p["date"] in FALLBACK_DATES]
    if not targets:
        return
    print(f"Re-fetching dates for {len(targets)} posts with legacy fallback dates...")
    fixed = 0
    dropped = 0
    for p in targets:
        new_date = fetch_article_date(p["url"])
        if not new_date:
            continue
        if new_date < CUTOFF_DATE:
            posts_map.pop(p["id"], None)
            dropped += 1
        elif new_date != p["date"]:
            p["date"] = new_date
            fixed += 1
    print(f"  Fixed {fixed} dates, dropped {dropped} pre-cutoff posts")


# ---------------------------------------------------------------------------
# Source-specific helpers
# ---------------------------------------------------------------------------

def _find_card(a, max_levels=3):
    """Find the smallest post card wrapping an <a> link.

    If the <a> itself contains a title (h-tag) or date (<time> / [datetime]),
    treat the <a> as the card. Otherwise walk up at most `max_levels` and
    take the first ancestor that contains a heading.
    """
    if a is None:
        return None
    if a.find(["h1", "h2", "h3", "h4"]) or a.find("time") or a.find(attrs={"datetime": True}):
        return a
    p = a
    for _ in range(max_levels):
        p = p.find_parent()
        if p is None:
            return None
        if p.find(["h1", "h2", "h3", "h4"]) or p.find("time") or p.find(attrs={"datetime": True}):
            return p
    return None


def _extract_title_from_card(card, a) -> str:
    """Get a clean title from a card."""
    if card is None:
        return ""
    h = card.find(["h1", "h2", "h3", "h4"])
    if h and h.get_text(strip=True):
        return h.get_text(strip=True)
    # Fall back to element with class containing "title" (but skip "subtitle" / "title-bar" noise)
    for el in card.find_all(class_=lambda c: c and "title" in " ".join(c).lower() if isinstance(c, list) else (c and "title" in c.lower())):
        text = el.get_text(strip=True)
        if text and len(text) >= 4:
            return text
    # Last resort: anchor text minus date prefix
    text = a.get_text(" ", strip=True)
    text = _DATE_RE.sub("", text).strip()
    return text


def _extract_date_from_card(card) -> str:
    """Find a date inside a card via <time>, [datetime] attr, .date class, or text scan."""
    if not card:
        return ""
    t = card.find("time")
    if t:
        d = parse_date(t.get("datetime", "") or t.get_text(strip=True))
        if d:
            return d
    el = card.find(attrs={"datetime": True})
    if el:
        d = parse_date(el.get("datetime", ""))
        if d:
            return d
    for el in card.find_all(class_=lambda c: c and ("date" in (" ".join(c) if isinstance(c, list) else c).lower())):
        d = parse_date(el.get_text(strip=True))
        if d:
            return d
    m = _DATE_RE.search(card.get_text(" ", strip=True))
    if m:
        return parse_date(m.group(0))
    return ""


# ---------------------------------------------------------------------------
# Fetchers (requests + BS4)
# ---------------------------------------------------------------------------

def fetch_anthropic(posts_map: dict):
    """Fetch Claude posts from claude.com/blog, anthropic.com/news, anthropic.com/research."""
    print("Fetching: Anthropic (Claude)...")
    sources = [
        ("https://claude.com/blog", "/blog/", "https://claude.com"),
        ("https://www.anthropic.com/news", "/news/", "https://www.anthropic.com"),
        ("https://www.anthropic.com/research", "/research/", "https://www.anthropic.com"),
    ]
    for base_url, prefix, domain in sources:
        try:
            print(f"  Scraping {base_url}...")
            resp = requests.get(base_url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            seen = set()
            for a in soup.select(f"a[href*='{prefix}']"):
                href = (a.get("href") or "").strip()
                if not href or href.rstrip("/") in (prefix.rstrip("/"),):
                    continue
                url = href if href.startswith("http") else f"{domain}{href}"
                url = clean_url(url)
                if url in seen:
                    continue
                seen.add(url)
                card = _find_card(a) or a
                title = _extract_title_from_card(card, a)
                if not title:
                    continue
                date_str = _extract_date_from_card(card)
                desc_el = card.find("p")
                desc = desc_el.get_text(strip=True) if desc_el else ""
                add_post(posts_map, "claude", title, date_str, url, desc)
        except Exception as e:
            print(f"  Error fetching {base_url}: {e}")


def fetch_openai(posts_map: dict):
    """Fetch OpenAI Developer Blog posts (all topics). Listing shows month/day
    without year, so dates are always looked up on the article page."""
    print("Fetching: OpenAI Developer Blog...")
    try:
        resp = requests.get("https://developers.openai.com/blog", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        seen = set()
        for a in soup.select("a.resource-item[href*='/blog/']"):
            href = (a.get("href") or "").strip()
            if not href or "/topic/" in href or href.rstrip("/").endswith("/blog"):
                continue
            url = href if href.startswith("http") else f"https://developers.openai.com{href}"
            url = clean_url(url)
            if url in seen:
                continue
            seen.add(url)
            title_el = a.select_one("[class*='line-clamp']")
            if title_el:
                title = title_el.get_text(strip=True)
            else:
                img = a.find("img", alt=True)
                title = img.get("alt", "").strip() if img else a.get_text(" ", strip=True)
            date_str = fetch_article_date(url)
            add_post(posts_map, "openai", title, date_str, url, "")
    except Exception as e:
        print(f"  Error fetching OpenAI Developer Blog: {e}")


def fetch_gemma(posts_map: dict):
    print("Fetching: Gemma (DeepMind RSS)...")
    try:
        resp = requests.get("https://deepmind.google/blog/rss.xml", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.iter("item"):
            title = (item.findtext("title", "") or "").strip()
            link = (item.findtext("link", "") or "").strip()
            pub_date = item.findtext("pubDate", "")
            desc = (item.findtext("description", "") or "").strip()
            if desc:
                desc = BeautifulSoup(desc, "html.parser").get_text(strip=True)[:200]
            date_str = parse_rss_date(pub_date)
            if title and link:
                add_post(posts_map, "gemma", title, date_str, link, desc)
    except Exception as e:
        print(f"  Error fetching Gemma: {e}")


def fetch_kimi(posts_map: dict):
    """Fetch Kimi blog posts from www.kimi.com/blog (server-rendered cards)."""
    print("Fetching: Kimi...")
    try:
        resp = requests.get("https://www.kimi.com/blog/", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.menu-card[href*='/blog/']"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            url = href if href.startswith("http") else f"https://www.kimi.com{href}"
            url = clean_url(url)
            title_el = a.select_one(".card-title")
            title = title_el.get_text(strip=True) if title_el else ""
            date_el = a.select_one(".card-date")
            date_str = parse_date(date_el.get_text(strip=True)) if date_el else ""
            desc_el = a.select_one(".card-desc")
            desc = desc_el.get_text(strip=True) if desc_el else ""
            add_post(posts_map, "kimi", title, date_str, url, desc)
    except Exception as e:
        print(f"  Error fetching Kimi: {e}")


def fetch_openclaw(posts_map: dict):
    """OpenClaw uses Astro post-card markup with .post-title/.post-date inside the <a>."""
    print("Fetching: OpenClaw...")
    try:
        resp = requests.get("https://openclaw.ai/blog", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.post-card[href*='/blog/']"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            url = href if href.startswith("http") else f"https://openclaw.ai{href}"
            url = clean_url(url)
            title_el = a.select_one(".post-title")
            title = title_el.get_text(strip=True) if title_el else ""
            date_el = a.select_one(".post-date")
            date_str = parse_date(date_el.get_text(strip=True)) if date_el else ""
            desc_el = a.select_one(".post-description")
            desc = desc_el.get_text(strip=True) if desc_el else ""
            add_post(posts_map, "openclaw", title, date_str, url, desc)
    except Exception as e:
        print(f"  Error fetching OpenClaw: {e}")


def fetch_ollama(posts_map: dict):
    """Fetch Ollama blog posts. Dates live in <h3 datetime="..."> inside the link."""
    print("Fetching: Ollama...")
    try:
        resp = requests.get("https://ollama.com/blog", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a[href^='/blog/']"):
            href = (a.get("href") or "").strip()
            if not href or href in ("/blog", "/blog/"):
                continue
            url = f"https://ollama.com{href}"
            url = clean_url(url)
            h2 = a.find("h2")
            title = h2.get_text(strip=True) if h2 else ""
            date_el = a.find(attrs={"datetime": True}) or a.find("time")
            date_str = ""
            if date_el:
                date_str = parse_date(date_el.get("datetime", "")) or parse_date(date_el.get_text(strip=True))
            p = a.find("p")
            desc = p.get_text(strip=True) if p else ""
            add_post(posts_map, "ollama", title, date_str, url, desc)
    except Exception as e:
        print(f"  Error fetching Ollama: {e}")


# ---------------------------------------------------------------------------
# Fetchers using Playwright
# ---------------------------------------------------------------------------

def fetch_cursor(posts_map: dict):
    """Fetch Cursor blog posts from cursor.com/blog. HTML is SSR'd, no Playwright needed.

    Two card styles exist: 'a.card' (featured with image) and
    'a.blog-directory__row' (text-only row). Title is in img alt or in
    <p class="text-theme-text">; date is in <time datetime="...">.
    """
    print("Fetching: Cursor...")
    try:
        resp = requests.get("https://cursor.com/blog", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        seen = set()
        for a in soup.select("a.card[href*='/blog/'], a.blog-directory__row[href*='/blog/']"):
            href = (a.get("href") or "").strip()
            if not href or "/topic/" in href or "/category/" in href or href.rstrip("/").endswith("/blog"):
                continue
            url = href if href.startswith("http") else f"https://cursor.com{href}"
            url = clean_url(url)
            if url in seen:
                continue
            seen.add(url)
            img = a.find("img", alt=True)
            title = (img.get("alt") or "").strip() if img else ""
            if not title:
                # Title <p> has class "text-theme-text" (not "...-mid" / "...-sec" variants)
                for p_el in a.select("p.text-theme-text"):
                    txt = p_el.get_text(strip=True)
                    if txt:
                        title = txt
                        break
            t = a.find("time")
            date_str = ""
            if t:
                date_str = parse_date(t.get("datetime", "") or t.get_text(strip=True))
            # Description: the <p> after the title, if present
            desc = ""
            for p_el in a.find_all("p"):
                txt = p_el.get_text(strip=True)
                if txt and txt != title:
                    desc = txt
                    break
            add_post(posts_map, "cursor", title, date_str, url, desc)
    except Exception as e:
        print(f"  Error fetching Cursor: {e}")


_PERP_DATE_RE = re.compile(rf"{_MONTHS_RE}\.?\s+\d{{1,2}},?\s+20\d{{2}}")


def _clean_perplexity_title(text: str) -> str:
    """Perplexity cards concatenate title + 'Month D, YYYY' + categories
    with no separator. Take everything before the first date occurrence."""
    if not text:
        return text
    text = " ".join(text.split())
    m = _PERP_DATE_RE.search(text)
    if m:
        return text[: m.start()].strip().rstrip("·,").strip()
    return text


def fetch_perplexity(posts_map: dict):
    """Fetch Perplexity hub posts. Only /hub/blog/<slug> — skip careers/legal/etc."""
    print("Fetching: Perplexity (Playwright)...")
    try:
        html = fetch_with_playwright("https://www.perplexity.ai/hub", wait_selector="a[href*='/hub/']", scroll=True)
        soup = BeautifulSoup(html, "html.parser")
        seen = set()
        for a in soup.select("a[href*='/hub/blog/']"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            url = href if href.startswith("http") else f"https://www.perplexity.ai{href}"
            url = clean_url(url)
            if url in seen:
                continue
            seen.add(url)
            card = _find_card(a) or a
            h = card.find(["h1", "h2", "h3", "h4"]) if card else None
            if h and h.get_text(strip=True):
                title = h.get_text(strip=True)
            else:
                title = _clean_perplexity_title(a.get_text(" ", strip=True))
            date_str = _extract_date_from_card(card)
            desc_el = card.find("p") if card else None
            desc = desc_el.get_text(strip=True) if desc_el else ""
            add_post(posts_map, "perplexity", title, date_str, url, desc)
    except Exception as e:
        print(f"  Error fetching Perplexity: {e}")


def fetch_qwen(posts_map: dict):
    """Fetch Qwen posts from qwen.ai/research (JS-rendered).
    Post URLs look like https://qwen.ai/blog?id=<slug>."""
    print("Fetching: Qwen (Playwright)...")
    try:
        html = fetch_with_playwright("https://qwen.ai/research", wait_selector="a[href*='/blog?id=']", scroll=True)
        soup = BeautifulSoup(html, "html.parser")
        seen = set()
        for a in soup.select("a[href*='/blog?id=']"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            url = href if href.startswith("http") else f"https://qwen.ai{href}"
            url = clean_url(url)
            if url in seen:
                continue
            seen.add(url)
            card = _find_card(a) or a
            h = card.find(["h1", "h2", "h3", "h4"]) if card else None
            title = h.get_text(strip=True) if h else a.get_text(" ", strip=True)
            date_str = _extract_date_from_card(card)
            desc_el = card.find("p") if card else None
            desc = desc_el.get_text(strip=True) if desc_el else ""
            add_post(posts_map, "qwen", title, date_str, url, desc)
    except Exception as e:
        print(f"  Error fetching Qwen: {e}")


def fetch_xai(posts_map: dict):
    print("Fetching: xAI (Playwright)...")
    try:
        html = fetch_with_playwright("https://x.ai/news", wait_selector="a[href*='/news/']", scroll=True)
        soup = BeautifulSoup(html, "html.parser")
        seen = set()
        for a in soup.select("a[href*='/news/']"):
            href = (a.get("href") or "").strip()
            if not href or href.rstrip("/").endswith("/news"):
                continue
            url = href if href.startswith("http") else f"https://x.ai{href}"
            url = clean_url(url)
            if url in seen:
                continue
            seen.add(url)
            card = _find_card(a) or a
            h = card.find(["h1", "h2", "h3", "h4"]) if card else None
            title = h.get_text(strip=True) if h else a.get_text(strip=True)
            date_str = _extract_date_from_card(card)
            desc_el = card.find("p") if card else None
            desc = desc_el.get_text(strip=True) if desc_el else ""
            add_post(posts_map, "xai", title, date_str, url, desc)
    except Exception as e:
        print(f"  Error fetching xAI: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"AI Blog Aggregator - {datetime.now().isoformat()}")
    print(f"Cutoff date: {CUTOFF_DATE}")
    print("=" * 50)

    posts_map = load_existing()
    existing_count = len(posts_map)
    print(f"Loaded {existing_count} existing posts")

    migrate_source(posts_map, "codex", "openai")
    cleanup_stale(posts_map)
    cleanup_titles(posts_map)
    refetch_fallback_dates(posts_map)
    print()

    standard_fetchers = [
        fetch_anthropic,
        fetch_openai,
        fetch_gemma,
        fetch_kimi,
        fetch_openclaw,
        fetch_ollama,
        fetch_cursor,
    ]
    for fetcher in standard_fetchers:
        try:
            fetcher(posts_map)
        except Exception as e:
            print(f"  Unexpected error in {fetcher.__name__}: {e}")
        print()

    playwright_fetchers = [
        fetch_perplexity,
        fetch_qwen,
        fetch_xai,
    ]
    print("--- Headless browser fetchers ---\n")
    for fetcher in playwright_fetchers:
        try:
            fetcher(posts_map)
        except Exception as e:
            print(f"  Unexpected error in {fetcher.__name__}: {e}")
        print()

    close_browser()

    new_count = len(posts_map) - existing_count
    print("=" * 50)
    print(f"Total posts: {len(posts_map)} ({'+' if new_count >= 0 else ''}{new_count} vs start)")
    save_posts(posts_map)


if __name__ == "__main__":
    main()
