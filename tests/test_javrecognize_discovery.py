"""已完成下载任务只读发现：真实路径过滤、版本去重及宿主查询边界。"""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.plugins.xiaojiejiescraping import XiaojiejieScraping  # pylint: disable=import-error,no-name-in-module
from app.plugins.xiaojiejiescraping import discovery  # pylint: disable=import-error,no-name-in-module
from app.schemas.transfer import DownloaderFile, DownloaderTorrent
from app.schemas.system import DownloaderConf
from app.schemas.types import SystemConfigKey, TorrentStatus
from app.application import service as service_directory


HASH = "a" * 40


@pytest.fixture
def torrent(scope_context):
    return DownloaderTorrent(
        downloader="qbit-main", hash=HASH, tags="other, JAV, reviewed", progress=100,
        state="completed", content_path=scope_context.path,
        save_path=str(Path(scope_context.path).parent),
    )


def selected(torrent, roots, **kwargs):
    return discovery.torrent_files(torrent, downloader="qbit-main", tag="JAV", roots=roots, **kwargs)


@pytest.mark.parametrize(("raw", "expected"), [
    (None, set()), ("", set()), (" JAV , JAV, other ", {"JAV", "other"}),
    ("JAV-extra,jav", {"JAV-extra", "jav"}),
])
def test_tags_are_exact_case_sensitive_tokens(raw, expected):
    assert discovery.exact_tags(raw) == expected


def test_normalized_downloader_record_uses_mapped_content_path(torrent, scope_context):
    root = Path(scope_context.path).parent
    assert selected(torrent, (root,)) == (Path(scope_context.path),)
    torrent.content_path = None
    torrent.path = Path(scope_context.path)
    assert selected(torrent, (root,)) == (Path(scope_context.path),)
    torrent.path = None
    torrent.save_path = scope_context.path
    assert selected(torrent, (root,)) == ()


@pytest.mark.parametrize(("field", "value"), [
    ("downloader", "qbit-other"), ("tags", "JAV-extra,jav"), ("tags", None),
    ("progress", None), ("progress", 99.999), ("progress", "100"),
    ("progress", float("nan")), ("progress", float("inf")),
    ("state", "downloading"), ("state", "error"),
    ("hash", "x" * 40), ("hash", "a" * 39), ("hash", "../private"),
    ("content_path", "relative/TEST-123.mkv"),
])
def test_unfinished_mismatched_or_malformed_torrents_are_rejected(torrent, scope_context, field, value):
    malformed = torrent.model_copy(update={field: value})
    assert selected(malformed, (Path(scope_context.path).parent,)) == ()


@pytest.mark.parametrize("state", ["completed", "paused"])
@pytest.mark.parametrize("infohash", ["a" * 40, "B" * 64])
def test_only_complete_normalized_states_and_valid_hashes_are_selected(torrent, scope_context, state, infohash):
    torrent.state, torrent.hash = state, infohash
    assert selected(torrent, (Path(scope_context.path).parent,)) == (Path(scope_context.path),)


def test_directory_walk_is_bounded_and_skips_nonvideo_and_symlink_subtrees(torrent, scope_context, tmp_path):
    root = Path(scope_context.path).parent
    nested = root / "nested"
    nested.mkdir()
    video = nested / "TEST-124.MP4"
    video.write_text("synthetic fixture")
    (nested / "poster.jpg").write_text("not an image")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "TEST-999.mkv").write_text("synthetic fixture")
    (root / "outside-link").symlink_to(outside, target_is_directory=True)
    (root / "escape.mkv").symlink_to(outside / "TEST-999.mkv")
    torrent.content_path = str(root)
    assert selected(torrent, (root,)) == (Path(scope_context.path), video)
    assert selected(torrent, (root,), max_files=1) == ()


def test_outside_directory_is_rejected_before_walking(torrent, scope_context, tmp_path, monkeypatch):
    outside = tmp_path / "allowed-other"
    outside.mkdir()
    torrent.content_path = str(outside)

    def forbidden_walk(*_args, **_kwargs):
        pytest.fail("out-of-scope directory must not be traversed")

    monkeypatch.setattr(discovery.os, "walk", forbidden_walk)
    assert selected(torrent, (Path(scope_context.path).parent,)) == ()


