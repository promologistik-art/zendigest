import io
import logging
from datetime import datetime

import httpx

from sources import collect

log = logging.getLogger(__name__)
CAPTION_LIMIT = 1024
IMAGE_MAX_BYTES = 9_500_000


def _news_date(value):
    if not value:
        return "дата не указана"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(value)[:10]


def _telegram_length(value):
    # Telegram entities and message lengths use UTF-16 code units.
    return len(value.encode("utf-16-le")) // 2


def _truncate_utf16(value, max_units):
    result = []
    used = 0
    for character in value:
        units = len(character.encode("utf-16-le")) // 2
        if used + units > max_units:
            break
        result.append(character)
        used += units
    return "".join(result).rstrip()


def build_caption(item, digest_date):
    title = _truncate_utf16(
        (item.get("title_ru") or item.get("title") or "Культурная новость").strip(), 180
    )
    category = item.get("category", "Культура")
    summary = (item.get("summary") or "").strip()
    why = _truncate_utf16((item.get("why") or "").strip(), 180)
    url = (item.get("url") or "").strip()

    if not url or not item.get("image_url"):
        raise ValueError("A Telegram photo post requires both source URL and image URL")

    prefix = f"🎬 {title}\\n{category.upper()} · {_news_date(item.get('published_at'))}\\n\\n"
    suffix = f"\\n\\n💡 {why}\\n\\n🔗 Источник: {url}"

    # Keep the source link intact. If needed, reduce the editorial note and headline
    # before allocating the remaining Telegram caption budget to the story itself.
    while _telegram_length(prefix + suffix) > CAPTION_LIMIT and why:
        why = _truncate_utf16(why, max(0, _telegram_length(why) - 20))
        suffix = f"\\n\\n💡 {why}\\n\\n🔗 Источник: {url}"
    while _telegram_length(prefix + suffix) > CAPTION_LIMIT and title:
        title = _truncate_utf16(title, max(0, _telegram_length(title) - 20))
        prefix = f"🎬 {title}\\n{category.upper()} · {_news_date(item.get('published_at'))}\\n\\n"

    available = CAPTION_LIMIT - _telegram_length(prefix + suffix)
    if available < 0:
        raise ValueError("Source URL is too long for a single Telegram photo caption")
    body = _truncate_utf16(summary, available)
    caption = prefix + body + suffix

    if _telegram_length(caption) > CAPTION_LIMIT:
        raise ValueError("Caption exceeds Telegram's 1024-character limit")
    return caption
