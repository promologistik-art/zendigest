import logging
from datetime import time

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from datetime import datetime

from publisher import render, run_digest
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
        "/run — тестовый выпуск\n"
        "/myid — ваш Telegram ID\n"
        "/help — помощь"
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await start(update, ctx)


async def myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    await update.effective_message.reply_text(
        f"Ваш Telegram ID: {user.id}"
    )


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    settings = ctx.application.bot_data["settings"]
    if not admin(update, settings):
        return

    data = ctx.application.bot_data["db"].status()
    last = data["last"]
    last_run = (
        f"{last['started']} · {last['status']}"
        if last else "ещё не запускался"
    )

    await update.effective_message.reply_text(
        "📊 ZenDigest\n"
        f"Материалов: {data['total']}\n"
        f"Опубликовано: {data['published']}\n"
        f"Ожидают: {data['pending']}\n"
        f"Последний запуск: {last_run}\n"
        f"Канал: {settings.channel}"
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

        # Preview is deliberately read-only: do not insert, annotate, mark,
        # or publish anything to the configured channel.
        fresh = await collect()
        pending = db.pending(settings.fetch_hours)
        by_url = {item["url"]: item for item in pending}
        for item in fresh:
            by_url.setdefault(item["url"], item)
        articles = list(by_url.values())

        if not articles:
            await message.edit_text("Подходящих свежих материалов не найдено.")
            return

        selected = await editor.edit(articles, settings.max_items)
        if not selected:
            await message.edit_text("Редактор не отобрал подходящих новостей.")
            return

        originals = {item["url"]: item for item in articles}
        items = [{**originals[item["url"]], **item} for item in selected]
        digest = render(
            items,
            datetime.now(settings.timezone).strftime("%d.%m.%Y"),
        )
        while len(digest) > 3900 and len(items) > 1:
            items.pop()
            digest = render(
                items,
                datetime.now(settings.timezone).strftime("%d.%m.%Y"),
            )

        await ctx.bot.send_message(
            chat_id=update.effective_user.id,
            text="👀 Предварительный просмотр ZenDigest (в канал не опубликовано):",
        )
        await ctx.bot.send_message(
            chat_id=update.effective_user.id,
            text=digest,
            parse_mode="HTML",
            disable_web_page_preview=False,
        )
        await message.edit_text(
            f"Предпросмотр отправлен вам в личные сообщения. "
            f"Материалов проверено: {len(articles)}; в выпуске: {len(items)}. "
            "База и канал не изменялись."
        )
    except Exception as exc:
        log.exception("Manual preview failed")
        await message.edit_text(
            f"Ошибка подготовки предпросмотра: {type(exc).__name__}. "
            "Публикации в канал не было; подробности в журнале сервера."
        )


async def scheduled(ctx: ContextTypes.DEFAULT_TYPE):
    app = ctx.application
    data = app.bot_data
    try:
        result = await run_digest(
            app, data["db"], data["editor"], data["settings"]
        )
        log.info("Scheduled result: %s", result)
    except Exception:
        log.exception("Scheduled run failed")


def register(
    app: Application,
    settings,
    db,
    editor,
):
    app.bot_data.update(settings=settings, db=db, editor=editor)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("run", run_cmd))
    app.add_handler(CommandHandler("myid", myid))

    app.job_queue.run_daily(
        scheduled,
        time=time(
            settings.hour,
            settings.minute,
            tzinfo=settings.timezone,
        ),
        name="daily-digest",
    )
