"""JavBus anonymous-source contract tests using synthetic HTTP responses."""
import asyncio
import json

import pytest

from app.plugins.xiaojiejiescraping.direct_secondary import JavBusProvider
from app.plugins.xiaojiejiescraping.core import project_record
from app.sdk.network import AsyncRequestUtils, RequestUtils


JAVBUS_COVER = "https://www.javbus.com/pics/cover/ao2z_b.jpg"
SAMPLE_ONE = "https://pics.dmm.co.jp/digital/video/sone00288/sone00288jp-1.jpg"
SAMPLE_TWO = "https://pics.dmm.co.jp/digital/video/sone00288/sone00288jp-2.jpg"

API_DETAIL = {
    "id": "SONE-288", "title": "Synthetic title", "img": JAVBUS_COVER,
    "date": "2024-08-09", "videoLength": "120",
    "director": {"id": "director-1", "name": "Synthetic Director"},
    "producer": {"id": "producer-1", "name": "Synthetic Producer"}, "publisher": None,
    "series": None, "genres": [],
    "stars": [{"id": "star-1", "name": "葵つかさ"}, {"id": "star-2", "name": "miru"}],
    "samples": [
        {"src": SAMPLE_ONE, "thumbnail": "https://www.javbus.com/pics/sample/sone00288/1.jpg",
         "alt": "https://unapproved.example/alt-is-not-a-source.jpg"},
        {"src": SAMPLE_ONE},
        None,
        "not an object",
        {"thumbnail": SAMPLE_TWO, "alt": SAMPLE_TWO},
        {"src": JAVBUS_COVER},
        {"src": "//pics.dmm.co.jp/digital/video/sone00288/sone00288jp-2.jpg"},
        {"src": "http://pics.dmm.co.jp/insecure.jpg"},
    ],
}


class APIResponse:
    status_code = 200
    headers = {"Content-Type": "application/json; charset=utf-8"}

    def __init__(self, document):
        self.content = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.closed = False

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("entry", ["search", "fetch"])
def test_javbus_search_uses_configured_json_api_without_global_proxy(monkeypatch, asynchronous, entry):
    calls, responses = [], []

    def make_response(client, method, url, **kwargs):
        assert method == "get"
        assert url == "http://api.example.test/base/api/movies/SONE-288"
        assert kwargs.get("allow_redirects", kwargs.get("follow_redirects")) is False
        assert client._verify is True
        assert client._proxies in ({}, None)
        if hasattr(client, "_anonymous_session"):
            assert client._anonymous_session.trust_env is False
        if hasattr(client, "_trust_env"):
            assert client._trust_env is False
        calls.append((method, url))
        response = APIResponse(API_DETAIL)
        responses.append(response)
        return response

    async def async_request(client, method, url, **kwargs):
        return make_response(client, method, url, **kwargs)

    monkeypatch.setattr(RequestUtils, "request", make_response)
    monkeypatch.setattr(AsyncRequestUtils, "request", async_request)
    provider = JavBusProvider(api_url="http://api.example.test/base")
    call = getattr(provider, ("async_" if asynchronous else "") + entry)
    result = asyncio.run(call("SONE-288")) if asynchronous else call("SONE-288")

    assert calls == [("get", "http://api.example.test/base/api/movies/SONE-288")]
    assert all(response.closed for response in responses)
    assert result["record"]["_source"] == "javbus"
    assert result["record"]["_id"] == "SONE-288"
    assert result["record"]["cover_image"] == JAVBUS_COVER
    assert result["record"]["backdrop_images"] == [SAMPLE_ONE, SAMPLE_TWO]
    assert result["record"]["share_date"] == "2024-08-09"
    assert result["record"]["directors"] == [{"name": "Synthetic Director"}]
    assert result["record"]["production_companies"] == [{"name": "Synthetic Producer"}]
    assert result["record"]["video_duration"] == 120
    assert result["actors"] == [
        {"id": "star-1", "name": "葵つかさ"}, {"id": "star-2", "name": "miru"},
    ]
    assert all("avdbs_actor" not in actor for actor in result["actors"])

    projected = project_record(result["record"], result["number"], result["actors"],
                               ("pics.dmm.co.jp",), source="javbus")
    assert projected["poster_path"] is None
    assert projected["backdrop_path"] == SAMPLE_ONE


