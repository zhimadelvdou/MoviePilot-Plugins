"""GFriends 官方文件树协议的合成回放；不抓取或展示演员图片。"""
import asyncio
from copy import deepcopy
from urllib.parse import quote
from xml.etree import ElementTree as ET
from types import SimpleNamespace

import pytest

from app.plugins.xiaojiejiescraping import gfriends  # pylint: disable=import-error,no-name-in-module
from app.sdk.network import AsyncRequestUtils, RequestUtils


@pytest.fixture
def manifest():
    """字段结构及质量顺序来自官方 README；姓名和文件名均为合成值。"""
    return {
        "Information": {"TotalNum": "3", "Timestamp": "123456789"},
        "Content": {
            "Low Quality": {"Synthetic Alias.jpg": "Synthetic Original.jpg?t=100"},
            "High Quality 合成": {
                "Synthetic Alias.jpg": "Synthetic Original.jpg?t=200",
                "Synthetic Original.jpg": "Synthetic Original.jpg?t=200",
            },
        },
    }


def test_exact_alias_selects_last_match_in_official_quality_order(manifest):
    expected = gfriends.CONTENT_URL + quote("High Quality 合成", safe="") + "/Synthetic%20Original.jpg?t=200"
    assert gfriends.actor_image(manifest, "Synthetic Alias") == expected
    assert gfriends.actor_image(manifest, "Synthetic Original") == expected
    assert gfriends.actor_image(manifest, "Synthetic") is None
    assert gfriends.actor_image(manifest, "synthetic alias") is None
    assert gfriends.actor_image(manifest, "Synthetic Aliases") is None


def test_quality_priority_is_insertion_order_not_alphabetical():
    data = {"Content": {
        "Z first": {"Synthetic Actor.jpg": "first.jpg"},
        "A last": {"Synthetic Actor.jpg": "last.jpg"},
    }}
    assert gfriends.actor_image(data, "Synthetic Actor") == gfriends.CONTENT_URL + "A%20last/last.jpg"


@pytest.mark.parametrize("name", [None, "", " Synthetic Alias", "Synthetic Alias ", ".", "..", "../actor", "a/b", "a\\b", "a\x00b", "a\nb"])
def test_invalid_actor_names_are_not_normalized_into_a_match(name):
    data = {"Content": {"Safe": {f"{name}.jpg": "safe.jpg"}}}
    assert gfriends.actor_image(data, name) is None


@pytest.mark.parametrize("filename", [
    "https://evil.example/actor.jpg", "//evil.example/actor.jpg", "//[broken/actor.jpg", "/actor.jpg",
    "../actor.jpg", "subdir/actor.jpg", "subdir\\actor.jpg", "actor.png",
    "actor.jpg#fragment", "actor.jpg?t=", "actor.jpg?token=secret",
    "actor.jpg?t=123&x=1", "actor.jpg?t=-123", "actor.jpg?t=12.3",
    "actor.jpg?t=１２３", "actor.jpg?t=²", "a\x00b.jpg", "a\nb.jpg", "\tactor.jpg",
])
def test_unsafe_targets_do_not_override_an_earlier_safe_image(filename):
    data = {"Content": {
        "Safe": {"Synthetic Actor.jpg": "safe.jpg?t=123"},
        "Unsafe": {"Synthetic Actor.jpg": filename},
    }}
    assert gfriends.actor_image(data, "Synthetic Actor") == gfriends.CONTENT_URL + "Safe/safe.jpg?t=123"


@pytest.mark.parametrize("company", ["", ".", "..", "../outside", "a/b", "a\\b", "a\x00b", "a\nb", "a\tb"])
def test_unsafe_company_components_are_rejected(company):
    assert gfriends.actor_image({"Content": {company: {"Synthetic Actor.jpg": "actor.jpg"}}}, "Synthetic Actor") is None


def test_unicode_spaces_and_literal_percent_are_encoded_as_single_segments():
    data = {"Content": {"合成 % Studio": {"Synthetic Actor.jpg": "合成 % Photo.JPG?t=123"}}}
    result = gfriends.actor_image(data, "Synthetic Actor")
    assert result == gfriends.CONTENT_URL + quote("合成 % Studio", safe="") + "/" + quote("合成 % Photo.JPG", safe="") + "?t=123"


