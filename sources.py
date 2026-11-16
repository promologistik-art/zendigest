import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode

import feedparser
import httpx

log = logging.getLogger(__name__)

FEEDS = [
    "https://www.theguardian.com/film/rss",
    "https://www.theguardian.com/books/rss",
    "https://www.theguardian.com/music/rss",
    "https://pitchfork.com/rss/news/",
    "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
]
QUERIES = [
    "кино премьера фильм режиссёр", "сериал премьера сериал стриминг",
    "книга роман писатель литературная премия", "музыка альбом релиз музыкант",
    "film movie cinema release", "TV series streaming release",
    "books publishing novel author", "music album release artist",
]
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def clean_url(url):
    parsed = urlsplit(url.strip())
    query = urlencode([
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ])
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), query, ""))


def _image_url(entry):
    # Prefer publisher-provided media metadata in the RSS entry.
    for field in ("media_content", "media_thumbnail"):
        for media in entry.get(field, []) or []:
            url = media.get("url")
            if url and str(media.get("type", "")).startswith("image"):
                return clean_url(url)
            if url:
                return clean_url(url)

    for link in entry.get("links", []) or []:
        media_type = str(link.get("type", "")).lower()
        if link.get("href") and (media_type.startswith("image/") or link.get("rel") == "enclosure"):
            return clean_url(link["href"])

    # Some feeds embed the lead image in the HTML summary/content.
    html_fields = [entry.get("summary", "")]
    html_fields.extend(item.get("value", "") for item in entry.get("content", []) or [])
    for value in html_fields:
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', value or "", re.I)
        if match:
            return clean_url(match.group(1))
    return None


async def resolve_article_url(client, url):
    host = (urlsplit(url).hostname or "").lower()
    if host not in {"news.google.com", "www.news.google.com"}:
        return clean_url(url)
    original = clean_url(url)
    try:
        response = await client.get(url, follow_redirects=True, timeout=10)
        final = clean_url(str(response.url))
        final_host = (urlsplit(final).hostname or "").lower()
        if response.status_code >= 400:
            log.warning("Article redirect returned HTTP %s", response.status_code)
            return None
        if final_host in {"consent.google.com", "www.consent.google.com"}:
            return original
        if final_host in {"news.google.com", "www.news.google.com"}:
            return original
        return final
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Could not resolve Google News article link: %s", type(exc).__name__)
        return original


def parse_date(entry):
    value = entry.get("published") or entry.get("updated")
    if value:
        try:
            parsed = parsedate_to_datetime(value)
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            pass
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else datetime.now(timezone.utc)


def strip(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


async def read_feed(client, url):
    response = await client.get(url)
    response.raise_for_status()
    parsed = feedparser.parse(response.text)
    out = []
    is_google_news = (urlsplit(url).hostname or "").lower() == "news.google.com"
    semaphore = asyncio.Semaphore(8)

    async def resolve(link):
        async with semaphore:
            return await resolve_article_url(client, link)

    entries = parsed.entries[:35]
    raw_links = [entry.get("link", "") for entry in entries]
    resolved = await asyncio.gather(*(
        resolve(link) if is_google_news else asyncio.sleep(0, result=clean_url(link))
        for link in raw_links
    ), return_exceptions=True)

    for entry, resolved_url in zip(entries, resolved):
        if isinstance(resolved_url, Exception) or not resolved_url:
            continue
        title = strip(entry.get("title", ""))
        if not title:
            continue
        published = parse_date(entry)
        if published > datetime.now(timezone.utc) + timedelta(hours=3):
            continue
        out.append({
            "url": resolved_url,
            "title": title,
            "description": strip(entry.get("summary", ""))[:2400],
            "source": entry.get("source", {}).get("title", "") or urlsplit(resolved_url).netloc,
            "published_at": published.isoformat(),
            "image_url": _image_url(entry),
        })
    return out


async def collect(max_age_hours=48):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(18, connect=8),
        follow_redirects=True,
        headers={"User-Agent": "ZenDigest/1.0 RSS reader"},
    ) as client:
        tasks = [read_feed(client, url) for url in FEEDS]
        tasks += [
            read_feed(client, "https://news.google.com/rss/search?q=" + quote(query) + "&hl=ru&gl=RU&ceid=RU:ru")
            for query in QUERIES
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    unique = {}
    for result in results:
        if isinstance(result, Exception):
            log.warning("Feed failed: %s", type(result).__name__)
            continue
        for item in result:
            unique.setdefault(item["url"], item)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, max_age_hours))
    fresh = []
    for item in unique.values():
        try:
            published = datetime.fromisoformat(item["published_at"])
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            log.warning("Skipping article with invalid publication date")
            continue
        if published >= cutoff:
            fresh.append(item)

    log.info("RSS collection: total=%s fresh_within_%sh=%s with_images=%s",
             len(unique), max_age_hours, len(fresh),
             sum(bool(item.get("image_url")) for item in fresh))
    return fresh
