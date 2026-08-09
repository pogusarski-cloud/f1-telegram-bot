"""Smoke-тест: реальный сбор RSS, дедупликация и ранжирование без Telegram."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp

from bot.collector import collect
from bot.config import settings
from bot.db import Database
from bot.dedupe import similarity
from bot.ranker import score_article


async def main() -> None:
    db = Database(Path("/tmp/f1_bot_smoke.db"))
    if db.path.exists():
        db.path.unlink()
    await db.init()

    session = aiohttp.ClientSession()
    try:
        articles = await collect(
            session,
            max_items=settings.max_items_per_feed,
            timeout=settings.http_timeout,
        )
    finally:
        await session.close()

    print(f"Собрано релевантных статей: {len(articles)}\n")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=settings.max_age_hours)
    recent = []
    new = dup = skipped = 0

    for art in sorted(articles, key=lambda a: a.published_at, reverse=True):
        if art.published_at < cutoff:
            skipped += 1
            continue
        best_sim = 0.0
        for tokens, title, description in recent:
            sim = similarity(art.title, art.description, title, description)
            best_sim = max(best_sim, sim)
        if best_sim >= settings.duplicate_threshold:
            dup += 1
            print(f"[DUP {best_sim:.2f}] {art.title[:70]}")
            await db.add_article(art, status="duplicate")
        else:
            new += 1
            await db.add_article(art, status="new")
            recent.append((art.tokens, art.title, art.description))
            recent = recent[: settings.keep_tokens_for]

    print(f"\nИтог: новых={new}, дубликатов={dup}, пропущено(старше)= {skipped}")

    candidates = await db.unposted(settings.max_age_hours)
    scored = []
    for c in candidates:
        s = score_article(c["published_at"], c["score"], c["title"], c["description"], now)
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)

    print("\nТОП-5 самых актуальных:")
    for s, c in scored[:5]:
        pub = c["published_at"].astimezone().strftime("%m.%d %H:%M")
        print(f"  [{s:.3f}] {c['title'][:75]} ({c['source']}, {pub})")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
