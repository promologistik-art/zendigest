import asyncio
import io
import logging
from datetime import datetime

import httpx

from sources import collect, find_article_image

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

    prefix = f"🎬 {title}\n{category.upper()} · {_news_date(item.get('published_at'))}\n\n"
    suffix = f"\n\n💡 {why}\n\n🔗 Источник: {url}"

    # Keep the source link intact; shorten editorial text only if required.
    while _telegram_length(prefix + suffix) > CAPTION_LIMIT and why:
        why = _truncate_utf16(why, max(0, _telegram_length(why) - 20))
        suffix = f"\n\n💡 {why}\n\n🔗 Источник: {url}"
    while _telegram_length(prefix + suffix) > CAPTION_LIMIT and title:
        title = _truncate_utf16(title, max(0, _telegram_length(title) - 20))
        prefix = f"🎬 {title}\n{category.upper()} · {_news_date(item.get('published_at'))}\n\n"

    available = CAPTION_LIMIT - _telegram_length(prefix + suffix)
    if available < 0:
        raise ValueError("Source URL is too long for a single Telegram photo caption")
    body = _truncate_utf16(summary, available)
    caption = prefix + body + suffix

    if _telegram_length(caption) > CAPTION_LIMIT:
        raise ValueError("Caption exceeds Telegram's 1024-character limit")
    return caption


async def prepare_daily(app, db, editor, settings):
    day = datetime.now(settings.timezone).date().isoformat()
    if db.has_prepared_day(day):
        log.info("Daily batch already prepared for %s", day)
        return {"status": "already_prepared", "day": day}

    run_id = db.start()
    try:
        fresh = await collect(settings.fetch_hours)
        inserted = db.add(fresh)
        pending = db.pending(settings.fetch_hours)
        if not pending:
            db.enqueue_day(day, [], f"collected={len(fresh)}; no fresh candidate stories")
            db.finish(run_id, "empty", f"collected={len(fresh)} new={inserted}; no candidates")
            return {"status": "empty", "collected": len(fresh), "new": inserted, "queued": 0}

        selected = await editor.edit(pending, settings.max_items)
        originals = {item["url"]: item for item in pending}

        async def prepare_item(selection):
            original = originals.get(selection["url"])
            if not original:
                return None
            image_url = original.get("image_url") or await find_article_image(original["url"])
            if not image_url:
                log.warning("Skipping selected story without a usable image")
                return None
            item = {**original, **selection, "image_url": image_url}
            return {**item, "caption": build_caption(item, day)}

        prepared_results = await asyncio.gather(*(prepare_item(item) for item in selected))
        prepared = [item for item in prepared_results if item]

        db.annotate(prepared)
        db.enqueue_day(
            day,
            prepared,
            f"collected={len(fresh)} new={inserted}; candidates={len(pending)}; queued={len(prepared)}",
        )
        status = "prepared" if prepared else "empty"
        db.finish(run_id, status, f"collected={len(fresh)} new={inserted}; queued={len(prepared)}")
        log.info(
            "Daily batch prepared: day=%s collected=%s new=%s queued=%s",
            day, len(fresh), inserted, len(prepared),
        )
        return {
            "status": status, "day": day, "collected": len(fresh),
            "new": inserted, "queued": len(prepared),
        }
    except Exception as exc:
        db.finish(run_id, "error", type(exc).__name__)
        log.exception("Daily preparation failed")
        raise


async def publish_next(app, db, editor, settings):
    day = datetime.now(settings.timezone).date().isoformat()
    if not db.has_prepared_day(day):
        await prepare_daily(app, db, editor, settings)

    item = db.next_queued(day)
    if not item:
        counts = db.queue_counts(day)
        log.info("No pending post for %s; queue=%s", day, counts)
        return {"status": "no_pending", "day": day, "queue": counts}

    queue_id = item["id"]
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(25, connect=10),
            follow_redirects=True,
            headers={"User-Agent": "ZenDigest/1.0 Telegram publisher"},
        ) as client:
            response = await client.get(item["image_url"])
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if not content_type.startswith("image/"):
                raise ValueError("The selected RSS image URL did not return an image")
            if len(response.content) > IMAGE_MAX_BYTES:
                raise ValueError("The selected image exceeds Telegram's photo upload limit")

        photo = io.BytesIO(response.content)
        photo.name = "zendigest-photo"
        sent = await app.bot.send_photo(
            chat_id=settings.channel,
            photo=photo,
            caption=item["caption"],
            read_timeout=35,
            write_timeout=35,
            connect_timeout=15,
            pool_timeout=15,
        )
        db.queue_sent(queue_id)
        db.mark([item["article_url"]])
        log.info(
            "Telegram photo post published: day=%s slot=%s message_id=%s",
            day, item["slot"], sent.message_id,
        )
        return {
            "status": "published", "day": day,
            "slot": item["slot"], "message_id": sent.message_id,
        }
    except Exception as exc:
        db.queue_failed(queue_id, type(exc).__name__)
        log.exception("Telegram photo post failed: day=%s slot=%s", day, item["slot"])
        raise
