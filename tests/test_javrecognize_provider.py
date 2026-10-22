"""在真实 SDK HTTP 边界回放合成数据，禁止真实服务请求。"""
import asyncio
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

# 生产命名空间由共享测试引导动态扩展，静态分析无法发现仓库外插件。
from app.plugins.xiaojiejiescraping import provider  # pylint: disable=import-error,no-name-in-module
from app.sdk.network import AsyncRequestUtils, RequestUtils


class ReplayResponse:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def json(self):
        return deepcopy(self.payload)

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


@pytest.fixture
def replay(monkeypatch, sample_payload):
    """保留真实 SDK 构造与 JSON/关闭逻辑，只替换最终 HTTP request 边界。"""
    state = {
        "responses": {
            "/search": {"data": [{"_id": "fixture_native_123"}], "total": 1},
            "/detail/fixture_native_123": {"data": sample_payload["record"]},
            "/actors/fixture_native_123": {"data": sample_payload["actors"]},
        },
        "calls": [],
        "closed": [],
        "proxies": [],
    }

    def respond(client, method, url, **kwargs):
        assert method == "get"
        assert url.startswith(provider.API + "/")
        assert client._verify is True
        assert client._timeout == 7
        suffix = url.removeprefix(provider.API)
        assert suffix in state["responses"], f"unexpected request: {suffix}"
        state["calls"].append((suffix, kwargs))
        state["proxies"].append(deepcopy(client._proxies))
        response = ReplayResponse(state["responses"][suffix])
        state["closed"].append(response)
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
    assert all(response.closed for response in state["closed"])


@pytest.mark.parametrize("asynchronous", [False, True])
def test_search_roundtrip_uses_real_sdk_and_only_number(replay, sample_payload, asynchronous):
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_search("TEST-123")) if asynchronous else client.search("TEST-123")
    assert result == sample_payload
    assert [suffix for suffix, _ in replay["calls"]] == [
        "/search", "/detail/fixture_native_123", "/actors/fixture_native_123",
    ]
    assert replay["calls"][0][1]["params"] == {"keyword": "TEST-123", "size": 24, "page": 1}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_id_roundtrip_preserves_identity(replay, sample_payload, asynchronous):
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_fetch("fixture_native_123")) if asynchronous else client.fetch("fixture_native_123")
    assert result == sample_payload
    assert [suffix for suffix, _ in replay["calls"]] == [
        "/detail/fixture_native_123", "/actors/fixture_native_123",
    ]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_two_exact_native_ids_are_ambiguous(replay, sample_payload, asynchronous):
    replay["responses"]["/search"] = {"data": [{"_id": "fixture_native_123"}, {"_id": "other_native"}], "total": 2}
    replay["responses"]["/detail/other_native"] = {
        "data": {**sample_payload["record"], "_id": "other_native", "title": "TEST-123 different title"}
    }
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_search("TEST-123")) if asynchronous else client.search("TEST-123")
    assert result is None
    assert all(not suffix.startswith("/actors/") for suffix, _ in replay["calls"])


@pytest.mark.parametrize("response", [
    {"data": [{"_id": "fixture_native_123"}], "total": 25},
    {"data": [{"_id": "fixture_native_123"}], "total": "25"},
    {"data": [{"_id": "fixture_native_123"}] * 25},
    {"data": {"_id": "fixture_native_123"}}, {"error": "unavailable"},
    {"data": [{"_id": "fixture_native_123"}]},
    {"data": [{"_id": "fixture_native_123"}], "total": None},
    {"data": [{"_id": "fixture_native_123"}], "total": True},
    {"data": [{"_id": "fixture_native_123"}], "total": False},
    {"data": [{"_id": "fixture_native_123"}], "total": -1},
    {"data": [{"_id": "fixture_native_123"}], "total": 0},
    {"data": [{"_id": "fixture_native_123"}, None], "total": 2},
    {"data": [{"_id": "fixture_native_123"}, "not a row"], "total": 2},
    {"data": [{"_id": "fixture_native_123"}], "total": 1.0},
])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_truncated_or_malformed_search_is_not_a_match(replay, response, asynchronous):
    replay["responses"]["/search"] = response
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_search("TEST-123")) if asynchronous else client.search("TEST-123")
    assert result is None
    assert len(replay["calls"]) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_wrong_detail_identity_fails_closed(replay, asynchronous):
    replay["responses"]["/detail/fixture_native_123"] = {"data": {"_id": "different", "number": "TEST-123"}}
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_fetch("fixture_native_123")) if asynchronous else client.fetch("fixture_native_123")
    assert result is None
    assert len(replay["calls"]) == 1


