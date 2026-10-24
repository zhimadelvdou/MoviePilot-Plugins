"""插件自有手动整理入口的真实 SDK 合同与路径护栏。"""
from pathlib import Path
import os
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.sdk.media import MediaInfo
from app.api.context import get_sync_session
from pydantic import ValidationError
from app.api.endpoints.plugin import get_current_active_superuser

from app.plugins.xiaojiejiescraping import XiaojiejieScraping
from app.plugins.xiaojiejiescraping.manual import ManualBrowseItem, ManualPreviewRequest, ManualTransferWorkspace
from app.plugins.xiaojiejiescraping.manual_api import api_routes


@pytest.fixture
def manual_workspace(monkeypatch, tmp_path, plugin_host_context):
    root = tmp_path / "source"
    target = tmp_path / "target"
    root.mkdir()
    target.mkdir()
    media_path = root / "TEST-123.mkv"
    media_path.write_bytes(b"synthetic media")
    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: tmp_path / "data")
    plugin = XiaojiejieScraping()
    plugin.init_plugin({"enabled": True, "recognize_dirs": [str(root)], "sources": ["manko"]})
    plugin._active_sources = ("manko",)
    plugin._handoff = SimpleNamespace(config=SimpleNamespace(target_path=str(target)))
    media = MediaInfo()
    media.from_dict({
        "media_source": "manko", "media_id": "native-123", "scrape_source": "manko",
        "type": "电影", "title": "Synthetic title", "year": "2026",
        "poster_path": None, "backdrop_path": None, "actors": [],
    })
    monkeypatch.setattr(plugin, "recognize_media", lambda **_: media)
    calls = []

    def transfer(**kwargs):
        calls.append(kwargs)
        if kwargs.get("preview"):
            selected_target = Path(kwargs["target_path"])
            source_path = kwargs["fileitem"].path
            return True, {"items": [{
                "success": True, "source": str(source_path), "source_storage": "local",
                "target": str(selected_target / Path(source_path).name),
            }]}
        return True, {}

    workspace = ManualTransferWorkspace(plugin, tmp_path / "data", transfer=transfer)
    return plugin, workspace, root, target, media_path, calls


def test_manual_options_and_routes_are_admin_only_with_explicit_models(manual_workspace):
    _, workspace, root, target, _, _ = manual_workspace
    options = workspace.options()
    assert options.enabled is True
    assert options.roots == [str(root)]
    assert options.default_target_path == str(target)
    routes = api_routes(workspace)
    assert {route["path"] for route in routes} == {
        "/manual/options", "/manual/browse", "/manual/preview", "/manual/execute", "/manual/status",
    }
    assert all(route["auth"] == "bear" and route["response_model"] is not None for route in routes)
    assert all(route["endpoint"].__defaults__[-1].dependency is not None for route in routes)


def test_manual_preview_pins_identity_and_duplicate_execute_does_not_resubmit(manual_workspace):
    _, workspace, root, target, source, calls = manual_workspace
    result = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    assert result.summary.success == 1
    assert result.items[0].media_id == "native-123"
    assert result.items[0].target == str(target / source.name)
    assert len(calls) == 1 and calls[0]["preview"] is True

    first = workspace.execute(result.preview_id)
    assert first.state == "accepted"
    assert first.summary.success == 1
    assert calls[-1]["preview"] is False
    assert calls[-1]["media_source"] == "manko"
    assert calls[-1]["media_id"] == "native-123"
    second = workspace.execute(result.preview_id)
    assert second.state == "accepted"
    assert len(calls) == 3  # 预执行复核预览 + 一次实际提交，不会重复提交
    assert source.read_bytes() == b"synthetic media"


def test_manual_preview_defaults_to_first_trusted_actress_in_source_order(manual_workspace):
    plugin, workspace, _, target, source, calls = manual_workspace
    plugin._actress_subfolder = True
    names = ["Second Actress", "First Actress"]
    plugin.actress_evidence = lambda _media, _number: (names, names)

    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
        actress_subfolder=True,
    ))

    assert preview.summary.success == 1
    assert preview.items[0].actor_candidates == names
    assert preview.items[0].selected_actress == "Second Actress"
    assert calls[0]["target_path"] == target / "Second Actress"
    result = workspace.execute(preview.preview_id)
    assert result.state == "accepted"
    assert len(calls) == 3  # 提交前复核预览 + 一次实际提交


