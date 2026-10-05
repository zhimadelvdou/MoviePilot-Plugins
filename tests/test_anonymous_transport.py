"""匿名来源使用真实 requests 请求准备流程，禁止环境凭据和 Cookie 泄漏。"""
import asyncio
from http.client import HTTPMessage
from io import BytesIO
import json
from types import SimpleNamespace

import pytest
import requests

from app.plugins.xiaojiejiescraping import transport, fc2ppvdb  # pylint: disable=import-error,no-name-in-module
from app.sdk.network import AsyncRequestUtils


@pytest.fixture
def prepared_boundary(monkeypatch):
    state = SimpleNamespace(calls=[], sessions=[], closed=[], netrc_calls=[], body=b'{"data": {"synthetic": true}}', content_type="application/json", fail=False)
    monkeypatch.setenv("NETRC", "/synthetic-must-not-be-read/netrc")
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient-proxy.invalid:8888")
    monkeypatch.setenv("HTTP_PROXY", "http://ambient-proxy.invalid:8888")

    def synthetic_netrc(url, *args, **kwargs):
        state.netrc_calls.append(url)
        return "synthetic-user", "synthetic-password"

    monkeypatch.setattr(requests.sessions, "get_netrc_auth", synthetic_netrc)
    monkeypatch.setattr(requests.utils, "get_netrc_auth", synthetic_netrc)
    original_close = requests.Session.close

    def close(session):
        state.closed.append(session)
        return original_close(session)

    def send(session, prepared, **kwargs):
        assert isinstance(prepared, requests.PreparedRequest)
        assert session.trust_env is False
        assert "Authorization" not in prepared.headers
        assert "Proxy-Authorization" not in prepared.headers
        assert "Cookie" not in prepared.headers
        assert kwargs["verify"] is True
        assert kwargs["allow_redirects"] is False
        assert kwargs["proxies"] == {"http": "http://127.0.0.1:18080", "https": "http://127.0.0.1:18080"}
        state.calls.append((prepared, kwargs))
        state.sessions.append(session)
        if state.fail:
            raise requests.exceptions.ConnectionError("synthetic transport failure")
        response = requests.Response()
        response.status_code = 200
        response.url = prepared.url
        response.request = prepared
        response.headers = {"Content-Type": state.content_type, "Set-Cookie": "synthetic_identity=secret; Path=/; Secure"}
        response._content = state.body
        response._content_consumed = True
        raw = BytesIO(state.body)
        message = HTTPMessage()
        message.add_header("Set-Cookie", "synthetic_identity=secret; Path=/; Secure")
        raw._original_response = SimpleNamespace(msg=message)
        response.raw = raw
        # requests.Session.send 正常会执行这一响应 Cookie 接收步骤。
        requests.cookies.extract_cookies_to_jar(session.cookies, prepared, raw)
        assert len(session.cookies) == 0
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    monkeypatch.setattr(requests.Session, "close", close)
    yield state
    assert state.netrc_calls == []
    assert all(session in state.closed for session in state.sessions)


def explicit_proxy():
    return {"http": "http://127.0.0.1:18080", "https": "http://127.0.0.1:18080"}


def test_anonymous_json_uses_real_prepared_request_without_environment_identity(prepared_boundary):
    client = transport.AnonymousRequestUtils(verify=True, proxies=explicit_proxy())
    result = client.get_json("https://metadata.example.test/index", allow_redirects=False)
    assert result == {"data": {"synthetic": True}}
    assert len(prepared_boundary.calls) == 1
    assert client._anonymous_session in prepared_boundary.closed


def test_server_cookie_is_neither_received_nor_sent_on_next_request(prepared_boundary):
    client = transport.AnonymousRequestUtils(verify=True, proxies=explicit_proxy())
    try:
        for path in ("first", "second"):
            response = client.get_res("https://metadata.example.test/" + path, allow_redirects=False)
            response.close()
        assert len(prepared_boundary.calls) == 2
        assert len(client._anonymous_session.cookies) == 0
    finally:
        client.close()


def test_transport_retry_stays_anonymous_and_closes_owned_session(prepared_boundary):
    prepared_boundary.fail = True
    client = transport.AnonymousRequestUtils(verify=True, proxies=explicit_proxy())
    assert client.get_json("https://metadata.example.test/index", allow_redirects=False) is None
    assert len(prepared_boundary.calls) == 2
    assert client._anonymous_session in prepared_boundary.closed


def test_fc2_real_fetch_wrapper_does_not_inherit_netrc_or_cookies(prepared_boundary, monkeypatch):
    page = {"component": "Articles/Show", "url": "/articles/1234567", "props": {
        "auth": {"user": None}, "errors": {}, "article": {
            "id": 99, "video_id": 1234567, "title": "Synthetic fixture",
            "release_date": "2026-01-02", "image_url": "https://images.example.test/fixture.jpg",
        },
    }}
    prepared_boundary.body = ('<html><body><script type="application/json" data-page="app">'
                              + json.dumps(page) + '</script></body></html>').encode()
    prepared_boundary.content_type = "text/html; charset=UTF-8"
    monkeypatch.setattr(fc2ppvdb.settings, "PROXY_HOST", "http://127.0.0.1:18080")
    result = fc2ppvdb.FC2PPVDBProvider().fetch("1234567")
    assert result["record"]["_id"] == "1234567"
    assert result["record"]["_source"] == "fc2ppvdb"
    assert len(prepared_boundary.calls) == 1
    assert prepared_boundary.calls[0][0].url == "https://fc2cmadb.com/articles/1234567"


def test_async_transport_explicitly_disables_environment_identity(monkeypatch):
    observed = []

    class Response:
        def json(self):
            return {"synthetic": True}

        async def aclose(self):
            observed.append("closed")

    async def request(client, method, url, **kwargs):
        assert client._trust_env is False
        assert client._verify is True
        assert client._proxies == "http://127.0.0.1:18080"
        assert method == "get" and url == "https://metadata.example.test/index"
        assert kwargs["follow_redirects"] is False
        observed.append("request")
        return Response()

    monkeypatch.setattr(AsyncRequestUtils, "request", request)
    client = transport.AnonymousAsyncRequestUtils(verify=True, proxies=explicit_proxy())
    assert asyncio.run(client.get_json("https://metadata.example.test/index", follow_redirects=False)) == {"synthetic": True}
    assert observed == ["request", "closed"]
