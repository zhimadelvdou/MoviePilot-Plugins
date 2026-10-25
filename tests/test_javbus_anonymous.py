"""JavBus anonymous-source contract tests using synthetic HTTP responses."""
import asyncio
import json

import pytest

from app.plugins.xiaojiejiescraping.direct_secondary import JavBusProvider
from app.sdk.network import AsyncRequestUtils, RequestUtils


API_DETAIL = {
    "id": "SONE-288", "title": "Synthetic title", "img": "https://images.example.test/cover.jpg",
    "date": "2024-08-09", "videoLength": "120",
    "director": {"id": "director-1", "name": "Synthetic Director"},
    "producer": {"id": "producer-1", "name": "Synthetic Producer"}, "publisher": None,
    "series": None, "genres": [],
    "stars": [{"id": "star-1", "name": "葵つかさ"}, {"id": "star-2", "name": "miru"}],
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
    assert result["record"]["share_date"] == "2024-08-09"
    assert result["record"]["directors"] == [{"name": "Synthetic Director"}]
    assert result["record"]["production_companies"] == [{"name": "Synthetic Producer"}]
    assert result["record"]["video_duration"] == 120
    assert result["actors"] == [
        {"id": "star-1", "name": "葵つかさ"}, {"id": "star-2", "name": "miru"},
    ]
    assert all("avdbs_actor" not in actor for actor in result["actors"])


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