@pytest.mark.parametrize("samples", [
    pytest.param(None, id="missing"),
    pytest.param("malformed", id="wrong-container"),
    pytest.param([None, "bad", {}, {"alt": SAMPLE_ONE}, {"src": "javascript:alert(1)"}], id="bad-items"),
])
def test_javbus_bad_or_missing_samples_keep_metadata_and_actress_evidence(samples):
    document = {key: value for key, value in API_DETAIL.items() if key != "samples"}
    if samples is not None:
        document["samples"] = samples

    packet = JavBusProvider(api_url="http://api.example.test")._parse_api_payload(document, "SONE-288")

    assert packet is not None
    assert packet["record"]["_id"] == "SONE-288"
    assert packet["record"]["title"] == "Synthetic title"
    assert packet["record"]["cover_image"] == JAVBUS_COVER
    assert packet["record"].get("backdrop_images", []) == []
    assert packet["_actress_evidence"]["names"] == ["葵つかさ", "miru"]


def test_javbus_samples_are_bounded_to_first_eight_valid_unique_sources():
    samples = [{"src": f"https://pics.dmm.co.jp/sample/{index}.jpg"} for index in range(10)]
    packet = JavBusProvider(api_url="http://api.example.test")._parse_api_payload(
        {**API_DETAIL, "samples": samples}, "SONE-288")

    assert packet is not None
    assert packet["record"]["backdrop_images"] == [
        f"https://pics.dmm.co.jp/sample/{index}.jpg" for index in range(8)
    ]


def test_javbus_project_record_never_leaks_unapproved_cover_or_sample_hosts():
    packet = JavBusProvider(api_url="http://api.example.test")._parse_api_payload(API_DETAIL, "SONE-288")

    projected = project_record(packet["record"], packet["number"], packet["actors"],
                               ("image.healertanker.com",), source="javbus")

    assert projected["poster_path"] is None
    assert projected["backdrop_path"] is None
    assert "javbus.com" not in str(projected)
    assert "pics.dmm.co.jp" not in str(projected)


def test_javbus_plugin_hook_keeps_metadata_and_evidence_when_images_are_unapproved(
        monkeypatch, tmp_path, plugin_host_context):
    from types import SimpleNamespace
    from app.plugins.xiaojiejiescraping import XiaojiejieScraping
    from app.schemas.types import MediaType

    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path / "data")
    monkeypatch.setattr(RequestUtils, "request", lambda *_args, **_kwargs: APIResponse(API_DETAIL))
    plugin = XiaojiejieScraping()
    plugin.init_plugin({
        "enabled": True, "sources": ["javbus"], "javbus_api_url": "http://api.example.test",
        "image_hosts": "image.healertanker.com",
    })

    result = plugin.recognize_media(
        meta=SimpleNamespace(org_string="SONE-288", type=MediaType.MOVIE), media_source="javbus")

    assert result is not None
    assert result.title == "Synthetic title"
    assert result.poster_path is None
    assert result.backdrop_path is None
    assert plugin.actress_evidence(result, "SONE-288") == (["葵つかさ", "miru"], ["葵つかさ", "miru"])


def _valid_jpeg_bytes():
    from io import BytesIO
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (800, 534), (32, 96, 160)).save(buffer, format="JPEG")
    return buffer.getvalue()