def test_discovery_journal_reloads_and_records_only_new_file_versions(torrent, scope_context, tmp_path):
    path = Path(scope_context.path)
    journal_path = tmp_path / "journal.json"
    journal = discovery.DiscoveryJournal(journal_path)
    first = journal.discover(torrent, (path,))
    assert len(first) == 1
    assert first[0]["state"] == "discovered"
    assert first[0]["hash"] == HASH
    assert first[0]["path"] == str(path)
    assert "submitted" not in first[0] and "completed" not in first[0]
    assert journal.discover(torrent, (path,)) == []
    restored = discovery.DiscoveryJournal(journal_path)
    assert restored.discover(torrent, (path,)) == []
    path.write_bytes(path.read_bytes() + b" updated")
    second = restored.discover(torrent, (path,))
    assert len(second) == 1 and second[0]["size"] != first[0]["size"]
    before = path.stat()
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000))
    assert len(restored.discover(torrent, (path,))) == 1
    assert restored.discover(torrent, (path.parent / "missing.mkv",)) == []


@pytest.fixture
def polling_plugin(monkeypatch, tmp_path, plugin_host_context, scope_context):
    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path)
    monkeypatch.setattr(service_directory, "_config_loader", lambda _key, _type: [
        DownloaderConf(name="qbit-main", type="qbittorrent", enabled=True),
    ])
    source = Path(scope_context.path)

    def raw_completed(**kwargs):
        ids = kwargs.get("ids")
        hashes = [HASH] if ids is None else ids
        return [{"hash": infohash, "state": "uploading", "progress": 1.0, "amount_left": 0,
                 "tags": "JAV", "completion_on": 4_000_000_000} for infohash in hashes]

    raw_module = SimpleNamespace(get_instances=lambda: {"qbit-main": SimpleNamespace(
        get_completed_torrents=raw_completed)})
    monkeypatch.setattr(service_directory, "_module_loader", lambda _kind: [raw_module])
    plugin = XiaojiejieScraping()
    config = {
        "enabled": True, "discovery_enabled": True, "downloader": "qbit-main",
        "torrent_tag": "JAV", "recognize_dirs": [str(Path(scope_context.path).parent)],
    }
    plugin.init_plugin(config)
    manifest_query = lambda **_kwargs: [DownloaderFile(
        id=0, name=source.name, size=source.stat().st_size, priority=1, progress=1)]
    plugin._test_manifest_query = manifest_query
    monkeypatch.setattr(plugin.chain, "torrent_files", manifest_query, raising=False)
    yield plugin, config
    plugin.stop_service()


def replace_list_torrents(plugin, list_torrents):
    """Replace only the normalized task query; keep the SDK public file manifest."""
    manifest_query = plugin._test_manifest_query
    plugin.chain = SimpleNamespace(list_torrents=list_torrents, torrent_files=manifest_query)


@pytest.mark.parametrize(("field", "value"), [
    ("enabled", False), ("discovery_enabled", False), ("downloader", ""),
    ("torrent_tag", ""), ("recognize_dirs", []), ("storage", "remote"),
])
def test_readonly_poll_requires_complete_explicit_configuration(polling_plugin, field, value):
    plugin, config = polling_plugin
    service = plugin.get_service()
    assert len(service) == 1 and service[0]["trigger"] == "interval"
    assert service[0]["kwargs"] == {"seconds": 300}
    assert service[0]["func"] == plugin.poll_completed
    plugin.init_plugin({**config, field: value})
    assert plugin.get_service() == []
    assert plugin.poll_completed() == []


def test_poll_uses_host_readonly_query_and_never_recognizes_or_submits(polling_plugin, torrent, monkeypatch):
    plugin, _ = polling_plugin
    calls = []

    def run_module(method, **kwargs):
        calls.append((method, kwargs))
        return [torrent]

    class NoMetadata:
        def __getattr__(self, name):
            pytest.fail(f"read-only discovery must not call metadata provider {name}")

    plugin._provider = NoMetadata()
    # 保留真实 PluginChain.list_torrents，只替换外部下载器模块分派边界。
    monkeypatch.setattr(plugin.chain, "run_module", run_module)
    result = plugin.poll_completed()
    assert len(result) == 1 and result[0]["state"] == "discovered"
    assert calls == [("list_torrents", {"status": TorrentStatus.TRANSFER, "hashs": None, "downloader": "qbit-main", "include_all_tags": True})]
    assert plugin.poll_completed() == []
    assert len(calls) == 2