def test_manual_preview_accepts_explicit_second_trusted_actress(manual_workspace):
    plugin, workspace, _, target, source, calls = manual_workspace
    plugin._actress_subfolder = True
    names = ["First Actress", "Second Actress"]
    plugin.actress_evidence = lambda _media, _number: (names, names)

    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
        actress_subfolder=True, actress_choices={str(source): "Second Actress"},
    ))

    assert preview.summary.success == 1
    assert preview.items[0].selected_actress == "Second Actress"
    assert calls[0]["target_path"] == target / "Second Actress"
    result = workspace.execute(preview.preview_id)
    assert result.state == "accepted"
    assert len(calls) == 3


def test_manual_default_choice_is_rejected_if_trusted_source_order_changes(manual_workspace):
    plugin, workspace, _, target, source, calls = manual_workspace
    plugin._actress_subfolder = True
    order = [["First Actress", "Second Actress"], ["Second Actress", "First Actress"]]
    evidence_calls = []

    def evidence(_media, _number):
        names = order[min(len(evidence_calls), 1)]
        evidence_calls.append(names)
        return names, names

    plugin.actress_evidence = evidence
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
        actress_subfolder=True,
    ))

    assert preview.summary.success == 1
    assert preview.items[0].selected_actress == "First Actress"
    result = workspace.execute(preview.preview_id)

    assert result.state == "failed"
    assert all(call.get("preview") is True for call in calls)


def test_manual_second_choice_is_rejected_if_its_trusted_evidence_is_revoked(manual_workspace):
    plugin, workspace, _, target, source, calls = manual_workspace
    plugin._actress_subfolder = True
    evidence_calls = []

    def evidence(_media, _number):
        evidence_calls.append(True)
        names = ["First Actress", "Second Actress"] if len(evidence_calls) == 1 else ["First Actress"]
        return names, names

    plugin.actress_evidence = evidence
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
        actress_subfolder=True, actress_choices={str(source): "Second Actress"},
    ))

    assert preview.summary.success == 1
    assert preview.items[0].selected_actress == "Second Actress"
    result = workspace.execute(preview.preview_id)

    assert result.state == "failed"
    assert all(call.get("preview") is True for call in calls)


def test_manual_preview_rejects_invalid_actress_choice_without_fallback(manual_workspace):
    plugin, workspace, _, target, source, calls = manual_workspace
    plugin._actress_subfolder = True
    names = ["First Actress", "Second Actress"]
    plugin.actress_evidence = lambda _media, _number: (names, names)

    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
        actress_subfolder=True, actress_choices={str(source): "Untrusted Actress"},
    ))

    assert preview.summary.failed == 1
    assert preview.items[0].selected_actress is None
    assert calls == []


def test_manual_preview_fails_when_there_is_no_trusted_actress(manual_workspace):
    plugin, workspace, _, target, source, calls = manual_workspace
    plugin._actress_subfolder = True
    plugin.actress_evidence = lambda _media, _number: ([], [])

    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
        actress_subfolder=True,
    ))

    assert preview.summary.failed == 1
    assert preview.items[0].needs_actress_choice is False
    assert calls == []


def test_manual_preview_accepts_user_selected_local_paths_outside_automatic_roots(manual_workspace, tmp_path):
    plugin, workspace, _, _, _, calls = manual_workspace
    source_root = tmp_path / "picked-source"
    target_root = tmp_path / "picked-target"
    source_root.mkdir()
    target_root.mkdir()
    source = source_root / "TEST-456.mkv"
    source.write_bytes(b"another fixture")
    configured_roots = plugin._dirs
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source_root), target_path=str(target_root), media_source="manko",
    ))

    assert preview.summary.success == 1
    assert preview.items[0].target == str(target_root / source.name)
    assert plugin._dirs == configured_roots
    assert calls[0]["target_path"] == target_root
    assert all(call.get("preview") is True for call in calls)


