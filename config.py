import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    token: str
    admins: frozenset[int]
    channel: str
    gemini_key: str
    model: str
    db_path: Path
    log_path: Path
    timezone: ZoneInfo
    hour: int
    minute: int
    max_items: int
    fetch_hours: int


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not token or not key:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and GEMINI_API_KEY in /home/zendigest/.env")

    try:
        admins = frozenset(
            int(value.strip())
            for value in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",")
            if value.strip()
        )
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_ADMIN_IDS must contain numeric IDs") from exc
    if not admins:
        raise RuntimeError("Set at least one Telegram numeric user ID in TELEGRAM_ADMIN_IDS")

    channel = os.getenv("TELEGRAM_CHANNEL_ID", "@zentestdigest").strip()
    if channel != "@zentestdigest":
        raise RuntimeError("Initial version is locked to @zentestdigest; do not change without owner approval")

    # The editorial queue has five fixed Moscow-time publication slots.
    timezone = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
    max_items = max(1, min(5, int(os.getenv("MAX_NEWS_PER_DAY", "5"))))
    fetch_hours = max(6, int(os.getenv("FETCH_HOURS", "48")))

    return Settings(
        token=token,
        admins=admins,
        channel=channel,
        gemini_key=key,
        model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip(),
        db_path=Path(os.getenv("DATABASE_PATH", "/home/zendigest/data/zendigest.sqlite3")),
        log_path=Path(os.getenv("LOG_PATH", "/home/zendigest/logs/zendigest.log")),
        timezone=timezone,
        hour=8,
        minute=0,
        max_items=max_items,
        fetch_hours=fetch_hours,
    )
