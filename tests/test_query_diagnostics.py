"""诊断的离线回放：分辨空结果来源，不输出请求、响应或异常中的私密文本。"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.plugins.xiaojiejiescraping import XiaojiejieScraping, diagnostics, provider, transport  # pylint: disable=import-error,no-name-in-module
from app.sdk.network import AsyncRequestUtils, RequestUtils

PRIVATE = "https://user:password@private.example.test/secret?token=secret\nAuthorization: private Cookie: private /private/nas/title"


@pytest.fixture
def messages(monkeypatch):
    result = []
    monkeypatch.setattr(diagnostics.logger, "info", result.append)
    yield result
    output = "\n".join(result)
    for private in ("private", "secret", "password", "Authorization", "Cookie", "TEST-123", "fixture_native_123"):
        assert private not in output
    assert all("\n" not in line and "\r" not in line for line in result)


class Response:
    def __init__(self, payload, status=200, *, asynchronous=False, invalid_json=False):
        self.payload, self.status_code = payload, status
        self.asynchronous, self.invalid_json = asynchronous, invalid_json
        self.closed = False

    def __bool__(self):
        # 保留 requests 和 httpx 响应的既有真值差异。
        return self.asynchronous or type(self.status_code) is not int or self.status_code < 400

    def json(self):
        if self.invalid_json:
            raise ValueError(PRIVATE)
        return deepcopy(self.payload)

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


@pytest.fixture
def replay(monkeypatch, sample_payload):
    state = SimpleNamespace(
        payloads={"/search": {"data": [{"_id": "fixture_native_123"}], "total": 1},
                  "/detail/fixture_native_123": {"data": sample_payload["record"]},
                  "/actors/fixture_native_123": {"data": sample_payload["actors"]}},
        status=200, invalid_json=False, unavailable=False, error=False, calls=[], responses=[],
    )

    def respond(method, url, kwargs, asynchronous):
        assert method == "get"
        assert kwargs.get("follow_redirects" if asynchronous else "allow_redirects") is False
        assert not any(key.startswith("diagnostic") for key in kwargs)
        suffix = url.removeprefix(provider.API)
        state.calls.append(suffix)
        if state.error:
            raise RuntimeError(PRIVATE)
        if state.unavailable:
            return None
        response = Response(state.payloads[suffix], state.status, asynchronous=asynchronous, invalid_json=state.invalid_json)
        state.responses.append(response)
        return response

    def request(_client, method, url, **kwargs):
        return respond(method, url, kwargs, False)

    async def async_request(_client, method, url, **kwargs):
        return respond(method, url, kwargs, True)

    monkeypatch.setattr(RequestUtils, "request", request)
    monkeypatch.setattr(AsyncRequestUtils, "request", async_request)
    yield state
    assert all(response.closed for response in state.responses)


def search(asynchronous):
    client = provider.MankoProvider()
    return asyncio.run(client.async_search("TEST-123")) if asynchronous else client.search("TEST-123")


def has_reason(messages, reason):
    return any(f"reason={reason}" in line for line in messages)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("payload,reason", [
    ({"data": [], "total": 0}, "no_candidates"),
    ({"data": {}}, "schema_invalid"),
    ({"data": [None], "total": 1}, "schema_invalid"),
    ({"data": [{"_id": "fixture_native_123"}]}, "search_incomplete"),
    ({"data": [{"_id": "fixture_native_123"}], "total": 2}, "search_incomplete"),
    ({"data": [{"_id": PRIVATE}], "total": 1}, "invalid_id"),
    ({"data": "not base91 😊"}, "payload_invalid"),
    ({"error": PRIVATE}, "payload_invalid"),
    ([], "payload_invalid"),
    (None, "payload_invalid"),
])
def test_search_failure_reasons(messages, replay, asynchronous, payload, reason):
    replay.payloads["/search"] = payload
    assert search(asynchronous) is None
    assert has_reason(messages, reason)
    assert not has_reason(messages, "no_exact_match")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("case,reason", [
    ("no_match", "no_exact_match"), ("ambiguous", "ambiguous_match"),
    ("wrong_identity", "identity_mismatch"), ("detail_schema", "schema_invalid"),
])
def test_detail_and_match_reasons(messages, replay, sample_payload, asynchronous, case, reason):
    if case == "no_match":
        replay.payloads["/detail/fixture_native_123"] = {"data": {"number": "TEST-124"}}
    elif case == "ambiguous":
        replay.payloads["/search"] = {"data": [{"_id": "fixture_native_123"}, {"_id": "second"}], "total": 2}
        replay.payloads["/detail/second"] = {"data": {**sample_payload["record"], "_id": "second"}}
    elif case == "wrong_identity":
        replay.payloads["/detail/fixture_native_123"] = {"data": {"_id": PRIVATE}}
    else:
        replay.payloads["/detail/fixture_native_123"] = {"data": []}
    assert search(asynchronous) is None
    assert has_reason(messages, reason)
    assert not any(path.startswith("/actors/") for path in replay.calls)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_success_and_optional_actor_failure(messages, replay, sample_payload, asynchronous):
    replay.payloads["/actors/fixture_native_123"] = {"data": {"unexpected": PRIVATE}}
    assert search(asynchronous) == {**sample_payload, "actors": []}
    assert has_reason(messages, "exact_match")
    assert has_reason(messages, "actors_invalid")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("status", [200, 302, 403, 429, 500, None, PRIVATE, True])
def test_http_status_is_observed_without_changing_sdk_truthiness(messages, replay, asynchronous, status):
    replay.status = status
    client = transport.AnonymousAsyncRequestUtils() if asynchronous else transport.AnonymousRequestUtils()
    arguments = {"diagnostic_source": "manko", "diagnostic_operation": "search",
                 "follow_redirects" if asynchronous else "allow_redirects": False}
    result = client.get_json(provider.API + "/search", **arguments)
    result = asyncio.run(result) if asynchronous else result
    expected = replay.payloads["/search"] if asynchronous or type(status) is not int or status < 400 else None
    assert result == expected
    if type(status) is int:
        assert any(f"http_status={status}" in line for line in messages)
        assert has_reason(messages, "http_error" if status >= 400 else "http_redirect" if status >= 300 else "http_response")
    else:
        assert not any("http_status=" in line for line in messages)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("case,reason", [("unavailable", "transport_unavailable"), ("invalid_json", "invalid_json"), ("error", "request_exception")])
def test_http_failure_does_not_log_exception_or_body(messages, replay, asynchronous, case, reason):
    setattr(replay, case, True)
    if case == "error":
        with pytest.raises(RuntimeError):
            search(asynchronous)
    else:
        assert search(asynchronous) is None
    assert has_reason(messages, reason)
    if case != "invalid_json":
        assert not any("http_status=" in line for line in messages)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_missing_sdk_status_is_not_invented(messages, replay, monkeypatch, asynchronous):
    response = Response({"data": [], "total": 0})
    del response.status_code
    monkeypatch.setattr(Response, "__bool__", lambda _: True)
    monkeypatch.setattr(RequestUtils, "request", lambda *args, **kwargs: response)

    async def request(*_args, **_kwargs):
        return response

    monkeypatch.setattr(AsyncRequestUtils, "request", request)
    assert search(asynchronous) is None
    assert has_reason(messages, "http_response")
    assert not any("http_status=" in line for line in messages)
    assert response.closed


@pytest.mark.parametrize("source,operation,reason,status,count", [
    (PRIVATE, PRIVATE, PRIVATE, PRIVATE, PRIVATE),
    (None, [], {}, True, False),
    ("manko", "search", "no_candidates", 99, -1),
    ("manko", "search", "no_candidates", 600, 10001),
])
def test_allowlist_redacts_every_field(messages, source, operation, reason, status, count):
    diagnostics.diagnose(source, operation, reason, status=status, count=count)
    assert len(messages) == 1
    assert "http_status=" not in messages[0] and "count=" not in messages[0]


@pytest.fixture
def plugin(monkeypatch, tmp_path, plugin_host_context):
    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path)
    instance = XiaojiejieScraping()
    instance.init_plugin({"enabled": True})
    yield instance
    instance.stop_service()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_manual_manko_search_returns_each_exact_ambiguous_native_id(messages, replay, plugin, asynchronous):
    first_id = "69a07400131836087e481b7a"
    second_id = "69a3697d1b1225b605c52f5f"
    first = {
        "_id": first_id,
        "category_id": "5f75927b5c425008d254a788",
        "share_date": "2026-02-27",
        "title_display_name": [{"lang": "en", "title": "ADN-765"}, {"lang": "ja", "title": "ADN-765"}],
    }
    second = {
        "_id": second_id,
        "category_id": "638ba0b6e6248f567f04b84c",
        "share_date": "2026-02-27",
        "title_display_name": [{"lang": "en", "title": "ADN-765"}, {"lang": "ja", "title": "ADN-765"}],
    }
    replay.payloads.update({
        "/search": {"data": [{"_id": first_id}, {"_id": second_id}], "total": 2},
        f"/detail/{first_id}": {"data": first},
        f"/detail/{second_id}": {"data": second},
        f"/actors/{first_id}": {"data": []},
        f"/actors/{second_id}": {"data": []},
    })
    arguments = {"meta": SimpleNamespace(org_string="ADN-765 synthetic.mkv", type=None), "media_source": "manko"}
    results = asyncio.run(plugin.async_search_medias(**arguments)) if asynchronous else plugin.search_medias(**arguments)

    assert [(item.media_source.value, item.media_id) for item in results] == [
        ("manko", first_id), ("manko", second_id),
    ]
    number_key = "manko:number:ADN-765"
    assert plugin._cache.get(number_key) is None

    def recognize(**kwargs):
        return asyncio.run(plugin.async_recognize_media(**kwargs)) if asynchronous else plugin.recognize_media(**kwargs)

    for identity in (first_id, second_id):
        selected = recognize(media_source="manko", media_id=identity)
        assert selected.media_source.value == "manko" and selected.media_id == identity
        assert plugin._cache.get(number_key) is None
    assert recognize(meta=arguments["meta"], media_source="manko", cache=True) is None
    assert plugin._cache.get(number_key) is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_manual_manko_search_deduplicates_duplicate_native_ids(messages, replay, plugin, asynchronous):
    identity = "69a07400131836087e481b7a"
    record = {
        "_id": identity,
        "category_id": "5f75927b5c425008d254a788",
        "share_date": "2026-02-27",
        "title_display_name": [{"lang": "en", "title": "ADN-765"}],
    }
    replay.payloads.update({
        "/search": {"data": [{"_id": identity}, {"_id": identity}], "total": 2},
        f"/detail/{identity}": {"data": record},
        f"/actors/{identity}": {"data": []},
    })
    arguments = {"meta": SimpleNamespace(org_string="ADN-765 synthetic.mkv", type=None), "media_source": "manko"}
    results = asyncio.run(plugin.async_search_medias(**arguments)) if asynchronous else plugin.search_medias(**arguments)

    assert [(item.media_source.value, item.media_id) for item in results] == [("manko", identity)]
    assert replay.calls.count(f"/detail/{identity}") == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("case", ["incomplete_total", "detail_identity", "two_numbers"])
def test_manual_manko_search_rejects_incomplete_or_unverified_candidates(messages, replay, plugin, asynchronous, case):
    identity = "69a07400131836087e481b7a"
    record = {
        "_id": identity,
        "category_id": "5f75927b5c425008d254a788",
        "share_date": "2026-02-27",
        "title_display_name": [{"lang": "en", "title": "ADN-765"}],
    }
    total = 1
    if case == "incomplete_total":
        total = 2
    elif case == "detail_identity":
        record["_id"] = "69a3697d1b1225b605c52f5f"
    else:
        record["title_display_name"] = [{"lang": "en", "title": "ADN-765 XYZ-123"}]
    replay.payloads.update({
        "/search": {"data": [{"_id": identity}], "total": total},
        f"/detail/{identity}": {"data": record},
        f"/actors/{identity}": {"data": []},
    })
    arguments = {"meta": SimpleNamespace(org_string="ADN-765 synthetic.mkv", type=None), "media_source": "manko"}

    results = asyncio.run(plugin.async_search_medias(**arguments)) if asynchronous else plugin.search_medias(**arguments)

    assert results == []
    if case == "incomplete_total":
        assert replay.calls == ["/search"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_manual_manko_search_rejects_results_after_configuration_changes(monkeypatch, messages, plugin, asynchronous):
    identity = "69a07400131836087e481b7a"
    packet = {
        "record": {
            "_id": identity,
            "category_id": "5f75927b5c425008d254a788",
            "share_date": "2026-02-27",
            "title_display_name": [{"lang": "en", "title": "ADN-765"}],
        },
        "number": "ADN-765",
        "actors": [],
    }
    request_provider = plugin._provider
    if asynchronous:
        async def search_matches(_number):
            plugin.init_plugin({"enabled": True})
            return [packet]
        monkeypatch.setattr(request_provider, "async_search_matches", search_matches)
        results = asyncio.run(plugin.async_search_medias(
            meta=SimpleNamespace(org_string="ADN-765 synthetic.mkv", type=None), media_source="manko",
        ))
    else:
        def search_matches(_number):
            plugin.init_plugin({"enabled": True})
            return [packet]
        monkeypatch.setattr(request_provider, "search_matches", search_matches)
        results = plugin.search_medias(
            meta=SimpleNamespace(org_string="ADN-765 synthetic.mkv", type=None), media_source="manko",
        )

    assert results == []
    assert has_reason(messages, "configuration_changed")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("case,reason", [("disabled", "disabled"), ("unselected", "source_unselected"), ("input", "number_not_unique"), ("source", "source_unselected")])
def test_plugin_rejections_do_not_reach_network(messages, replay, plugin, asynchronous, case, reason):
    arguments = {"meta": SimpleNamespace(org_string="TEST-123", type=None), "media_source": "manko"}
    if case == "disabled":
        plugin.init_plugin({"enabled": False})
    elif case == "unselected":
        plugin.init_plugin({"enabled": True, "sources": ["fanza"]})
    elif case == "input":
        arguments["meta"] = SimpleNamespace(org_string=PRIVATE, type=None)
    else:
        arguments["media_source"] = PRIVATE
    result = asyncio.run(plugin.async_search_medias(**arguments)) if asynchronous else plugin.search_medias(**arguments)
    assert result == [] and replay.calls == []
    assert has_reason(messages, reason)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_plugin_results_and_cache_have_safe_diagnostics(messages, replay, plugin, asynchronous):
    arguments = {"meta": SimpleNamespace(org_string="/private/nas/TEST-123.mkv", type=None), "media_source": "manko"}
    def run():
        return asyncio.run(plugin.async_recognize_media(**arguments)) if asynchronous else plugin.recognize_media(**arguments)
    assert run() is not None
    assert has_reason(messages, "cache_miss") and has_reason(messages, "accepted")
    calls = list(replay.calls)
    assert run() is not None
    assert replay.calls == calls and has_reason(messages, "cache_hit")


@pytest.mark.parametrize("asynchronous", [False, True])
def test_plugin_exception_is_safe_and_keeps_empty_result(messages, replay, plugin, asynchronous):
    replay.error = True
    arguments = {"meta": SimpleNamespace(org_string="TEST-123", type=None), "media_source": "manko"}
    result = asyncio.run(plugin.async_search_medias(**arguments)) if asynchronous else plugin.search_medias(**arguments)
    assert result == []
    assert has_reason(messages, "request_exception") and has_reason(messages, "query_exception")


@pytest.mark.parametrize("asynchronous", [False, True])
def test_logger_failure_does_not_change_result(monkeypatch, replay, sample_payload, asynchronous):
    def unavailable(_message):
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(diagnostics.logger, "info", unavailable)
    assert search(asynchronous) == sample_payload
