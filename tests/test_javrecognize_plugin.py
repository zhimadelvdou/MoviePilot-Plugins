"""真实 V3 SDK 下的来源边界、生命周期、缓存和刮削契约。"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

# 生产命名空间由共享测试引导动态扩展，静态分析无法发现仓库外插件。
from app.plugins.xiaojiejiescraping import XiaojiejieScraping  # pylint: disable=import-error,no-name-in-module
from app.sdk.media import MediaInfo, MetaInfo, build_media_key
from app.plugins.xiaojiejiescraping.scope import RecognitionContext  # pylint: disable=import-error,no-name-in-module
from app.schemas.types import MediaSource, MediaType
from app.runtime.extensions.module.contracts import diagnose_module_callable


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.before_return = None

    def _result(self, method, value):
        self.calls.append((method, value))
        if self.before_return:
            self.before_return()
        return deepcopy(self.payload)

    def search(self, number):
        return self._result("search", number)

    def search_matches(self, number):
        packet = self._result("search_matches", number)
        return [packet] if packet else []

    def fetch(self, identity):
        return self._result("fetch", identity)

    async def async_search(self, number):
        return self._result("async_search", number)

    async def async_search_matches(self, number):
        packet = self._result("async_search_matches", number)
        return [packet] if packet else []

    async def async_fetch(self, identity):
        return self._result("async_fetch", identity)


@pytest.fixture
def plugin(monkeypatch, tmp_path, sample_payload, plugin_host_context, scope_context):
    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path)
    instance = XiaojiejieScraping()
    instance.init_plugin({"enabled": True, "recognize_dirs": [str(Path(scope_context.path).parent)], "image_hosts": ["images.example.test"]})
    instance._provider = FakeProvider(sample_payload)
    yield instance
    instance.stop_service()


def meta(title="[JAV] TEST-123 synthetic.mkv", **kwargs):
    return SimpleNamespace(org_string=title, type=MediaType.MOVIE, **kwargs)


def test_stock_host_abstains_without_explicit_source_and_registers_manual_hooks(plugin, scope_context):
    assert plugin.get_state() is True
    modules = plugin.get_module()
    assert set(modules) == {
        "recognize_media", "async_recognize_media", "search_medias", "async_search_medias",
        "metadata_nfo", "metadata_img", "obtain_images", "async_obtain_images",
    }
    assert plugin.get_media_source()[0]["media_source"] == "manko"
    assert plugin.recognize_media(meta()) is None
    assert asyncio.run(plugin.async_recognize_media(meta())) is None
    assert plugin.recognize_media(media_id="fixture_native_123") is None
    assert plugin._provider.calls == []
    for name, method in modules.items():
        assert diagnose_module_callable(name, method) == ()
    plugin.stop_service()
    assert plugin.get_state() is False
    assert plugin.get_module() == {}
    assert plugin.get_media_source() == []
    assert plugin.recognize_scoped(scope_context, meta()) is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_explicit_manual_source_can_query_without_configured_paths(plugin, sample_payload, asynchronous):
    plugin.init_plugin({"enabled": True})
    fake = FakeProvider(sample_payload)
    plugin._provider = fake
    arguments = {"meta": meta("TEST-123"), "media_source": "manko"}
    result = asyncio.run(plugin.async_recognize_media(**arguments)) if asynchronous else plugin.recognize_media(**arguments)
    assert result.media_source.value == "manko"
    assert result.media_id == "fixture_native_123"
    arguments = {"media_source": "manko", "media_id": "fixture_native_123", "cache": False}
    by_id = asyncio.run(plugin.async_recognize_media(**arguments)) if asynchronous else plugin.recognize_media(**arguments)
    assert by_id.media_id == result.media_id
    assert fake.calls == [("async_search" if asynchronous else "search", "TEST-123"), ("async_fetch" if asynchronous else "fetch", "fixture_native_123")]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("sources", [None, MediaSource.TMDB, (MediaSource.Douban, MediaSource.TMDB), "otherplugin"])
def test_native_search_ignores_unselected_source(plugin, sources, asynchronous):
    result = asyncio.run(plugin.async_search_medias(meta(), media_source=sources)) if asynchronous else plugin.search_medias(meta(), media_source=sources)
    assert result == []
    assert plugin._provider.calls == []


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("sources", ["manko", MediaSource("manko"), (MediaSource.TMDB, MediaSource("manko")), ["manko"]])
def test_native_search_respects_explicit_single_or_multiple_source_selection(plugin, sources, asynchronous):
    result = asyncio.run(plugin.async_search_medias(meta(), media_source=sources)) if asynchronous else plugin.search_medias(meta(), media_source=sources)
    assert len(result) == 1
    assert result[0].media_source.value == "manko"
    assert result[0].media_id == "fixture_native_123"
    assert len(plugin._provider.calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_recognition_preserves_native_identity_and_sdk_roundtrip(plugin, scope_context, asynchronous):
    result = asyncio.run(plugin.async_recognize_scoped(scope_context, meta())) if asynchronous else plugin.recognize_scoped(scope_context, meta())
    assert isinstance(result, MediaInfo)
    assert result.media_source == MediaSource("manko")
    assert result.media_id == "fixture_native_123"
    assert result.tmdb_id is None and result.imdb_id is None
    assert build_media_key(result.media_source, result.media_id) == "manko:fixture_native_123"
    restored = MediaInfo()
    restored.from_dict(result.to_dict())
    assert restored.media_source == result.media_source
    assert restored.media_id == result.media_id
    assert restored.type == MediaType.MOVIE
    assert plugin._provider.calls == [("async_search" if asynchronous else "search", "TEST-123")]


@pytest.mark.parametrize("source", [MediaSource.TMDB, MediaSource.Douban, MediaSource.Bangumi, MediaSource.AniList, "otherplugin"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_other_explicit_sources_never_reach_provider(plugin, scope_context, source, asynchronous):
    arguments = {"meta": meta(), "media_source": source, "media_id": "fixture_native_123"}
    result = asyncio.run(plugin.async_recognize_scoped(scope_context, **arguments)) if asynchronous else plugin.recognize_scoped(scope_context, **arguments)
    assert result is None
    assert plugin._provider.calls == []


@pytest.mark.parametrize("arguments", [
    {"meta": meta("[JAV] ordinary movie")},
    {"meta": meta("[JAV] TEST-123 TEST-124")},
    {"meta": meta(), "mtype": MediaType.TV},
    {"meta": meta(), "mtype": MediaType.MUSIC},
    {"media_id": "fixture_native_123"},
    {"media_source": "manko", "media_id": "../private"},
])
def test_out_of_scope_inputs_fail_without_provider_calls(plugin, scope_context, arguments):
    assert plugin.recognize_scoped(scope_context, **arguments) is None
    assert plugin._provider.calls == []


def test_explicit_source_queries_number_and_native_id_inside_scope(plugin, scope_context):
    assert plugin.recognize_scoped(scope_context, meta("TEST-123"), media_source="manko") is not None
    assert plugin.recognize_scoped(scope_context, media_source="manko", media_id="fixture_native_123", cache=False) is not None
    assert plugin._provider.calls == [("search", "TEST-123"), ("fetch", "fixture_native_123")]


def test_source_id_and_cached_number_cannot_bypass_scope(plugin, scope_context, tmp_path):
    assert plugin.recognize_scoped(scope_context, meta("TEST-123.mkv")) is not None
    calls = list(plugin._provider.calls)
    outside = tmp_path / "outside" / "TEST-123.mkv"
    outside.parent.mkdir()
    outside.write_bytes(b"synthetic fixture")
    invalid = RecognitionContext("transfer", "local", str(outside))
    assert plugin.recognize_scoped(invalid, meta("TEST-123")) is None
    assert plugin.recognize_scoped(invalid, media_source="manko", media_id="fixture_native_123") is None
    assert plugin.recognize_scoped(None, media_source="manko", media_id="fixture_native_123") is None
    assert asyncio.run(plugin.async_recognize_scoped(invalid, media_source="manko", media_id="fixture_native_123")) is None
    assert asyncio.run(plugin.async_recognize_scoped(None, media_source="manko", media_id="fixture_native_123")) is None
    assert plugin._provider.calls == calls


def test_real_sdk_metainfo_and_filename_tag_are_not_scope_authority(plugin, scope_context, tmp_path):
    real_meta = MetaInfo("TEST-123 synthetic fixture.mkv")
    assert plugin.recognize_scoped(scope_context, real_meta) is not None
    forged = meta("[JAV] TEST-123", path=scope_context.path)
    assert plugin.recognize_media(forged) is None
    invalid = RecognitionContext("search", "local", scope_context.path)
    assert plugin.recognize_scoped(invalid, forged, media_source="manko") is None


def test_cached_recognition_and_reload_do_not_call_provider(plugin, scope_context, sample_payload):
    first = plugin.recognize_scoped(scope_context, meta())
    first.title = "host-mutated title"
    first.tmdb_id = 999
    second = plugin.recognize_scoped(scope_context, meta())
    assert second.title != first.title
    assert second.tmdb_id is None
    assert plugin._provider.calls == [("search", "TEST-123")]
    plugin.init_plugin({"enabled": True, "recognize_dirs": [str(Path(scope_context.path).parent)]})
    provider = FakeProvider(sample_payload)
    plugin._provider = provider
    restored = plugin.recognize_scoped(scope_context, media_source="manko", media_id="fixture_native_123")
    assert restored is not None
    assert restored.media_id == "fixture_native_123" and restored.tmdb_id is None
    assert provider.calls == []
    plugin.recognize_scoped(scope_context, media_source="manko", media_id="fixture_native_123", cache=False)
    assert provider.calls == [("fetch", "fixture_native_123")]


def test_cache_hits_do_not_extend_metadata_expiration(plugin, scope_context, monkeypatch):
    from app.plugins.xiaojiejiescraping import core  # pylint: disable=import-error,no-name-in-module

    monkeypatch.setattr(core.time, "time", lambda: 1000)
    assert plugin.recognize_scoped(scope_context, meta()) is not None
    expires = plugin._cache._data["manko:number:TEST-123"]["expires"]
    monkeypatch.setattr(core.time, "time", lambda: 1010)
    assert plugin.recognize_scoped(scope_context, meta()) is not None
    assert plugin._cache._data["manko:number:TEST-123"]["expires"] == expires
    assert len(plugin._provider.calls) == 1
    monkeypatch.setattr(core.time, "time", lambda: expires)
    assert plugin.recognize_scoped(scope_context, meta()) is not None
    assert len(plugin._provider.calls) == 2


@pytest.mark.parametrize("change", ["stop", "reload"])
def test_sync_stale_completion_cannot_write_cache(plugin, scope_context, tmp_path, change):
    provider = plugin._provider
    provider.before_return = plugin.stop_service if change == "stop" else lambda: plugin.init_plugin({"enabled": True})
    assert plugin.recognize_scoped(scope_context, meta()) is None
    assert not (tmp_path / "metadata-v1.json").exists()


@pytest.mark.parametrize("change", ["stop", "reload"])
def test_async_stale_completion_cannot_write_cache(plugin, scope_context, tmp_path, sample_payload, change):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed_search(number):
            assert number == "TEST-123"
            started.set()
            await release.wait()
            return sample_payload

        plugin._provider.async_search = delayed_search
        task = asyncio.create_task(plugin.async_recognize_scoped(scope_context, meta()))
        await started.wait()
        if change == "stop":
            plugin.stop_service()
        else:
            plugin.init_plugin({"enabled": True})
        release.set()
        assert await task is None

    asyncio.run(scenario())
    assert not (tmp_path / "metadata-v1.json").exists()


def test_provider_failure_or_mismatch_is_not_cached(plugin, scope_context, tmp_path, sample_payload):
    plugin._provider.payload = None
    assert plugin.recognize_scoped(scope_context, meta()) is None
    plugin._provider.payload = {**sample_payload, "number": "TEST-124"}
    assert plugin.recognize_scoped(scope_context, meta()) is None
    assert not (tmp_path / "metadata-v1.json").exists()


def test_async_cancel_propagates_without_committing(plugin, scope_context, tmp_path):
    async def scenario():
        started = asyncio.Event()

        async def pending_search(_number):
            started.set()
            await asyncio.Event().wait()

        plugin._provider.async_search = pending_search
        task = asyncio.create_task(plugin.async_recognize_scoped(scope_context, meta()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert not (tmp_path / "metadata-v1.json").exists()


def test_nfo_uses_verified_cache_despite_host_auxiliary_contamination(plugin, scope_context):
    result = plugin.recognize_scoped(scope_context, meta())
    original_title = result.title
    result.title = "unrelated host title"
    result.tmdb_id = 99999
    result.imdb_id = "tt99999"
    result.actors = [{"name": "unverified actor"}]
    result.category = "unverified category"
    nfo = plugin.metadata_nfo(meta(), result)
    document = ET.fromstring(nfo)
    assert document.findtext("title") == original_title
    assert document.findtext("uniqueid") == "fixture_native_123"
    assert "unverified" not in nfo and "tmdb" not in nfo and "imdb" not in nfo
    assert plugin.metadata_nfo(meta(), result, season=1) is None
    assert plugin.metadata_nfo(meta(), result, episode=1) is None
    assert set(plugin.metadata_img(result)) == {"poster.jpg", "fanart.jpg"}


def test_metadata_hooks_ignore_other_sources_without_mutation(plugin, scope_context):
    foreign = MediaInfo(media_source=MediaSource.TMDB, media_id="fixture_native_123", title="ordinary movie", poster_path="keep")
    before = foreign.to_dict()
    assert plugin.metadata_nfo(meta(), foreign) is None
    assert plugin.metadata_img(foreign) is None
    assert plugin.obtain_images(foreign) is None
    assert asyncio.run(plugin.async_obtain_images(foreign)) is None
    assert foreign.to_dict() == before


def test_images_require_explicit_allowlist_and_owned_cache(plugin, scope_context):
    result = plugin.recognize_scoped(scope_context, meta())
    result.poster_path = "https://unverified.example/image.jpg"
    assert plugin.obtain_images(result) is result
    assert result.poster_path.startswith("https://images.example.test/")
    plugin.init_plugin({"enabled": True})
    assert plugin.metadata_img(result) is None
    assert plugin.obtain_images(result) is result
    assert result.poster_path is None and result.backdrop_path is None
    unknown = MediaInfo(media_source=MediaSource("manko"), media_id="uncached")
    assert plugin.metadata_nfo(meta(), unknown) is None
    assert plugin.metadata_img(unknown) is None


def test_real_host_still_dispatches_tmdb_auxiliary_for_plugin_source(plugin, scope_context, monkeypatch):
    """锁定已知宿主风险；插件来源不会自动跳过 TMDB 辅助信息分派。"""
    from app.chain.media.auxiliary import MediaAuxiliaryOwner

    result = plugin.recognize_scoped(scope_context, meta())
    assert result.library_category == "AV"
    auxiliary = MediaInfo(
        media_source=MediaSource.TMDB, media_id="99999", tmdb_id=99999,
        imdb_id="tt99999", genre_ids=[18], title="unrelated auxiliary title",
    )
    owner = object.__new__(MediaAuxiliaryOwner)
    calls = []
    finalizations = []

    def classify(info, **kwargs):
        finalizations.append(kwargs)
        info.set_library_category("host-rule-category")
        return info

    monkeypatch.setattr(owner, "classification_service", SimpleNamespace(finalize=classify), raising=False)
    monkeypatch.setattr(owner, "_resolve_auxiliary_sources", lambda source: (source,))
    monkeypatch.setattr(owner, "run_module", lambda method, **kwargs: calls.append((method, kwargs)) or [auxiliary])
    assert owner.supplement_tmdb_info(result) is result
    assert calls[0][0] == "get_media_auxiliary_info"
    assert calls[0][1]["media_source"] == (MediaSource.TMDB,)
    assert result.media_source == MediaSource("manko")
    assert result.media_id == "fixture_native_123"
    assert result.tmdb_id == 99999 and result.imdb_id == "tt99999"
    assert result.genre_ids == [18]
    assert result.library_category == "host-rule-category"
    assert finalizations == [{"effective_override": None, "refresh": True}]
    nfo = plugin.metadata_nfo(meta(), result)
    assert "99999" not in nfo and "unrelated auxiliary" not in nfo


@pytest.fixture
def multisource_plugin(plugin, sample_payload):
    secondary = deepcopy(sample_payload)
    secondary["record"]["_source"] = "testsource"
    secondary["record"]["title_display_name"] = [{"language": "zh", "title": "Secondary synthetic title"}]
    secondary["record"]["cover_image"] = "https://images.example.test/secondary.jpg"
    plugin._providers["testsource"] = FakeProvider(secondary)
    plugin._active_sources = ("manko", "testsource")
    return plugin


@pytest.mark.parametrize("asynchronous", [False, True])
def test_same_native_id_and_number_are_isolated_between_sources(multisource_plugin, asynchronous):
    plugin = multisource_plugin
    results = {}
    for source in ("manko", "testsource"):
        arguments = {"meta": meta(), "media_source": source}
        result = asyncio.run(plugin.async_recognize_media(**arguments)) if asynchronous else plugin.recognize_media(**arguments)
        assert result.media_source.value == source
        assert result.media_id == "fixture_native_123"
        results[source] = result
    assert results["manko"].title != results["testsource"].title
    assert results["manko"].poster_path != results["testsource"].poster_path
    for source in ("manko", "testsource"):
        cached = plugin.recognize_media(media_source=source, media_id="fixture_native_123")
        assert cached.title == results[source].title
        assert plugin._cache.get(source + ":number:TEST-123")["projected"]["media_source"] == source
        assert plugin._cache.get(source + ":id:fixture_native_123")["projected"]["media_source"] == source
        document = ET.fromstring(plugin.metadata_nfo(meta(), cached))
        assert document.find("uniqueid").get("type") == source
        assert document.findtext("uniqueid") == "fixture_native_123"
        assert document.findtext("title") == results[source].title
        assert len(plugin._adapters()[source].calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_multisource_search_returns_distinct_results_in_selected_order(multisource_plugin, asynchronous):
    plugin = multisource_plugin
    arguments = {"meta": meta(), "media_source": ("testsource", "manko", "testsource")}
    result = asyncio.run(plugin.async_search_medias(**arguments)) if asynchronous else plugin.search_medias(**arguments)
    assert [item.media_source.value for item in result] == ["testsource", "manko"]
    assert len(plugin._provider.calls) == 1
    assert len(plugin._providers["testsource"].calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("first_result", ["miss", "wrong_source", "error", "success"])
def test_scoped_automatic_recognition_uses_ordered_exact_fallback(multisource_plugin, scope_context, asynchronous, first_result):
    plugin = multisource_plugin
    if first_result == "miss":
        plugin._provider.payload = None
    elif first_result == "wrong_source":
        plugin._provider.payload["record"]["_source"] = "other_source"
    elif first_result == "error":
        def unavailable():
            raise ConnectionError("synthetic first provider failure")
        plugin._provider.before_return = unavailable
    result = asyncio.run(plugin.async_recognize_scoped(scope_context, meta())) if asynchronous else plugin.recognize_scoped(scope_context, meta())
    assert result.media_source.value == ("manko" if first_result == "success" else "testsource")
    assert len(plugin._provider.calls) == 1
    assert len(plugin._providers["testsource"].calls) == (0 if first_result == "success" else 1)
    if first_result != "success":
        assert plugin._cache.get("manko:number:TEST-123") is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_explicit_source_miss_does_not_query_an_unselected_provider(multisource_plugin, asynchronous):
    plugin = multisource_plugin
    plugin._provider.payload = None
    arguments = {"meta": meta(), "media_source": "manko"}
    result = asyncio.run(plugin.async_recognize_media(**arguments)) if asynchronous else plugin.recognize_media(**arguments)
    assert result is None
    assert len(plugin._provider.calls) == 1
    assert plugin._providers["testsource"].calls == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_wrong_claimed_source_cannot_populate_requested_source_cache(multisource_plugin, asynchronous):
    plugin = multisource_plugin
    plugin._providers["testsource"].payload["record"]["_source"] = "manko"
    arguments = {"meta": meta(), "media_source": "testsource"}
    result = asyncio.run(plugin.async_recognize_media(**arguments)) if asynchronous else plugin.recognize_media(**arguments)
    assert result is None
    assert plugin._cache.get("testsource:number:TEST-123") is None
    assert plugin._cache.get("manko:number:TEST-123") is None
    assert plugin._provider.calls == []


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("mtype", [MediaType.TV, MediaType.MUSIC])
def test_manual_multisource_type_mismatch_never_calls_provider(multisource_plugin, asynchronous, mtype):
    plugin = multisource_plugin
    arguments = {"meta": meta(), "media_source": "testsource", "mtype": mtype}
    result = asyncio.run(plugin.async_recognize_media(**arguments)) if asynchronous else plugin.recognize_media(**arguments)
    assert result is None
    assert plugin._provider.calls == []
    assert plugin._providers["testsource"].calls == []


def test_removing_source_disables_its_cached_metadata_hooks(multisource_plugin):
    plugin = multisource_plugin
    result = plugin.recognize_media(meta(), media_source="testsource")
    assert plugin.metadata_nfo(meta(), result) is not None
    plugin._active_sources = ("manko",)
    assert plugin.metadata_nfo(meta(), result) is None
    assert plugin.metadata_img(result) is None
    assert plugin.obtain_images(result) is None
    assert plugin.metadata_nfo(meta(), None) is None
    assert plugin.metadata_img(None) is None
    assert plugin.obtain_images(None) is None
    assert plugin.recognize_media(meta(), media_source="testsource") is None


def test_multisource_identity_without_source_never_chooses_first_adapter(multisource_plugin, scope_context):
    plugin = multisource_plugin
    assert plugin.recognize_media(media_id="fixture_native_123") is None
    assert plugin.recognize_scoped(scope_context, media_id="fixture_native_123") is None
    assert asyncio.run(plugin.async_recognize_media(media_id="fixture_native_123")) is None
    assert asyncio.run(plugin.async_recognize_scoped(scope_context, media_id="fixture_native_123")) is None
    assert plugin._provider.calls == []
    assert plugin._providers["testsource"].calls == []


@pytest.mark.parametrize("sources", ["fanza\nmanko\nfc2ppvdb\nunknown\nfanza", ["fanza", "manko", "fc2ppvdb", "unknown", "fanza"]])
def test_source_configuration_registers_only_available_selected_adapters_in_order(plugin, sources):
    plugin.init_plugin({"enabled": True, "sources": sources})
    assert plugin._active_sources == ("fanza", "manko", "fc2ppvdb")
    assert [item["media_source"] for item in plugin.get_media_source()] == ["fanza", "manko", "fc2ppvdb"]
    assert plugin.recognize_media(meta(), media_source="javbus") is None
    assert plugin.recognize_media(meta(), media_source="unknown") is None
    plugin.init_plugin({"enabled": False, "sources": sources})
    assert plugin.get_media_source() == []
    assert plugin.get_module() == {}


@pytest.mark.parametrize("sources", ["", [], None, ["unknown", "not_implemented"]])
def test_empty_or_invalid_source_configuration_does_not_invent_default_queries(plugin, sources):
    plugin.init_plugin({"enabled": True, "sources": sources})
    assert plugin._active_sources == ()
    assert plugin.get_media_source() == []
    assert plugin.recognize_media(meta(), media_source="manko") is None
    assert plugin.search_medias(meta(), media_source=("manko", "fanza")) == []