def test_poll_is_bounded_to_100_torrents(polling_plugin, torrent, monkeypatch):
    plugin, _ = polling_plugin
    replace_list_torrents(plugin, lambda **_kwargs: [torrent] * 150)
    calls = []
    monkeypatch.setattr(plugin._journal, "discover", lambda item, files: calls.append((item, files)) or [])
    assert plugin.poll_completed() == []
    assert len(calls) == 100


@pytest.mark.parametrize("change", ["stop", "reload"])
def test_inflight_poll_does_not_commit_after_lifecycle_change(polling_plugin, torrent, tmp_path, change):
    plugin, config = polling_plugin

    def stale_query(**_kwargs):
        plugin.stop_service() if change == "stop" else plugin.init_plugin(config)
        return [torrent]

    plugin.chain = SimpleNamespace(list_torrents=stale_query)
    assert plugin.poll_completed() == []
    assert not (tmp_path / "discovery-v1.json").exists()


def test_overlapping_poll_returns_without_querying(polling_plugin):
    plugin, _ = polling_plugin
    plugin.chain = SimpleNamespace()
    assert plugin._poll_lock.acquire(blocking=False)
    try:
        assert plugin.poll_completed() == []
    finally:
        plugin._poll_lock.release()


def test_failed_downloader_read_releases_lock_for_next_poll(polling_plugin, torrent, tmp_path):
    plugin, _ = polling_plugin

    def unavailable(**_kwargs):
        raise ConnectionError("synthetic downloader unavailable")

    plugin.chain = SimpleNamespace(list_torrents=unavailable)
    with pytest.raises(ConnectionError, match="synthetic downloader unavailable"):
        plugin.poll_completed()
    assert plugin._poll_lock.locked() is False
    assert not (tmp_path / "discovery-v1.json").exists()
    replace_list_torrents(plugin, lambda **_kwargs: [torrent])
    assert len(plugin.poll_completed()) == 1


def test_journal_cursor_is_sorted_persistent_and_wraps_without_starvation(torrent, tmp_path):
    torrents = [torrent.model_copy(update={"hash": f"{index:040x}"}) for index in range(1, 102)]
    path = tmp_path / "journal.json"
    journal = discovery.DiscoveryJournal(path)
    first = journal.next_batch(list(reversed(torrents)))
    assert [item.hash for item in first] == [item.hash for item in torrents[:100]]
    assert journal.cache.get("poll_cursor") is None
    assert journal.next_batch(torrents) == first
    journal.advance(first[-1])
    restored = discovery.DiscoveryJournal(path)
    second = restored.next_batch(torrents)
    assert len(second) == 100
    assert second[0].hash == torrents[100].hash
    assert [item.hash for item in second[1:]] == [item.hash for item in torrents[:99]]
    assert restored.next_batch([]) == []


def test_seen_first_100_tasks_cannot_starve_101st_after_restart(polling_plugin, torrent):
    plugin, config = polling_plugin
    torrents = [torrent.model_copy(update={"hash": f"{index:040x}"}) for index in range(1, 102)]
    replace_list_torrents(plugin, lambda **_kwargs: list(reversed(torrents)))
    first = plugin.poll_completed()
    assert len(first) == 100
    assert {item["hash"] for item in first} == {item.hash for item in torrents[:100]}
    plugin.init_plugin(config)
    replace_list_torrents(plugin, lambda **_kwargs: list(reversed(torrents)))
    second = plugin.poll_completed()
    assert [item["hash"] for item in second] == [torrents[100].hash]
    assert plugin.poll_completed() == []


@pytest.mark.parametrize("mismatch", [{"tags": "JAV-extra"}, {"downloader": "another-downloader"}])
def test_downloader_and_tag_prefilter_run_before_batch_limit(polling_plugin, torrent, mismatch):
    plugin, _ = polling_plugin
    unrelated = [torrent.model_copy(update={"hash": f"{index:040x}", **mismatch}) for index in range(1, 102)]
    eligible = torrent.model_copy(update={"hash": "f" * 40})
    replace_list_torrents(plugin, lambda **_kwargs: unrelated + [eligible])
    assert [item["hash"] for item in plugin.poll_completed()] == [eligible.hash]