def test_manual_browse_starts_from_local_root_and_keeps_roots_as_shortcuts(manual_workspace):
    _, workspace, _, _, _, _ = manual_workspace
    options = workspace.options()
    listing = workspace.browse("", "source")
    assert options.roots
    assert listing.path == str(Path("/"))
    assert listing.parent is None


def test_manual_browse_returns_metadata_for_allowed_items_and_keeps_legacy_model_compatible(manual_workspace):
    _, workspace, root, target, _, _ = manual_workspace
    video = root / "TEST-456.mp4"
    video.write_bytes(b"video bytes")
    os.utime(video, (1_700_000_002, 1_700_000_002))
    folder = root / "Folder 2"
    folder.mkdir()
    os.utime(folder, (1_700_000_001, 1_700_000_001))
    (root / "notes.txt").write_text("ignored", encoding="utf-8")

    source_listing = workspace.browse(str(root), "source")
    source_items = {item.name: item for item in source_listing.items}
    assert set(source_items) == {"TEST-123.mkv", "TEST-456.mp4", "Folder 2"}
    assert source_items["TEST-456.mp4"].modify_time == pytest.approx(1_700_000_002)
    assert source_items["TEST-456.mp4"].size == len(b"video bytes")
    assert source_items["Folder 2"].modify_time == pytest.approx(1_700_000_001)
    assert source_items["Folder 2"].size is None

    target_folder = target / "Library Folder"
    target_folder.mkdir()
    (target / "ignored.mp4").write_bytes(b"target files are not selectable")
    target_listing = workspace.browse(str(target), "target")
    assert [item.name for item in target_listing.items] == ["Library Folder"]
    assert target_listing.items[0].size is None

    legacy_item = ManualBrowseItem.model_validate({"name": "legacy.mp4", "path": "/legacy.mp4", "type": "file"})
    assert legacy_item.modify_time is None
    assert legacy_item.size is None


@pytest.mark.parametrize("path", ["../outside/TEST-123.mkv"])
def test_manual_preview_rejects_paths_outside_configured_roots(manual_workspace, path):
    _, workspace, _, target, _, _ = manual_workspace
    with pytest.raises(ValueError, match="源路径"):
        workspace.preview(ManualPreviewRequest(
            path=path, target_path=str(target), media_source="manko",
        ))


def test_manual_preview_rejects_target_inside_source_root(manual_workspace):
    _, workspace, root, _, source, _ = manual_workspace
    with pytest.raises(ValueError, match="目标"):
        workspace.preview(ManualPreviewRequest(
            path=str(source), target_path=str(root), media_source="manko",
        ))

def test_manual_preview_accepts_user_selected_target_outside_configured_tree(manual_workspace, tmp_path):
    _, workspace, _, _, source, _ = manual_workspace
    other = tmp_path / "other-target"
    other.mkdir()
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(other), media_source="manko",
    ))
    assert preview.summary.success == 1
    assert preview.items[0].target == str(other / source.name)


def test_manual_preview_works_without_automatic_roots_or_valid_global_target(manual_workspace, tmp_path):
    plugin, workspace, _, _, _, calls = manual_workspace
    source_root = tmp_path / "independent-source"
    target_root = tmp_path / "independent-target"
    source_root.mkdir()
    target_root.mkdir()
    source = source_root / "TEST-456.mkv"
    source.write_bytes(b"manual only")
    plugin._dirs = ()
    plugin._handoff.config.target_path = "/"

    assert workspace.options().enabled is True
    assert workspace.options().roots == []
    assert workspace.options().default_target_path is None
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target_root), media_source="manko",
    ))

    assert preview.summary.success == 1
    assert plugin._dirs == ()
    assert calls[0]["target_path"] == target_root


def test_manual_preview_rejects_unselected_source_and_client_identity(manual_workspace):
    _, workspace, _, target, source, _ = manual_workspace
    with pytest.raises(ValueError, match="未启用"):
        workspace.preview(ManualPreviewRequest(
            path=str(source), target_path=str(target), media_source="tmdb",
        ))
    with pytest.raises(ValidationError):
        ManualPreviewRequest(path=str(source), target_path=str(target), media_source="manko",
                             media_id="client-controlled")


