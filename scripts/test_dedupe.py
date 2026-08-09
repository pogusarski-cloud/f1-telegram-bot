"""Интеграционный тест пайплайна с мок-коллектором (без сети и Telegram)."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.collector import Article, tokens_of
from bot.db import Database
import bot.pipeline as pipeline


def make_article(i: int, title: str, desc: str) -> Article:
    now = datetime.now(timezone.utc)
    return Article(
        source=f"Src{i}",
        source_url=f"https://s{i}.ru/",
        url=f"https://s{i}.ru/a{i}",
        title=title,
        description=desc,
        published_at=now,
        guid=f"guid{i}",
        tokens=tokens_of(title, desc),
        score=1.0,
    )


async def run() -> None:
    db = Database(Path("/tmp/f1_bot_dup3.db"))
    if db.path.exists():
        db.path.unlink()
    await db.init()

    fake_articles = [
        make_article(1, "Ферстаппен высказался об ошибках Антонелли в дебютном сезоне",
                     "Макс Ферстаппен прокомментировал ошибки Кими Антонелли."),
        make_article(2, "Ферстаппен высказался об ошибках Антонелли в дебютном сезоне",
                     "Макс Ферстаппен прокомментировал ошибки Кими Антонелли."),
        make_article(3, "Хэмилтон стал послом новой команды Cadillac",
                     "Льюис Хэмилтон присоединится к новому проекту в 2026 году."),
    ]

    async def fake_collect(session=None, sources=None, max_items=50, timeout=20.0):
        return fake_articles

    pipeline.collect = fake_collect

    stats = await pipeline.collect_and_store(db)
    print("stats:", stats)

    cur = await db._conn.execute("SELECT title, status FROM articles ORDER BY id")
    rows = await cur.fetchall()
    for row in rows:
        print(" ", row["title"][:55], "->", row["status"])

    assert stats["new"] == 2, "должно быть 2 новых (дубль отброшен)"
    assert stats["duplicate"] == 1, "должен быть 1 дубликат"
    statuses = [row["status"] for row in rows]
    assert statuses.count("duplicate") == 1

    await db.close()
    print("\nOK: дедупликация работает")


if __name__ == "__main__":
    asyncio.run(run())
