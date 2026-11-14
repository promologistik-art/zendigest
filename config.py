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
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    key=os.getenv("GEMINI_API_KEY","").strip()
    if not token or not key:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and GEMINI_API_KEY in /home/zendigest/.env")
    try:
        admins=frozenset(int(x.strip()) for x in os.getenv("TELEGRAM_ADMIN_IDS","").split(",") if x.strip())
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_ADMIN_IDS must contain numeric IDs") from exc
    if not admins:
        raise RuntimeError("Set at least one Telegram numeric user ID in TELEGRAM_ADMIN_IDS")
    channel=os.getenv("TELEGRAM_CHANNEL_ID","@zentestdigest").strip()
    if channel != "@zentestdigest":
        raise RuntimeError("Initial version is locked to @zentestdigest; do not change without owner approval")
    hour=int(os.getenv("PUBLISH_HOUR","8")); minute=int(os.getenv("PUBLISH_MINUTE","0"))
    if not 0<=hour<=23 or not 0<=minute<=59: raise RuntimeError("Invalid publish time")
    return Settings(token,admins,channel,key,os.getenv("GEMINI_MODEL","gemini-2.5-flash").strip(),
        Path(os.getenv("DATABASE_PATH","/home/zendigest/data/zendigest.sqlite3")),
        Path(os.getenv("LOG_PATH","/home/zendigest/logs/zendigest.log")),
        ZoneInfo(os.getenv("TIMEZONE","Etc/GMT-4")),hour,minute,
        max(1,min(5,int(os.getenv("MAX_NEWS_PER_DAY","5")))),
        max(6,int(os.getenv("FETCH_HOURS","48"))))