def test_manual_preview_expires_when_source_changes(manual_workspace):
    _, workspace, _, target, source, calls = manual_workspace
    result = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    source.write_bytes(b"changed fixture")
    response = workspace.execute(result.preview_id)
    assert response.state == "failed"
    assert all(call.get("preview") is True for call in calls)


def test_manual_preview_intent_blocks_duplicate_preview_sessions(manual_workspace):
    _, workspace, _, target, source, calls = manual_workspace
    request = ManualPreviewRequest(path=str(source), target_path=str(target), media_source="manko")
    first = workspace.preview(request)
    second = workspace.preview(request)
    assert workspace.execute(first.preview_id).state == "accepted"
    assert workspace.execute(second.preview_id).state == "failed"
    assert len([call for call in calls if not call.get("preview")]) == 1


def test_manual_preview_is_invalid_after_workspace_restart(manual_workspace, tmp_path):
    plugin, workspace, _, target, source, calls = manual_workspace
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    restarted = ManualTransferWorkspace(plugin, tmp_path / "data", transfer=workspace.transfer)
    assert restarted.execute(preview.preview_id).state == "failed"
    assert not any(not call.get("preview") for call in calls)


def test_manual_preview_is_invalid_after_target_directory_replacement(manual_workspace):
    _, workspace, _, target, source, calls = manual_workspace
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    target.rename(target.with_name("target-old"))
    target.mkdir()
    assert workspace.execute(preview.preview_id).state == "failed"
    assert not any(not call.get("preview") for call in calls)


def test_manual_preview_is_invalid_after_selected_subdirectory_replacement(manual_workspace):
    _, workspace, _, target, source, calls = manual_workspace
    selected = target / "selected"
    selected.mkdir()
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(selected), media_source="manko",
    ))
    selected.rename(target / "selected-old")
    selected.mkdir()
    assert workspace.execute(preview.preview_id).state == "failed"
    assert not any(not call.get("preview") for call in calls)


def test_manual_preview_is_invalid_after_source_disappears(manual_workspace):
    _, workspace, _, target, source, calls = manual_workspace
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    source.unlink()
    assert workspace.execute(preview.preview_id).state == "failed"
    assert not any(not call.get("preview") for call in calls)


def test_manual_preview_is_invalid_after_expiry(manual_workspace, monkeypatch):
    _, workspace, _, target, source, calls = manual_workspace
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    monkeypatch.setattr("app.plugins.xiaojiejiescraping.manual.time.time",
                        lambda: preview.expires_at + 1)
    assert workspace.execute(preview.preview_id).state == "failed"
    assert not any(not call.get("preview") for call in calls)


def test_manual_preview_stops_when_source_is_disabled(manual_workspace):
    plugin, workspace, _, target, source, calls = manual_workspace
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    plugin._active_sources = ()
    assert workspace.execute(preview.preview_id).state == "failed"
    assert not any(not call.get("preview") for call in calls)


def test_manual_preview_rejects_cross_device_hardlink_before_native_preview(manual_workspace, monkeypatch):
    _, workspace, _, target, source, calls = manual_workspace
    original_stat = Path.stat

    class DifferentDevice:
        def __init__(self, value):
            self._value = value

        def __getattr__(self, name):
            if name == "st_dev":
                return self._value.st_dev + 1
            return getattr(self._value, name)

    def stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        return DifferentDevice(value) if path == source else value

    monkeypatch.setattr(Path, "stat", stat)
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    assert preview.summary.failed == 1
    assert "不在同一卷" in preview.items[0].message
    assert calls == []


