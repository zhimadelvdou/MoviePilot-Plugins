"""Synthetic offline contracts for the FC2 official Product JSON-LD adapter."""
import asyncio
import json

import pytest

from app.plugins.xiaojiejiescraping import fc2_official as source  # pylint: disable=import-error,no-name-in-module
from app.plugins.xiaojiejiescraping.registry import PROVIDERS, ProviderRegistry
from app.sdk.network import AsyncRequestUtils, RequestUtils

IDENTITY = "1229107"
NUMBER = "FC2-PPV-1229107"
URL = f"{source.BASE}/article/{IDENTITY}/"


def product(**updates):
    value = {"@type": "Product", "@id": "https://example.test/item", "sku": IDENTITY,
             "productID": IDENTITY, "name": "Synthetic item title",
             "description": "Synthetic description", "image": {
                 "@type": "ImageObject", "url": "https://storage.example.contents.fc2.com/synthetic.jpg"}}
    value.update(updates)
    return value


def html(value=None):
    value = product() if value is None else value
    return ('<!doctype html><html><head><script type="application/ld+json">'
            + json.dumps(value) + '</script></head><body></body></html>')


class Response:
    def __init__(self, body=None, *, status=200, content_type="text/html; charset=UTF-8", url=URL):
        self.content = (html() if body is None else body).encode() if isinstance(body, str) else body
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.url = url
        self.closed = False

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


@pytest.fixture
def replay(monkeypatch):
    state = {"body": html(), "calls": [], "responses": []}

    def respond(client, method, url, **kwargs):
        assert method == "get" and url == URL
        assert client._timeout == 7 and client._verify is True
        assert client._headers["Accept"] == "text/html" and client._cookies is None
        state["calls"].append((url, kwargs))
        response = state["body"] if isinstance(state["body"], Response) else Response(state["body"])
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
    assert all(response.closed for response in state["responses"])


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def asynchronous(request):
    return request.param


def invoke(method, value, asynchronous):
    provider = source.FC2OfficialProvider(timeout=7)
    call = getattr(provider, ("async_" if asynchronous else "") + method)
    return asyncio.run(call(value)) if asynchronous else call(value)


def test_search_aliases_and_fetch_return_same_record(replay, asynchronous):
    for alias in ("1229107", "FC2-1229107", NUMBER):
        assert invoke("search", alias, asynchronous)["number"] == NUMBER
    assert invoke("fetch", IDENTITY, asynchronous)["number"] == NUMBER
    assert len(replay["calls"]) == 4
    assert all(url == URL for url, _ in replay["calls"])


def test_extracts_one_product_and_leaves_absent_date_and_actors_empty(replay, asynchronous):
    replay["body"] = html({"@context": "https://schema.org", "@graph": [
        {"@type": "WebPage", "name": "ignored"}, product()]})
    result = invoke("fetch", IDENTITY, asynchronous)
    assert result == {"number": NUMBER, "actors": [], "record": {
        "_source": "fc2", "_id": IDENTITY, "number": NUMBER,
        "title": "Synthetic item title", "share_date": "",
        "cover_image": "https://storage.example.contents.fc2.com/synthetic.jpg",
        "discription": [{"language": "ja", "overview": "Synthetic description"}]}}


@pytest.mark.parametrize("number", [None, "1234", "1234567890", "FC2-12", "ABC-12345", "FC2-PPV-1234567-1"])
def test_non_fc2_or_invalid_input_makes_no_request(replay, asynchronous, number):
    assert invoke("search", number, asynchronous) is None
    assert replay["calls"] == []


@pytest.mark.parametrize("identity", [None, 1229107, "1229107/", "1234", "../1229107", "FC2-PPV-1229107"])
def test_invalid_fetch_identity_makes_no_request(replay, asynchronous, identity):
    assert invoke("fetch", identity, asynchronous) is None
    assert replay["calls"] == []


@pytest.mark.parametrize("fields", [
    {"sku": "1229108"}, {"productID": "1229108"},
    {"sku": "1229107", "productID": "1229108"}, {"name": "  \n "},
    {"@type": "WebPage"}, {"sku": "bad"},
])
def test_mismatch_or_bad_identity_and_empty_title_fail_closed(replay, asynchronous, fields):
    replay["body"] = html(product(**fields))
    assert invoke("fetch", IDENTITY, asynchronous) is None


def test_two_exact_product_matches_are_ambiguous(replay, asynchronous):
    replay["body"] = html([product(), product()])
    assert invoke("fetch", IDENTITY, asynchronous) is None


