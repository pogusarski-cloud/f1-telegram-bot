"""Анализ текстов на дублирование.

Используем два признака схожести:
1. Jaccard-подобие по триграммам слов (устойчиво к пересказу/перестановкам).
2. Символьные n-граммы нормализованного заголовка (устойчиво к коротким текстам).
"""

from __future__ import annotations

import html
import re
from functools import lru_cache

STOPWORDS = frozenset(
    """и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только
    ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него
    до вас нибудь уж вам неё себя зато них ка из-за при тот факт самый об после под над чем потому этот
    про для ой мы их нам них при чем всего же ж бы хоть сейчас будто было бывают сюда туда еще же а вот
    которые который которая которое которым которого с которой со который своего свои своей своими
    это этот эта эти том та тех тот там тут также еще нежели либо вместе тем более
    он она оно они его её их имя них""".split()
)

_WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_tags(text: str) -> str:
    return _TAGS.sub(" ", text or "")


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = strip_tags(text)
    text = _WS.sub(" ", text)
    return text.strip()


@lru_cache(maxsize=4096)
def normalize_tokens(text: str) -> tuple[str, ...]:
    text = html.unescape(text or "").lower().replace("ё", "е")
    text = strip_tags(text)
    tokens = _WORD.findall(text)
    return tuple(t for t in tokens if len(t) > 2 and t not in STOPWORDS)


def _shingles(tokens: tuple[str, ...], n: int = 3) -> set[tuple[str, ...]]:
    if not tokens:
        return set()
    if len(tokens) <= n:
        return {tokens}
    return {tokens[i : i + n] for i in range(len(tokens) - n + 1)}


def _char_grams(text: str, n: int = 4) -> set[str]:
    text = re.sub(r"[^a-zа-я0-9]", "", (text or "").lower().replace("ё", "е"))
    if len(text) <= n:
        return {text} if text else set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(a: set, b: set) -> float:
    """Доля пересечения в меньшем из множеств: ловит близкие пересказы."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def similarity(title_a: str, desc_a: str, title_b: str, desc_b: str) -> float:
    """Итоговая схожесть 0..1 для двух статей.

    Заголовок — главный сигнал (одинаковые заголовки = одна и та же статья,
    зеркалируемая между источниками), поэтому ему отдаётся больший вес:
    - символьные n-граммы заголовков (containment): 45%
    - слово-триграммы заголовков (Jaccard):          30%
    - слово-триграммы всего текста (Jaccard):        25%
    """
    tokens_a = normalize_tokens(f"{title_a} {desc_a}")
    tokens_b = normalize_tokens(f"{title_b} {desc_b}")
    title_char = containment(_char_grams(title_a), _char_grams(title_b))
    title_word = jaccard(_shingles(normalize_tokens(title_a)), _shingles(normalize_tokens(title_b)))
    full_word = jaccard(_shingles(tokens_a), _shingles(tokens_b))
    return 0.45 * title_char + 0.30 * title_word + 0.25 * full_word


def tokens_of(title: str, description: str) -> tuple[str, ...]:
    return normalize_tokens(f"{title} {description}")
