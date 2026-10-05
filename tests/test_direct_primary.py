"""Synthetic HTML, replayed at the real SDK HTTP boundary; zero live requests."""
import asyncio
import json
from copy import deepcopy

import pytest

# The plugin namespace is registered dynamically by tests/conftest.py.
# pylint: disable-next=import-error,no-name-in-module
from app.plugins.xiaojiejiescraping import (
    direct_primary as direct,
)
from app.sdk.network import AsyncRequestUtils, RequestUtils

CLASSES = (direct.FANZAProvider, direct.MGStageProvider, direct.DUGAProvider, direct.SODProvider)
IDENTITIES = {"fanza": "fixture00123", "mgstage": "TEST-123", "duga": "fixture-123", "sod": "TEST-123"}


def html(body):
    return f'<!doctype html><html><head><meta charset="utf-8"></head><body>{body}</body></html>'


def product_html(source, number="TEST-123", title=None, identity=None):
    title = title if title is not None else f"{number} synthetic title"
    identity = identity or IDENTITIES[source]
    if source == "fanza":
        product = {"@type": "Product", "sku": identity, "name": title,
                   "description": "Synthetic description & text", "image": ["https://images.example.test/cover.jpg"],
                   "subjectOf": {"@type": "VideoObject", "uploadDate": "2026-01-02"}}
        return html(f'<script type="application/ld+json">{json.dumps(product)}</script>'
                    f'<table><tr><th>品番：</th><td>{number}</td></tr>'
                    '<tr><th>出演者：</th><td><a>Synthetic Actor</a></td></tr></table>')
    if source == "mgstage":
        return html(f'<div id="center_column"><h1>{title}</h1></div>'
                    f'<table><tr><th>品番：</th><td>{number}</td></tr>'
                    '<tr><th>出演：</th><td><a>Synthetic Actor</a></td></tr>'
                    '<tr><th>配信開始日：</th><td>2026/01/02</td></tr></table>'
                    '<div id="introduction">Synthetic description &amp; text</div>'
                    '<a id="EnlargeImage" href="https://images.example.test/cover.jpg"></a>')
    if source == "duga":
        return html(f'<meta property="og:url" content="https://duga.jp/ppv/{identity}/">'
                    f'<meta property="og:title" content="{title}"><span itemprop="mpn">{number}</span>'
                    '<meta property="og:description" content="Synthetic description &amp; text">'
                    '<table class="infomation"><tr><th>公開日</th><td>2026年01月02日</td></tr>'
                    '<tr><td><ul class="performer"><li><strong><a>Synthetic Actor</a></strong></li></ul></td></tr></table>'
                    '<div class="imagebox"><a itemprop="image" href="https://images.example.test/cover.jpg"></a></div>')
    return html(f'<div id="videos_head"><h1>{title}</h1></div>'
                f'<table id="v_introduction"><tr><td class="v_intr_ti">品番</td><td class="v_intr_tx">{number}</td></tr>'
                '<tr><td class="v_intr_ti">出演者</td><td class="v_intr_tx"><a>Synthetic Actor</a></td></tr>'
                '<tr><td class="v_intr_ti">発売年月日</td><td class="v_intr_tx">2026年 01月 02日</td></tr></table>'
                '<div class="videos_textli"><article>Synthetic description &amp; text</article></div>'
                '<div class="videos_samimg"><a href="https://images.example.test/cover.jpg"></a></div>')


def search_html(provider, identities):
    links = "".join(f'<a href="{provider._url(identity)}">Synthetic result</a>' for identity in identities)
    return html(f'<div id="searchresultarea">{links}</div>')


class ReplayResponse:
    def __init__(self, body, status=200, content_type="text/html; charset=utf-8"):
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


@pytest.fixture
def replay(monkeypatch):
    state = {"responses": {}, "calls": [], "closed": [], "proxies": []}

    def request(client, method, url, **kwargs):
        assert method == "get"
        assert client._timeout == 7
        assert client._verify is True
        assert client._cookies is None  # No implicit age/session assertions.
        assert kwargs.get("allow_redirects", kwargs.get("follow_redirects")) is False
        assert url in state["responses"], f"unexpected URL {url}"
        state["calls"].append((url, kwargs))
        state["proxies"].append(deepcopy(client._proxies))
        response = state["responses"][url]
        response = response() if callable(response) else ReplayResponse(response)
        state["closed"].append(response)
        return response

    async def arequest(client, method, url, **kwargs):
        return request(client, method, url, **kwargs)

    monkeypatch.setattr(RequestUtils, "request", request)
    monkeypatch.setattr(AsyncRequestUtils, "request", arequest)
    yield state
    assert all(response.closed for response in state["closed"])


def prepare(replay, cls):
    provider = cls(timeout=7)
    identity = IDENTITIES[provider.source]
    replay["responses"][provider._url(identity)] = product_html(provider.source)
    if isinstance(provider, direct._SearchProvider):
        url, _ = provider._search_request("TEST-123")
        replay["responses"][url] = search_html(provider, [identity])
    return provider


