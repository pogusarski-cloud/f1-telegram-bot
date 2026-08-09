"""Точка входа бота."""

from __future__ import annotations

import asyncio
import logging
import socket

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from bot.config import settings
from bot.db import Database
from bot.pipeline import collect_and_store
from bot.scheduler import build_scheduler

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("f1bot")


class IPv4AiohttpSession(AiohttpSession):
    """Сессия Telegram API только по IPv4 (в некоторых сетях IPv6 заблокирован)."""

    def __init__(self, proxy=None, limit: int = 100, **kwargs):
        super().__init__(proxy=proxy, limit=limit, **kwargs)
        self._connector_init["family"] = socket.AF_INET


async def main() -> None:
    settings.validate()
    log.info("Старт бота. ТЗ=%s, канал=%s", settings.timezone, settings.channel_id)

    db = Database(settings.db_path)
    await db.init()

    bot = Bot(
        settings.bot_token,
        session=IPv4AiohttpSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    scheduler = build_scheduler(bot, db)

    try:
        scheduler.start()
        # Первый сбор сразу после старта
        try:
            await collect_and_store(db)
        except Exception:  # noqa: BLE001 — падение сбора не должно убивать бота
            log.exception("Ошибка первого сбора статей")
        try:
            await bot.send_message(
                settings.channel_id, "🚀 F1-бот запущен. Следующие публикации по расписанию."
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось отправить сообщение о запуске: %s", exc)
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        pass
    finally:
        scheduler.shutdown(wait=False)
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