@pytest.mark.parametrize("identity", ["../private", "https://evil.example", "native/id", "", "x" * 129])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_invalid_native_id_does_not_reach_http(replay, identity, asynchronous):
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_fetch(identity)) if asynchronous else client.fetch(identity)
    assert result is None
    assert replay["calls"] == []


@pytest.mark.parametrize("number", ["/private/folder/TEST-123.mkv", "TEST-123 full title", "TEST-123 TEST-124", ""])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_search_does_not_transmit_paths_or_unvalidated_titles(replay, number, asynchronous):
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_search(number)) if asynchronous else client.search(number)
    assert result is None
    assert replay["calls"] == []


def test_decoder_accepts_plain_json_and_rejects_bad_or_large_payloads():
    assert provider.decode_payload({"data": [{"_id": "native"}], "total": 1}) == {
        "data": [{"_id": "native"}], "total": 1,
    }
    for invalid in (None, [], {"data": "not base91 😊"}, {"data": "A" * 2_000_001}, {"data": 123}):
        assert provider.decode_payload(invalid) == {}


def test_decoder_replays_base91_encoded_synthetic_fixture():
    encoded = "~G?e4+B5BGV6bybpdPk1v)R1;%Os9YASMiBw1]7N;RpwFZ1dT2^d1i~RCz!"
    assert provider.decode_payload({"data": encoded}) == {
        "data": {"_id": "fixture_native_123", "number": "TEST-123"},
    }


@pytest.mark.parametrize("asynchronous", [False, True])
def test_system_proxy_is_read_for_every_request_and_after_change(replay, monkeypatch, asynchronous):
    client = provider.MankoProvider(timeout=7)
    for proxy in ("http://127.0.0.1:18080", "http://127.0.0.1:28080"):
        monkeypatch.setattr(provider.settings, "PROXY_HOST", proxy)
        result = asyncio.run(client.async_search("TEST-123")) if asynchronous else client.search("TEST-123")
        assert result is not None
        expected = proxy if asynchronous else {"http": proxy, "https": proxy}
        assert replay["proxies"][-3:] == [expected] * 3


@pytest.mark.parametrize("asynchronous", [False, True])
def test_system_proxy_absent_does_not_invent_provider_proxy(replay, monkeypatch, asynchronous):
    monkeypatch.setattr(provider.settings, "PROXY_HOST", None)
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(variable, raising=False)
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_search("TEST-123")) if asynchronous else client.search("TEST-123")
    assert result is not None
    assert replay["proxies"] == [None, None, None]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_scoped_pipeline_through_real_provider_and_sdk(replay, monkeypatch, tmp_path, scope_context, plugin_host_context, asynchronous):
    """组合真实范围检查、来源适配、SDK、投影、缓存及 NFO，仅在 HTTP 边界回放。"""
    from app.plugins.xiaojiejiescraping import XiaojiejieScraping  # pylint: disable=import-error,no-name-in-module
    from app.sdk.media import MediaInfo, MetaInfo

    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path)
    plugin = XiaojiejieScraping()
    plugin.init_plugin({
        "enabled": True, "recognize_dirs": [str(Path(scope_context.path).parent)],
        "image_hosts": ["images.example.test"],
    })
    plugin._provider = provider.MankoProvider(timeout=7)
    try:
        metadata = MetaInfo("TEST-123 synthetic fixture.mkv")
        result = asyncio.run(plugin.async_recognize_scoped(scope_context, metadata)) if asynchronous else plugin.recognize_scoped(scope_context, metadata)
        assert isinstance(result, MediaInfo)
        assert result.media_source.value == "manko"
        assert result.media_id == "fixture_native_123"
        assert result.tmdb_id is None
        assert len(replay["calls"]) == 3
        document = ET.fromstring(plugin.metadata_nfo(metadata, result))
        assert document.findtext("uniqueid") == "fixture_native_123"
        assert set(plugin.metadata_img(result)) == {"poster.jpg"}
        cached = plugin.recognize_scoped(scope_context, media_source="manko", media_id="fixture_native_123")
        assert cached.media_id == result.media_id
        assert len(replay["calls"]) == 3
    finally:
        plugin.stop_service()


