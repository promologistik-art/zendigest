import logging
from datetime import time

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from publisher import run_digest

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
        "⏳ Собираю материалы и готовлю выпуск…"
    )
    try:
        result = await run_digest(
            ctx.application,
            ctx.application.bot_data["db"],
            ctx.application.bot_data["editor"],
            settings,
        )
        await message.edit_text(
            f"Готово: {result['status']}. "
            f"Собрано: {result['collected']}, "
            f"новых: {result['new']}, "
            f"опубликовано: {result['published']}."
        )
    except Exception as exc:
        log.exception("Manual run failed")
        await message.edit_text(
            f"Ошибка подготовки выпуска: {type(exc).__name__}. "
            "Подробности в журнале сервера."
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
