"""Хранение статей в SQLite."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from bot.collector import Article

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    tokens TEXT NOT NULL DEFAULT '[]',
    image_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    score REAL NOT NULL DEFAULT 0,
    posted_at TEXT,
    UNIQUE(source, guid)
);
CREATE INDEX IF NOT EXISTS idx_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_published ON articles(published_at);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(str(self.path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Добавляет новые колонки в уже существующую базу."""
        cur = await self._conn.execute("PRAGMA table_info(articles)")
        cols = [row[1] for row in await cur.fetchall()]
        if "image_url" not in cols:
            await self._conn.execute(
                "ALTER TABLE articles ADD COLUMN image_url TEXT NOT NULL DEFAULT ''"
            )

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def article_seen(self, article: Article) -> bool:
        cur = await self._conn.execute(
            "SELECT 1 FROM articles WHERE source=? AND guid=?",
            (article.source, article.guid),
        )
        return await cur.fetchone() is not None

    async def add_article(
        self, article: Article, status: str, is_duplicate_of: str | None = None
    ) -> int:
        now = datetime.now(timezone.utc)
        cur = await self._conn.execute(
            """INSERT OR REPLACE INTO articles
               (guid, url, source, source_url, title, description, published_at,
                collected_at, tokens, image_url, status, score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article.guid,
                article.url,
                article.source,
                article.source_url,
                article.title,
                article.description,
                _iso(article.published_at),
                _iso(now),
                json.dumps(list(article.tokens), ensure_ascii=False),
                article.image_url,
                status,
                article.score,
            ),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def recent_tokens(self, limit: int) -> list[tuple[tuple[str, ...], str, str]]:
        """Возвращает (tokens, title, description) недавних статей для сравнения на дубли."""
        cur = await self._conn.execute(
            "SELECT tokens, title, description FROM articles "
            "WHERE tokens != '[]' AND status IN ('new', 'posted', 'duplicate') "
            "ORDER BY collected_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        result = []
        for row in rows:
            try:
                tokens = tuple(json.loads(row["tokens"]))
            except (json.JSONDecodeError, TypeError):
                continue
            result.append((tokens, row["title"], row["description"]))
        return result

    async def unposted(self, max_age_hours: float, limit: int = 100) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        cur = await self._conn.execute(
            """SELECT * FROM articles
               WHERE status='new' AND published_at >= ?
               ORDER BY published_at ASC LIMIT ?""",
            (_iso(cutoff), limit),
        )
        rows = await cur.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "id": row["id"],
                    "url": row["url"],
                    "source": row["source"],
                    "source_url": row["source_url"],
                    "title": row["title"],
                    "description": row["description"],
                    "published_at": _parse_iso(row["published_at"]),
                    "score": row["score"],
                    "image_url": row["image_url"],
                }
            )
        return out

    async def mark_posted(self, article_id: int) -> None:
        await self._conn.execute(
            "UPDATE articles SET status='posted', posted_at=? WHERE id=?",
            (_iso(datetime.now(timezone.utc)), article_id),
        )
        await self._conn.commit()

    async def counts(self) -> dict[str, int]:
        cur = await self._conn.execute(
            "SELECT status, COUNT(*) AS c FROM articles GROUP BY status"
        )
        rows = await cur.fetchall()
        return {row["status"]: row["c"] for row in rows}
