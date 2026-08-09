"""Сбор статей из RSS-источников и фильтрация по релевантности Формуле-1."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp
import feedparser

from bot.dedupe import clean_text, tokens_of

log = logging.getLogger(__name__)

# (label, url, weight) — weight влияет на ранжирование актуальности
SOURCES: list[tuple[str, str, float]] = [
    ("Чемпионат F1", "https://www.championat.com/rss/news/auto/f1/", 1.0),
    ("Autosport.ru", "https://autosport.com.ru/rss.xml", 1.0),
    ("F1News.ru", "https://www.f1news.ru/export/news.xml", 1.1),
    ("Motor.ru", "https://motor.ru/export/rss/f1", 0.8),
    (
        "Google Новости: F1",
        "https://news.google.com/rss/search?q=%22%D1%84%D0%BE%D1%80%D0%BC%D1%83%D0%BB%D0%B0%201%22&hl=ru&gl=RU&ceid=RU:ru",
        0.9,
    ),
]

# Ядро словаря F1: совпадение хотя бы одного — статья релевантна
F1_CORE_KEYWORDS = (
    "формула-1", "формулы-1", "формуле-1", "формулой-1", "формула 1", "формулы 1",
    "formula 1", "formula one", "гран-при", "гран при", "ф1", "f1",
    "ферстаппен", "verstappen", "хэмилтон", "hamilton", "леклер", "leclerc",
    "норрис", "norris", "пиастри", "piastri", "рассел", "russell", "сейнс", "sainz",
    "антонелли", "antonelli", "хаджар", "hadjar", "перес", "perez", "алонсо", "alonso",
    "боттас", "bottas", "хюлкенберг", "hulkenberg", "мерседес", "mercedes",
    "ферарри", "ferrari", "макларен", "mclaren", "ред булл", "red bull",
    "уильямс", "williams", "астон мартин", "aston martin", "альпин", "alpine",
    "хаас", "haas", "ауди", "audi", "кадиллак", "cadillac", "racing bulls",
    "пит-стоп", "pit stop", "пит-лейн", "болид", "автодром", "квалификац",
    "грид", "подшлемник", "хейло", "даунфорс", "пелотон", "покрышк",
    "ф-1",
    "монца", "имола", "сильверстоун", "силверстоун", "зандворт", "сузука",
    "монте-карло", "модьород", "будапешт", "джидда", "баку", "мельбурн",
    "майами", "вегас", "чемпионат формул", "чемпионат f1", "спринт", "поул",
)

# Дополнительные слова — нужно минимум 2, чтобы считать релевантным
F1_SOFT_KEYWORDS = (
    "гонка", "гонки", "гонщик", "гонщики", "трасс", "команда", "команды",
    "пилот", "пилоты", "этап", "уик-энд", "титул", "чемпион", "чемпионат",
    "бокс", "лоб",
)

# Другие серии: если упоминаются без F1-сигнала — статья не релевантна
F1_EXCLUDE_KEYWORDS = (
    "motogp", "мотогп", "мотогонк", "супербайк", "формула-е", "формула е",
    "формула-e", "indycar", "индикар", "wec", "лманас", "ралли",
)


@dataclass
class Article:
    source: str
    source_url: str
    url: str
    title: str
    description: str
    published_at: datetime
    guid: str
    tokens: tuple[str, ...] = field(default=())
    score: float = 0.0
    is_duplicate: bool = False
    image_url: str = ""

    def domain(self) -> str:
        return urlparse(self.source_url).netloc


def _strip_google_suffix(title: str) -> str:
    """Убирает хвост ' - Источник' у статей Google News."""
    parts = re.split(r"\s+-\s+", title)
    if len(parts) > 1 and 0 < len(parts[-1]) < 60:
        return " - ".join(parts[:-1])
    return title


def _entry_image(entry: dict) -> str:
    """Картинка из RSS: media:content > enclosure > media:thumbnail."""
    for field in ("media_content", "media_thumbnail"):
        items = entry.get(field)
        if items:
            for it in items:
                url = (it.get("url") or "").strip()
                if url:
                    return url
    for enc in entry.get("enclosures") or []:
        url = (enc.get("href") or "").strip()
        if url:
            return url
    return ""


def _normalize_entry(source_label: str, source_url: str, entry: dict, now: datetime) -> Article | None:
    title = entry.get("title") or ""
    if not title:
        return None
    title = _strip_google_suffix(title).strip()
    title = clean_text(title)

    link = entry.get("link") or entry.get("id") or ""
    guid = entry.get("id") or entry.get("guid") or link

    description = entry.get("summary") or entry.get("description") or ""
    if entry.get("content"):
        description = entry["content"][0].get("value", description)
    description = clean_text(description)

    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        published_at = datetime(*published[:6], tzinfo=timezone.utc)
    else:
        published_at = now

    return Article(
        source=source_label,
        source_url=source_url,
        url=link,
        title=title,
        description=description,
        published_at=published_at,
        guid=guid,
        image_url=_entry_image(entry),
    )


def _is_relevant(article: Article) -> bool:
    text = f"{article.title} {article.description}".lower()
    if not article.url:
        return False
    if any(kw in text for kw in F1_EXCLUDE_KEYWORDS):
        # исключаем другие серии; остаются только статьи с явным F1-сигналом
        f1_signal = ("формул", "formula", "ф-1", " ф1", "f1")
        if not any(kw in text for kw in f1_signal):
            return False
    core = sum(1 for kw in F1_CORE_KEYWORDS if kw in text)
    soft = sum(1 for kw in F1_SOFT_KEYWORDS if kw in text)
    return core >= 1 or soft >= 2


def make_client_session() -> aiohttp.ClientSession:
    """HTTP-сессия с принудительным IPv4.

    В некоторых сетях IPv6 заблокирован (соединение сбрасывается), из-за чего
    aiohttp падает, пробуя подключиться по IPv6 первым.
    """
    connector = aiohttp.TCPConnector(
        family=socket.AF_INET,
        limit=50,
        ttl_dns_cache=3600,
    )
    return aiohttp.ClientSession(connector=connector)


def build_ssl_context() -> ssl.SSLContext | None:
    """SSL-контекст для HTTP-запросов.

    Если системные CA-сертификаты не загружены (типично для macOS-сборок
    python.org), используем bundle из certifi. При SSL_VERIFY=false —
    отключаем проверку (только для отладки).
    """
    if os.getenv("SSL_VERIFY", "true").lower() in ("0", "false", "no"):
        log.warning("Проверка SSL-сертификатов отключена (SSL_VERIFY=false)")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) > 0:
        return ctx
    try:
        import certifi  # type: ignore

        log.info("Системные CA не найдены, использую certifi")
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        log.warning("CA-сертификаты не найдены и certifi недоступен")
        return ctx


async def _fetch_feed(session: aiohttp.ClientSession, url: str, timeout: float) -> bytes:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    ssl_ctx = build_ssl_context()
    async with session.get(
        url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout), ssl=ssl_ctx
    ) as resp:
        resp.raise_for_status()
        return await resp.read()


def _parse_feed(raw: bytes, source_label: str, source_url: str, now: datetime, max_items: int) -> list[Article]:
    feed = feedparser.parse(raw)
    articles: list[Article] = []
    for entry in feed.entries[:max_items]:
        try:
            art = _normalize_entry(source_label, source_url, entry, now)
            if art and _is_relevant(art):
                art.tokens = tokens_of(art.title, art.description)
                articles.append(art)
        except Exception:  # noqa: BLE001 — одна плохая запись не должна ронять всё
            log.exception("Ошибка разбора записи из %s", source_label)
    return articles


async def collect(
    session: aiohttp.ClientSession,
    sources: list[tuple[str, str, float]] | None = None,
    max_items: int = 50,
    timeout: float = 20.0,
) -> list[Article]:
    """Забирает все источники параллельно, возвращает релевантные статьи."""
    sources = sources or SOURCES
    now = datetime.now(timezone.utc)
    articles: list[Article] = []

    async def worker(source_label: str, source_url: str, weight: float) -> None:
        try:
            raw = await _fetch_feed(session, source_url, timeout)
        except Exception:  # noqa: BLE001
            log.warning("Не удалось получить %s (%s)", source_label, source_url)
            return
        items = _parse_feed(raw, source_label, source_url, now, max_items)
        for art in items:
            art.score = weight
        log.info("%s: получено %d релевантных статей", source_label, len(items))
        articles.extend(items)

    await asyncio.gather(*(worker(label, url, w) for label, url, w in sources))
    return articles