def test_invalid_completed_candidates_do_not_permanently_block_later_eligible_task(polling_plugin, torrent):
    plugin, _ = polling_plugin
    invalid = [torrent.model_copy(update={"hash": f"{index:040x}", "progress": 99}) for index in range(1, 101)]
    eligible = torrent.model_copy(update={"hash": "f" * 40})
    replace_list_torrents(plugin, lambda **_kwargs: invalid + [eligible])
    assert plugin.poll_completed() == []
    assert [item["hash"] for item in plugin.poll_completed()] == [eligible.hash]


def test_parallel_journals_atomically_discover_a_file_version_only_once(torrent, scope_context, tmp_path):
    path = tmp_path / "journal.json"
    journals = [discovery.DiscoveryJournal(path) for _ in range(6)]
    barrier = Barrier(len(journals))

    def discover_once(journal):
        barrier.wait()
        return journal.discover(torrent, (Path(scope_context.path),))

    with ThreadPoolExecutor(max_workers=len(journals)) as executor:
        results = list(executor.map(discover_once, journals))
    assert sum(len(result) for result in results) == 1
    assert discovery.DiscoveryJournal(path).discover(torrent, (Path(scope_context.path),)) == []


@pytest.mark.parametrize("configuration", [
    None,
    DownloaderConf(name="qbit-main", type="transmission", enabled=True),
    DownloaderConf(name="qbit-main", type="qbittorrent", enabled=False),
    DownloaderConf(name="other-name", type="qbittorrent", enabled=True),
])
def test_real_service_configuration_boundary_rejects_unknown_disabled_or_nonqbit(polling_plugin, monkeypatch, tmp_path, configuration):
    plugin, _ = polling_plugin
    calls = []

    def configs(key, model):
        calls.append((key, model))
        return [configuration] if configuration is not None else []

    def forbidden_query(**_kwargs):
        pytest.fail("downloader query requires an enabled matching qBittorrent configuration")

    monkeypatch.setattr(service_directory, "_config_loader", configs)
    plugin.chain = SimpleNamespace(list_torrents=forbidden_query)
    assert plugin.poll_completed() == []
    assert calls == [(SystemConfigKey.Downloaders, DownloaderConf)]
    assert not (tmp_path / "discovery-v1.json").exists()


def test_service_configuration_error_fails_closed_without_downloader_query(polling_plugin, monkeypatch):
    plugin, _ = polling_plugin

    def unavailable(*_args):
        raise RuntimeError("synthetic configuration unavailable")

    monkeypatch.setattr(service_directory, "_config_loader", unavailable)
    plugin.chain = SimpleNamespace()
    assert plugin.poll_completed() == []
    assert plugin._poll_lock.locked() is False


def test_500_file_cutoff_advances_only_the_torrent_actually_processed(polling_plugin, torrent, scope_context, monkeypatch):
    from app.plugins import xiaojiejiescraping as plugin_module  # pylint: disable=import-error,no-name-in-module

    plugin, config = polling_plugin
    # Path-only discovery has no tag branch and therefore does not require an ownership manifest.
    plugin.init_plugin({**config, "automatic_match_mode": "path", "torrent_tag": ""})
    torrents = [torrent.model_copy(update={"hash": f"{index:040x}"}) for index in range(1, 102)]
    plugin.chain = SimpleNamespace(list_torrents=lambda **_kwargs: list(reversed(torrents)))
    monkeypatch.setattr(plugin_module, "torrent_files", lambda *_args, **_kwargs: (Path(scope_context.path),) * 500)
    processed = []

    def discover_batch(item, files):
        processed.append(item.hash)
        return [{"hash": item.hash, "state": "discovered"} for _ in files]

    monkeypatch.setattr(plugin._journal, "discover", discover_batch)
    first = plugin.poll_completed()
    assert len(first) == 500 and processed == [torrents[0].hash]
    second = plugin.poll_completed()
    assert len(second) == 500 and processed == [torrents[0].hash, torrents[1].hash]


@pytest.fixture
def ownership_manifest(torrent, scope_context):
    path = Path(scope_context.path)
    torrent.save_path = str(path.parent)
    item = {"id": 0, "name": path.name, "size": path.stat().st_size, "priority": 1, "progress": 1}
    return torrent, (path,), [item]


def test_manifest_intersection_uses_real_sdk_file_rows_and_excludes_shared_root_extras(ownership_manifest):
    torrent, candidates, manifest = ownership_manifest
    extra = candidates[0].parent / "unrelated-TEST-999.mkv"
    extra.write_bytes(b"not owned by this torrent")
    rows = [DownloaderFile(**manifest[0])]
    assert discovery.owned_torrent_files(torrent, candidates + (extra,), rows) == candidates


