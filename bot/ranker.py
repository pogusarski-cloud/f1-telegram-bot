"""Оценка актуальности статьи для публикации."""

from __future__ import annotations

import math
from datetime import datetime, timezone

# «Горячие» темы дают дополнительный вес
HOT_KEYWORDS = (
    "гонка", "гран при", "гран-при", "квалификац", "результаты", "обзор",
    "интервью", "презентаци", "анонс", "расшифровк", "итоги", "стартов",
    "авария", "столкновение", "дтп", "штраф", "дисквалификац", "ошибка",
    "дождь", "рекорд", "победа", "лидер", "титул", "договор", "переход",
    "переподпис", "контракт",
)

_TAU_HOURS = 12.0  # характерное время «устаревания» новости


def freshness(dt: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    age_hours = max(0.0, (now - dt).total_seconds() / 3600.0)
    return math.exp(-age_hours / _TAU_HOURS)


def content_quality(title: str, description: str) -> float:
    """Оценка содержательности: длина текста + горячие темы."""
    text = f"{title} {description}".lower()
    length = min(len(description.strip()) / 400.0, 1.0)
    hot = min(sum(1 for kw in HOT_KEYWORDS if kw in text) / 3.0, 1.0)
    return 0.55 * length + 0.45 * hot


def score_article(
    published_at: datetime,
    source_weight: float,
    title: str,
    description: str,
    now: datetime | None = None,
) -> float:
    return 0.55 * freshness(published_at, now) + 0.30 * source_weight + 0.15 * content_quality(title, description)


def pick_best(candidates: list[dict], now: datetime | None = None) -> dict | None:
    """Возвращает самую актуальную статью из кандидатов."""
    if not candidates:
        return None
    now = now or datetime.now(timezone.utc)

    def key(item: dict) -> float:
        return score_article(
            published_at=item["published_at"],
            source_weight=item["score"],
            title=item["title"],
            description=item["description"],
            now=now,
        )

    return max(candidates, key=key)