@pytest.mark.parametrize("data", [None, [], {}, {"Content": []}, {"Content": {"Company": []}}, {"Content": {"Company": {"Synthetic Actor.jpg": None}}}])
def test_malformed_manifest_is_a_safe_miss(data):
    assert gfriends.actor_image(data, "Synthetic Actor") is None


def test_oversized_company_index_is_not_traversed():
    data = {"Content": {str(index): {} for index in range(1001)}}
    assert gfriends.actor_image(data, "Synthetic Actor") is None


@pytest.fixture
def index_replay(monkeypatch, manifest):
    state = {"payload": manifest, "calls": [], "responses": []}

    class Response:
        def __init__(self, payload):
            self.payload, self.closed = payload, False

        def json(self):
            return deepcopy(self.payload)

        def close(self):
            self.closed = True

        async def aclose(self):
            self.closed = True

    def respond(client, method, url, kwargs, asynchronous):
        assert method == "get"
        assert url == gfriends.INDEX_URL
        assert client._timeout == 30
        assert client._verify is True
        assert kwargs.get("follow_redirects" if asynchronous else "allow_redirects") is False
        state["calls"].append((asynchronous, client._proxies, url))
        response = Response(state["payload"])
        state["responses"].append(response)
        return response

    def request(client, method, url, **kwargs):
        return respond(client, method, url, kwargs, False)

    async def async_request(client, method, url, **kwargs):
        return respond(client, method, url, kwargs, True)

    monkeypatch.setattr(RequestUtils, "request", request)
    monkeypatch.setattr(AsyncRequestUtils, "request", async_request)
    yield state
    assert all(response.closed for response in state["responses"])


@pytest.mark.parametrize("asynchronous", [False, True])
def test_official_index_only_inherits_dynamic_system_proxy(index_replay, monkeypatch, manifest, asynchronous):
    client = gfriends.GFriendsProvider()
    for proxy in ("http://127.0.0.1:18080", "http://127.0.0.1:28080"):
        monkeypatch.setattr(gfriends.settings, "PROXY_HOST", proxy)
        result = asyncio.run(client.async_fetch_index()) if asynchronous else client.fetch_index()
        assert result == manifest
        expected = proxy if asynchronous else {"http": proxy, "https": proxy}
        assert index_replay["calls"][-1] == (asynchronous, expected, gfriends.INDEX_URL)
        assert gfriends.actor_image(result, "Synthetic Alias") is not None
    assert len(index_replay["calls"]) == 2
    assert all(url == gfriends.INDEX_URL for _, _, url in index_replay["calls"])


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("payload", [None, [], {}, {"Content": []}, {"error": "synthetic unavailable"}])
def test_invalid_index_response_is_not_reported_as_success(index_replay, payload, asynchronous):
    index_replay["payload"] = payload
    client = gfriends.GFriendsProvider()
    result = asyncio.run(client.async_fetch_index()) if asynchronous else client.fetch_index()
    assert result is None
    assert len(index_replay["calls"]) == 1


@pytest.fixture
def actor_pipeline(monkeypatch, tmp_path, plugin_host_context, sample_payload, manifest):
    from app.plugins.xiaojiejiescraping import XiaojiejieScraping  # pylint: disable=import-error,no-name-in-module

    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path)
    packet = deepcopy(sample_payload)
    packet["actors"] = [{"name": "Synthetic Alias"}, {"name": "Unknown Actor"}]

    class MetadataProvider:
        def __init__(self):
            self.calls = []

        def search(self, number):
            self.calls.append(number)
            return deepcopy(packet)

        async def async_search(self, number):
            return self.search(number)

    class IndexProvider:
        def __init__(self):
            self.calls, self.error, self.before_return = [], None, None

        def response(self, mode):
            self.calls.append(mode)
            if self.before_return:
                self.before_return()
            if self.error:
                raise self.error
            return deepcopy(manifest)

        def fetch_index(self):
            return self.response("sync")

        async def async_fetch_index(self):
            return self.response("async")

    plugin, metadata, index = XiaojiejieScraping(), MetadataProvider(), IndexProvider()

    def configure(**changes):
        config = {"enabled": True, "actor_images": True, "image_hosts": ["images.example.test", "raw.githubusercontent.com"], **changes}
        plugin.init_plugin(config)
        plugin._provider, plugin._gfriends = metadata, index
        return config

    configure()
    yield plugin, metadata, index, configure, packet
    plugin.stop_service()


