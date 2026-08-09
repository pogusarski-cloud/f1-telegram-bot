"""Точка входа для запуска в GitHub Actions (cron-режим).

GitHub Actions каждый раз поднимает свежую виртуалку, поэтому бот работает
«по вызовам»: каждые 15 минут запускается заново и делает:
1. подтягивает базу статей из репозитория;
2. собирает новые статьи из RSS;
3. если текущее время (TZ) попадает в расписание публикаций — постит одну
   самую актуальную статью в канал;
4. коммитит обновлённую базу обратно в репозиторий (состояние переживает
   между запусками).

Компьютер пользователя при этом может быть выключен.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.collector import make_client_session
from bot.config import settings
from bot.db import Database
from bot.pipeline import collect_and_store, publish_one
from main import IPv4AiohttpSession

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

STATE_SLOT = "next_post_slot"


def git(*args: str) -> None:
    subprocess.run(["git", *args], check=False, capture_output=True)


def slot_to_dt(date: str, hhmm: str) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    dt = datetime.fromisoformat(f"{date} {hour:02d}:{minute:02d}")
    return dt.replace(tzinfo=ZoneInfo(settings.timezone))


def next_slot_after(now: datetime) -> datetime:
    """Ближайший пост-тайм строго после now (на сегодня или завтра)."""
    for t in settings.post_times:
        candidate = slot_to_dt(now.date().isoformat(), t)
        if candidate > now:
            return candidate
    first = slot_to_dt(now.date().isoformat(), settings.post_times[0])
    return first + timedelta(days=1)


async def main() -> None:
    git("pull", "--rebase", "origin", "main")

    db = Database(settings.db_path)
    await db.init()

    session = make_client_session()
    try:
        await collect_and_store(db, session)
    finally:
        await session.close()

    now = datetime.now(ZoneInfo(settings.timezone))
    raw = await db.get_state(STATE_SLOT)
    if raw:
        slot = datetime.fromisoformat(raw)
        if slot.tzinfo is None:
            slot = slot.replace(tzinfo=ZoneInfo(settings.timezone))
    else:
        slot = next_slot_after(now)
        await db.set_state(STATE_SLOT, slot.isoformat())
        print(f"Инициализирован слот публикации: {slot:%d.%m %H:%M}")

    if now >= slot:
        bot = Bot(
            settings.bot_token,
            session=IPv4AiohttpSession(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            await publish_one(db, bot)
        finally:
            await bot.session.close()
        slot = next_slot_after(now)
        await db.set_state(STATE_SLOT, slot.isoformat())
        print(f"Следующий слот: {slot:%d.%m %H:%M}")
    else:
        print(f"Публикация не запланирована (следующий слот {slot:%d.%m %H:%M})")

    # Сохраняем состояние базы в репозиторий
    await db._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    await db.close()

    git("add", "data/")
    git(
        "-c", "user.name=f1-bot",
        "-c", "user.email=f1-bot@users.noreply.github.com",
        "commit", "-m", f"update state {now:%Y-%m-%d %H:%M}",
    )
    git("push", "origin", "main")


if __name__ == "__main__":
    asyncio.run(main())
