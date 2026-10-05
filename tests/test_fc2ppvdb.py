"""Fresh synthetic FC2CMADB contracts at the real V3 SDK HTTP boundary; no network."""
import asyncio
from copy import deepcopy
import json

import pytest

from app.plugins.xiaojiejiescraping import fc2ppvdb as source  # pylint: disable=import-error,no-name-in-module
from app.sdk.network import AsyncRequestUtils, RequestUtils


IDENTITY = "1234567"
NUMBER = "FC2-PPV-1234567"
URL = "https://fc2cmadb.com/articles/1234567"


def payload():
    return {
        "component": "Articles/Show", "url": "/articles/1234567", "version": "synthetic-version",
        "props": {
            "auth": {"user": None}, "errors": {},
            "article": {
                "id": 76543, "video_id": 1234567,
                "title": "合成 metadata & title", "release_date": "2026-01-02",
                "image_url": "https://images.example.test/synthetic.jpg",
                "writer": {"name": "Synthetic seller"},
            },
        },
    }


def html(page=None):
    page = payload() if page is None else page
    # No source page, title, person, image or media content is copied into fixtures.
    return ('<!DOCTYPE html><html><body><div id="app"></div>'
            '<script type="application/json" data-page="app">'
            + json.dumps(page, ensure_ascii=False) + '</script></body></html>')


class Response:
    def __init__(self, body=None, *, status=200, content_type="text/html; charset=UTF-8", url=URL, headers=None):
        body = html() if body is None else body
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.url = url
        self.closed = False

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


@pytest.fixture
def replay(monkeypatch):
    state = {"body": html(), "calls": [], "responses": [], "proxies": [], "clients": []}

    def respond(client, method, url, **kwargs):
        assert method == "get"
        assert url == URL
        assert client._timeout == 7
        assert client._verify is True
        assert client._headers["Accept"] == "text/html"
        assert client._cookies is None
        assert not any(k.lower() in ("authorization", "cookie") or k.lower().startswith("x-inertia")
                       for k in client._headers)
        assert not kwargs.get("params")
        assert not kwargs.get("cookies")
        assert not kwargs.get("data")
        state["calls"].append((method, url, kwargs))
        state["proxies"].append(deepcopy(client._proxies))
        state["clients"].append(client)
        body = state["body"]
        response = Response(body) if isinstance(body, (str, bytes)) else body
        state["responses"].append(response)
        return response

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


def invoke(method, value, asynchronous):
    client = source.FC2PPVDBProvider(timeout=7)
    call = getattr(client, "async_" + method if asynchronous else method)
    return asyncio.run(call(value)) if asynchronous else call(value)


def test_exact_number_native_permalink_roundtrip(replay, asynchronous):
    found = invoke("search", "fc2_1234567", asynchronous)
    assert found == invoke("fetch", IDENTITY, asynchronous)
    assert found == {
        "number": NUMBER, "actors": [],
        "record": {
            "_source": "fc2ppvdb", "_id": IDENTITY, "number": NUMBER,
            "title": "合成 metadata & title", "share_date": "2026-01-02",
            "cover_image": "https://images.example.test/synthetic.jpg", "discription": [],
        },
    }
    assert found["record"]["_id"] != str(payload()["props"]["article"]["id"])
    assert len(replay["calls"]) == 2  # Exactly one anonymous GET per operation.


@pytest.mark.parametrize("video_id", [1234567, "1234567"])
def test_accepts_explicit_integer_or_digit_string_identity(replay, asynchronous, video_id):
    page = payload()
    page["props"]["article"]["video_id"] = video_id
    replay["body"] = html(page)
    assert invoke("fetch", IDENTITY, asynchronous)["number"] == NUMBER


@pytest.mark.parametrize("value", [None, 1234567, True, "1234567", "TEST-123", "FC2-PPV-1234567-1",
                                 "FC2-PPV-1234567?x=1", "FC2-PPV-1234567890", "FC2-12", ""])
