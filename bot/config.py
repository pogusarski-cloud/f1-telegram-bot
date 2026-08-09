"""Конфигурация бота: чтение из переменных окружения и файла .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


_load_dotenv(BASE_DIR / ".env")


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(x.strip() for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    # Обязательные
    bot_token: str = os.getenv("BOT_TOKEN", "")
    channel_id: str = os.getenv("CHANNEL_ID", "")

    # Необязательные
    admin_chat_id: str = os.getenv("ADMIN_CHAT_ID", "")
    timezone: str = os.getenv("TZ", "Europe/Moscow")
    collect_interval_min: int = int(os.getenv("COLLECT_INTERVAL_MIN", "15"))
    post_times: tuple[str, ...] = _csv(
        "POST_TIMES",
        "07:30,08:15,09:00,09:45,10:30,11:15,12:00,12:45,13:30,14:15,"
        "15:00,15:45,16:30,17:15,18:00,18:45,19:30,20:15,21:00,21:45",
    )
    max_age_hours: float = float(os.getenv("MAX_AGE_HOURS", "30"))
    duplicate_threshold: float = float(os.getenv("DUPLICATE_THRESHOLD", "0.62"))
    keep_tokens_for: int = int(os.getenv("KEEP_TOKENS_FOR", "300"))
    http_timeout: float = float(os.getenv("HTTP_TIMEOUT", "20"))
    max_items_per_feed: int = int(os.getenv("MAX_ITEMS_PER_FEED", "50"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    db_path: Path = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "f1_bot.db")))

    def validate(self) -> None:
        if not self.bot_token:
            raise RuntimeError("BOT_TOKEN не задан (см. .env.example)")
        if not self.channel_id:
            raise RuntimeError("CHANNEL_ID не задан (см. .env.example)")


settings = Settings()