def actor_recognize(plugin, asynchronous, cache=True):
    arguments = {"meta": SimpleNamespace(org_string="TEST-123 synthetic", type=None), "media_source": "manko", "cache": cache}
    return asyncio.run(plugin.async_recognize_media(**arguments)) if asynchronous else plugin.recognize_media(**arguments)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_opted_in_actor_pipeline_enriches_exact_alias_and_reuses_manifest(actor_pipeline, tmp_path, asynchronous):
    plugin, metadata, index, configure, original = actor_pipeline
    result = actor_recognize(plugin, asynchronous)
    expected = gfriends.CONTENT_URL + quote("High Quality 合成", safe="") + "/Synthetic%20Original.jpg?t=200"
    assert result.actors[0]["profile_path"] == expected
    assert result.actors[1]["profile_path"] is None
    assert all("profile_path" not in actor for actor in original["actors"])
    document = ET.fromstring(plugin.metadata_nfo(mediainfo=result))
    assert document.findtext("actor/thumb") == expected
    assert document.findall("actor")[1].find("thumb") is None
    assert index.calls == ["async" if asynchronous else "sync"]
    assert (tmp_path / "actors-v1.json").is_file()
    assert actor_recognize(plugin, asynchronous, cache=False).actors[0]["profile_path"] == expected
    assert len(metadata.calls) == 2 and len(index.calls) == 1
    configure()
    assert actor_recognize(plugin, asynchronous, cache=False).actors[0]["profile_path"] == expected
    assert len(index.calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("changes", [{"actor_images": False}, {"image_hosts": ["images.example.test"]}])
def test_actor_images_require_both_optin_and_approved_host(actor_pipeline, tmp_path, changes, asynchronous):
    plugin, _, index, configure, _ = actor_pipeline
    configure(**changes)
    result = actor_recognize(plugin, asynchronous)
    assert result is not None
    assert all(actor.get("profile_path") is None for actor in result.actors)
    assert index.calls == []
    assert not (tmp_path / "actors-v1.json").exists()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_actor_index_failure_does_not_discard_verified_metadata(actor_pipeline, tmp_path, asynchronous):
    plugin, _, index, _, _ = actor_pipeline
    index.error = ConnectionError("synthetic index unavailable")
    result = actor_recognize(plugin, asynchronous)
    assert result.media_id == "fixture_native_123"
    assert all(actor.get("profile_path") is None for actor in result.actors)
    assert not (tmp_path / "actors-v1.json").exists()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("change", ["stop", "reload"])
def test_actor_manifest_completion_after_lifecycle_change_cannot_commit(actor_pipeline, tmp_path, asynchronous, change):
    plugin, _, index, configure, _ = actor_pipeline
    index.before_return = plugin.stop_service if change == "stop" else configure
    result = actor_recognize(plugin, asynchronous)
    assert result is None
    assert not (tmp_path / "actors-v1.json").exists()
    assert not (tmp_path / "metadata-v1.json").exists()


def test_revoked_image_host_cannot_remain_in_cached_nfo(actor_pipeline):
    plugin, _, index, configure, _ = actor_pipeline
    original = actor_recognize(plugin, False)
    assert "raw.githubusercontent.com" in plugin.metadata_nfo(mediainfo=original)
    configure(image_hosts=["images.example.test"])
    cached = actor_recognize(plugin, False)
    assert all(actor.get("profile_path") is None for actor in cached.actors)
    assert "raw.githubusercontent.com" not in plugin.metadata_nfo(mediainfo=cached)
    assert len(index.calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_disabling_actor_images_removes_gfriends_enrichment_from_persisted_metadata(actor_pipeline, asynchronous):
    plugin, metadata, index, configure, _ = actor_pipeline
    original = actor_recognize(plugin, asynchronous)
    packet = plugin._cache.get("manko:id:fixture_native_123")["packet"]
    assert packet["actors"][0]["_image_source"] == "gfriends"
    assert original.actors[0]["profile_path"].startswith(gfriends.CONTENT_URL)
    configure(actor_images=False)
    cached = actor_recognize(plugin, asynchronous)
    assert all(actor.get("profile_path") is None for actor in cached.actors)
    assert ET.fromstring(plugin.metadata_nfo(mediainfo=original)).find("actor/thumb") is None
    assert len(index.calls) == 1 and len(metadata.calls) == 1
    # 当前输出被撤销，但缓存事实保留，重新选择时无需污染或重写来源原始数据。
    assert plugin._cache.get("manko:id:fixture_native_123")["packet"]["actors"][0]["_image_source"] == "gfriends"