def test_manual_delayed_callback_is_invalid_after_same_config_reinitialization(manual_workspace):
    plugin, workspace, _, target, source, _ = manual_workspace
    callbacks = []
    native_transfer = workspace.transfer

    def deferred_transfer(**kwargs):
        if not kwargs.get("preview"):
            callbacks.append(kwargs["continue_callback"])
        return native_transfer(**kwargs)

    workspace.transfer = deferred_transfer
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    assert workspace.execute(preview.preview_id).state == "accepted"
    assert callbacks[0]() is True
    plugin.init_plugin({"enabled": True, "recognize_dirs": [str(target.parent / "source")], "sources": ["manko"]})
    # Recreate the same configured target on the same plugin object. The boot ID and
    # signature remain unchanged, so this specifically exercises the generation guard.
    plugin._handoff = SimpleNamespace(config=SimpleNamespace(target_path=str(target)))
    assert plugin._enabled is True
    assert callbacks[0]() is False


@pytest.mark.parametrize("result_kind, expected_state", [
    ("hardlink", "completed"),
    ("missing", "accepted"),
    ("copy", "accepted"),
    ("symlink", "accepted"),
    ("mutated_source", "accepted"),
])
def test_manual_status_completes_only_after_hardlink_readback(manual_workspace, result_kind, expected_state):
    _, workspace, _, target, source, _ = manual_workspace
    native_transfer = workspace.transfer

    def transfer(**kwargs):
        result = native_transfer(**kwargs)
        if not kwargs.get("preview"):
            destination = target / source.name
            if result_kind == "hardlink":
                os.link(source, destination)
            elif result_kind == "copy":
                import shutil
                shutil.copy2(source, destination)
            elif result_kind == "symlink":
                destination.symlink_to(source)
            elif result_kind == "mutated_source":
                os.link(source, destination)
                source.write_bytes(b"changed after submit")
        return result

    workspace.transfer = transfer
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    response = workspace.execute(preview.preview_id)
    assert response.items[0].state == expected_state
    if result_kind == "hardlink":
        assert response.state == "completed"
        assert response.completion_scope == "file_transfer"
        assert response.items[0].completion_scope == "file_transfer"
        assert "刮削请见原生历史" in response.items[0].message
    else:
        assert response.state == "accepted"
        assert response.completion_scope is None
        assert response.items[0].completion_scope is None


def test_manual_preview_stops_when_pinned_metadata_identity_changes(manual_workspace, monkeypatch):
    plugin, workspace, _, target, source, calls = manual_workspace
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    changed = MediaInfo()
    changed.from_dict({
        "media_source": "manko", "media_id": "different-native-id", "scrape_source": "manko",
        "type": "电影", "title": "Synthetic title", "year": "2026", "actors": [],
    })
    monkeypatch.setattr(plugin, "recognize_media", lambda **_: changed)
    assert workspace.execute(preview.preview_id).state == "failed"
    assert not any(not call.get("preview") for call in calls)


def test_manual_preview_rejects_symlink_escape(manual_workspace, tmp_path):
    _, workspace, root, target, _, _ = manual_workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = outside / "TEST-222.mkv"
    escaped.write_bytes(b"outside fixture")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前测试宿主不允许创建目录符号链接")
    with pytest.raises(ValueError, match="源路径"):
        workspace.preview(ManualPreviewRequest(
            path=str(link / escaped.name), target_path=str(target), media_source="manko",
        ))


def test_manual_preview_atomic_claim_prevents_concurrent_duplicate_execute(manual_workspace):
    _, workspace, _, target, source, calls = manual_workspace
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    barrier = Barrier(2)
    get_preview = workspace._get_preview

    def coordinated_get_preview(preview_id):
        record = get_preview(preview_id)
        if record and record["state"] == "previewed":
            barrier.wait(timeout=5)
        return record

    workspace._get_preview = coordinated_get_preview
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: workspace.execute(preview.preview_id), range(2)))
    assert sum(not call.get("preview") for call in calls) <= 1
    assert all(response.state in ("accepted", "preparing", "failed", "submitting", "uncertain") for response in responses)