class RelayResponse:
    status_code = 200

    def __init__(self, body, content_type="image/jpeg", content_length=None):
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.body = body
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size > 0
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class RelayClient:
    instances = []
    next_response = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.closed = False
        self.__class__.instances.append(self)

    def get_res(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.__class__.next_response

    def close(self):
        self.closed = True


def _signed_relay_request(relay, url, generation=7, hosts=("www.javbus.com", "pics.dmm.co.jp")):
    from urllib.parse import parse_qs, urlsplit

    signed = relay.signed_url(url, generation, hosts)
    assert signed is not None
    query = parse_qs(urlsplit(signed).query)
    return query["url"][0], int(query["expires"][0]), query["signature"][0]


def test_javbus_artwork_relay_signed_image_has_fixed_referer_and_closes_resources(monkeypatch):
    from app.plugins.xiaojiejiescraping import artwork_relay
    from app.sdk.config import settings
    from fastapi import Response

    state = {"value": (True, True, 7, ("www.javbus.com", "pics.dmm.co.jp"))}
    relay = artwork_relay.JavBusArtworkRelay(lambda: state["value"])
    relay.rotate(7)
    url, expires, signature = _signed_relay_request(relay, JAVBUS_COVER)
    body = _valid_jpeg_bytes()
    RelayClient.instances.clear()
    RelayClient.next_response = RelayResponse(body)
    monkeypatch.setattr(artwork_relay, "AnonymousRequestUtils", RelayClient)

    response = relay.fetch(url, expires, signature)

    assert isinstance(response, Response)
    assert response.status_code == 200
    assert response.body == body
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=300"
    client = RelayClient.instances[-1]
    assert client.kwargs["timeout"] <= 8
    assert client.kwargs["verify"] is True
    assert client.kwargs["proxies"] == settings.PROXY
    assert client.calls == [(JAVBUS_COVER, {
        "headers": {"Referer": "https://www.javbus.com/"},
        "allow_redirects": False, "stream": True,
    })]
    assert RelayClient.next_response.closed and client.closed


@pytest.mark.parametrize("failure", [
    "tampered-url", "expired", "future", "revoked-host", "disabled", "unselected", "generation",
])
def test_javbus_artwork_relay_rejects_invalid_signatures_before_network(monkeypatch, failure):
    from app.plugins.xiaojiejiescraping import artwork_relay
    from fastapi import HTTPException

    state = {"value": (True, True, 7, ("www.javbus.com",))}
    relay = artwork_relay.JavBusArtworkRelay(lambda: state["value"])
    relay.rotate(7)
    url, expires, signature = _signed_relay_request(relay, JAVBUS_COVER, hosts=("www.javbus.com",))
    if failure == "tampered-url":
        url = url.replace("ao2z_b.jpg", "other.jpg")
    elif failure == "expired":
        expires = int(__import__("time").time()) - 1
    elif failure == "future":
        expires = int(__import__("time").time()) + artwork_relay.MAX_TTL_SECONDS + 1
    elif failure == "revoked-host":
        state["value"] = (True, True, 7, ("image.healertanker.com",))
    elif failure == "disabled":
        state["value"] = (False, True, 7, ("www.javbus.com",))
    elif failure == "unselected":
        state["value"] = (True, False, 7, ("www.javbus.com",))
    else:
        relay.rotate(8)
        state["value"] = (True, True, 8, ("www.javbus.com",))

    def unexpected(**_kwargs):
        pytest.fail("invalid artwork request must fail before constructing a network client")

    monkeypatch.setattr(artwork_relay, "AnonymousRequestUtils", unexpected)
    with pytest.raises(HTTPException) as error:
        relay.fetch(url, expires, signature)
    assert error.value.status_code == 404


@pytest.mark.parametrize(("status", "content_type", "body", "content_length"), [
    (302, "image/jpeg", b"", None),
    (200, "text/html", b"<html>challenge</html>", None),
    (200, "image/svg+xml", b"<svg/>", None),
    (200, "image/jpeg", b"<html>not jpeg</html>", None),
    (200, "image/jpeg", b"x", 8 * 1024 * 1024 + 1),
    (200, "image/jpeg", b"x" * (8 * 1024 * 1024 + 1), None),
])
def test_javbus_artwork_relay_rejects_redirect_mime_and_oversize_bodies(
        monkeypatch, status, content_type, body, content_length):
    from app.plugins.xiaojiejiescraping import artwork_relay
    from fastapi import HTTPException

    state = {"value": (True, True, 7, ("www.javbus.com",))}
    relay = artwork_relay.JavBusArtworkRelay(lambda: state["value"])
    relay.rotate(7)
    url, expires, signature = _signed_relay_request(relay, JAVBUS_COVER, hosts=("www.javbus.com",))
    RelayClient.instances.clear()
    response = RelayResponse(body, content_type=content_type, content_length=content_length)
    response.status_code = status
    RelayClient.next_response = response
    monkeypatch.setattr(artwork_relay, "AnonymousRequestUtils", RelayClient)

    with pytest.raises(HTTPException) as error:
        relay.fetch(url, expires, signature)

    assert error.value.status_code == 502
    assert response.closed
    assert RelayClient.instances[-1].closed


def test_javbus_artwork_routes_are_the_only_anonymous_plugin_routes(monkeypatch, tmp_path, plugin_host_context):
    from app.plugins.xiaojiejiescraping import XiaojiejieScraping

    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path / "data")
    plugin = XiaojiejieScraping()
    plugin.init_plugin({"enabled": True, "sources": ["javbus"], "javbus_api_url": "http://api.example.test"})
    routes = plugin.get_api()
    anonymous = [route for route in routes if route.get("allow_anonymous") is True]

    assert len(anonymous) == 1
    assert anonymous[0]["path"] == "/artwork"
    assert anonymous[0]["methods"] == ["GET"]
    assert all(route.get("auth") == "bear" for route in routes if route is not anonymous[0])