_EQUIVALENT_TRANSPORT_FIELDS = {
    "_id", "category_id", "jable_hls", "missav_video", "mmtv_embed",
    "mmtv_m3u8", "mmtv_time", "njav_player", "upload18_player",
    "wasabi_embed_url", "wasabi_url_1",
}


def _install_equivalent_records(replay, ids):
    common = {
        "number": "TEST-123",
        "title": "TEST-123 synthetic title",
        "title_display_name": [{"language": "en", "title": "TEST-123 synthetic title", "also_known_as": ["Synthetic Alias"]}],
        "share_date": "2026-01-02",
        "cover_image": "https://images.example.test/cover.jpg",
        "backdrop_images": ["https://images.example.test/backdrop.jpg"],
        "discription": [{"language": "en", "overview": "Synthetic plot."}],
        "video_duration": 1,
        "tags": ["synthetic-tag"],
        "imdb_score": "7.0",
        "rating": "synthetic-rating",
    }
    records = {}
    for identity, category in ((ids[0], "category-a"), (ids[1], "category-b")):
        records[identity] = {
            **common,
            "_id": identity,
            "category_id": category,
            "jable_hls": f"https://video.example.test/{identity}/jable",
            "missav_video": f"https://video.example.test/{identity}/missav",
            "mmtv_embed": f"https://video.example.test/{identity}/embed",
            "mmtv_m3u8": f"https://video.example.test/{identity}/stream",
            "mmtv_time": identity,
            "njav_player": f"https://video.example.test/{identity}/njav",
            "upload18_player": f"https://video.example.test/{identity}/upload18",
            "wasabi_embed_url": f"https://video.example.test/{identity}/wasabi-embed",
            "wasabi_url_1": f"https://video.example.test/{identity}/wasabi",
        }

    differences = {
        key for key in set(records[ids[0]]) | set(records[ids[1]])
        if records[ids[0]].get(key) != records[ids[1]].get(key)
    }
    assert differences <= _EQUIVALENT_TRANSPORT_FIELDS

    actors = [{
        "id": "actor-1",
        "name": "Synthetic Actor",
        "also_known_as": ["Synthetic Alias"],
        "profile_path": "https://images.example.test/actor.jpg",
    }]
    replay["responses"].update({
        "/search": {"data": [{"_id": identity} for identity in ids], "total": 2},
        **{f"/detail/{identity}": {"data": record} for identity, record in records.items()},
        **{f"/actors/{identity}": {"data": actors} for identity in ids},
    })
    return records, actors


def _assert_equivalent_search_is_stable(replay, search):
    ids = ("id_a", "id_b")
    records, actors = _install_equivalent_records(replay, ids)

    forward = search("TEST-123")
    replay["calls"].clear()
    replay["responses"]["/search"] = {"data": [{"_id": ids[1]}, {"_id": ids[0]}], "total": 2}
    reverse = search("TEST-123")

    assert forward is not None
    assert reverse == forward
    assert forward["record"] == records[ids[0]]
    assert forward["actors"] == actors
    assert {suffix for suffix, _ in replay["calls"]} >= {
        "/detail/id_a", "/detail/id_b", "/actors/id_a", "/actors/id_b",
    }


