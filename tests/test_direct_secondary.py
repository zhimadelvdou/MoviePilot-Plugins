"""真实 MoviePilot SDK HTTP 边界的合成回放；不读取媒体或源站图片。"""
import asyncio
from copy import deepcopy
import json

import pytest

from app.plugins.xiaojiejiescraping import direct_secondary as sources  # pylint: disable=import-error,no-name-in-module
from app.sdk.network import AsyncRequestUtils, RequestUtils


BUS = """<html><body><div class="container"><h3>TEST-123 Synthetic title</h3>
<a class="bigImage"><img src="https://images.example.test/bus.jpg"></a>
<div class="col-md-3 info"><p><span>識別碼:</span><span>TEST-123</span></p>
<p><span>發行日期:</span> 2026-01-02</p></div>
<a class="avatar-box"><div><img title="Synthetic Actor"></div></a>
</div></body></html>"""

JAV321 = """<html><head><link rel="canonical" href="/video/native00123"></head>
<body><ul class="dropdown-menu"><li><a href="/video/native00123">Japanese</a></li></ul>
<div class="panel-heading"><h3>Synthetic title</h3></div>
<div class="col-md-9"><b>品番</b>: TEST-123<br><b>配信開始日</b>: 2026-01-02<br></div>
<div class="thumbnail"><a href="/star/synthetic"><img src="https://images.example.test/actor.jpg">Synthetic Actor</a></div>
<div class="col-xs-12 col-md-12"><p><a><img class="img-responsive" src="https://images.example.test/321.jpg"></a></p></div>
<div class="panel-body"><div class="row"><div class="col-md-12">Synthetic overview &amp; text</div></div></div>
</body></html>"""

HUB_LEGACY = """<html><body><h1>FC2-12345</h1><h1>Synthetic title</h1>
<a data-fancybox="gallery" href="//images.example.test/hub.jpg"></a>
<div class="col-8">Seller Is Not Actor</div><div class="col des">Synthetic description</div>
</body></html>"""

HUB_PATH = "/video/7654321/id12345/synthetic-title"


def hub_current(**overrides):
    movie = {
        "@type": "Movie", "name": "Synthetic title", "identifier": ["FC2-PPV-12345", "12345"],
        "datePublished": "2026-01-02", "image": "https://images.example.test/hub.jpg",
        "actor": ["Synthetic Actor", {"name": "Second Synthetic Actor"}],
        "description": "Synthetic description",
    }
    movie.update(overrides)
    return (f'<html><head><link rel="canonical" href="{HUB_PATH}"></head><body>'
            '<h1 class="card-title fc2-id">FC2-PPV-12345</h1>'
            '<h1 class="card-text fc2-title">Synthetic title</h1>'
            f'<script type="application/ld+json">{json.dumps(movie)}</script></body></html>')


class Response:
    def __init__(self, text, status=200, headers=None):
        self.text = text
        self.content = text.encode("utf-8") if isinstance(text, str) else text
        self.status_code = status
        self.headers = {"Content-Type": "text/html", **(headers or {})}
        self.closed = False

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


