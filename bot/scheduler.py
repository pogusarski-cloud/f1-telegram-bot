"""Планировщик: сбор по интервалу, публикация по расписанию, дневной отчёт."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import settings
from bot.db import Database
from bot.pipeline import collect_and_store, publish_one

log = logging.getLogger(__name__)


async def _daily_report(db: Database, bot) -> None:
    if not settings.admin_chat_id:
        return
    counts = await db.counts()
    total = sum(counts.values())
    text = (
        "📊 Дневной отчёт F1-бота\n\n"
        f"Всего статей в базе: {total}\n"
        f"Новых: {counts.get('new', 0)}\n"
        f"Опубликовано: {counts.get('posted', 0)}\n"
        f"Дубликатов: {counts.get('duplicate', 0)}"
    )
    await bot.send_message(settings.admin_chat_id, text)


def build_scheduler(bot, db: Database) -> AsyncIOScheduler:
    tz = ZoneInfo(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)

    scheduler.add_job(
        collect_and_store,
        trigger="interval",
        minutes=settings.collect_interval_min,
        kwargs={"db": db},
        id="collect",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    for t in settings.post_times:
        try:
            hour, minute = (int(x) for x in t.split(":"))
        except ValueError:
            log.warning("Некорректное время поста %r — пропускаю", t)
            continue
        scheduler.add_job(
            publish_one,
            trigger="cron",
            hour=hour,
            minute=minute,
            kwargs={"db": db, "bot": bot},
            id=f"post_{t}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,
        )

    if settings.admin_chat_id:
        scheduler.add_job(
            _daily_report,
            trigger="cron",
            hour=23,
            minute=50,
            kwargs={"db": db, "bot": bot},
            id="daily_report",
            max_instances=1,
            coalesce=True,
        )

    log.info(
        "Расписание: сбор каждые %d мин; публикации в %s",
        settings.collect_interval_min,
        ", ".join(settings.post_times) or "—",
    )
    return scheduler
