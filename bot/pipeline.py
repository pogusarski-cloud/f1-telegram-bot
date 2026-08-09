"""Основной пайплайн: сбор -> дедупликация -> сохранение -> публикация."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from bot.collector import Article, SOURCES, collect, build_ssl_context, make_client_session
from bot.config import settings
from bot.db import Database
from bot.dedupe import similarity
from bot.images import enrich_pages
from bot.poster import post_best

log = logging.getLogger(__name__)


async def collect_and_store(db: Database, session: aiohttp.ClientSession | None = None) -> dict[str, int]:
    """Один цикл сбора. Возвращает статистику (new / duplicate / seen)."""
    stats = {"seen": 0, "duplicate": 0, "new": 0}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=settings.max_age_hours)

    created_session = session is None
    session = session or make_client_session()
    try:
        articles: list[Article] = await collect(
            session,
            sources=list(SOURCES),
            max_items=settings.max_items_per_feed,
            timeout=settings.http_timeout,
        )
        # Обогащаем только свежие ещё не виденные статьи: og:image + описание
        fresh_unseen = [
            a
            for a in articles
            if a.published_at >= cutoff and not await db.article_seen(a)
        ]
        await enrich_pages(session, fresh_unseen, ssl_ctx=build_ssl_context())
    finally:
        if created_session:
            await session.close()

    recent = await db.recent_tokens(settings.keep_tokens_for)

    for article in articles:
        if article.published_at < cutoff:
            continue
        if await db.article_seen(article):
            stats["seen"] += 1
            continue

        best_sim = 0.0
        for tokens, title, description in recent:
            sim = similarity(
                article.title, article.description, title, description
            )
            if sim > best_sim:
                best_sim = sim

        if best_sim >= settings.duplicate_threshold:
            stats["duplicate"] += 1
            await db.add_article(article, status="duplicate")
        else:
            stats["new"] += 1
            await db.add_article(article, status="new")
            recent.append((article.tokens, article.title, article.description))
            recent = recent[: settings.keep_tokens_for]

    log.info("Сбор завершён: %s", stats)
    return stats


async def publish_one(db: Database, bot) -> bool:
    """Публикует одну самую актуальную статью. True — если что-то опубликовано."""
    candidates = await db.unposted(settings.max_age_hours)
    if not candidates:
        log.info("Публикация пропущена: нет новых статей в очереди")
        return False
    posted = await post_best(bot, settings.channel_id, candidates)
    if posted:
        await db.mark_posted(posted["id"])
        return True
    return False