def test_javbus_search_media_fields_are_signed_but_metadata_img_keeps_original_urls(
        monkeypatch, tmp_path, plugin_host_context):
    from urllib.parse import parse_qs, urlsplit
    from types import SimpleNamespace
    from app.plugins.xiaojiejiescraping import XiaojiejieScraping
    from app.schemas.types import MediaType

    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path / "data")
    monkeypatch.setattr(RequestUtils, "request", lambda *_args, **_kwargs: APIResponse(API_DETAIL))
    monkeypatch.setattr("app.plugins.xiaojiejiescraping.is_usable_landscape", lambda _url: False)
    plugin = XiaojiejieScraping()
    plugin.init_plugin({
        "enabled": True, "sources": ["javbus"], "javbus_api_url": "http://api.example.test",
        "image_hosts": "www.javbus.com\npics.dmm.co.jp",
    })
    old_cache_key = "javbus:number:SONE-288"
    old_id_cache_key = "javbus:id:SONE-288"
    plugin._cache.put(old_cache_key, {"packet": {"stale": True}, "projected": {"title": "stale"}})
    plugin._cache.put(old_id_cache_key, {"packet": {"stale_id": True}, "projected": {"title": "stale"}})

    result = plugin.recognize_media(
        meta=SimpleNamespace(org_string="SONE-288", type=MediaType.MOVIE), media_source="javbus")

    assert result is not None
    poster = urlsplit(result.poster_path)
    backdrop = urlsplit(result.backdrop_path)
    assert poster.path == backdrop.path == "/api/v1/plugin/XiaojiejieScraping/artwork"
    assert parse_qs(poster.query)["url"] == [JAVBUS_COVER]
    assert parse_qs(backdrop.query)["url"] == [SAMPLE_ONE]
    assert plugin._cache.get(old_cache_key)["packet"] == {"stale": True}
    assert plugin._cache.get(old_id_cache_key)["packet"] == {"stale_id": True}
    assert plugin._cache.get("javbus:number:artwork-v1:SONE-288")["packet"]["number"] == "SONE-288"
    assert plugin._cache.get("javbus:id:artwork-v1:SONE-288")["packet"]["record"]["cover_image"] == JAVBUS_COVER
    id_request = plugin._request(None, None, None, "javbus", "SONE-288", explicit_source=True)
    assert id_request[3] == "javbus:id:artwork-v1:SONE-288"
    images = plugin.metadata_img(result)
    assert images["poster.jpg"] == JAVBUS_COVER
    assert "fanart.jpg" not in images

    detail_result = plugin.obtain_images(result)
    assert detail_result is result
    assert urlsplit(result.poster_path).path == "/api/v1/plugin/XiaojiejieScraping/artwork"
    assert parse_qs(urlsplit(result.poster_path).query)["url"] == [JAVBUS_COVER]
    assert result.backdrop_path is None


