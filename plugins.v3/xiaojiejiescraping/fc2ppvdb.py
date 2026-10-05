"""Conservative FC2PPVDB-lineage adapter for FC2CMADB; offline verified only.

Public software contracts read 2026-10-05 (fresh implementation, no copied code):
* MDCx (GPL-3.0), detail GET and Inertia field/selector facts:
  https://github.com/z291173301/MDCx/blob/05ca8a8675cd40259823acc482309edee60501b4/mdcx/crawlers/fc2ppvdb.py
* Peach (AGPL-3.0), product identity and anonymous synthetic schema facts only:
  https://github.com/peach-mitao/peach/blob/489653d0edff03104c091a814679ca4b4ebdcc24/tests/test_metadata_fc2.py#L115-L140

The source's /articles/{video_id} permalink uses the FC2 product ID. Its internal
article.id is DIFFERENT and is never substituted for that route identity.
The stable provider key remains fc2ppvdb. The separate fc2ppv-db.com application
is not this adapter. Public-source evidence is not a live availability test.

One ordinary anonymous HTML GET only: no credentials, cookies, redirects,
partial/deferred actress requests, browser automation, age/challenge bypass,
MetaTube dependency, or image requests. Missing/gated schemas fail closed.
"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from datetime import date
import ipaddress
import json
import re
from urllib.parse import urljoin, urlsplit

from lxml import etree

from app.sdk.config import settings
from .transport import AnonymousAsyncRequestUtils as AsyncRequestUtils, AnonymousRequestUtils as RequestUtils

from .core import clean_text, normalize_number, record_numbers


MAX_HTML_BYTES = 2_000_000
PRODUCT_ID = re.compile(r"[0-9]{5,9}\Z")


def _unique_object(pairs):
    """Duplicate JSON keys are ambiguous even when a JSON decoder would overwrite."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-JSON numeric constant")


def _cover_url(value, base: str) -> str | None:
    """Return a metadata URL only; final image-host permission stays with the host."""
    if (not isinstance(value, str) or not value or len(value) > 8192
            or "\\" in value or any(char.isspace() or ord(char) < 32 for char in value)
            or "no-image" in value):
        return None
    try:
        url = urljoin(base + "/", value)
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment
                or host == "localhost" or host.endswith((".localhost", ".local"))):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return url
    except ValueError:
        pass
    return None


class FC2PPVDBProvider:
    """A source product permalink identity, not an internal database primary key."""
    source = "fc2ppvdb"
    base = "https://fc2cmadb.com"

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    @staticmethod
    def _valid_identity(identity) -> bool:
        return isinstance(identity, str) and PRODUCT_ID.fullmatch(identity) is not None

    def _parse(self, response, identity: str) -> dict | None:
        if response is None or response.status_code != 200:
            return None
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in ("text/html", "application/xhtml+xml"):
            return None
        path = "/articles/" + identity
        # SDK redirects are disabled; reject mismatched response URLs as well.
        if str(getattr(response, "url", "") or self.base + path) != self.base + path:
            return None
        body = response.content
        if not isinstance(body, bytes) or not body or len(body) > MAX_HTML_BYTES:
            return None
        try:
            text = body.decode("utf-8")
            document = etree.fromstring(
                text.encode("utf-8"),
                etree.HTMLParser(no_network=True, recover=False, encoding="utf-8"),
            )
            if document is None:
                return None
            scripts = document.xpath('//script[@data-page="app" and @type="application/json"]')
            if len(scripts) != 1:
                return None
            payload = scripts[0].text
            if not isinstance(payload, str) or not payload.strip():
                return None
            page = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except (ValueError, TypeError, RecursionError, etree.LxmlError):
            return None
        if (not isinstance(page, dict) or page.get("component") != "Articles/Show"
                or page.get("url") != path):
            return None
        props = page.get("props")
        if not isinstance(props, dict) or props.get("status") or props.get("errors"):
            return None
        auth = props.get("auth")
        if auth is not None and (not isinstance(auth, dict) or auth.get("user") is not None):
            return None
        article = props.get("article")
        if not isinstance(article, dict):
            return None
        video_id = article.get("video_id")
        if type(video_id) not in (str, int) or str(video_id) != identity:
            return None
        title = article.get("title")
        if not isinstance(title, str) or len(title) > 4096:
            return None
        title = clean_text(title).strip()
        if not title or title.casefold() in ("fc2cmadb", "fc2ppvdb"):
            return None
        released = article.get("release_date")
        try:
            released = date.fromisoformat(released).isoformat() if isinstance(released, str) else ""
        except ValueError:
            released = ""
        number = "FC2-PPV-" + identity
        record = {
            "_source": self.source, "_id": identity, "number": number,
            "title": title, "share_date": released,
            "cover_image": _cover_url(article.get("image_url"), self.base),
            "discription": [],
        }
        if record_numbers(record) != {number}:
            return None
        # Deferred actresses are deliberately not requested or inferred from sellers.
        return {"record": record, "number": number, "actors": []}

    def fetch(self, identity: str) -> dict | None:
        if not self._valid_identity(identity):
            return None
        client = RequestUtils(timeout=self.timeout, verify=True, proxies=settings.PROXY,
                              accept_type="text/html", cookies=None)
        response = None
        try:
            response = client.get_res(self.base + "/articles/" + identity, allow_redirects=False)
            return self._parse(response, identity)
        finally:
            if response is not None:
                response.close()
            client.close()

    async def async_fetch(self, identity: str) -> dict | None:
        if not self._valid_identity(identity):
            return None
        client = AsyncRequestUtils(timeout=self.timeout, verify=True, proxies=settings.PROXY,
                                   accept_type="text/html", cookies=None)
        response = None
        try:
            response = await client.get_res(self.base + "/articles/" + identity, allow_redirects=False)
            return self._parse(response, identity)
        finally:
            if response is not None:
                await response.aclose()

    def search(self, number: str) -> dict | None:
        normalized = normalize_number(number) if isinstance(number, str) else None
        if not normalized or not normalized.startswith("FC2-PPV-"):
            return None
        return self.fetch(normalized.removeprefix("FC2-PPV-"))

    async def async_search(self, number: str) -> dict | None:
        normalized = normalize_number(number) if isinstance(number, str) else None
        if not normalized or not normalized.startswith("FC2-PPV-"):
            return None
        return await self.async_fetch(normalized.removeprefix("FC2-PPV-"))