def invoke(provider, operation, value, asynchronous):
    return asyncio.run(getattr(provider, "async_" + operation)(value)) if asynchronous else getattr(provider, operation)(value)


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("operation", ["search", "fetch"])
def test_success_preserves_source_native_identity_and_exact_number(replay, cls, asynchronous, operation):
    provider = prepare(replay, cls)
    result = invoke(provider, operation, "TEST-123" if operation == "search" else IDENTITIES[provider.source], asynchronous)
    assert result["number"] == "TEST-123"
    assert result["record"] == {
        "_id": IDENTITIES[provider.source], "_source": provider.source, "number": "TEST-123",
        "title": "TEST-123 synthetic title", "discription": [{"language": "ja", "overview": "Synthetic description & text"}],
        "share_date": "2026-01-02", "cover_image": "https://images.example.test/cover.jpg",
    }
    assert result["actors"] == [{"name": "Synthetic Actor"}]


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("value", [None, "", "../secret", "https://evil.example", "private/TEST-123.mkv", "TEST-123 title", "TEST-123 TEST-124", "x" * 129])
def test_invalid_input_never_reaches_network(replay, cls, asynchronous, value):
    provider = cls(timeout=7)
    assert invoke(provider, "fetch", value, asynchronous) is None
    assert invoke(provider, "search", value, asynchronous) is None
    assert replay["calls"] == []


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_conflicting_title_fails_closed(replay, cls, asynchronous):
    provider = prepare(replay, cls)
    replay["responses"][provider._url(IDENTITIES[provider.source])] = product_html(provider.source, title="OTHER-124 synthetic")
    assert invoke(provider, "search", "TEST-123", asynchronous) is None


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("body,status,content_type", [
    ("<html>Age confirmation required</html>", 200, "text/html"),
    ("<html>Temporary gate</html>", 403, "text/html"),
    ("<html>Moved</html>", 302, "text/html"),
    ("<html>Rate limited</html>", 429, "text/html"),
    (b"a" * (direct.MAX_HTML_BYTES + 1), 200, "text/html"),
    ("{}", 200, "application/json"),
])
def test_gate_redirect_non_html_or_oversized_body_is_not_metadata(replay, cls, asynchronous, body, status, content_type):
    provider = prepare(replay, cls)
    replay["responses"][provider._url(IDENTITIES[provider.source])] = lambda: ReplayResponse(body, status, content_type)
    assert invoke(provider, "fetch", IDENTITIES[provider.source], asynchronous) is None
    assert len(replay["calls"]) == 1


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_every_request_rereads_system_proxy(replay, monkeypatch, cls, asynchronous):
    provider = prepare(replay, cls)
    for proxy in ("http://127.0.0.1:18080", "http://127.0.0.1:28080"):
        monkeypatch.setattr(direct.settings, "PROXY_HOST", proxy)
        count = len(replay["calls"])
        assert invoke(provider, "search", "TEST-123", asynchronous)
        expected = proxy if asynchronous else {"http": proxy, "https": proxy}
        assert replay["proxies"][count:] == [expected] * (len(replay["calls"]) - count)


@pytest.mark.parametrize("cls", [direct.FANZAProvider, direct.DUGAProvider])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_multiple_native_id_matches_are_rejected(replay, cls, asynchronous):
    provider = prepare(replay, cls)
    alternate = "other0001" if provider.source == "fanza" else "other-1"
    replay["responses"][provider._url(alternate)] = product_html(provider.source, identity=alternate)
    replay["responses"][provider._search_request("TEST-123")[0]] = search_html(provider, [IDENTITIES[provider.source], alternate])
    assert invoke(provider, "search", "TEST-123", asynchronous) is None


@pytest.mark.parametrize("cls", [direct.FANZAProvider, direct.DUGAProvider])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("extra", ['<a rel="next" href="?page=2">more</a>', '<a href="?page=2">2</a>', '<a href="?next=1">次へ</a>'])
def test_pagination_fails_closed_before_detail_requests(replay, cls, asynchronous, extra):
    provider = prepare(replay, cls)
    url = provider._search_request("TEST-123")[0]
    replay["responses"][url] += extra
    assert invoke(provider, "search", "TEST-123", asynchronous) is None
    assert len(replay["calls"]) == 1


@pytest.mark.parametrize("cls", [direct.FANZAProvider, direct.DUGAProvider])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_search_links_cannot_redirect_to_another_host(replay, cls, asynchronous):
    provider = prepare(replay, cls)
    url = provider._search_request("TEST-123")[0]
    replay["responses"][url] = search_html(provider, [IDENTITIES[provider.source]]).replace(provider.host, "evil.example")
    assert invoke(provider, "search", "TEST-123", asynchronous) is None
    assert len(replay["calls"]) == 1


