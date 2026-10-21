"""自动触发只处理新完成任务，并对检查 API 保持只读。"""
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.endpoints.plugin import get_current_active_superuser
from app.api.context import get_sync_session
from app.application import service as service_directory
from app.db.oper.systemconfig import SystemConfigOper
from app.plugins.xiaojiejiescraping import XiaojiejieScraping
from app.schemas.transfer import DownloaderFile
from app.schemas.types import SystemConfigKey
from app.plugins.xiaojiejiescraping.ledger import HandoffLedger
from test_javrecognize_discovery import polling_plugin, torrent


@pytest.fixture
def automatic_plugin(polling_plugin, torrent, monkeypatch, tmp_path):
    plugin, base = polling_plugin
    target = tmp_path / "library"
    target.mkdir()
    config = {
        **base, "auto_new_only": True, "target_path": str(target),
        "handoff_mode": "discover", "transfer_type": "link", "sources": ["manko"],
    }
    raw = {"hash": torrent.hash, "state": "uploading", "progress": 1.0,
           "amount_left": 0, "tags": torrent.tags, "completion_on": int(time.time())}
    calls = []

    def get_completed_torrents(**kwargs):
        calls.append(kwargs)
        ids = kwargs.get("ids")
        if ids is None or torrent.hash in ids:
            return [dict(raw)]
        return []

    instance = SimpleNamespace(
        get_completed_torrents=get_completed_torrents)
    module = SimpleNamespace(get_instances=lambda: {"qbit-main": instance})
    monkeypatch.setattr(service_directory, "_module_loader", lambda _kind: [module])
    plugin.init_plugin(config)
    plugin.chain = SimpleNamespace(list_torrents=lambda **_kwargs: [torrent])
    return plugin, config, torrent, raw, calls, target


def set_started_at(plugin, timestamp):
    ledger = HandoffLedger(Path(plugin.get_data_path()) / "auto-trigger.sqlite3")
    record = ledger.list_records(limit=10)[0]
    assert record["payload"]["kind"] == "auto_trigger_control"
    assert ledger.transition(record["key"], (record["state"],), record["state"],
                             {**record["payload"], "started_at": timestamp, "armed": True})
    plugin._auto_guard._record = ledger.get(record["key"])


def test_auto_new_only_skips_old_completion_then_discovers_new_completion(automatic_plugin):
    plugin, _, torrent, raw, _, _ = automatic_plugin
    started_at = plugin._auto_guard.started_at
    assert started_at is not None
    raw["completion_on"] = started_at - 1
    assert plugin.poll_completed() == []
    raw["completion_on"] = int(time.time())
    set_started_at(plugin, int(time.time()) - 2)
    result = plugin.poll_completed()
    assert [item["hash"] for item in result] == [torrent.hash]


@pytest.mark.parametrize("completion_on", [None, 0, True, "123", float("inf"), float("nan"), 4_000_000_000])
def test_auto_new_only_rejects_missing_invalid_or_future_completion_time(automatic_plugin, completion_on):
    plugin, _, _, raw, _, _ = automatic_plugin
    set_started_at(plugin, int(time.time()) - 2)
    raw["completion_on"] = completion_on
    assert plugin.poll_completed() == []


def test_auto_new_only_rejects_old_task_after_exact_tag_is_added(automatic_plugin):
    plugin, _, torrent, raw, _, _ = automatic_plugin
    set_started_at(plugin, int(time.time()) - 5)
    raw["completion_on"] = plugin._auto_guard.started_at - 1
    torrent.tags = "other, JAV"
    assert plugin.poll_completed() == []
    assert plugin._auto_counts["new_only_rejected"] >= 1


def test_auto_new_only_checks_at_most_one_batch_and_cursor_reaches_task_101(automatic_plugin, monkeypatch):
    plugin, _, torrent, _, calls, _ = automatic_plugin
    started_at = int(time.time()) - 2
    set_started_at(plugin, started_at)
    torrents = [torrent.model_copy(update={"hash": f"{index:040x}"})
                for index in range(1, 102)]
    plugin.chain = SimpleNamespace(list_torrents=lambda **_kwargs: torrents)
    completion = {
        item.hash: (int(time.time()) if item.hash == torrents[100].hash else started_at - 1)
        for item in torrents
    }
    calls.clear()

    def get_completed_torrents(**kwargs):
        calls.append(kwargs)
        ids = kwargs.get("ids")
        selected = list(completion) if ids is None else ids
        return [{"hash": key, "state": "uploading", "progress": 1.0, "amount_left": 0,
                 "tags": "JAV", "completion_on": completion[key]}
                for key in selected if key in completion]

    module = SimpleNamespace(get_instances=lambda: {"qbit-main": SimpleNamespace(
        get_completed_torrents=get_completed_torrents)})
    monkeypatch.setattr(service_directory, "_module_loader", lambda _kind: [module])
    assert plugin.poll_completed() == []
    assert len(calls) == 100
    assert all(torrents[100].hash not in call["ids"] for call in calls)

    result = plugin.poll_completed()
    assert len(calls) == 200
    assert any(item["hash"] == torrents[100].hash for item in result)


