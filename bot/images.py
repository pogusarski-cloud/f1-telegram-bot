"""Извлечение метаданных со страницы статьи: og:image и meta description.

Нужно для источников вроде Google News, где RSS-фид не отдаёт ни картинок,
ни внятных описаний.
"""

from __future__ import annotations

import asyncio
import logging
import re

import aiohttp

log = logging.getLogger(__name__)

# <meta property="og:image" content="..."> в обоих порядках атрибутов
_OG_IMAGE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    re.IGNORECASE,
)
_META_DESC = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_META_DESC_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']description["\']',
    re.IGNORECASE,
)

_BAD_EXTENSIONS = (".gif", ".webp", ".svg", ".ico", ".png")

MAX_PAGE_BYTES = 300_000
MAX_IMAGE_BYTES = 9 * 1024 * 1024  # лимит Telegram — 10 МБ
_TIMEOUT = 12.0


def _looks_like_image(url: str) -> bool:
    clean = url.split("?")[0].lower()
    return not clean.endswith(_BAD_EXTENSIONS)


async def fetch_page_meta(
    session: aiohttp.ClientSession, article_url: str, ssl_ctx=None
) -> tuple[str | None, str | None]:
    """Открывает страницу статьи, возвращает (og:image_url, meta_description)."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,*/*",
    }
    try:
        async with session.get(
            article_url,
            headers=headers,
            ssl=ssl_ctx,
            timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
        ) as resp:
            if resp.status >= 400:
                return None, None
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return None, None
            body = await resp.content.read(MAX_PAGE_BYTES)
    except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
        return None, None
    except Exception:  # noqa: BLE001
        log.debug("meta: неожиданная ошибка для %s", article_url, exc_info=True)
        return None, None

    text = body.decode("utf-8", errors="ignore")

    image_url = None
    for pattern in (_OG_IMAGE, _OG_IMAGE_REV):
        match = pattern.search(text)
        if match and match.group(1).strip():
            url = match.group(1).strip()
            if _looks_like_image(url):
                image_url = url
                break

    description = None
    for pattern in (_META_DESC, _META_DESC_REV):
        match = pattern.search(text)
        if match and match.group(1).strip():
            description = re.sub(r"\s+", " ", match.group(1)).strip()
            break

    return image_url, description


async def enrich_pages(
    session: aiohttp.ClientSession,
    articles: list,
    ssl_ctx=None,
    min_desc_len: int = 280,
    limit: int = 5,
) -> None:
    """Для статей без картинки или с коротким описанием догружает метаданные.

    Догружает в объекты статей: image_url и описание (если нашлось лучше текущего).
    Ссылки news.google.com пропускаем: их превью-страницы на JS и метаданных не отдают.
    """
    need = [
        a
        for a in articles
        if (not a.image_url or len(a.description) < min_desc_len)
        and "news.google.com" not in a.url
    ]
    if not need:
        return

    sem = asyncio.Semaphore(limit)

    async def worker(article) -> None:
        async with sem:
            og_image, meta_desc = await fetch_page_meta(session, article.url, ssl_ctx)
        if og_image:
            article.image_url = og_image
        if meta_desc and len(meta_desc) > len(article.description):
            article.description = meta_desc

    await asyncio.gather(*(worker(a) for a in need))
