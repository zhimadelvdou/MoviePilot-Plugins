"""JavBus anonymous-source contract tests using synthetic HTTP responses."""
from app.plugins.xiaojiejiescraping.direct_secondary import JavBusProvider
from app.sdk.network import RequestUtils


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



def test_javbus_search_accepts_star_name_actor_markup(monkeypatch):
    calls = []
    responses = []

    def respond(_client, method, url, **kwargs):
        assert method == "get"
        assert url == "https://www.javbus.com/SONE-288"
        assert kwargs.get("allow_redirects") is False
        response = SyntheticResponse(HTML)
        calls.append((method, url))
        responses.append(response)
        return response

    monkeypatch.setattr(RequestUtils, "request", respond)
    result = JavBusProvider().search("SONE-288")

    assert calls == [("get", "https://www.javbus.com/SONE-288")]
    assert all(response.closed for response in responses)
    assert result is not None
    assert result["record"]["_id"] == "SONE-288"
    assert result["number"] == "SONE-288"
    assert result["record"]["title"] == "SONE-288 Synthetic title"
    assert result["record"]["share_date"] == "2024-03-01"
    assert result["actors"] == [{"name": "Synthetic Actor"}]


def test_javbus_reports_safe_sync_and_async_access_reasons(monkeypatch):
    import asyncio
    from app.plugins.xiaojiejiescraping import diagnostics
    from app.sdk.network import AsyncRequestUtils

    class ReplayResponse:
        def __init__(self, status, location, body="private synthetic body"):
            self.status_code = status
            self.headers = {"Content-Type": "text/html", "Location": location}
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
        assert method == "get" and url == "https://www.javbus.com/SONE-288"
        assert kwargs.get("allow_redirects") is False
        return current["response"]

    async def async_request(_client, method, url, **kwargs):
        assert method == "get" and url == "https://www.javbus.com/SONE-288"
        assert kwargs.get("follow_redirects") is False
        return current["response"]

    monkeypatch.setattr(RequestUtils, "request", sync_request)
    monkeypatch.setattr(AsyncRequestUtils, "request", async_request)
    monkeypatch.setattr(diagnostics.logger, "info", messages.append)

    cases = [
        (302, "/doc/driver-verify?token=private-query", "private synthetic body", "access_verification_required"),
        (200, "", "<html><title>Login</title><input name=\"password\"></html>", "login_required"),
        (200, "", "<html><title>Security Verification</title><div id='captcha'></div></html>", "access_verification_required"),
        (429, "", "private synthetic body", "rate_limited"),
        (302, "/login?token=private-query", "private synthetic body", "login_required"),
        (302, "https://external.example.test/login?token=private-query", "private synthetic body", "access_restricted"),
        (404, "", "private synthetic body", "not_found"),
        (None, "", "private synthetic body", "network_unavailable"),
    ]
    for status, location, body, reason in cases:
        for asynchronous in (False, True):
            response = ReplayResponse(status, location, body) if status is not None else None
            current["response"] = response
            messages.clear()
            provider = JavBusProvider()
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


def test_javbus_details_with_login_modal_or_challenge_script_still_parse(monkeypatch):
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
        provider = JavBusProvider()
        result = asyncio.run(provider.async_search("SONE-288")) if asynchronous else provider.search("SONE-288")
        assert result is not None
        assert result["number"] == "SONE-288"
        assert not any("reason=login_required" in line or "reason=access_verification_required" in line
                       for line in messages)


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
                                 request=httpx.Request("GET", "https://www.javbus.com/SONE-288"))

    monkeypatch.setattr(RequestUtils, "request", sync_request)
    monkeypatch.setattr(AsyncRequestUtils, "request", async_request)
    for asynchronous in (False, True):
        messages.clear()
        provider = JavBusProvider()
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
    plugin.init_plugin({"enabled": True, "sources": ["javbus"], "recognize_dirs": [str(source)]})

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