def test_equivalent_manko_records_auto_select_stable_id(replay):
    client = provider.MankoProvider(timeout=7)
    _assert_equivalent_search_is_stable(replay, client.search)


def test_async_equivalent_manko_records_auto_select_stable_id(replay):
    client = provider.MankoProvider(timeout=7)

    def search(number):
        return asyncio.run(client.async_search(number))

    _assert_equivalent_search_is_stable(replay, search)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(("field", "value"), [
    ("title", "TEST-123 other title"),
    ("discription", [{"language": "en", "overview": "Different plot."}]),
    ("share_date", "2025-12-31"),
    ("cover_image", "https://images.example.test/other-cover.jpg"),
    ("backdrop_images", ["https://images.example.test/other-backdrop.jpg"]),
    ("video_duration", True),
    ("tags", ["other-tag"]),
    ("imdb_score", "8.0"),
    ("rating", "other-rating"),
    ("unmapped_upstream_field", {"value": "different"}),
])
def test_different_raw_record_fields_stay_ambiguous(replay, asynchronous, field, value):
    ids = ("id_a", "id_b")
    records, _ = _install_equivalent_records(replay, ids)
    records[ids[1]][field] = value
    replay["responses"][f"/detail/{ids[1]}"] = {"data": records[ids[1]]}
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_search("TEST-123")) if asynchronous else client.search("TEST-123")
    assert result is None
    assert not any(suffix.startswith("/actors/") for suffix, _ in replay["calls"])


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("field", ["title", "share_date", "cover_image", "discription"])
def test_incomplete_equivalent_record_stays_ambiguous_before_actor_lookup(replay, asynchronous, field):
    ids = ("id_a", "id_b")
    records, _ = _install_equivalent_records(replay, ids)
    for identity in ids:
        records[identity].pop(field)
        if field == "title":
            records[identity].pop("title_display_name")
        replay["responses"][f"/detail/{identity}"] = {"data": records[identity]}
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_search("TEST-123")) if asynchronous else client.search("TEST-123")
    assert result is None
    assert not any(suffix.startswith("/actors/") for suffix, _ in replay["calls"])


@pytest.mark.parametrize("asynchronous", [False, True])
def test_different_complete_actor_records_stay_ambiguous(replay, asynchronous):
    ids = ("id_a", "id_b")
    _install_equivalent_records(replay, ids)
    replay["responses"][f"/actors/{ids[1]}"] = {"data": [{
        "id": "actor-2", "name": "Different Actor", "also_known_as": [],
        "profile_path": "https://images.example.test/other-actor.jpg",
    }]}
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_search("TEST-123")) if asynchronous else client.search("TEST-123")
    assert result is None
    assert {suffix for suffix, _ in replay["calls"] if suffix.startswith("/actors/")} == {
        f"/actors/{ids[0]}", f"/actors/{ids[1]}",
    }


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("second_actor_response", [
    {"error": "unavailable"}, {"data": []}, {"data": [{"name": ""}]},
    {"data": [{"name": "Synthetic Actor"}, {}]},
    {"data": [{"name": "Synthetic Actor"}], "total": 2},
])
def test_missing_incomplete_or_failed_actor_response_cannot_prove_equivalence(
    replay, asynchronous, second_actor_response
):
    ids = ("id_a", "id_b")
    _install_equivalent_records(replay, ids)
    replay["responses"][f"/actors/{ids[1]}"] = second_actor_response
    client = provider.MankoProvider(timeout=7)
    result = asyncio.run(client.async_search("TEST-123")) if asynchronous else client.search("TEST-123")
    assert result is None
    assert {suffix for suffix, _ in replay["calls"] if suffix.startswith("/actors/")} == {
        f"/actors/{ids[0]}", f"/actors/{ids[1]}",
    }