def test_invalid_search_does_not_request(replay, asynchronous, value):
    assert invoke("search", value, asynchronous) is None
    assert replay["calls"] == []


@pytest.mark.parametrize("value", [None, 1234567, True, "FC2-PPV-1234567", "1234567/", "1234567?x=1",
                                 "../1234567", "1234", "1234567890", " 1234567", "１２３４５６７", ""])
def test_invalid_native_identity_does_not_request(replay, asynchronous, value):
    assert invoke("fetch", value, asynchronous) is None
    assert replay["calls"] == []


@pytest.mark.parametrize("component", [None, "Error", "Auth/Login", "AgeVerification", "Articles/Index"])
def test_rejects_errors_and_gate_components(replay, asynchronous, component):
    page = payload()
    page["component"] = component
    replay["body"] = html(page)
    assert invoke("fetch", IDENTITY, asynchronous) is None


@pytest.mark.parametrize("path", [None, "/articles/1234568", "/articles/76543", URL,
                                "/articles/1234567/", "/articles/1234567?x=1", "/login", "/age-verify"])
def test_requires_exact_source_page_path(replay, asynchronous, path):
    page = payload()
    page["url"] = path
    replay["body"] = html(page)
    assert invoke("fetch", IDENTITY, asynchronous) is None


@pytest.mark.parametrize("video_id", [None, False, True, 76543, "1234568", "12345670", "01234567",
                                    " 1234567", 1234567.0, [], {}, "FC2-PPV-1234567"])
def test_never_substitutes_internal_id_or_guesses_number(replay, asynchronous, video_id):
    page = payload()
    page["props"]["article"]["id"] = 1234567
    page["props"]["article"]["video_id"] = video_id
    replay["body"] = html(page)
    assert invoke("fetch", IDENTITY, asynchronous) is None


@pytest.mark.parametrize("title", [None, 123, [], "", " \n\t", "FC2CMADB", "fc2ppvdb", "x" * 4097,
                                 "FC2-PPV-1234568 other product", "TEST-456 other product"])
def test_rejects_missing_generic_and_conflicting_titles(replay, asynchronous, title):
    page = payload()
    page["props"]["article"]["title"] = title
    replay["body"] = html(page)
    assert invoke("fetch", IDENTITY, asynchronous) is None


@pytest.mark.parametrize("key,value", [
    ("status", 403), ("status", "404"), ("errors", {"message": "authentication required"}),
    ("auth", {"user": {"id": 1}}), ("auth", []), ("article", None), ("article", []),
])
def test_rejects_invalid_or_authenticated_props(replay, asynchronous, key, value):
    page = payload()
    page["props"][key] = value
    replay["body"] = html(page)
    assert invoke("fetch", IDENTITY, asynchronous) is None


@pytest.mark.parametrize("page", [[], None, {"component": "Articles/Show", "url": "/articles/1234567", "props": []}])
def test_rejects_invalid_page_schema(replay, asynchronous, page):
    replay["body"] = '<script data-page="app" type="application/json">' + json.dumps(page) + '</script>'
    assert invoke("fetch", IDENTITY, asynchronous) is None


