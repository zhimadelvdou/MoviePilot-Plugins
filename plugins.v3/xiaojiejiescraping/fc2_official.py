"""Conservative anonymous adapter for FC2's official product pages."""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlsplit

from lxml import etree

from app.sdk.config import settings
from .core import clean_text, normalize_number
from .transport import AnonymousAsyncRequestUtils as AsyncRequestUtils
from .transport import AnonymousRequestUtils as RequestUtils

# Parse-time response-body ceiling; the SDK has already buffered the response.
MAX_HTML_BYTES = 2_000_000
PRODUCT_ID = re.compile(r"[0-9]{5,9}\Z")
BASE = "https://adult.contents.fc2.com"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-JSON numeric constant")


def _products(value):
    """Yield only Product nodes from JSON-LD values and @graph containers."""
    if isinstance(value, list):
        for item in value:
            yield from _products(item)
    elif isinstance(value, dict):
        kind = value.get("@type")
        if kind == "Product" or isinstance(kind, list) and "Product" in kind:
            yield value
        graph = value.get("@graph")
        if isinstance(graph, (list, dict)):
            yield from _products(graph)


def _image_url(value):
    """Return a candidate only; `core.safe_image_url` retains final host approval."""
    if not isinstance(value, dict) or value.get("@type") != "ImageObject":
        return None
    url = value.get("url")
    if not isinstance(url, str) or len(url) > 8192 or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if not host or parsed.username or parsed.password or parsed.fragment:
            return None
        if parsed.scheme == "http":
            # The official Product JSON-LD currently exposes this image host as
            # HTTP. Upgrade only this exact host, preserving its path and query
            # verbatim; never return an HTTP candidate downstream.
            if (host != "storage57000.contents.fc2.com" or parsed.port is not None
                    or parsed.netloc.lower() != host or "#" in url):
                return None
            return "https://" + url[7:]
        if parsed.scheme != "https" or parsed.port not in (None, 443):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            return url if host.endswith(".contents.fc2.com") else None
    except ValueError:
        return None


class FC2OfficialProvider:
    """Fetch one exact official FC2 product without inventing absent metadata."""
    source = "fc2"

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    @staticmethod
    def _valid_identity(identity):
        return isinstance(identity, str) and PRODUCT_ID.fullmatch(identity) is not None

    def _parse(self, response, identity):
        expected_url = f"{BASE}/article/{identity}/"
        if response is None or response.status_code != 200:
            return None
        if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "text/html":
            return None
        if str(getattr(response, "url", "") or "") != expected_url:
            return None
        body = response.content
        if not isinstance(body, bytes) or not body or len(body) > MAX_HTML_BYTES:
            return None
        try:
            text = body.decode("utf-8")
            document = etree.fromstring(text.encode("utf-8"), etree.HTMLParser(
                no_network=True, recover=True, encoding="utf-8"))
            og_urls = document.xpath('//meta[@property="og:url"]/@content')
            if og_urls:
                if len(og_urls) != 1:
                    return None
                og_url = urlsplit(og_urls[0])
                if (og_url.scheme != "https" or og_url.hostname != "adult.contents.fc2.com"
                        or og_url.path.rstrip("/") != f"/article/{identity}"
                        or og_url.query or og_url.fragment):
                    return None
            scripts = document.xpath('//script[@type="application/ld+json"]')
            parsed = [json.loads(script.text, object_pairs_hook=_unique_object,
                                  parse_constant=_reject_constant)
                      for script in scripts if isinstance(script.text, str) and script.text.strip()]
        except (ValueError, TypeError, RecursionError, etree.LxmlError):
            return None
        products = [product for document_value in parsed for product in _products(document_value)]
        matches = []
        for product in products:
            ids = []
            for key in ("sku", "productID"):
                value = product.get(key)
                if value is not None:
                    if not isinstance(value, (str, int)) or isinstance(value, bool):
                        return None
                    value = str(value)
                    if not PRODUCT_ID.fullmatch(value):
                        return None
                    ids.append(value)
            if len(set(ids)) > 1:
                return None
            if not ids or any(value != identity for value in ids):
                continue
            matches.append(product)
        if len(matches) != 1:
            return None
        product = matches[0]
        title = product.get("name")
        if not isinstance(title, str) or len(title) > 4096:
            return None
        title = clean_text(title).strip()
        if not title:
            return None
        description = product.get("description")
        description = clean_text(description).strip() if isinstance(description, str) else ""
        number = f"FC2-PPV-{identity}"
        record = {"_source": self.source, "_id": identity, "number": number,
                  "title": title, "share_date": "",
                  "cover_image": _image_url(product.get("image")),
                  "discription": [{"language": "ja", "overview": description}] if description else []}
        return {"number": number, "actors": [], "record": record}

    def fetch(self, identity):
        if not self._valid_identity(identity):
            return None
        url = f"{BASE}/article/{identity}/"
        client = RequestUtils(timeout=self.timeout, verify=True, proxies=settings.PROXY,
                              accept_type="text/html", cookies=None)
        response = None
        try:
            response = client.get_res(url, allow_redirects=False)
            return self._parse(response, identity)
        finally:
            if response is not None:
                response.close()
            client.close()

    async def async_fetch(self, identity):
        if not self._valid_identity(identity):
            return None
        url = f"{BASE}/article/{identity}/"
        client = AsyncRequestUtils(timeout=self.timeout, verify=True, proxies=settings.PROXY,
                                   accept_type="text/html", cookies=None)
        response = None
        try:
            response = await client.get_res(url, allow_redirects=False)
            return self._parse(response, identity)
        finally:
            if response is not None:
                await response.aclose()

    def search(self, number):
        normalized = ("FC2-PPV-" + number if isinstance(number, str)
                      and PRODUCT_ID.fullmatch(number) else
                      normalize_number(number) if isinstance(number, str) else None)
        if not normalized or not normalized.startswith("FC2-PPV-"):
            return None
        return self.fetch(normalized.removeprefix("FC2-PPV-"))

    async def async_search(self, number):
        normalized = ("FC2-PPV-" + number if isinstance(number, str)
                      and PRODUCT_ID.fullmatch(number) else
                      normalize_number(number) if isinstance(number, str) else None)
        if not normalized or not normalized.startswith("FC2-PPV-"):
            return None
        return await self.async_fetch(normalized.removeprefix("FC2-PPV-"))