def test_javbus_obtain_images_clears_raw_urls_if_host_revoked_after_selection(
        monkeypatch, tmp_path, plugin_host_context):
    from types import SimpleNamespace
    from app.plugins.xiaojiejiescraping import XiaojiejieScraping
    from app.schemas.types import MediaType

    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path / "data")
    monkeypatch.setattr(RequestUtils, "request", lambda *_args, **_kwargs: APIResponse(API_DETAIL))
    monkeypatch.setattr(
        "app.plugins.xiaojiejiescraping.is_usable_landscape", lambda url: url == SAMPLE_ONE)
    plugin = XiaojiejieScraping()
    config = {
        "enabled": True, "sources": ["javbus"], "javbus_api_url": "http://api.example.test",
        "image_hosts": "www.javbus.com\npics.dmm.co.jp",
    }
    plugin.init_plugin(config)
    result = plugin.recognize_media(
        meta=SimpleNamespace(org_string="SONE-288", type=MediaType.MOVIE), media_source="javbus")
    assert result is not None

    select_artwork = plugin._selected_artwork
    selected = {}

    def select_then_revoke(data):
        images = select_artwork(data)
        selected.update(images)
        # Revoke both image hosts in the gap between selection and relay signing.
        plugin.init_plugin({**config, "image_hosts": ""})
        return images

    monkeypatch.setattr(plugin, "_selected_artwork", select_then_revoke)
    assert plugin.obtain_images(result) is result

    assert selected["poster.jpg"] == JAVBUS_COVER
    assert selected["fanart.jpg"] == SAMPLE_ONE
    assert result.poster_path is None
    assert result.backdrop_path is None
    assert JAVBUS_COVER not in str(result.poster_path)
    assert SAMPLE_ONE not in str(result.backdrop_path)


def test_javbus_api_packet_carries_ordered_source_specific_actress_evidence():
    document = {**API_DETAIL, "stars": [
        {"id": "star-2", "name": "miru"},
        {"id": "star-1", "name": "葵つかさ"},
        {"id": "star-2", "name": "miru"},
        {"id": "../unsafe", "name": "Unsafe/Directory"},
        {"id": "star-3", "name": "Unsafe/Directory"},
    ]}

    packet = JavBusProvider(api_url="http://api.example.test")._parse_api_payload(document, "SONE-288")

    assert packet["_actress_evidence"] == {
        "source": "javbus", "media_id": "SONE-288", "number": "SONE-288",
        "names": ["miru", "葵つかさ"],
    }
    assert packet["actors"] == [
        {"id": "star-2", "name": "miru"},
        {"id": "star-1", "name": "葵つかさ"},
        {"id": "star-2", "name": "miru"},
        {"id": "../unsafe", "name": "Unsafe/Directory"},
        {"id": "star-3", "name": "Unsafe/Directory"},
    ]
    assert all("avdbs_actor" not in actor for actor in packet["actors"])


@pytest.mark.parametrize("stars", [
    [{"id": "star-1", "name": "miru"}, {"id": "star-1", "name": "葵つかさ"}],
    [{"id": "star-1", "name": "miru"}, {"id": "star-2", "name": "miru"}],
])
def test_javbus_conflicting_star_directory_identities_leave_metadata_but_no_candidates(stars):
    document = {**API_DETAIL, "stars": stars}

    packet = JavBusProvider(api_url="http://api.example.test")._parse_api_payload(document, "SONE-288")

    assert packet is not None
    assert packet["record"]["_id"] == "SONE-288"
    assert [actor["name"] for actor in packet["actors"]] == [star["name"] for star in stars]
    assert packet["_actress_evidence"]["names"] == []