def test_recovers_unrelated_html_layout_error_without_relaxing_product_checks(replay, asynchronous):
    replay["body"] = html(product()).replace(
        "<body></body>", '<body><div id="duplicate" id="duplicate"></div></body>')
    assert invoke("fetch", IDENTITY, asynchronous)["number"] == NUMBER


def test_passive_age_prompt_does_not_hide_a_valid_official_product(replay, asynchronous):
    replay["body"] = html(product()).replace("<body></body>", "<body><p>18+ age notice</p></body>")
    assert invoke("fetch", IDENTITY, asynchronous)["number"] == NUMBER


def test_age_verification_gate_without_product_fails_closed(replay, asynchronous):
    replay["body"] = '<html><body><p>Age verification required</p><form></form></body></html>'
    assert invoke("fetch", IDENTITY, asynchronous) is None


def test_response_over_parse_ceiling_fails_closed(replay, asynchronous):
    replay["body"] = " " * (source.MAX_HTML_BYTES + 1)
    assert invoke("fetch", IDENTITY, asynchronous) is None


@pytest.mark.parametrize("response", [
    Response(status=403), Response(status=404), Response(status=302),
    Response(content_type="application/json"),
    Response(url="https://other.example/article/1229107/"),
])
def test_http_gates_redirect_mime_and_cross_host_fail_closed(replay, asynchronous, response):
    replay["body"] = response
    assert invoke("fetch", IDENTITY, asynchronous) is None


@pytest.mark.parametrize("body", ["", "not html", '<script type="application/ld+json">{bad}</script>',
                                   '<script type="application/ld+json">{"sku":"1","sku":"2"}</script>'])
def test_bad_json_and_missing_product_fail_closed(replay, asynchronous, body):
    replay["body"] = body
    assert invoke("fetch", IDENTITY, asynchronous) is None


def test_source_is_registered_and_ordered(replay):
    assert any(item.key == "fc2" and item.label == "FC2官方"
               and item.state == "limited_live" for item in PROVIDERS)
    calls = []

    class Adapter:
        def search(self, number):
            calls.append(number)
            return source.FC2OfficialProvider(timeout=7).search(number)

        async def async_search(self, number):
            return self.search(number)

        def fetch(self, identity):
            return None

        async def async_fetch(self, identity):
            return None

    result = ProviderRegistry({"fc2": Adapter()}).search("FC2-1229107", ("fc2",))
    assert result[0] == "fc2"
    assert calls == ["FC2-1229107"]


@pytest.mark.parametrize("image", ["http://storage.example.contents.fc2.com/a.jpg",
                                    "https://example.invalid/a.jpg", "https://127.0.0.1/a.jpg"])
def test_unsafe_or_unverified_image_is_not_a_candidate(replay, asynchronous, image):
    replay["body"] = html(product(image={"@type": "ImageObject", "url": image}))
    assert invoke("fetch", IDENTITY, asynchronous)["record"]["cover_image"] is None


def test_exact_official_http_image_host_is_upgraded_without_changing_path_or_query(
        replay, asynchronous):
    image = "http://storage57000.contents.fc2.com/image/path%2Fname.jpg?x=1&y=%2F"
    replay["body"] = html(product(image={"@type": "ImageObject", "url": image}))
    assert invoke("fetch", IDENTITY, asynchronous)["record"]["cover_image"] == (
        "https://storage57000.contents.fc2.com/image/path%2Fname.jpg?x=1&y=%2F")


@pytest.mark.parametrize("image", [
    "http://storage.example.contents.fc2.com/image.jpg",
    "http://other.storage57000.contents.fc2.com/image.jpg",
    "http://storage57000.contents.fc2.com:80/image.jpg",
    "http://storage57000.contents.fc2.com:/image.jpg",
    "http://user@storage57000.contents.fc2.com/image.jpg",
    "http://@storage57000.contents.fc2.com/image.jpg",
    "http://storage57000.contents.fc2.com/image.jpg#fragment",
    "http://storage57000.contents.fc2.com/image.jpg#",
    "http://storage57000.contents.fc2.com/image with space.jpg",
])
def test_http_image_upgrade_rejects_other_hosts_and_ambiguous_urls(
        replay, asynchronous, image):
    replay["body"] = html(product(image={"@type": "ImageObject", "url": image}))
    assert invoke("fetch", IDENTITY, asynchronous)["record"]["cover_image"] is None
