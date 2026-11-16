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


def build_caption(item, digest_date):
    title = (item.get("title_ru") or item.get("title") or "Культурная новость").strip()
    category = item.get("category", "Культура")
    summary = (item.get("summary") or "").strip()
    why = (item.get("why") or "").strip()
    url = (item.get("url") or "").strip()

    def compose(body):
        return (
            f"🎬 {title}\n"
            f"{category.upper()} · { _news_date(item.get('published_at')) }\n\n"
            f"{body}\n\n"
            f"💡 {why}\n\n"
            f"🔗 Источник: {url}"
        )

    caption = compose(summary)
    while _telegram_length(caption) > CAPTION_LIMIT and summary:
        overflow = _telegram_length(caption) - CAPTION_LIMIT
        summary = summary[:max(0, len(summary) - overflow - 8)].rstrip()
        if summary and len(summary) > 3:
            summary = summary.rsplit(" ", 1)[0].rstrip(" ,;:")
        caption = compose(summary)

    if _telegram_length(caption) > CAPTION_LIMIT and why:
        why = why[:max(0, len(why) - (_telegram_length(caption) - CAPTION_LIMIT) - 8)].rstrip()
        caption = compose(summary)

    if _telegram_length(caption) > CAPTION_LIMIT:
        # Last-resort safety for unusually long titles or source URLs.
        fixed = (
            f"🎬 {title[:160]}\n{category.upper()} · {_news_date(item.get('published_at'))}\n\n"
            f"💡 {why[:120]}\n\n🔗 Источник: {url}"
        )
        allowance = max(0, CAPTION_LIMIT - _telegram_length(fixed))
        caption = fixed if allowance == 0 else (
            f"🎬 {title[:160]}\n{category.upper()} · {_news_date(item.get('published_at'))}\n\n"
            f"{summary[:allowance]}\n\n💡 {why[:120]}\n\n🔗 Источник: {url}"
        )
        while _telegram_length(caption) > CAPTION_LIMIT:
            caption = caption[:-2].rstrip()

    if not url or not item.get("image_url"):
        raise ValueError("A Telegram photo post requires both source URL and image URL")
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
        with_images = [item for item in pending if item.get("image_url")]
        if not with_images:
            db.enqueue_day(day, [], f"collected={len(fresh)}; no image-backed stories")
            db.finish(run_id, "empty", f"collected={len(fresh)} new={inserted}; no image-backed stories")
            return {"status": "empty", "collected": len(fresh), "new": inserted, "queued": 0}

        selected = await editor.edit(with_images, settings.max_items)
        originals = {item["url"]: item for item in with_images}
        prepared = []
        for selection in selected:
            original = originals.get(selection["url"])
            if not original or not original.get("image_url"):
                continue
            item = {**original, **selection}
            caption = build_caption(item, day)
            prepared.append({**item, "caption": caption})

        db.annotate(prepared)
        db.enqueue_day(
            day, prepared,
            f"collected={len(fresh)} new={inserted}; candidates={len(with_images)}; queued={len(prepared)}",
        )
        status = "prepared" if prepared else "empty"
        db.finish(run_id, status, f"collected={len(fresh)} new={inserted}; queued={len(prepared)}")
        log.info("Daily batch prepared: day=%s collected=%s new=%s queued=%s",
                 day, len(fresh), inserted, len(prepared))
        return {"status": status, "day": day, "collected": len(fresh),
                "new": inserted, "queued": len(prepared)}
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
        log.info("Telegram photo post published: day=%s slot=%s message_id=%s",
                 day, item["slot"], sent.message_id)
        return {"status": "published", "day": day, "slot": item["slot"],
                "message_id": sent.message_id}
    except Exception as exc:
        db.queue_failed(queue_id, type(exc).__name__)
        log.exception("Telegram photo post failed: day=%s slot=%s", day, item["slot"])
        raise
