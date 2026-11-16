import logging
from logging.handlers import RotatingFileHandler

from telegram import Update
from telegram.ext import Application

from config import load_settings
from db import Database
from ai import Editor
from bot import register


def main():
    settings = load_settings()
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                settings.log_path,
                maxBytes=3_000_000,
                backupCount=4,
                encoding="utf-8",
            ),
        ],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # HTTPX INFO logs include full Telegram Bot API URLs (which contain the bot token).
    # Keep network diagnostics useful without writing credentials to logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)

    db = Database(settings.db_path)
    editor = Editor(settings.gemini_key, settings.model)
    app = Application.builder().token(settings.token).build()
    register(app, settings, db, editor)

    logging.getLogger(__name__).info(
        "ZenDigest starts; channel=%s timezone=%s preparation=07:30 posts=08:00,10:00,12:00,14:00,16:00",
        settings.channel,
        settings.timezone,
    )
    app.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
