"""Signed, bounded JavBus artwork relay for browser preview fields only."""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import hashlib
import hmac
import io
import re
import secrets
import time
import warnings
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError

from app.sdk.config import settings

from .transport import AnonymousRequestUtils

MAX_URL_LENGTH = 4096
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_TTL_SECONDS = 24 * 60 * 60
URL_TTL_SECONDS = 60 * 60
MAX_SIDE = 8192
MAX_PIXELS = 40_000_000
ALLOWED_MIME = {
    "image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP",
    "image/gif": "GIF", "image/bmp": "BMP",
}
_PATHS = {
    "www.javbus.com": ("/pics/cover/", "/pics/sample/"),
    "pics.dmm.co.jp": ("/digital/video/",),
}
_SAFE_PATH = re.compile(r"/[A-Za-z0-9._~/-]{1,2048}\Z")


def safe_javbus_artwork_url(value: str, allowed_hosts: tuple[str, ...]) -> str | None:
    """Accept only canonical HTTPS paths owned by the two configured image origins."""
    if (not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH
            or any(char.isspace() or ord(char) < 0x20 for char in value)
            or "\\" in value or "%" in value):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.netloc.lower() not in (host, host + ":443")
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.query or parsed.fragment
                or host not in _PATHS or host not in allowed_hosts
                or not _SAFE_PATH.fullmatch(parsed.path)
                or not any(parsed.path.startswith(prefix) for prefix in _PATHS[host])
                or any(part in (".", "..") for part in parsed.path.split("/"))
                or "//" in parsed.path):
            return None
        return value
    except ValueError:
        return None


class JavBusArtworkRelay:
    """Rotate signatures with plugin generation and proxy only approved image paths."""

    def __init__(self, state_provider):
        self._state_provider = state_provider
        self._secret = secrets.token_bytes(32)
        self._generation = 0

    def rotate(self, generation: int) -> None:
        self._secret = secrets.token_bytes(32)
        self._generation = generation

    @staticmethod
    def _message(url: str, expires: int, generation: int) -> bytes:
        return f"javbus-artwork-v1\n{generation}\n{expires}\n{url}".encode("utf-8")

    def _signature(self, url: str, expires: int, generation: int) -> str:
        return hmac.new(self._secret, self._message(url, expires, generation), hashlib.sha256).hexdigest()

    def signed_url(self, url: str, generation: int, allowed_hosts: tuple[str, ...]) -> str | None:
        if generation != self._generation or safe_javbus_artwork_url(url, allowed_hosts) is None:
            return None
        expires = int(time.time()) + URL_TTL_SECONDS
        signature = self._signature(url, expires, generation)
        return "/api/v1/plugin/XiaojiejieScraping/artwork?" + urlencode({
            "url": url, "expires": expires, "signature": signature,
        })

    def project_media_info(self, projected: dict, generation: int,
                           allowed_hosts: tuple[str, ...]) -> dict:
        """Return signed browser fields without changing cached canonical projection."""
        result = dict(projected)
        for field in ("poster_path", "backdrop_path"):
            url = result.get(field)
            if url is not None:
                signed = self.signed_url(url, generation, allowed_hosts) if isinstance(url, str) else None
                # These are JavBus-only browser display fields. Never expose an
                # unsigned upstream URL when signing loses a generation/host race.
                result[field] = signed
        return result

    @staticmethod
    def _state_tuple(state):
        if (not isinstance(state, tuple) or len(state) != 4
                or state[0] is not True or state[1] is not True
                or type(state[2]) is not int or not isinstance(state[3], tuple)):
            raise HTTPException(status_code=404, detail="Artwork unavailable")
        return state[2], state[3]

    @staticmethod
    def _decode_image(body: bytes, content_type: str):
        expected_format = ALLOWED_MIME.get(content_type)
        if not expected_format:
            return None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(body)) as image:
                    if image.format != expected_format:
                        return None
                    width, height = image.size
                    if (width < 1 or height < 1 or width > MAX_SIDE or height > MAX_SIDE
                            or width * height > MAX_PIXELS):
                        return None
                    image.verify()
                with Image.open(io.BytesIO(body)) as image:
                    image.load()
            return expected_format
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
                Image.DecompressionBombWarning):
            return None

    @staticmethod
    def _read_bounded(response) -> bytes | None:
        length = response.headers.get("Content-Length")
        try:
            if length is not None and int(length) > MAX_BODY_BYTES:
                return None
        except (TypeError, ValueError):
            return None
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not isinstance(chunk, bytes):
                return None
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                return None
            chunks.append(chunk)
        return b"".join(chunks) if total else None

    def fetch(self, url: str, expires: int, signature: str) -> Response:
        generation, hosts = self._state_tuple(self._state_provider())
        now = int(time.time())
        if (self._generation != generation or type(expires) is not int
                or expires < now or expires > now + MAX_TTL_SECONDS
                or not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature)
                or safe_javbus_artwork_url(url, hosts) is None):
            raise HTTPException(status_code=404, detail="Artwork unavailable")
        if not hmac.compare_digest(self._signature(url, expires, generation), signature):
            raise HTTPException(status_code=404, detail="Artwork unavailable")

        client = response = None
        try:
            client = AnonymousRequestUtils(timeout=8, verify=True, proxies=settings.PROXY)
            response = client.get_res(url, headers={"Referer": "https://www.javbus.com/"},
                                      allow_redirects=False, stream=True)
            if response is None or getattr(response, "status_code", None) != 200:
                raise HTTPException(status_code=502, detail="Artwork unavailable")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in ALLOWED_MIME:
                raise HTTPException(status_code=502, detail="Artwork unavailable")
            body = self._read_bounded(response)
            image_format = self._decode_image(body, content_type) if body else None
            if image_format is None:
                raise HTTPException(status_code=502, detail="Artwork unavailable")
            # Recheck after I/O so a concurrent disable/reconfiguration never returns old bytes.
            current_generation, current_hosts = self._state_tuple(self._state_provider())
            if (current_generation != generation or self._generation != generation
                    or safe_javbus_artwork_url(url, current_hosts) is None):
                raise HTTPException(status_code=404, detail="Artwork unavailable")
            return Response(content=body, media_type=content_type,
                            headers={"Cache-Control": "private, max-age=300",
                                     "X-Content-Type-Options": "nosniff"})
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Artwork unavailable") from exc
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


def api_routes(relay: JavBusArtworkRelay) -> list[dict]:
    return [{"path": "/artwork", "endpoint": relay.fetch, "methods": ["GET"],
             "allow_anonymous": True, "summary": "读取限时签名的 JavBus 预览图片"}]