def test_manifest_with_all_missing_public_ids_uses_response_position_as_internal_identity(ownership_manifest):
    torrent, candidates, manifest = ownership_manifest
    rows = [DownloaderFile.model_validate({**manifest[0], "id": None})]

    assert rows[0].id is None
    assert discovery.owned_torrent_files(torrent, candidates, rows) == candidates
    missing_field_row = {key: value for key, value in manifest[0].items() if key != "id"}
    assert discovery.owned_torrent_files(torrent, candidates, [missing_field_row]) == candidates


@pytest.mark.parametrize(("field", "value"), [
    ("name", "../TEST-123.mkv"), ("name", "not-present.mkv"),
    ("size", 999), ("priority", None), ("progress", 0.99),
])
def test_missing_id_does_not_relax_other_manifest_ownership_checks(ownership_manifest, field, value):
    torrent, candidates, manifest = ownership_manifest
    row = {**manifest[0], "id": None, field: value}

    assert discovery.owned_torrent_files(torrent, candidates, [row]) is None


def test_missing_id_does_not_allow_duplicate_names(ownership_manifest):
    torrent, candidates, manifest = ownership_manifest
    rows = [{**manifest[0], "id": None}, {**manifest[0], "id": None}]

    assert discovery.owned_torrent_files(torrent, candidates, rows) is None


def test_manifest_rejects_mixing_missing_and_explicit_ids(ownership_manifest):
    torrent, candidates, manifest = ownership_manifest
    second = candidates[0].parent / "TEST-124.mkv"
    second.write_bytes(b"another completed video")
    candidates = candidates + (second,)
    rows = [
        DownloaderFile.model_validate({**manifest[0], "id": None}),
        DownloaderFile.model_validate({
            "id": 1, "name": second.name, "size": second.stat().st_size,
            "priority": 1, "progress": 1,
        }),
    ]

    assert discovery.owned_torrent_files(torrent, candidates, rows) is None


@pytest.mark.parametrize(("field", "value"), [
    ("id", True), ("id", "0"), ("id", -1), ("id", 1),
    ("name", "../TEST-123.mkv"), ("name", "/TEST-123.mkv"), ("name", "a\\file.mkv"),
    ("name", "./TEST-123.mkv"), ("name", "a//file.mkv"), ("name", "a\x00file.mkv"),
    ("name", "not-present.mkv"), ("size", -1), ("size", True), ("size", "35"),
    ("size", 999), ("priority", True), ("priority", -1), ("priority", "1"),
    ("progress", True), ("progress", float("nan")), ("progress", float("inf")),
    ("progress", -1), ("progress", 1.1), ("progress", 0.99),
])
def test_unsafe_or_incomplete_manifest_rows_fail_closed(ownership_manifest, field, value):
    torrent, candidates, rows = ownership_manifest
    rows[0][field] = value
    assert discovery.owned_torrent_files(torrent, candidates, rows) is None


@pytest.mark.parametrize("manifest", [None, [], {}, [None]])
def test_missing_manifest_cannot_prove_file_ownership(ownership_manifest, manifest):
    torrent, candidates, _ = ownership_manifest
    assert discovery.owned_torrent_files(torrent, candidates, manifest) is None


def test_deselected_files_are_ignored_and_do_not_authorize_candidates(ownership_manifest):
    torrent, candidates, rows = ownership_manifest
    rows[0].update(priority=0, progress=0)
    assert discovery.owned_torrent_files(torrent, candidates, rows) == ()


def test_manifest_requires_unique_contiguous_ids_and_unique_names(ownership_manifest):
    torrent, candidates, rows = ownership_manifest
    assert discovery.owned_torrent_files(torrent, candidates, rows + [dict(rows[0])]) is None
    assert discovery.owned_torrent_files(torrent, candidates, rows + [{**rows[0], "id": 1}]) is None
    assert discovery.owned_torrent_files(torrent, candidates + candidates, rows) is None


def test_manifest_never_guesses_or_remaps_raw_save_path(ownership_manifest):
    torrent, candidates, rows = ownership_manifest
    for save_path in (None, "/", "relative", "/unmapped/downloader/container/path"):
        torrent.save_path = save_path
        assert discovery.owned_torrent_files(torrent, candidates, rows) is None