HTML = """<html><body>
<div class="container"><h3>SONE-288 Synthetic title</h3>
<a class="bigImage"><img src="https://images.example.test/cover.jpg"></a>
<div class="col-md-3 info">
  <p><span>識別碼:</span><span>SONE-288</span></p>
  <p><span>發行日期:</span> 2024-03-01</p>
</div>
<div class="star-name"><a title="Synthetic Actor" href="/star/synthetic">Actor</a></div>
</div></body></html>"""


class SyntheticResponse:
    status_code = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def __init__(self, body):
        self.content = body.encode("utf-8")
        self.closed = False

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True



def test_javbus_search_does_not_fallback_to_html(monkeypatch):
    calls = []
    responses = []

    def respond(_client, method, url, **kwargs):
        assert method == "get"
        assert url == "http://api.example.test/api/movies/SONE-288"
        assert kwargs.get("allow_redirects") is False
        response = SyntheticResponse(HTML)
        calls.append((method, url))
        responses.append(response)
        return response

    monkeypatch.setattr(RequestUtils, "request", respond)
    result = JavBusProvider(api_url="http://api.example.test").search("SONE-288")

    assert calls == [("get", "http://api.example.test/api/movies/SONE-288")]
    assert all(response.closed for response in responses)
    assert result is None


def test_javbus_reports_safe_sync_and_async_access_reasons(monkeypatch):
    import asyncio
    from app.plugins.xiaojiejiescraping import diagnostics
    from app.sdk.network import AsyncRequestUtils

    class ReplayResponse:
        def __init__(self, status, location, body="private synthetic body"):
            self.status_code = status
            self.headers = {"Content-Type": "application/json", "Location": location}
            self.content = body.encode("utf-8")
            self.closed = False

        def close(self):
            self.closed = True

        async def aclose(self):
            self.closed = True

        def __bool__(self):
            # Match requests.Response: HTTP error statuses are falsey.
            return isinstance(self.status_code, int) and self.status_code < 400

    current = {"response": None}
    messages = []

    def sync_request(_client, method, url, **kwargs):
        assert method == "get" and url == "http://api.example.test/api/movies/SONE-288"
        assert kwargs.get("allow_redirects") is False
        return current["response"]

    async def async_request(_client, method, url, **kwargs):
        assert method == "get" and url == "http://api.example.test/api/movies/SONE-288"
        assert kwargs.get("follow_redirects") is False
        return current["response"]

    monkeypatch.setattr(RequestUtils, "request", sync_request)
    monkeypatch.setattr(AsyncRequestUtils, "request", async_request)
    monkeypatch.setattr(diagnostics.logger, "info", messages.append)

    cases = [
        (302, "/doc/driver-verify?token=private-query", "private synthetic body", "http_redirect"),
        (429, "", "private synthetic body", "rate_limited"),
        (404, "", "private synthetic body", "not_found"),
        (200, "", "<html><title>Login</title><input name=\"password\"></html>", "invalid_json"),
        (None, "", "private synthetic body", "transport_unavailable"),
    ]
    for status, location, body, reason in cases:
        for asynchronous in (False, True):
            response = ReplayResponse(status, location, body) if status is not None else None
            current["response"] = response
            messages.clear()
            provider = JavBusProvider(api_url="http://api.example.test")
            result = (asyncio.run(provider.async_search("SONE-288")) if asynchronous
                      else provider.search("SONE-288"))
            assert result is None
            assert any(f"source=javbus operation=search reason={reason}" in line for line in messages)
            assert not any("private synthetic body" in line or "private-query" in line for line in messages)
            if response is not None:
                assert response.closed




