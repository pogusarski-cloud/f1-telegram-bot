"""Оформление и отправка постов в Telegram-канал.

Формат поста:
- краткий заголовок (виден сразу);
- подробная выжимка, скрытая спойлером <tg-spoiler> — раскрывается по тапу;
- источник, время и ссылка на статью.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from io import BytesIO

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from bot.collector import build_ssl_context, make_client_session
from bot.images import MAX_IMAGE_BYTES
from bot.ranker import pick_best

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")

_CAPTION_LIMIT = 1024  # лимит подписи Telegram у фото
_TEXT_LIMIT = 4096  # лимит текстового сообщения

# Лимиты выжимки: для фото-постов короче (место в подписи ограничено)
_TITLE_LIMIT = 110
_SUMMARY_LIMIT_TEXT = 800
_SUMMARY_LIMIT_CAPTION = 480


def _truncate(text: str, limit: int) -> str:
    text = _WS.sub(" ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return cut + "…"


def format_message(item: dict, caption: bool = False) -> str:
    title = html.escape(_truncate(item["title"], _TITLE_LIMIT))
    summary_limit = _SUMMARY_LIMIT_CAPTION if caption else _SUMMARY_LIMIT_TEXT
    summary = html.escape(_truncate(item["description"], summary_limit))
    published: datetime = item["published_at"]
    local = published.astimezone()

    source = html.escape(item["source"])
    time_str = local.strftime("%d.%m в %H:%M")

    lines = [f"<b>{title}</b>"]
    if summary:
        lines.append(f"<tg-spoiler>{summary}</tg-spoiler>")
    lines.append(f"📰 <i>{source}</i> · 🕐 {time_str}")
    lines.append(f"🔗 {item['url']}")

    text = "\n\n".join(lines)
    if caption and len(text) > _CAPTION_LIMIT:
        # ужимаем выжимку до допустимого размера подписи
        while len(text) > _CAPTION_LIMIT and len(summary) > 120:
            summary = _truncate(summary, len(summary) - 80)
            lines = [
                f"<b>{title}</b>",
                f"<tg-spoiler>{html.escape(summary)}</tg-spoiler>",
                f"📰 <i>{source}</i> · 🕐 {time_str}",
                f"🔗 {item['url']}",
            ]
            text = "\n\n".join(lines)
    return text


async def _download_image(session, url: str, ssl_ctx=None) -> bytes | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
    }
    try:
        async with session.get(
            url, headers=headers, ssl=ssl_ctx, timeout=15
        ) as resp:
            if resp.status >= 400:
                return None
            content_type = resp.headers.get("Content-Type", "")
            if "image" not in content_type.lower():
                return None
            data = await resp.read()
            if not data or len(data) > MAX_IMAGE_BYTES:
                return None
            return data
    except Exception:  # noqa: BLE001
        log.debug("Не удалось скачать фото %s", url, exc_info=True)
        return None


async def post_best(bot: Bot, channel_id: str, candidates: list[dict]) -> dict | None:
    best = pick_best(candidates)
    if not best:
        log.info("Нет кандидатов для публикации")
        return None

    image_url = best.get("image_url") or ""
    if image_url:
        session = make_client_session()
        try:
            data = await _download_image(session, image_url, ssl_ctx=build_ssl_context())
            if data:
                await bot.send_photo(
                    channel_id,
                    photo=BufferedInputFile(data, filename="article.jpg"),
                    caption=format_message(best, caption=True),
                    parse_mode=ParseMode.HTML,
                )
                log.info("Опубликовано (с фото): %s", best["title"])
                return best
            log.info("Фото недоступно, публикуем текст: %s", best["title"])
        finally:
            await session.close()

    await bot.send_message(
        channel_id,
        format_message(best),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=False,
    )
    log.info("Опубликовано: %s", best["title"])
    return best