def test_manual_claimed_intent_survives_crash_before_submitting_without_retry(manual_workspace, monkeypatch):
    _, workspace, _, target, source, calls = manual_workspace
    preview = workspace.preview(ManualPreviewRequest(
        path=str(source), target_path=str(target), media_source="manko",
    ))
    ledger = workspace._ledger_db()
    original_transition = ledger.transition

    def interrupt_after_claim(key, expected, state, patch=None):
        if key == preview.preview_id and state == "submitting":
            raise SystemExit("simulated process interruption")
        return original_transition(key, expected, state, patch)

    monkeypatch.setattr(ledger, "transition", interrupt_after_claim)
    with pytest.raises(SystemExit, match="simulated process interruption"):
        workspace.execute(preview.preview_id)

    stored_preview = workspace._get_preview(preview.preview_id)
    intent_key = stored_preview["payload"]["items"][0]["intent_key"]
    assert ledger.get(intent_key)["state"] == "claimed"
    status = workspace.status(preview.preview_id)
    assert status.items[0].state == "claimed"
    assert status.state == "preparing"
    assert not any(not call.get("preview") for call in calls)

    # A restarted request can only read the preparing state; it must never replay.
    assert workspace.execute(preview.preview_id).items[0].state == "claimed"
    assert not any(not call.get("preview") for call in calls)


def test_manual_callbacks_keep_per_file_source_when_native_defers_them(manual_workspace):
    _, workspace, root, target, first_source, calls = manual_workspace
    second_source = root / "TEST-456.mkv"
    second_source.write_bytes(b"second synthetic media")
    callbacks = []
    native_transfer = workspace.transfer

    def deferred_transfer(**kwargs):
        result = native_transfer(**kwargs)
        if not kwargs.get("preview"):
            callbacks.append(kwargs["continue_callback"])
        return result

    workspace.transfer = deferred_transfer
    preview = workspace.preview(ManualPreviewRequest(
        path=str(root), target_path=str(target), media_source="manko",
    ))
    assert preview.summary.success == 2
    assert workspace.execute(preview.preview_id).state == "accepted"
    assert len(callbacks) == 2
    first_source.write_bytes(b"modified after submission")
    assert callbacks[0]() is False
    assert callbacks[1]() is True


def test_manual_fastapi_routes_require_admin_and_use_response_models(manual_workspace):
    _, workspace, _, target, source, calls = manual_workspace
    app = FastAPI()
    for route in api_routes(workspace):
        app.add_api_route(route["path"], route["endpoint"], methods=route["methods"],
                          response_model=route["response_model"])
    app.dependency_overrides[get_sync_session] = lambda: None
    client = TestClient(app)
    assert client.get("/manual/options").status_code in (401, 403)
    assert client.get("/manual/browse", params={"path": str(target), "kind": "target"}).status_code in (401, 403)
    assert client.post("/manual/preview", json={
        "path": str(source), "target_path": str(target), "media_source": "manko",
    }).status_code in (401, 403)
    assert client.post("/manual/execute", json={"preview_id": "unknown"}).status_code in (401, 403)
    assert client.get("/manual/status", params={"preview_id": "unknown"}).status_code in (401, 403)
    assert calls == []
    app.dependency_overrides[get_current_active_superuser] = lambda: object()
    client = TestClient(app)
    assert client.get("/manual/options").status_code == 200
    assert client.get("/manual/browse", params={"path": str(target), "kind": "target"}).status_code == 200
    preview = client.post("/manual/preview", json={
        "path": str(source), "target_path": str(target), "media_source": "manko",
    })
    assert preview.status_code == 200
    preview_id = preview.json()["preview_id"]
    assert client.get("/manual/status", params={"preview_id": preview_id}).status_code == 200
    assert client.post("/manual/execute", json={"preview_id": preview_id}).status_code == 200


def test_manual_number_extraction_rejects_ambiguity_and_parent_conflict(manual_workspace):
    _, workspace, root, _, _, _ = manual_workspace
    ambiguous = root / "TEST-123_OTHER-456.mkv"
    assert workspace._number_for(ambiguous, workspace.roots) is None
    conflict_dir = root / "TEST-456"
    conflict_dir.mkdir()
    conflict = conflict_dir / "TEST-123.mkv"
    assert workspace._number_for(conflict, workspace.roots) is None
    fallback_dir = root / "TEST-789"
    fallback_dir.mkdir()
    fallback = fallback_dir / "sample.mkv"
    assert workspace._number_for(fallback, workspace.roots) == "TEST-789"
