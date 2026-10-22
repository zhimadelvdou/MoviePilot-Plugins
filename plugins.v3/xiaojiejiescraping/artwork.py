"""Bounded, anonymous image-header probes for safe backdrop selection."""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from PIL import ImageFile
from app.sdk.config import settings

from .core import safe_image_url
from .transport import AnonymousRequestUtils

MAX_CANDIDATES = 8
MAX_HEADER_BYTES = 256 * 1024
MAX_SIDE = 8192
MAX_PIXELS = 40_000_000


def backdrop_candidates(record: dict, allowed_hosts: tuple[str, ...]) -> tuple[str, ...]:
    """Return bounded, distinct, allowlisted background URLs excluding cover."""
    cover = safe_image_url(record.get("cover_image"), allowed_hosts)
    values = record.get("backdrop_images")
    if not isinstance(values, list):
        return ()
    result = []
    for value in values[:MAX_CANDIDATES]:
        url = safe_image_url(value, allowed_hosts)
        if url and url != cover and url not in result:
            result.append(url)
    return tuple(result)


def _dimensions(url: str) -> tuple[int, int] | None:
    client = None
    response = None
    try:
        client = AnonymousRequestUtils(timeout=5, verify=True, proxies=settings.PROXY)
        response = client.get_res(url, allow_redirects=False, stream=True)
        if getattr(response, "status_code", None) != 200:
            return None
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}:
            return None
        parser = ImageFile.Parser()
        consumed = 0
        for chunk in response.iter_content(chunk_size=8192):
            if not isinstance(chunk, bytes) or not chunk:
                continue
            remaining = MAX_HEADER_BYTES - consumed
            if remaining <= 0:
                break
            data = chunk[:remaining]
            consumed += len(data)
            parser.feed(data)
            if parser.image is not None:
                image = parser.image
                width, height = image.size
                return width, height
        return None
    except Exception:
        return None
    finally:
        try:
            if response is not None:
                response.close()
        except Exception:
            pass
        try:
            if client is not None:
                client.close()
        except Exception:
            pass


def is_usable_landscape(url: str) -> bool:
    dimensions = _dimensions(url)
    if dimensions is None:
        return False
    width, height = dimensions
    return (width >= 320 and height >= 180 and width > height
            and width <= MAX_SIDE and height <= MAX_SIDE
            and width * height <= MAX_PIXELS)