def test_auto_new_completion_is_enqueued_for_existing_handoff(automatic_plugin):
    plugin, config, torrent, raw, _, _ = automatic_plugin
    set_started_at(plugin, int(time.time()) - 2)
    raw["completion_on"] = int(time.time())
    source = Path(torrent.content_path)
    torrent.save_path = str(source.parent)
    plugin.init_plugin({**config, "handoff_mode": "preview"})
    set_started_at(plugin, int(time.time()) - 2)
    plugin.chain = SimpleNamespace(
        list_torrents=lambda **_kwargs: [torrent],
        torrent_files=lambda **_kwargs: [DownloaderFile(
            id=0, name=source.name, size=source.stat().st_size, priority=1, progress=1,
        )],
    )
    # This test proves durable enqueue only; recognition/transfer execution has separate contracts.
    plugin._handoff.run_pending = lambda *_args, **_kwargs: []
    plugin.poll_completed()
    records = plugin._handoff.ledger.list_records(limit=10)
    assert len(records) == 1
    assert records[0]["payload"]["download_hash"] == torrent.hash


def test_auto_new_only_rechecks_completion_time_at_final_candidate_boundary(automatic_plugin):
    plugin, config, torrent, raw, _, _ = automatic_plugin
    source = Path(torrent.content_path)
    torrent.save_path = str(source.parent)
    plugin.init_plugin({**config, "handoff_mode": "preview"})
    set_started_at(plugin, int(time.time()) - 2)
    calls = []

    def get_completed_torrents(**kwargs):
        calls.append(kwargs)
        completion_on = int(time.time()) if len(calls) < 4 else plugin._auto_guard.started_at - 1
        return [{**raw, "completion_on": completion_on}]

    instance = SimpleNamespace(
        get_completed_torrents=get_completed_torrents)
    module = SimpleNamespace(get_instances=lambda: {"qbit-main": instance})
    from app.application import service as service_directory
    plugin._auto_counts = {"checked": 0, "new_only_rejected": 0, "discovered": 0, "enqueued": 0}
    plugin.chain = SimpleNamespace(
        list_torrents=lambda **_kwargs: [torrent],
        torrent_files=lambda **_kwargs: [DownloaderFile(
            id=0, name=source.name, size=source.stat().st_size, priority=1, progress=1,
        )],
    )
    # Fixture-level module loader is replaced only for this boundary-race probe.
    service_directory._module_loader = lambda _kind: [module]
    recognized = []
    plugin._handoff.recognize = lambda *args, **kwargs: recognized.append(True)
    plugin.poll_completed()
    assert len(plugin._handoff.ledger.list_records(limit=10)) == 1
    assert recognized == []


def test_auto_new_only_reload_preserves_boundary_and_disable_reenable_resets(automatic_plugin, monkeypatch):
    plugin, config, _, _, _, _ = automatic_plugin
    first = plugin._auto_guard.started_at
    plugin.stop_service()
    plugin.init_plugin(config)
    assert plugin._auto_guard.started_at == first

    plugin.init_plugin({**config, "enabled": False})
    assert plugin._auto_guard.armed is False
    plugin.init_plugin(config)
    assert plugin._auto_guard.armed is True
    assert plugin._auto_guard.started_at > first


def test_auto_new_only_local_remote_local_resets_boundary(automatic_plugin):
    plugin, config, _, _, _, _ = automatic_plugin
    first = plugin._auto_guard.started_at
    plugin.init_plugin({**config, "storage": "remote"})
    assert plugin._auto_guard.armed is False
    plugin.init_plugin(config)
    assert plugin._auto_guard.armed is True
    assert plugin._auto_guard.started_at > first


@pytest.mark.parametrize("change", [
    {"downloader": "qbit-other"}, {"torrent_tag": "OTHER"}, {"recognize_dirs": ["/other"]},
    {"target_path": "/other-target"}, {"sources": ["javbus"]}, {"transfer_type": "copy"},
])
def test_auto_new_only_scope_change_rearms(automatic_plugin, change):
    plugin, config, _, _, _, _ = automatic_plugin
    previous = plugin._auto_guard.started_at
    previous_scope = plugin._auto_guard.scope_signature
    plugin.init_plugin({**config, **change})
    assert plugin._auto_guard.armed is True
    assert plugin._auto_guard.scope_signature != previous_scope
    assert plugin._auto_guard.started_at > previous


