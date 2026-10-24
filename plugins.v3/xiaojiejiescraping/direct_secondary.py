"""独立直连来源适配；仅以公开软件合同和合成数据离线验证。

选择器/路径合同参考（未复制实现，也不依赖 MetaTube）：
* Yuukiy/JavSP, javsp/web/{javbus,jav321}.py, GPL-3.0
  https://github.com/Yuukiy/JavSP/tree/master/javsp/web
* Hazard804/mdcx, mdcx/crawlers/fc2hub.py 和 tests/crawlers/test_fc2hub.py,
  GPL-3.0: https://github.com/Hazard804/mdcx/blob/master/mdcx/crawlers/fc2hub.py
* ShotHeadman/mdcz, packages/runtime/src/crawler/sites/fc2hub.ts 和对应测试,
  GPL-3.0: https://github.com/ShotHeadman/mdcz/blob/main/packages/runtime/src/crawler/sites/fc2hub.ts

FC2Hub 的 /id{番号}/ 和 /video/{站内ID}/id{番号}/{slug} 共用真实 permalink
段 id{番号}。回查重新解析搜索页，不能猜测 slug 或将站内 video ID 当成番号。
FC2PPVDB 后续已依据更新的匿名初始页面合同独立实现，见 fc2ppvdb.py；
此模块不复用旧登录型爬虫或提供旧版占位适配器。
"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from datetime import date
import json
import re
from urllib.parse import urljoin, urlsplit

from lxml import etree
import httpx
import requests

from app.sdk.config import settings
from .transport import AnonymousAsyncRequestUtils as AsyncRequestUtils, AnonymousRequestUtils as RequestUtils

from .core import IDENTIFIER, clean_text, normalize_number, record_numbers
from .diagnostics import diagnose


MAX_HTML = 2_000_000
FC2PPVDB_MIGRATION_SOURCE = (
    "https://github.com/adadsws/fc2cmadb-crawler/blob/"
    "d2984abd8f24eeafc8fa3cbdfac32c070eb80389/CHANGELOG.md"
)


def _document(text: str):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_HTML:
        return None
    try:
        return etree.fromstring(text.encode("utf-8"), etree.HTMLParser(no_network=True, encoding="utf-8"))
    except (ValueError, etree.LxmlError):
        return None


def _texts(nodes) -> list[str]:
    return [value for node in nodes if (value := clean_text(
        "".join(node.itertext()) if hasattr(node, "itertext") else node
    ).strip())]


def _single(nodes) -> str:
    values = set(_texts(nodes))
    return next(iter(values)) if len(values) == 1 else ""


def _field(nodes) -> str:
    """字段后面的独立文本；不从整页或推荐标题猜测番号。"""
    return _single([node.tail.strip(" \n\r\t:：") for node in nodes if node.tail])


def _https_asset(value: str, base: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        url = urljoin(base, value.strip())
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname and not parsed.username
                and not parsed.password and parsed.port in (None, 443) and not parsed.fragment):
            return url
    except ValueError:
        pass
    return None


def _packet(source: str, identity: str, number: str, title: str, *,
            actors=(), overview: str = "", released: str = "", cover: str = "", base: str = "") -> dict | None:
    if not IDENTIFIER.fullmatch(identity) or not title or len(title) > 4096:
        return None
    release = str(released or "")[:10].replace("/", "-")
    try:
        date.fromisoformat(release)
    except ValueError:
        release = ""
    record = {
        "_source": source, "_id": identity, "number": number,
        "title": clean_text(title), "share_date": release,
        "cover_image": _https_asset(cover, base),
        "discription": [{"language": "ja", "overview": clean_text(overview)[:50_000]}] if overview else [],
    }
    if record_numbers(record) != {number}:
        return None
    names = list(dict.fromkeys(_texts(actors)))[:64]
    return {"record": record, "number": number,
            "actors": [{"name": name[:512]} for name in names]}


class _HtmlProvider:
    """HTTP 仅使用宿主代理；关闭自动跳转，逐跳校验同源详情地址。"""
    source = ""
    base = ""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def _detail_path(self, path: str) -> bool:
        raise NotImplementedError

    def _safe_detail_url(self, value: str) -> str | None:
        try:
            url = urljoin(self.base + "/", value)
            parsed = urlsplit(url)
            origin = urlsplit(self.base)
            if (parsed.scheme != "https" or parsed.netloc != origin.netloc
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or "\\" in url or not self._detail_path(parsed.path)):
                return None
            return url
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _response(response):
        if response is None:
            return 0, "", ""
        status = response.status_code
        location = response.headers.get("Location", "")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        text = ""
        if status == 200 and content_type in ("", "text/html", "application/xhtml+xml"):
            # requests 默认的 text/html Latin-1 解码会破坏日文标签；沿用 SDK get()
            # 的 UTF-8 契约，基于字节限长，而不是使用猜测后的 response.text。
            content = response.content
            if isinstance(content, bytes) and len(content) <= MAX_HTML:
                try:
                    text = content.decode("utf-8")
                except UnicodeError:
                    pass
        return status, location, text

    def _load(self, path: str, *, data: dict | None = None, params: dict | None = None):
        url, method = self.base + path, "post" if data is not None else "get"
        for _ in range(4):
            client = RequestUtils(timeout=self.timeout, verify=True, proxies=settings.PROXY,
                                  accept_type="text/html")
            response = None
            try:
                response = (client.post_res(url, data=data, allow_redirects=False) if method == "post"
                            else client.get_res(url, params=params, allow_redirects=False))
                status, location, text = self._response(response)
            finally:
                if response is not None:
                    response.close()
                client.close()
            if status == 200:
                return _document(text), url
            target = self._safe_detail_url(location) if status in (301, 302, 303, 307, 308) else None
            if not target:
                return None, url
            url, params = target, None
            if status in (301, 302, 303):
                method, data = "get", None
        return None, url

    async def _aload(self, path: str, *, data: dict | None = None, params: dict | None = None):
        url, method = self.base + path, "post" if data is not None else "get"
        for _ in range(4):
            client = AsyncRequestUtils(timeout=self.timeout, verify=True, proxies=settings.PROXY,
                                       accept_type="text/html")
            response = None
            try:
                response = (await client.post_res(url, data=data, allow_redirects=False) if method == "post"
                            else await client.get_res(url, params=params, allow_redirects=False))
                status, location, text = self._response(response)
            finally:
                if response is not None:
                    await response.aclose()
            if status == 200:
                return _document(text), url
            target = self._safe_detail_url(location) if status in (301, 302, 303, 307, 308) else None
            if not target:
                return None, url
            url, params = target, None
            if status in (301, 302, 303):
                method, data = "get", None
        return None, url


class JavBusProvider(_HtmlProvider):
    source = "javbus"
    base = "https://www.javbus.com"

    def _location_kind(self, location: str) -> str:
        try:
            target = urlsplit(urljoin(self.base + "/", location))
            origin = urlsplit(self.base)
            if (target.scheme != "https" or target.netloc != origin.netloc
                    or target.username or target.password):
                return ""
            path = target.path.lower()
        except (TypeError, ValueError):
            return ""
        if any(token in path for token in ("driver-verify", "captcha", "verify", "verification")):
            return "access_verification_required"
        if path.rstrip("/") in ("/login", "/signin", "/sign-in"):
            return "login_required"
        return ""

    @staticmethod
    def _body_kind(text: str) -> str:
        html = _document(text)
        if html is None:
            return ""
        # A details page may contain modal login controls or challenge scripts;
        # only classify a page when the normal JavBus identity/title structure is absent.
        has_details = bool(html.xpath(
            "//div[contains(concat(' ',normalize-space(@class),' '),' info ')]"
            "/p/span[normalize-space(.)='識別碼:']/following-sibling::span[1]"
        ) and html.xpath("//div[contains(concat(' ',normalize-space(@class),' '),' container ')]/h3"))
        if has_details:
            return ""
        titles = [value.strip().lower() for value in html.xpath("//title/text() | //h1/text()")]
        if any(value in ("login", "sign in", "ログイン", "登入") for value in titles):
            return "login_required"
        if html.xpath("//form[@id='login' or @name='login' or contains(@action, '/login')]"
                      " | //form[.//input[@type='password']]"):
            return "login_required"
        if html.xpath("//*[contains(concat(' ',normalize-space(@id),' '),' driver-verify ')"
                      " or contains(concat(' ',normalize-space(@class),' '),' driver-verify ')"
                      " or contains(concat(' ',normalize-space(@id),' '),' captcha ')"
                      " or contains(concat(' ',normalize-space(@class),' '),' captcha ')]"):
            return "access_verification_required"
        if any(value in ("verification", "security verification", "captcha", "人机验证", "验证码")
               for value in titles):
            return "access_verification_required"
        return ""

    def _response(self, response):
        status, location, text = super()._response(response)
        reason = ""
        if response is None:
            reason = "network_unavailable"
        elif status == 429:
            reason = "rate_limited"
        elif status == 404:
            reason = "not_found"
        elif status in (401,):
            reason = "login_required"
        elif status == 403:
            reason = "access_restricted"
        elif status >= 500:
            reason = "upstream_unavailable"
        elif status in (301, 302, 303, 307, 308):
            reason = self._location_kind(location)
            if not reason and not self._safe_detail_url(location):
                reason = "access_restricted"
        elif status == 200:
            reason = self._body_kind(text)
        elif status:
            reason = "access_restricted"
        if reason:
            diagnose(self.source, "search", reason, status=status or None)
        return status, location, text

    def _detail_path(self, path: str) -> bool:
        return bool(re.fullmatch(r"/[A-Za-z0-9_-]{1,128}/?", path)
                    and normalize_number(path.strip("/")))

    def _parse(self, html, expected: str) -> dict | None:
        if html is None:
            return None
        codes = html.xpath("//div[contains(concat(' ',normalize-space(@class),' '),' info ')]"
                           "/p/span[normalize-space(.)='識別碼:']/following-sibling::span[1]")
        number = normalize_number(_single(codes))
        if number != expected:
            return None
        title = _single(html.xpath("//div[contains(concat(' ',normalize-space(@class),' '),' container ')]/h3"))
        released = _field(html.xpath("//div[contains(concat(' ',normalize-space(@class),' '),' info ')]"
                                    "/p/span[normalize-space(.)='發行日期:']"))
        # Keep both JavBus cast layouts in document order. These names never
        # become trusted actress evidence.
        actors = html.xpath(
            "//a[contains(concat(' ',normalize-space(@class),' '),' avatar-box ')]/div/img/@title | "
            "//div[contains(concat(' ',normalize-space(@class),' '),' star-name ')]//a[@title]/@title"
        )
        cover = _single(html.xpath("//a[contains(concat(' ',normalize-space(@class),' '),' bigImage ')]/img/@src"))
        return _packet(self.source, number, number, title, actors=actors, released=released, cover=cover, base=self.base)

    def search(self, number: str) -> dict | None:
        number = normalize_number(number)
        if not number:
            return None
        try:
            html = self._load("/" + number)[0]
        except requests.exceptions.RequestException:
            diagnose(self.source, "search", "network_unavailable")
            return None
        result = self._parse(html, number)
        if not result:
            diagnose(self.source, "search", "response_invalid")
        return result

    async def async_search(self, number: str) -> dict | None:
        number = normalize_number(number)
        if not number:
            return None
        try:
            html = (await self._aload("/" + number))[0]
        except httpx.RequestError:
            diagnose(self.source, "search", "network_unavailable")
            return None
        result = self._parse(html, number)
        if not result:
            diagnose(self.source, "search", "response_invalid")
        return result

    def fetch(self, identity: str) -> dict | None:
        return self.search(identity) if normalize_number(identity) == identity else None

    async def async_fetch(self, identity: str) -> dict | None:
        return await self.async_search(identity) if normalize_number(identity) == identity else None


class Jav321Provider(_HtmlProvider):
    source = "jav321"
    base = "https://www.jav321.com"

    def _detail_path(self, path: str) -> bool:
        return bool(re.fullmatch(r"/video/[A-Za-z0-9][A-Za-z0-9_-]{0,127}/?", path))

    def _parse(self, html, *, expected_number: str | None = None, expected_id: str | None = None) -> dict | None:
        if html is None:
            return None
        links = html.xpath("//link[@rel='canonical']/@href | "
                           "//ul[contains(concat(' ',normalize-space(@class),' '),' dropdown-menu ')]/li/a/@href")
        identities = {urlsplit(url).path.strip("/").split("/")[-1]
                      for link in links if (url := self._safe_detail_url(link))}
        if len(identities) != 1:
            return None
        identity = next(iter(identities))
        if expected_id is not None and identity != expected_id:
            return None
        info_path = "//div[contains(concat(' ',normalize-space(@class),' '),' col-md-9 ')]"
        number = normalize_number(_field(html.xpath(info_path + "/b[normalize-space(.)='品番']")))
        if not number or (expected_number is not None and expected_number != number):
            return None
        title = _single(html.xpath("//div[contains(concat(' ',normalize-space(@class),' '),' panel-heading ')]/h3"))
        released = _field(html.xpath(info_path + "/b[normalize-space(.)='配信開始日']"))
        actors = html.xpath("//div[contains(concat(' ',normalize-space(@class),' '),' thumbnail ')]"
                            "/a[contains(@href,'/star/')]/text()")
        covers = _texts(html.xpath("//div[@class='col-xs-12 col-md-12']/p/a/img[@class='img-responsive']/@src"))
        if not covers:
            covers = _texts(html.xpath("//div[@class='col-md-3']/img[@onerror and @class='img-responsive']/@src"))
        overview = _single(html.xpath("//div[@class='panel-body']/div[@class='row']/div[@class='col-md-12']/text()"))
        return _packet(self.source, identity, number, title, actors=actors, released=released,
                       overview=overview, cover=covers[0] if covers else "", base=self.base)

    def search(self, number: str) -> dict | None:
        number = normalize_number(number)
        return self._parse(self._load("/search", data={"sn": number})[0], expected_number=number) if number else None

    async def async_search(self, number: str) -> dict | None:
        number = normalize_number(number)
        return self._parse((await self._aload("/search", data={"sn": number}))[0], expected_number=number) if number else None

    def fetch(self, identity: str) -> dict | None:
        return self._parse(self._load("/video/" + identity)[0], expected_id=identity) if isinstance(identity, str) and IDENTIFIER.fullmatch(identity) else None

    async def async_fetch(self, identity: str) -> dict | None:
        return self._parse((await self._aload("/video/" + identity))[0], expected_id=identity) if isinstance(identity, str) and IDENTIFIER.fullmatch(identity) else None


class FC2HubProvider(_HtmlProvider):
    source = "fc2hub"
    base = "https://javten.com"
    identity_pattern = re.compile(r"id([0-9]{5,9})\Z")
    legacy_path = re.compile(r"/id([0-9]{5,9})/?\Z")
    current_path = re.compile(r"/video/[0-9]{1,12}/id([0-9]{5,9})(?:/[^/?#\\]{1,512})?/?\Z")

    @classmethod
    def _path_number(cls, path: str) -> str | None:
        match = cls.legacy_path.fullmatch(path) or cls.current_path.fullmatch(path)
        return "FC2-PPV-" + match.group(1) if match else None

    def _detail_path(self, path: str) -> bool:
        return self._path_number(path) is not None

    def _candidate(self, html, number: str) -> str | None:
        if html is None:
            return None
        links = html.xpath("//link/@href | //meta[@property='og:url']/@content | //a/@href")
        matches = {url.rstrip("/") for link in links if (url := self._safe_detail_url(link))
                   and self._path_number(urlsplit(url).path) == number}
        return next(iter(matches)) if len(matches) == 1 else None

    @staticmethod
    def _movies(html) -> list[dict]:
        movies = []
        for text in html.xpath("//script[@type='application/ld+json']/text()")[:16]:
            try:
                raw = json.loads(text)
            except (ValueError, TypeError, RecursionError):
                continue
            entries = raw if isinstance(raw, list) else [raw]
            for entry in entries[:64]:
                if not isinstance(entry, dict):
                    continue
                graph = entry.get("@graph")
                for item in (graph[:64] if isinstance(graph, list) else [entry]):
                    if isinstance(item, dict) and (item.get("@type") == "Movie"
                            or isinstance(item.get("@type"), list) and "Movie" in item["@type"]):
                        movies.append(item)
        return movies

    def _parse(self, html, number: str) -> dict | None:
        if html is None:
            return None
        codes = {normalized for value in _texts(html.xpath("//h1")) if (normalized := normalize_number(value))}
        movies = self._movies(html)
        if len(movies) > 1:
            return None
        movie = movies[0] if movies else None
        if movie:
            raw_ids = movie.get("identifier", [])
            raw_ids = raw_ids if isinstance(raw_ids, list) else [raw_ids]
            for raw in raw_ids:
                if isinstance(raw, str):
                    normalized = normalize_number("FC2-PPV-" + raw if re.fullmatch(r"[0-9]{5,9}", raw) else raw)
                    if normalized:
                        codes.add(normalized)
        if codes != {number}:
            return None
        titles = _texts(html.xpath("//h1[contains(concat(' ',normalize-space(@class),' '),' fc2-title ')]"))
        if not titles:
            titles = [value for value in _texts(html.xpath("//h1")) if not normalize_number(value)]
        title = _single(titles) or (movie.get("name", "") if movie else "")
        if not isinstance(title, str):
            return None
        movie = movie or {}
        actors = movie.get("actor", [])
        actors = [actor.get("name", "") if isinstance(actor, dict) else actor
                  for actor in actors[:64]] if isinstance(actors, list) else []
        actors = [actor for actor in actors if isinstance(actor, str)]
        covers = movie.get("image", "")
        cover = covers[0] if isinstance(covers, list) and covers else covers
        if not isinstance(cover, str) or not cover:
            images = _texts(html.xpath("//a[@data-fancybox='gallery']/@href | //meta[@property='og:image']/@content"))
            cover = images[0] if images else ""
        overview = _single(html.xpath("//div[contains(concat(' ',normalize-space(@class),' '),' col ')"
                                     "and contains(concat(' ',normalize-space(@class),' '),' des ')]"))
        if not overview and isinstance(movie.get("description"), str):
            overview = movie["description"]
        return _packet(self.source, "id" + number.removeprefix("FC2-PPV-"), number, title,
                       actors=actors, overview=overview, released=movie.get("datePublished", ""),
                       cover=cover, base=self.base)

    def search(self, number: str) -> dict | None:
        number = normalize_number(number)
        if not number or not number.startswith("FC2-PPV-"):
            return None
        html, _ = self._load("/search", params={"kw": number.removeprefix("FC2-PPV-")})
        candidate = self._candidate(html, number)
        if not candidate:
            return None
        result = self._parse(html, number)
        if result:
            return result
        return self._parse(self._load(urlsplit(candidate).path)[0], number)

    async def async_search(self, number: str) -> dict | None:
        number = normalize_number(number)
        if not number or not number.startswith("FC2-PPV-"):
            return None
        html, _ = await self._aload("/search", params={"kw": number.removeprefix("FC2-PPV-")})
        candidate = self._candidate(html, number)
        if not candidate:
            return None
        result = self._parse(html, number)
        if result:
            return result
        return self._parse((await self._aload(urlsplit(candidate).path))[0], number)

    def fetch(self, identity: str) -> dict | None:
        match = self.identity_pattern.fullmatch(str(identity))
        return self.search("FC2-PPV-" + match.group(1)) if match else None

    async def async_fetch(self, identity: str) -> dict | None:
        match = self.identity_pattern.fullmatch(str(identity))
        return await self.async_search("FC2-PPV-" + match.group(1)) if match else None
