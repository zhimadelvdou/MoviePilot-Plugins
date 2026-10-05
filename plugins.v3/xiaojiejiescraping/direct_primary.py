"""Primary-site adapters, implemented from public schema facts; offline tested only.

No MetaTube code, API, browser, credentials, age-confirmation or challenge bypass.
Only explicit product-code evidence is accepted: a FANZA SKU is NOT a catalog
number, and its prefixes/zeroes are never guessed away. The FANZA adapter covers
modern /av/content/ Product JSON-LD only. Other catalog variants fail closed.

Schema references (read 2026-10-05; implementations below are original):
- FANZA: https://github.com/javinizer/javinizer-go/blob/main/internal/scraper/dmm/jsonld.go
  and scraper.go (MIT; field names and endpoint contracts only).
- MGStage: https://github.com/yyymess/avdc/blob/master/WebCrawler/mgstage.py
  (GPLv3; table labels / product URL facts only).
- DUGA: https://github.com/stashapp/CommunityScrapers/blob/master/scrapers/duga.jp.yml
  (AGPLv3; OpenGraph, schema.org mpn and endpoint facts only; no YAML copied).
- SOD: https://github.com/hibikidesu/javscraper/blob/master/javscraper/sod.py
  and https://github.com/libredmm/librefanza/blob/master/app/models/sod_item.rb
  (page element facts only; no source or session-handshake implementation copied).

Searches are bounded and reject visible pagination. Metadata fetches accept
same-provider fixed HTTPS endpoints only, never page-controlled destinations.
Age/session/region gates, redirects and missing identity evidence return None.
"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from app.sdk.config import settings
from .transport import AnonymousAsyncRequestUtils as AsyncRequestUtils, AnonymousRequestUtils as RequestUtils
from lxml import etree

from .core import IDENTIFIER, clean_text, normalize_number, record_numbers

MAX_HTML_BYTES = 2_000_000
MAX_CANDIDATES = 24


def _text(value) -> str:
    if isinstance(value, etree._Element):
        value = " ".join(value.itertext())
    return " ".join(clean_text(value).split()) if isinstance(value, str) else ""


def _first(document, xpath: str) -> str:
    values = document.xpath(xpath)
    return _text(values[0]) if values else ""


def _unique(document, xpath: str) -> str:
    values = {_text(value) for value in document.xpath(xpath)}
    return next(iter(values)) if len(values) == 1 else ""


def _cells(document, labels: tuple[str, ...], root: str = "//tr") -> list:
    """Read exact row labels, tolerating full-width punctuation and whitespace."""
    found = []
    for row in document.xpath(root):
        children = row.xpath("./th|./td")
        if len(children) >= 2 and _text(children[0]).rstrip(":： ") in labels:
            found.extend(children[1:])
    return found


def _value(document, labels: tuple[str, ...], root: str = "//tr") -> str:
    values = list(dict.fromkeys(_text(c) for c in _cells(document, labels, root) if _text(c)))
    return values[0] if len(values) == 1 else ""


def _actors(cells: list) -> list[dict]:
    names = []
    for cell in cells:
        links = cell.xpath(".//a")
        names.extend(_text(link) for link in links)
        if not links:
            names.extend(_text(v) for v in cell.xpath(".//text()"))
    return [{"name": name} for name in dict.fromkeys(names) if name and name not in ("---", "----", "-", "未公開")]


def _date(value: str) -> str:
    matched = re.fullmatch(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})日?", value)
    if not matched:
        return ""
    try:
        return date(*(int(part) for part in matched.groups())).isoformat()
    except ValueError:
        return ""


def _packet(source: str, identity: str, number: str, title: str, *, actors=(), overview="", released="", cover="") -> dict | None:
    number = normalize_number(number)
    if not number or not title or not IDENTIFIER.fullmatch(identity):
        return None
    record = {
        "_id": identity, "_source": source, "number": number, "title": title,
        "discription": [{"language": "ja", "overview": overview}] if overview else [],
        "share_date": _date(released), "cover_image": cover,
    }
    if record_numbers(record) != {number}:
        return None
    return {"record": record, "number": number, "actors": list(actors)}


def _document(response):
    """Never parse redirects, non-HTML payloads, oversized documents or entities."""
    if response is None or response.status_code != 200:
        return None
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type and content_type not in ("text/html", "application/xhtml+xml"):
        return None
    body = response.content
    if not isinstance(body, bytes) or not body or len(body) > MAX_HTML_BYTES:
        return None
    try:
        return etree.HTML(body, parser=etree.HTMLParser(no_network=True, recover=False))
    except (ValueError, etree.LxmlError):
        return None


def _safe_url(value: str, base: str, host: str):
    """The parser may inspect links; it never requests an arbitrary parsed URL."""
    if not isinstance(value, str) or any(c in value for c in "\\\r\n\t"):
        return None
    try:
        parsed = urlsplit(urljoin(base, value))
        if (parsed.scheme != "https" or parsed.hostname != host or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            return None
        return parsed
    except ValueError:
        return None


def _paginated(document) -> bool:
    for link in document.xpath("//a[@href]|//link[@href]"):
        if "next" in str(link.get("rel", "")).lower().split():
            return True
        text = _text(link)
        if text in ("次", "次へ", "次のページ", "Next", "next", "›", "»"):
            return True
        href = link.get("href", "")
        if re.search(r"(?:[?&]|/)(?:page|p)=([2-9]|[1-9]\d+)(?:[&#/]|$)", href):
            return True
    return False


class _DirectProvider:
    source = ""
    host = ""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def _get(self, url: str, params: dict | None = None):
        client = RequestUtils(timeout=self.timeout, verify=True, proxies=settings.PROXY, accept_type="text/html,application/xhtml+xml")
        response = None
        try:
            response = client.get_res(url, params=params, allow_redirects=False)
            return _document(response)
        finally:
            if response is not None:
                response.close()
            client.close()

    async def _aget(self, url: str, params: dict | None = None):
        response = await AsyncRequestUtils(timeout=self.timeout, verify=True, proxies=settings.PROXY, accept_type="text/html,application/xhtml+xml").get_res(
            url, params=params, allow_redirects=False,
        )
        try:
            return _document(response)
        finally:
            if response is not None:
                await response.aclose()

    def _url(self, identity: str) -> str | None:
        raise NotImplementedError

    def _parse(self, document, identity: str) -> dict | None:
        raise NotImplementedError

    def fetch(self, identity: str) -> dict | None:
        url = self._url(identity)
        if not url:
            return None
        document = self._get(url)
        return self._parse(document, identity) if document is not None else None

    async def async_fetch(self, identity: str) -> dict | None:
        url = self._url(identity)
        if not url:
            return None
        document = await self._aget(url)
        return self._parse(document, identity) if document is not None else None

    def search(self, number: str) -> dict | None:
        number = normalize_number(number)
        result = self.fetch(number) if number else None
        return result if result and result["number"] == number else None

    async def async_search(self, number: str) -> dict | None:
        number = normalize_number(number)
        result = await self.async_fetch(number) if number else None
        return result if result and result["number"] == number else None


class MGStageProvider(_DirectProvider):
    """Exact detail URL; no numeric-prefix guessing and no implicit age cookie."""
    source = "mgstage"
    host = "www.mgstage.com"

    def _url(self, identity: str) -> str | None:
        return f"https://{self.host}/product/product_detail/{identity}/" if isinstance(identity, str) and normalize_number(identity) == identity else None

    def _parse(self, document, identity: str) -> dict | None:
        code = _value(document, ("品番",))
        if normalize_number(code) != identity:
            return None
        return _packet(self.source, identity, code,
                       _unique(document, "//*[@id='center_column']//h1"),
                       actors=_actors(_cells(document, ("出演",))),
                       released=_value(document, ("配信開始日",)),
                       overview=_first(document, "//*[@id='introduction']"),
                       cover=_first(document, "//a[@id='EnlargeImage']/@href"))


class SODProvider(_DirectProvider):
    """Direct detail contract only; gated _ontime.php session flow is not used."""
    source = "sod"
    host = "ec.sod.co.jp"

    def _url(self, identity: str) -> str | None:
        return f"https://{self.host}/prime/videos/?id={identity}" if isinstance(identity, str) and normalize_number(identity) == identity else None

    def _parse(self, document, identity: str) -> dict | None:
        root = "//table[@id='v_introduction']//tr"
        code = _value(document, ("品番",), root)
        if normalize_number(code) != identity:
            return None
        return _packet(self.source, identity, code,
                       _unique(document, "//*[@id='videos_head']/h1[not(contains(@style, 'none'))]"),
                       actors=_actors(_cells(document, ("出演者",), root)),
                       released=_value(document, ("発売年月日",), root),
                       overview=_first(document, "//div[contains(concat(' ', normalize-space(@class), ' '), ' videos_textli ')]/article"),
                       cover=_first(document, "//div[contains(concat(' ', normalize-space(@class), ' '), ' videos_samimg ')]/a/@href"))


class _SearchProvider(_DirectProvider):
    def _search_request(self, number: str) -> tuple[str, dict | None]:
        raise NotImplementedError

    def _candidates(self, document) -> list[str] | None:
        raise NotImplementedError

    @staticmethod
    def _choose(results: list[dict], number: str) -> dict | None:
        selected = {p["record"]["_id"]: p for p in results if p["number"] == number}
        return next(iter(selected.values())) if len(selected) == 1 else None

    def search(self, number: str) -> dict | None:
        number = normalize_number(number)
        if not number:
            return None
        document = self._get(*self._search_request(number))
        if document is None or _paginated(document):
            return None
        candidates = self._candidates(document)
        if not candidates or len(candidates) > MAX_CANDIDATES:
            return None
        results = []
        for identity in candidates:
            result = self.fetch(identity)
            if result is None:
                return None  # An unverified candidate cannot establish uniqueness.
            results.append(result)
        return self._choose(results, number)

    async def async_search(self, number: str) -> dict | None:
        number = normalize_number(number)
        if not number:
            return None
        document = await self._aget(*self._search_request(number))
        if document is None or _paginated(document):
            return None
        candidates = self._candidates(document)
        if not candidates or len(candidates) > MAX_CANDIDATES:
            return None
        results = []
        for identity in candidates:
            result = await self.async_fetch(identity)
            if result is None:
                return None
            results.append(result)
        return self._choose(results, number)


class DUGAProvider(_SearchProvider):
    source = "duga"
    host = "duga.jp"

    def _url(self, identity: str) -> str | None:
        return f"https://{self.host}/ppv/{identity}/" if isinstance(identity, str) and re.fullmatch(r"[a-z0-9]+-\d{1,12}", identity) else None

    def _identity(self, value: str) -> str | None:
        parsed = _safe_url(value, f"https://{self.host}/", self.host)
        match = re.fullmatch(r"/ppv/([a-z0-9]+-\d{1,12})/?", parsed.path) if parsed and not parsed.query else None
        return match[1] if match else None

    def _search_request(self, number: str) -> tuple[str, dict]:
        return f"https://{self.host}/search/", {"search": "1", "q": number}

    def _candidates(self, document) -> list[str] | None:
        links = document.xpath("//*[@id='searchresultarea']//a[@href]/@href")
        identities = []
        for link in links:
            if "/ppv/" not in link:
                continue
            identity = self._identity(link)
            if not identity:
                return None
            identities.append(identity)
        return list(dict.fromkeys(identities))

    def _parse(self, document, identity: str) -> dict | None:
        urls = document.xpath("//meta[@property='og:url']/@content")
        if not urls or {self._identity(value) for value in urls} != {identity}:
            return None
        codes = {_text(value) for value in document.xpath("//*[@itemprop='mpn']")}
        if len(codes) != 1:
            return None
        return _packet(self.source, identity, next(iter(codes)),
                       _unique(document, "//meta[@property='og:title']/@content"),
                       actors=_actors(document.xpath("//table[contains(concat(' ', normalize-space(@class), ' '), ' infomation ')]//ul[contains(concat(' ', normalize-space(@class), ' '), ' performer ')]")),
                       released=_first(document, "//table[contains(concat(' ', normalize-space(@class), ' '), ' infomation ')]//tr[1]/td"),
                       overview=_first(document, "//meta[@property='og:description']/@content"),
                       cover=_first(document, "//div[contains(concat(' ', normalize-space(@class), ' '), ' imagebox ')]/a[@itemprop='image']/@href"))


class FANZAProvider(_SearchProvider):
    """Modern av/Product schema only; SKU validates native ID, never guessed code."""
    source = "fanza"
    host = "video.dmm.co.jp"

    def _url(self, identity: str) -> str | None:
        return f"https://{self.host}/av/content/?id={identity}" if isinstance(identity, str) and re.fullmatch(r"[a-z0-9_]{1,80}", identity) else None

    def _identity(self, value: str) -> str | None:
        parsed = _safe_url(value, f"https://{self.host}/", self.host)
        if not parsed or parsed.path != "/av/content/":
            return None
        params = parse_qs(parsed.query)
        values = params.get("id", [])
        return values[0] if len(values) == 1 and self._url(values[0]) else None

    def _search_request(self, number: str) -> tuple[str, None]:
        return "https://www.dmm.co.jp/search/=/searchstr=" + quote(number, safe="") + "/", None

    def _candidates(self, document) -> list[str] | None:
        identities = []
        for link in document.xpath("//a[@href]/@href"):
            # Unsupported legacy / amateur variants cannot establish uniqueness.
            if ("/detail/=/cid=" in link or "/amateur/content/" in link):
                return None
            if "/av/content/" not in link:
                continue
            identity = self._identity(link)
            if not identity:
                return None
            identities.append(identity)
        return list(dict.fromkeys(identities))

    def _parse(self, document, identity: str) -> dict | None:
        products = []
        for text in document.xpath("//script[@type='application/ld+json']/text()"):
            try:
                value = json.loads(text)
            except (ValueError, TypeError, RecursionError):
                return None
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, dict) and item.get("@type") == "Product":
                    products.append(item)
        if len(products) != 1:
            return None
        product = products[0]
        if product.get("sku") != identity:
            return None
        title = _text(product.get("name"))
        code_cells = _cells(document, ("品番",))
        explicit_codes = {_text(cell) for cell in code_cells}
        if len(explicit_codes) > 1:
            return None
        explicit_code = next(iter(explicit_codes)) if explicit_codes else ""
        subject = product.get("subjectOf")
        subject_title = _text(subject.get("name")) if isinstance(subject, dict) else ""
        # A title can provide the code only when no different explicit code exists.
        numbers = record_numbers({"title": title, "name": subject_title, "number": explicit_code})
        if len(numbers) != 1 or (explicit_codes and normalize_number(explicit_code) not in numbers):
            return None
        images = product.get("image")
        cover = images[0] if isinstance(images, list) and images else images
        return _packet(self.source, identity, next(iter(numbers)), title,
                       actors=_actors(_cells(document, ("出演者",))),
                       released=_text(subject.get("uploadDate")) if isinstance(subject, dict) else "",
                       overview=_text(product.get("description")), cover=_text(cover))