@pytest.mark.parametrize("body", [
    "", "<html><title>Just a moment...</title></html>",
    "<html><form action='/login'><input name='password'></form></html>",
    '<script data-page="app" type="application/json">{bad json}</script>',
    html().replace('data-page="app"', 'data-page="other"'),
    html().replace('type="application/json"', 'type="application/ld+json"'),
    html() + html(),
    html().replace('"video_id": 1234567', '"video_id": 1234567, "video_id": 76543'),
    html().replace('"video_id": 1234567', '"video_id": 76543, "video_id": 1234567'),
    html().replace('"id": 76543', '"id": NaN'),
    html().replace('"version": "synthetic-version"', '"version": Infinity'),
    b"\xff\xfe" + html().encode(),
    " " * (source.MAX_HTML_BYTES + 1),
    '<script data-page="app" type="application/json">' + "[" * 2000 + "]" * 2000 + '</script>',
])
def test_malformed_ambiguous_nonutf8_and_large_documents_fail_closed(replay, asynchronous, body):
    replay["body"] = body
    assert invoke("fetch", IDENTITY, asynchronous) is None


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 404, 429, 500])
def test_never_follows_redirect_or_gate_response(replay, asynchronous, status):
    replay["body"] = Response(status=status, headers={"Location": URL, "Set-Cookie": "session=synthetic"})
    assert invoke("fetch", IDENTITY, asynchronous) is None
    assert len(replay["calls"]) == 1


@pytest.mark.parametrize("mime", ["", "application/json", "image/jpeg", "text/plain", "application/octet-stream"])
def test_only_html_response_contract(replay, asynchronous, mime):
    replay["body"] = Response(content_type=mime)
    assert invoke("fetch", IDENTITY, asynchronous) is None


@pytest.mark.parametrize("url", ["https://other.example/articles/1234567", "https://fc2cmadb.com/login",
                               "https://fc2cmadb.com/articles/1234568", "http://fc2cmadb.com/articles/1234567"])
def test_mismatched_response_url_fails_closed(replay, asynchronous, url):
    replay["body"] = Response(url=url)
    assert invoke("fetch", IDENTITY, asynchronous) is None


def test_no_response_returns_none(replay, asynchronous):
    replay["body"] = None
    assert invoke("fetch", IDENTITY, asynchronous) is None


@pytest.mark.parametrize("image", [None, {}, "", "http://images.example.test/a.jpg", "javascript:alert(1)",
                                 "//user:pass@images.example.test/a.jpg", "https://127.0.0.1/a.jpg",
                                 "https://localhost/a.jpg", "https://images.example.test/a.jpg#fragment",
                                 "https://images.example.test:444/a.jpg", "https://images.example.test/a b.jpg",
                                 "/storage/images/article/no-image.jpg"])
def test_invalid_cover_is_discarded_without_discarding_metadata(replay, asynchronous, image):
    page = payload()
    page["props"]["article"]["image_url"] = image
    replay["body"] = html(page)
    assert invoke("fetch", IDENTITY, asynchronous)["record"]["cover_image"] is None
    assert len(replay["calls"]) == 1


@pytest.mark.parametrize("released", [None, [], "bad date", "2026-02-30"])
def test_invalid_date_is_empty(replay, asynchronous, released):
    page = payload()
    page["props"]["article"]["release_date"] = released
    replay["body"] = html(page)
    assert invoke("fetch", IDENTITY, asynchronous)["record"]["share_date"] == ""


def test_no_deferred_calls_or_seller_to_actor_inference(replay, asynchronous):
    page = payload()
    page["deferredProps"] = {"default": ["actresses"]}
    page["props"]["actresses"] = [{"name": "Synthetic actor"}]
    page["props"]["article"]["actresses"] = [{"name": "Another synthetic actor"}]
    replay["body"] = html(page)
    assert invoke("fetch", IDENTITY, asynchronous)["actors"] == []
    assert len(replay["calls"]) == 1


def test_reads_current_host_proxy_and_reuses_no_cookie_session(replay, asynchronous, monkeypatch):
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(variable, raising=False)
    for proxy in ("http://proxy-one.example:8080", "http://proxy-two.example:9090", None):
        monkeypatch.setattr(source.settings, "PROXY_HOST", proxy)
        assert invoke("fetch", IDENTITY, asynchronous)
        configured = source.settings.PROXY
        expected = (configured.get("https") if isinstance(configured, dict) else configured) if asynchronous else configured
        assert replay["proxies"][-1] == expected
    assert len({id(client) for client in replay["clients"]}) == 3