@pytest.fixture
def replay(monkeypatch):
    state = {"routes": {}, "calls": [], "responses": [], "proxies": []}

    def respond(client, method, url, **kwargs):
        assert client._verify is True
        assert client._timeout == 7
        assert client._headers["Accept"] == "text/html"
        state["calls"].append((method, url, kwargs))
        state["proxies"].append(deepcopy(client._proxies))
        key = (method, url)
        assert key in state["routes"], f"unexpected request {key}"
        route = state["routes"][key]
        result = route() if callable(route) else Response(route) if isinstance(route, str) else route
        state["responses"].append(result)
        return result

    def request(client, method, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        return respond(client, method, url, **kwargs)

    async def async_request(client, method, url, **kwargs):
        assert kwargs.get("follow_redirects") is False
        return respond(client, method, url, **kwargs)

    monkeypatch.setattr(RequestUtils, "request", request)
    monkeypatch.setattr(AsyncRequestUtils, "request", async_request)
    yield state
    assert all(response is None or response.closed for response in state["responses"])


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def asynchronous(request):
    return request.param


def invoke(client, method, value, asynchronous):
    if asynchronous:
        return asyncio.run(getattr(client, "async_" + method)(value))
    return getattr(client, method)(value)


def test_javbus_exact_search_and_native_fetch(replay, asynchronous):
    client = sources.JavBusProvider(timeout=7)
    replay["routes"][("get", client.base + "/TEST-123")] = BUS
    search = invoke(client, "search", "test_123", asynchronous)
    assert search == invoke(client, "fetch", "TEST-123", asynchronous)
    assert search["number"] == "TEST-123"
    assert search["record"]["_source"] == "javbus"
    assert search["record"]["_id"] == "TEST-123"
    assert search["record"]["share_date"] == "2026-01-02"
    assert search["actors"] == [{"name": "Synthetic Actor"}]
    assert len(replay["calls"]) == 2


@pytest.mark.parametrize("page", [
    BUS.replace("<span>TEST-123</span>", "<span>TEST-124</span>"),
    BUS.replace("<span>TEST-123</span>", ""),
    BUS.replace("Synthetic title", "TEST-124 conflicting title"),
    BUS.replace("</div>\n<a class=", "<p><span>識別碼:</span><span>TEST-124</span></p></div>\n<a class="),
    "<html><title>TEST-123</title><h3>TEST-123 Synthetic title</h3></html>",
])
def test_javbus_does_not_guess_identity(replay, asynchronous, page):
    client = sources.JavBusProvider(timeout=7)
    replay["routes"][("get", client.base + "/TEST-123")] = page
    assert invoke(client, "search", "TEST-123", asynchronous) is None


def test_jav321_posts_only_number_and_preserves_native_identity(replay, asynchronous):
    client = sources.Jav321Provider(timeout=7)
    replay["routes"][("post", client.base + "/search")] = JAV321
    replay["routes"][("get", client.base + "/video/native00123")] = JAV321
    result = invoke(client, "search", "TEST-123", asynchronous)
    assert result == invoke(client, "fetch", "native00123", asynchronous)
    assert result["record"]["_id"] == "native00123"
    assert result["record"]["_source"] == "jav321"
    assert result["number"] == "TEST-123"
    assert result["actors"] == [{"name": "Synthetic Actor"}]
    assert result["record"]["discription"] == [{"language": "ja", "overview": "Synthetic overview & text"}]
    assert replay["calls"][0][2]["data"] == {"sn": "TEST-123"}


def test_jav321_follows_validated_search_redirect(replay, asynchronous):
    client = sources.Jav321Provider(timeout=7)
    replay["routes"][("post", client.base + "/search")] = Response("", 302, {"Location": "/video/native00123"})
    replay["routes"][("get", client.base + "/video/native00123")] = JAV321
    result = invoke(client, "search", "TEST-123", asynchronous)
    assert result["record"]["_id"] == "native00123"
    assert len(replay["calls"]) == 2
    assert replay["calls"][1][2].get("data") is None


@pytest.mark.parametrize("destination", [
    "https://evil.example/video/native00123", "//evil.example/video/native00123",
    "http://www.jav321.com/video/native00123", "/login", "/age-verification", "/search",
    "https://user:password@www.jav321.com/video/native00123", "/video/native00123?token=secret",
])
def test_jav321_rejects_external_and_auth_redirects(replay, asynchronous, destination):
    client = sources.Jav321Provider(timeout=7)
    replay["routes"][("post", client.base + "/search")] = Response("", 302, {"Location": destination})
    assert invoke(client, "search", "TEST-123", asynchronous) is None
    assert len(replay["calls"]) == 1


@pytest.mark.parametrize("page", [
    JAV321.replace(": TEST-123", ": TEST-124"),
    JAV321.replace("<b>品番</b>: TEST-123", "TEST-123"),
    JAV321.replace('href="/video/native00123">Japanese', 'href="/video/another_native">Japanese'),
    JAV321.replace("native00123", "search"),
])
def test_jav321_number_or_native_mismatch_fails_closed(replay, asynchronous, page):
    client = sources.Jav321Provider(timeout=7)
    replay["routes"][("get", client.base + "/video/native00123")] = page
    result = invoke(client, "fetch", "native00123", asynchronous)
    # Fetch permits another explicitly identified number for the same native ID;
    # search additionally pins the requested number.
    if ": TEST-124" in page:
        assert result["number"] == "TEST-124"
        replay["routes"][("post", client.base + "/search")] = page
        assert invoke(client, "search", "TEST-123", asynchronous) is None
    else:
        assert result is None


def test_fc2hub_legacy_permalink_roundtrip_does_not_promote_seller_to_actor(replay, asynchronous):
    client = sources.FC2HubProvider(timeout=7)
    replay["routes"][("get", client.base + "/search")] = '<link href="/id12345/">'
    replay["routes"][("get", client.base + "/id12345")] = HUB_LEGACY
    result = invoke(client, "search", "FC2-12345", asynchronous)
    assert result == invoke(client, "fetch", "id12345", asynchronous)
    assert result["record"]["_id"] == "id12345"
    assert result["record"]["_source"] == "fc2hub"
    assert result["record"]["cover_image"] == "https://images.example.test/hub.jpg"
    assert result["number"] == "FC2-PPV-12345"
    assert result["actors"] == []
    assert replay["calls"][0][2]["params"] == {"kw": "12345"}
    assert len(replay["calls"]) == 4


def test_fc2hub_current_direct_hit_and_jsonld(replay, asynchronous):
    client = sources.FC2HubProvider(timeout=7)
    replay["routes"][("get", client.base + "/search")] = hub_current()
    result = invoke(client, "search", "FC2-PPV-12345", asynchronous)
    assert result["record"]["_id"] == "id12345"
    assert result["record"]["share_date"] == "2026-01-02"
    assert result["actors"] == [{"name": "Synthetic Actor"}, {"name": "Second Synthetic Actor"}]
    assert len(replay["calls"]) == 1


def test_fc2hub_current_search_resolves_source_path(replay, asynchronous):
    client = sources.FC2HubProvider(timeout=7)
    replay["routes"][("get", client.base + "/search")] = f'<a href="{HUB_PATH}">Synthetic</a>'
    replay["routes"][("get", client.base + HUB_PATH)] = hub_current()
    assert invoke(client, "search", "FC2-PPV-12345", asynchronous)["record"]["_id"] == "id12345"
    assert len(replay["calls"]) == 2


@pytest.mark.parametrize("html", [
    '<link href="/id123456/">',
    '<link href="https://evil.example/id12345/">',
    '<link href="/id12345/?credential=secret">',
    '<link href="/id12345/#fragment">',
    '<link href="/video/111/id12345/title"><link href="/video/222/id12345/title">',
    '<link href="/tw/id12345/">',
    '<a href="javascript:alert(12345)">12345</a>',
])
def test_fc2hub_candidate_is_exact_safe_and_unique(replay, asynchronous, html):
    client = sources.FC2HubProvider(timeout=7)
    replay["routes"][("get", client.base + "/search")] = html
    assert invoke(client, "search", "FC2-PPV-12345", asynchronous) is None
    assert len(replay["calls"]) == 1


@pytest.mark.parametrize("detail", [
    HUB_LEGACY.replace("FC2-12345", "FC2-12346"),
    HUB_LEGACY.replace("FC2-12345", "Unrelated heading"),
    hub_current(identifier=["FC2-PPV-12346"]),
    hub_current().replace("</body>", '<script type="application/ld+json">{"@type":"Movie","identifier":"FC2-12346"}</script></body>'),
])
def test_fc2hub_requires_detail_identity_evidence(replay, asynchronous, detail):
    client = sources.FC2HubProvider(timeout=7)
    replay["routes"][("get", client.base + "/search")] = '<link href="/id12345/">'
    replay["routes"][("get", client.base + "/id12345")] = detail
    assert invoke(client, "search", "FC2-PPV-12345", asynchronous) is None


@pytest.mark.parametrize("provider", [sources.JavBusProvider, sources.Jav321Provider, sources.FC2HubProvider])
@pytest.mark.parametrize("value", ["../private", "https://evil.example", "native/id", "", "x" * 129, None, 123])
def test_invalid_identity_never_reaches_http(replay, asynchronous, provider, value):
    assert invoke(provider(timeout=7), "fetch", value, asynchronous) is None
    assert replay["calls"] == []


@pytest.mark.parametrize("provider", [sources.JavBusProvider, sources.Jav321Provider, sources.FC2HubProvider])
@pytest.mark.parametrize("value", ["/private/TEST-123.mkv", "TEST-123 full title", "TEST-123 TEST-124", "", None])
def test_search_never_transmits_path_or_title(replay, asynchronous, provider, value):
    assert invoke(provider(timeout=7), "search", value, asynchronous) is None
    assert replay["calls"] == []


@pytest.mark.parametrize("response", [
    lambda: None,
    lambda: Response("Access denied", 403),
    lambda: Response("Not found", 404),
    lambda: Response("", 503),
    lambda: Response("<html><title>Sign in</title></html>"),
    lambda: Response("x" * (sources.MAX_HTML + 1)),
    lambda: Response(b"\xff\xfeinvalid UTF-8"),
    lambda: Response(JAV321, headers={"Content-Type": "application/json"}),
])
def test_http_failures_and_wrong_media_type_fail_closed(replay, asynchronous, response):
    client = sources.Jav321Provider(timeout=7)
    replay["routes"][("post", client.base + "/search")] = response
    assert invoke(client, "search", "TEST-123", asynchronous) is None


def test_redirect_loop_is_bounded(replay, asynchronous):
    client = sources.Jav321Provider(timeout=7)
    replay["routes"][("get", client.base + "/video/native00123")] = lambda: Response("", 302, {"Location": "/video/native00123"})
    assert invoke(client, "fetch", "native00123", asynchronous) is None
    assert len(replay["calls"]) == 4


def test_http_text_charset_guess_does_not_corrupt_identity_labels(replay, asynchronous):
    client = sources.Jav321Provider(timeout=7)
    response = Response(JAV321)
    response.text = response.content.decode("latin-1")
    replay["routes"][("post", client.base + "/search")] = response
    assert invoke(client, "search", "TEST-123", asynchronous)["number"] == "TEST-123"


@pytest.mark.parametrize("provider,method,path,body,number", [
    (sources.JavBusProvider, "get", "/TEST-123", BUS, "TEST-123"),
    (sources.Jav321Provider, "post", "/search", JAV321, "TEST-123"),
    (sources.FC2HubProvider, "get", "/search", hub_current(), "FC2-12345"),
])
def test_proxy_changes_apply_to_every_request(replay, monkeypatch, asynchronous, provider, method, path, body, number):
    client = provider(timeout=7)
    replay["routes"][(method, client.base + path)] = body
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(variable, raising=False)
    for proxy in ("http://127.0.0.1:18080", "http://127.0.0.1:28080", None):
        monkeypatch.setattr(sources.settings, "PROXY_HOST", proxy)
        assert invoke(client, "search", number, asynchronous) is not None
        expected = proxy if asynchronous or proxy is None else {"http": proxy, "https": proxy}
        assert replay["proxies"][-1] == expected


def test_fc2ppvdb_uses_separate_current_contract_not_legacy_secondary_adapter():
    assert "fc2cmadb-crawler" in sources.FC2PPVDB_MIGRATION_SOURCE
    assert not hasattr(sources, "FC2PPVDBProvider")


def test_dates_and_asset_urls_do_not_get_invented():
    packet = sources._packet("jav321", "native00123", "TEST-123", "Synthetic title",
                             released="2026-02-30", cover="javascript:alert(1)", base="https://www.jav321.com")
    assert packet["record"]["share_date"] == ""
    assert packet["record"]["cover_image"] is None