def test_javbus_failure_is_captured_for_manual_feedback_without_response_data(monkeypatch):
    from app.plugins.xiaojiejiescraping.diagnostics import capture_query_diagnostics, diagnose

    messages = []
    monkeypatch.setattr("app.plugins.xiaojiejiescraping.diagnostics.logger.info", messages.append)
    with capture_query_diagnostics() as events:
        diagnose("javbus", "search", "rate_limited", status=429)
        diagnose("javbus", "search", "response_invalid")

    assert events == [("javbus", "search", "rate_limited", 429),
                      ("javbus", "search", "response_invalid", None)]
    assert all("private" not in line and "https://" not in line for line in messages)


def test_javbus_api_rejects_html_login_or_challenge_page(monkeypatch):
    import asyncio
    from app.plugins.xiaojiejiescraping import diagnostics
    from app.sdk.network import AsyncRequestUtils

    body = HTML.replace("</body>", "<div class='modal'><form><input type='password'></form></div>"
                        "<script>captcha verification widget</script></body>")
    messages = []

    class Response(SyntheticResponse):
        headers = {"Content-Type": "text/html; charset=utf-8"}

    def response(*_args, **_kwargs):
        return Response(body)

    async def async_response(*_args, **_kwargs):
        return Response(body)

    monkeypatch.setattr(RequestUtils, "request", response)
    monkeypatch.setattr(AsyncRequestUtils, "request", async_response)
    monkeypatch.setattr(diagnostics.logger, "info", messages.append)
    for asynchronous in (False, True):
        messages.clear()
        provider = JavBusProvider(api_url="http://api.example.test")
        result = asyncio.run(provider.async_search("SONE-288")) if asynchronous else provider.search("SONE-288")
        assert result is None
        assert any("reason=response_invalid" in line for line in messages)


def test_javbus_known_network_exceptions_become_safe_failure(monkeypatch):
    import asyncio
    import httpx
    import requests
    from app.plugins.xiaojiejiescraping import diagnostics
    from app.sdk.network import AsyncRequestUtils
    from app.plugins.xiaojiejiescraping.diagnostics import capture_query_diagnostics

    messages = []
    monkeypatch.setattr(diagnostics.logger, "info", messages.append)

    def sync_request(*_args, **_kwargs):
        raise requests.Timeout("private synthetic timeout detail")

    async def async_request(*_args, **_kwargs):
        raise httpx.ConnectError("private synthetic connection detail",
                                 request=httpx.Request("GET", "http://api.example.test/api/movies/SONE-288"))

    monkeypatch.setattr(RequestUtils, "request", sync_request)
    monkeypatch.setattr(AsyncRequestUtils, "request", async_request)
    for asynchronous in (False, True):
        messages.clear()
        provider = JavBusProvider(api_url="http://api.example.test")
        with capture_query_diagnostics() as events:
            result = asyncio.run(provider.async_search("SONE-288")) if asynchronous else provider.search("SONE-288")
        assert result is None
        assert ("javbus", "search", "network_unavailable", None) in events
        assert any("reason=network_unavailable" in line for line in messages)
        assert not any("private synthetic" in line or "SONE-288" in line for line in messages)


def test_javbus_manual_preview_uses_fixed_429_message(monkeypatch, tmp_path, plugin_host_context):
    from app.plugins.xiaojiejiescraping import XiaojiejieScraping

    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    video = source / "SONE-288.mkv"
    video.write_bytes(b"synthetic media")
    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path / "data")
    plugin = XiaojiejieScraping()
    plugin.init_plugin({"enabled": True, "sources": ["javbus"], "javbus_api_url": "http://api.example.test",
                        "recognize_dirs": [str(source)]})

    class TooManyRequests:
        status_code = 429
        headers = {"Content-Type": "text/html"}
        content = b"private upstream body"
        def close(self):
            pass

    monkeypatch.setattr(RequestUtils, "request", lambda *_args, **_kwargs: TooManyRequests())
    from app.plugins.xiaojiejiescraping.manual import ManualPreviewRequest
    preview = plugin._manual.preview(ManualPreviewRequest(
        path=str(video), target_path=str(target), media_source="javbus"))
    assert preview.summary.failed == 1
    assert preview.items[0].message == "JavBus 限流（HTTP 429），请稍后重试"