def test_auto_new_only_corrupt_control_ledger_fails_closed(automatic_plugin, tmp_path):
    plugin, config, torrent, raw, _, _ = automatic_plugin
    path = Path(plugin.get_data_path()) / "auto-trigger.sqlite3"
    path.write_bytes(b"not a sqlite database")
    plugin.init_plugin(config)
    assert plugin._auto_guard.armed is False
    assert plugin.automatic_status()["error"] == "新任务边界账本不可用；自动触发已阻止"
    raw["completion_on"] = int(time.time())
    assert plugin.poll_completed() == []
    assert not plugin._journal.discover(torrent, ())


@pytest.mark.parametrize("started_at", [0, -1])
def test_auto_new_only_semantically_invalid_positive_boundary_fails_closed(automatic_plugin, started_at):
    plugin, config, _, raw, _, _ = automatic_plugin
    path = Path(plugin.get_data_path()) / "auto-trigger.sqlite3"
    ledger = HandoffLedger(path)
    record = ledger.list_records(limit=10)[0]
    payload = {**record["payload"], "started_at": started_at, "armed": True}
    assert ledger.transition(record["key"], (record["state"],), record["state"], payload)

    plugin.init_plugin(config)
    assert plugin._auto_guard.armed is False
    assert plugin.automatic_status()["error"] == "新任务边界账本不可用；自动触发已阻止"
    raw["completion_on"] = int(time.time())
    assert plugin.poll_completed() == []


def test_disabled_discovery_still_registers_authenticated_status_and_check(automatic_plugin, monkeypatch):
    plugin, config, _, _, calls, _ = automatic_plugin
    plugin.init_plugin({**config, "discovery_enabled": False})
    plugin.automatic_check = lambda: {
        "ready": True, "message": "条件检查通过", "downloader_type": "qbittorrent",
        "completion_capability": True, "completion_rows_checked": 1, "completion_time_rows": 1,
        "native_monitor_overlap": False, "native_monitor_count": 0,
        "directories_available": True, "errors": [],
    }
    app = FastAPI()
    for route in plugin.get_api():
        if route["path"].startswith("/automatic/"):
            app.add_api_route(route["path"], route["endpoint"], methods=route["methods"],
                              response_model=route["response_model"])
    app.dependency_overrides[get_sync_session] = lambda: None
    client = TestClient(app)
    assert client.get("/automatic/status").status_code in (401, 403)
    assert client.post("/automatic/check").status_code in (401, 403)
    app.dependency_overrides[get_current_active_superuser] = lambda: object()
    client = TestClient(app)
    status = client.get("/automatic/status")
    check = client.post("/automatic/check")
    assert status.status_code == check.status_code == 200
    assert status.json()["enabled"] is False
    assert status.json()["interval_seconds"] == 300
    assert check.json()["ready"] is True and check.json()["message"] == "条件检查通过"
    assert calls == []


def test_automatic_check_summarizes_public_rows_and_native_directory_overlap(automatic_plugin, monkeypatch):
    plugin, _, _, _, _, _ = automatic_plugin
    monkeypatch.setattr(SystemConfigOper, "get", lambda _self, key: [
        {"name": "native", "storage": "local", "download_path": str(Path(plugin._dirs[0]) / "nested"),
         "monitor_type": "monitor"},
        {"name": "other", "storage": "local", "download_path": "/unrelated", "monitor_type": "manual"},
    ] if key == SystemConfigKey.Directories else [])
    result = plugin.automatic_check()
    assert result["completion_rows_checked"] == 1
    assert result["completion_time_rows"] == 1
    assert result["native_monitor_overlap"] is True
    assert result["ready"] is False
    assert "download_path" not in str(result) and "completion_on" not in str(result)


@pytest.mark.parametrize("completion_on", [0, -1, True, 4_000_000_000])
def test_automatic_check_rejects_invalid_completion_time_rows(automatic_plugin, monkeypatch, completion_on):
    plugin, _, _, raw, _, _ = automatic_plugin
    raw["completion_on"] = completion_on
    monkeypatch.setattr(SystemConfigOper, "get", lambda _self, _key: [])
    result = plugin.automatic_check()
    assert result["completion_rows_checked"] == 1
    assert result["completion_time_rows"] == 0
    assert result["ready"] is False