@pytest.mark.parametrize("cls", [direct.FANZAProvider, direct.DUGAProvider])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_mismatched_native_identity_is_rejected(replay, cls, asynchronous):
    provider = prepare(replay, cls)
    replay["responses"][provider._url(IDENTITIES[provider.source])] = product_html(provider.source, identity="other-456")
    assert invoke(provider, "fetch", IDENTITIES[provider.source], asynchronous) is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_fanza_never_guesses_catalog_number_from_sku(replay, asynchronous):
    provider = prepare(replay, direct.FANZAProvider)
    replay["responses"][provider._url(IDENTITIES[provider.source])] = html(
        '<script type="application/ld+json">' + json.dumps({"@type": "Product", "sku": "fixture00123", "name": "Synthetic title"}) + "</script>"
    )
    assert invoke(provider, "fetch", "fixture00123", asynchronous) is None


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("mutate", [
    lambda value: value.replace("\"sku\": \"fixture00123\"", "\"sku\": 123"),
    lambda value: value.replace("</body>", '<table><tr><th>品番</th><td>OTHER-123</td></tr></table></body>'),
    lambda value: value.replace('"@type": "VideoObject"', '"@type": "VideoObject", "name": "OTHER-123"'),
    lambda value: value.replace("</body>", '<script type="application/ld+json">{"@type":"Product","sku":"fixture00123","name":"TEST-123"}</script></body>'),
    lambda value: value.replace("</body>", '<script type="application/ld+json">broken json</script></body>'),
])
def test_fanza_conflicting_or_malformed_jsonld_fails_closed(replay, asynchronous, mutate):
    provider = prepare(replay, direct.FANZAProvider)
    replay["responses"][provider._url("fixture00123")] = mutate(product_html("fanza"))
    assert invoke(provider, "fetch", "fixture00123", asynchronous) is None


def test_parser_rejects_invalid_dates_without_inventing_them():
    for value in ("2026-02-30", "2026-13-01", "yesterday", "2026-01-02 trailing"):
        assert direct._date(value) == ""
    assert direct._date("2026年 1月 2日") == "2026-01-02"


@pytest.mark.parametrize("value", ["https://evil.example/ppv/fixture-123/", "http://duga.jp/ppv/fixture-123/", "https://duga.jp:444/ppv/fixture-123/", "https://u:p@duga.jp/ppv/fixture-123/", "https://duga.jp/ppv/fixture-123/#x", "//duga.jp/ppv/../fixture-123/", "https://duga.jp\\@evil.example/ppv/fixture-123/"])
def test_duga_identity_url_is_strict(value):
    assert direct.DUGAProvider()._identity(value) is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_mgstage_numeric_prefix_and_zeroes_are_preserved(replay, asynchronous):
    provider = direct.MGStageProvider(timeout=7)
    replay["responses"][provider._url("123TEST-001")] = product_html("mgstage", number="123TEST-001")
    result = invoke(provider, "search", "123test_001", asynchronous)
    assert result["number"] == result["record"]["_id"] == "123TEST-001"
    assert len(replay["calls"]) == 1


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_blank_title_does_not_create_metadata(replay, cls, asynchronous):
    provider = prepare(replay, cls)
    replay["responses"][provider._url(IDENTITIES[provider.source])] = product_html(provider.source, title="")
    assert invoke(provider, "fetch", IDENTITIES[provider.source], asynchronous) is None


@pytest.mark.parametrize("cls", [direct.MGStageProvider, direct.SODProvider])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_number_in_detail_must_match_requested_native_id(replay, cls, asynchronous):
    provider = prepare(replay, cls)
    replay["responses"][provider._url("TEST-123")] = product_html(provider.source, number="TEST-0123")
    assert invoke(provider, "fetch", "TEST-123", asynchronous) is None


@pytest.mark.parametrize("cls", [direct.FANZAProvider, direct.DUGAProvider])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_excessive_candidates_fail_before_any_detail_request(replay, cls, asynchronous):
    provider = prepare(replay, cls)
    identities = [f"fixture{i}" if provider.source == "fanza" else f"fixture-{i}" for i in range(25)]
    replay["responses"][provider._search_request("TEST-123")[0]] = search_html(provider, identities)
    assert invoke(provider, "search", "TEST-123", asynchronous) is None
    assert len(replay["calls"]) == 1


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_unconfigured_proxy_is_not_replaced_by_provider_proxy(replay, monkeypatch, cls, asynchronous):
    monkeypatch.setattr(direct.settings, "PROXY_HOST", None)
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(variable, raising=False)
    provider = prepare(replay, cls)
    assert invoke(provider, "search", "TEST-123", asynchronous)
    assert replay["proxies"] and all(value is None for value in replay["proxies"])


@pytest.mark.parametrize("cls", [direct.MGStageProvider, direct.DUGAProvider, direct.SODProvider])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_conflicting_duplicate_titles_are_rejected(replay, cls, asynchronous):
    provider = prepare(replay, cls)
    body = product_html(provider.source)
    body = body.replace("</h1>", "</h1><h1>OTHER-123 conflicting</h1>") if provider.source != "duga" else body.replace("</body>", '<meta property="og:title" content="OTHER-123 conflicting"></body>')
    replay["responses"][provider._url(IDENTITIES[provider.source])] = body
    assert invoke(provider, "fetch", IDENTITIES[provider.source], asynchronous) is None
