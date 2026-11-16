import logging
from datetime import datetime, time

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from publisher import build_caption, prepare_daily, publish_next
from sources import collect

log = logging.getLogger(__name__)


def admin(update: Update, settings) -> bool:
    user = update.effective_user
    return bool(user and user.id in settings.admins)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    settings = ctx.application.bot_data["settings"]
    if not admin(update, settings):
        await update.effective_message.reply_text(
            "ZenDigest работает. Команды управления доступны администратору."
        )
        return
    await update.effective_message.reply_text(
        "ZenDigest запущен.\n"
        "/status — состояние\n"
        "/run — предпросмотр новостей\n"
        "/prepare — подготовить очередь на сегодня\n"
        "/publish — отправить следующий пост в тестовый канал\n"
        "/myid — ваш Telegram ID\n"
        "/help — помощь"
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await start(update, ctx)


async def myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        await update.effective_message.reply_text(f"Ваш Telegram ID: {user.id}")


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    settings = ctx.application.bot_data["settings"]
    if not admin(update, settings):
        return
    db = ctx.application.bot_data["db"]
    data = db.status()
    day = datetime.now(settings.timezone).date().isoformat()
    queue = db.queue_counts(day)
    last = data["last"]
    last_run = f"{last['started']} · {last['status']}" if last else "ещё не запускался"
    await update.effective_message.reply_text(
        "📊 ZenDigest\n"
        f"Материалов в базе: {data['total']}\n"
        f"Опубликовано: {data['published']}\n"
        f"Ожидают: {data['pending']}\n"
        f"Очередь сегодня: {queue}\n"
        f"Последний запуск: {last_run}\n"
        f"Канал: {settings.channel}\n"
        "Публикации: 08:00, 10:00, 12:00, 14:00, 16:00 МСК"
    )


async def run_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    settings = ctx.application.bot_data["settings"]
    if not admin(update, settings):
        return
    message = await update.effective_message.reply_text(
        "⏳ Собираю материалы для предварительного просмотра…"
    )
    try:
        db = ctx.application.bot_data["db"]
        editor = ctx.application.bot_data["editor"]
        fresh = await collect(settings.fetch_hours)
        pending = db.pending(settings.fetch_hours)
        by_url = {item["url"]: item for item in pending}
        for item in fresh:
            by_url.setdefault(item["url"], item)
        articles = [item for item in by_url.values() if item.get("image_url")]
        if not articles:
            await message.edit_text("Не найдено свежих материалов с изображениями.")
            return
        selected = await editor.edit(articles, settings.max_items)
        originals = {item["url"]: item for item in articles}
        items = [{**originals[item["url"]], **item} for item in selected]
        if not items:
            await message.edit_text("Редактор не отобрал подходящих новостей.")
            return
        await ctx.bot.send_message(
            chat_id=update.effective_user.id,
            text="👀 Предпросмотр ZenDigest. Это личный предпросмотр, в канал ничего не отправлено.",
        )
        for index, item in enumerate(items, start=1):
            caption = build_caption(
                item, datetime.now(settings.timezone).strftime("%d.%m.%Y")
            )
            await ctx.bot.send_message(
                chat_id=update.effective_user.id,
                text=f"Предпросмотр поста {index}/{len(items)}:\n\n{caption}",
                disable_web_page_preview=True,
            )
        await message.edit_text(
            f"Предпросмотр отправлен в личные сообщения. Материалов: {len(items)}. "
            "Канал и очередь публикаций не изменялись."
        )
    except Exception as exc:
        log.exception("Manual preview failed")
        await message.edit_text(
            f"Ошибка подготовки предпросмотра: {type(exc).__name__}. "
            "Публикации в канал не было; подробности в журнале сервера."
        )


async def prepare_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    settings = ctx.application.bot_data["settings"]
    if not admin(update, settings):
        return
    message = await update.effective_message.reply_text("⏳ Готовлю очередь публикаций…")
    try:
        result = await prepare_daily(
            ctx.application, ctx.application.bot_data["db"],
            ctx.application.bot_data["editor"], settings,
        )
        await message.edit_text(f"Подготовка завершена: {result}")
    except Exception as exc:
        await message.edit_text(
            f"Подготовка не удалась: {type(exc).__name__}. "
            "Канал не затронут; проверьте журнал сервера."
        )


async def publish_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    settings = ctx.application.bot_data["settings"]
    if not admin(update, settings):
        return
    message = await update.effective_message.reply_text("⏳ Отправляю следующий пост в тестовый канал…")
    try:
        result = await publish_next(
            ctx.application, ctx.application.bot_data["db"],
            ctx.application.bot_data["editor"], settings,
        )
        await message.edit_text(f"Результат публикации: {result}")
    except Exception as exc:
        await message.edit_text(
            f"Публикация не удалась: {type(exc).__name__}. "
            "Подробности доступны в журнале сервера."
        )


async def scheduled_prepare(ctx: ContextTypes.DEFAULT_TYPE):
    data = ctx.application.bot_data
    try:
        result = await prepare_daily(ctx.application, data["db"], data["editor"], data["settings"])
        log.info("Scheduled preparation result: %s", result)
    except Exception:
        log.exception("Scheduled preparation failed")


async def scheduled_publish(ctx: ContextTypes.DEFAULT_TYPE):
    data = ctx.application.bot_data
    try:
        result = await publish_next(ctx.application, data["db"], data["editor"], data["settings"])
        log.info("Scheduled publication result: %s", result)
    except Exception:
        log.exception("Scheduled publication failed")


def register(app: Application, settings, db, editor):
    app.bot_data.update(settings=settings, db=db, editor=editor)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("run", run_cmd))
    app.add_handler(CommandHandler("prepare", prepare_cmd))
    app.add_handler(CommandHandler("publish", publish_cmd))
    app.add_handler(CommandHandler("myid", myid))

    app.job_queue.run_daily(
        scheduled_prepare,
        time=time(7, 30, tzinfo=settings.timezone),
        name="daily-prepare",
    )
    for hour in (8, 10, 12, 14, 16):
        app.job_queue.run_daily(
            scheduled_publish,
            time=time(hour, 0, tzinfo=settings.timezone),
            name=f"photo-post-{hour:02d}",
        )
