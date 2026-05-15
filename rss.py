import asyncio
import re
import logging
from datetime import datetime, timezone, timedelta

import aiohttp
import feedparser

from config import config, RSS_FEEDS
from db import is_seen

log = logging.getLogger("autoposter.rss")

_IMG_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.IGNORECASE)
_IMG_TAG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def _extract_image_url(entry) -> str | None:
    """Best-effort: pull the first usable image URL from a feedparser entry."""
    # 1. <media:content> / <media:thumbnail> — used by Wired, TechCrunch, Ars Technica
    for attr in ("media_content", "media_thumbnail"):
        for item in getattr(entry, attr, None) or []:
            url = item.get("url")
            if not url:
                continue
            medium = item.get("medium", "")
            mtype = item.get("type", "")
            if medium == "image" or mtype.startswith("image/") or _IMG_EXT_RE.search(url):
                return url

    # 2. <enclosure> attachments
    for enc in getattr(entry, "enclosures", None) or []:
        url = enc.get("href") or enc.get("url")
        if url and enc.get("type", "").startswith("image/"):
            return url

    # 3. First <img> embedded in summary / content HTML
    for field in ("summary", "content"):
        val = getattr(entry, field, None)
        if isinstance(val, list) and val:
            val = val[0].get("value", "") if isinstance(val[0], dict) else ""
        if isinstance(val, str):
            m = _IMG_TAG_RE.search(val)
            if m:
                return m.group(1)

    return None


async def _fetch_feed_async(session: aiohttp.ClientSession, feed_cfg: dict, cutoff: datetime) -> list[dict]:
    name = feed_cfg["name"]
    url = feed_cfg["url"]
    has_comments = feed_cfg.get("has_comments", False)
    articles = []

    log.info("Fetching %s …", name)
    try:
        async with session.get(url, timeout=20) as resp:
            resp.raise_for_status()
            xml_data = await resp.text()
    except Exception as e:
        log.warning("Failed to download %s: %s", name, e)
        return []

    try:
        # Feedparser parses strings synchronously but this is mostly CPU bound.
        parsed = feedparser.parse(xml_data)
    except Exception as e:
        log.warning("Failed to parse %s: %s", name, e)
        return []

    now = datetime.now(timezone.utc)
    for entry in parsed.entries:
        # Resolve published time
        published = None
        for attr in ("published_parsed", "updated_parsed"):
            tp = getattr(entry, attr, None)
            if tp:
                published = datetime(*tp[:6], tzinfo=timezone.utc)
                break
        if published is None:
            published = now  # assume fresh if no timestamp

        if published < cutoff:
            continue  # older than window

        # Comment count (Hacker News exposes this via hnrss)
        comment_count = 0
        if has_comments:
            # hnrss puts comment count in the <comments> tag content or slash:comments
            comment_count = int(getattr(entry, "slash_comments", 0) or 0)

        link = getattr(entry, "link", "")
        title = getattr(entry, "title", "(no title)")
        summary = getattr(entry, "summary", "")
        # Strip HTML from summary
        summary = re.sub(r"<[^>]+>", "", summary)[:500]

        articles.append({
            "title": title,
            "link": link,
            "source": name,
            "published": published,
            "comment_count": comment_count,
            "summary": summary,
            "image_url": _extract_image_url(entry),
        })
    return articles

async def _fetch_all_articles_async() -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=config.hotness_window_hours)
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for feed_cfg in RSS_FEEDS:
            tasks.append(_fetch_feed_async(session, feed_cfg, cutoff))
        
        results = await asyncio.gather(*tasks)
        
    return [article for feed_articles in results for article in feed_articles]

def fetch_articles() -> list[dict]:
    """Fetch articles from all configured RSS feeds and return a flat list."""
    return asyncio.run(_fetch_all_articles_async())

def score_articles(articles: list[dict]) -> list[dict]:
    """
    Rank articles by a simple hotness score.
    Score = recency_score (0–1) + popularity_score (comment-based, 0–1).
    """
    if not articles:
        return []

    now = datetime.now(timezone.utc)
    max_age = config.hotness_window_hours * 3600

    # Normalise comment count
    max_comments = max((a["comment_count"] for a in articles), default=1) or 1

    for a in articles:
        age_seconds = (now - a["published"]).total_seconds()
        recency = max(0.0, 1.0 - age_seconds / max_age)
        popularity = a["comment_count"] / max_comments
        a["score"] = round(recency * 0.6 + popularity * 0.4, 4)

    articles.sort(key=lambda a: a["score"], reverse=True)
    return articles

def deduplicate(articles: list[dict], conn) -> list[dict]:
    """Remove articles we have already processed."""
    fresh = []
    for a in articles:
        if not is_seen(conn, a["link"]):
            fresh.append(a)
    return fresh