@pytest.mark.parametrize(("api_url", "reason"), [
    ("", "configuration_missing"),
    ("http://user:secret@api.example.test", "configuration_invalid"),
    ("https://api.example.test/path?token=secret", "configuration_invalid"),
])
def test_javbus_bad_api_configuration_fails_before_request(monkeypatch, api_url, reason):
    from app.plugins.xiaojiejiescraping.diagnostics import capture_query_diagnostics

    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid API configuration must not request")

    monkeypatch.setattr(RequestUtils, "request", unexpected)
    provider = JavBusProvider(api_url=api_url)
    with capture_query_diagnostics() as events:
        assert provider.search("SONE-288") is None
    assert ("javbus", "search", reason, None) in events


@pytest.mark.parametrize(("change", "reason"), [
    (lambda value: {**value, "id": "SONE-289"}, "identity_mismatch"),
    (lambda value: {**value, "title": "  "}, "schema_invalid"),
    (lambda value: {**value, "stars": "葵つかさ"}, "schema_invalid"),
    (lambda value: {**value, "date": "2024-02-30"}, "schema_invalid"),
])
def test_javbus_invalid_api_document_fails_closed(monkeypatch, change, reason):
    from app.plugins.xiaojiejiescraping.diagnostics import capture_query_diagnostics

    monkeypatch.setattr(RequestUtils, "request", lambda *_args, **_kwargs: APIResponse(change(API_DETAIL)))
    with capture_query_diagnostics() as events:
        assert JavBusProvider(api_url="http://api.example.test").search("SONE-288") is None
    assert any(event[2] == reason for event in events)


def test_javbus_retries_one_transient_server_error_then_parses(monkeypatch):
    calls = []

    class RetryResponse(APIResponse):
        def __init__(self, status, document):
            super().__init__(document)
            self.status_code = status

    def respond(*_args, **_kwargs):
        calls.append(1)
        return RetryResponse(500, {}) if len(calls) == 1 else APIResponse(API_DETAIL)

    monkeypatch.setattr(RequestUtils, "request", respond)
    result = JavBusProvider(api_url="http://api.example.test").search("SONE-288")
    assert result is not None
    assert len(calls) == 2


def test_plugin_form_and_provider_keep_api_configuration_opt_in(monkeypatch, tmp_path, plugin_host_context):
    from app.plugins.xiaojiejiescraping import XiaojiejieScraping

    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path / "data")
    plugin = XiaojiejieScraping()
    plugin.init_plugin({"enabled": True})
    assert plugin._active_sources == ("manko",)
    assert plugin._providers["javbus"].api_url == ""
    fields, defaults = plugin.get_form()
    assert defaults["sources"] == ["manko"]
    assert defaults["javbus_api_url"] == ""
    assert any(field.get("props", {}).get("model") == "javbus_api_url" for field in fields)

    plugin.init_plugin({"enabled": True, "javbus_api_url": "http://api.example.test"})
    assert plugin._providers["javbus"].api_url == "http://api.example.test"


def test_javbus_root_api_url_without_trailing_slash_is_valid():
    provider = JavBusProvider(api_url="http://192.168.100.133:8922")
    assert provider.api_url == "http://192.168.100.133:8922"
    assert provider._detail_api_url("SONE-288") == "http://192.168.100.133:8922/api/movies/SONE-288"


def test_javbus_retries_sdk_httpx2_request_error(monkeypatch):
    calls = []

    class RequestError(Exception):
        __module__ = "httpx2._exceptions"

    async def respond(*_args, **_kwargs):
        calls.append(1)
        raise RequestError("private detail")

    monkeypatch.setattr(AsyncRequestUtils, "request", respond)
    result = asyncio.run(JavBusProvider(api_url="http://api.example.test").async_search("SONE-288"))
    assert result is None
    assert len(calls) == 2
