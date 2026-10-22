"""原生交接协调器的离线合同：真实模型与 SQLite，仅在外部执行边界模拟。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.plugins.xiaojiejiescraping.handoff import HandoffConfig, HandoffCoordinator  # pylint: disable=import-error,no-name-in-module
from app.plugins.xiaojiejiescraping.ledger import HandoffLedger, LedgerFullError  # pylint: disable=import-error,no-name-in-module
from app.schemas.file import FileItem
from app.schemas.transfer import DownloaderFile, DownloaderTorrent
from app.schemas.types import MediaSource, MediaType
from app.sdk.media import MediaInfo, MetaBase
from app.schemas.system import DownloaderConf, TransferDirectoryConf
from app.application import service as service_directory


class SimulatedCrash(BaseException):
    """模拟进程终止，不能被普通 Exception 降级逻辑吞掉。"""


@pytest.fixture
def handoff(tmp_path, plugin_host_context):
    inputs, output = tmp_path / "inputs", tmp_path / "output"
    inputs.mkdir()
    output.mkdir()
    source = inputs / "TEST-123 (2026).mkv"
    source.write_bytes(b"synthetic fixture; not media content")
    target = output / "TEST-123.mkv"
    config = HandoffConfig(
        mode="execute", target_path=str(output), operation="copy", roots=(inputs,),
        sources=("manko",), ownership_ack=True, auxiliary_ack=True,
    )
    torrent = DownloaderTorrent(downloader="qbit-main", hash="a" * 40)
    state = SimpleNamespace(
        source=source, target=target, inputs=inputs, output=output, config=config, torrent=torrent,
        ledger_path=tmp_path / "handoff.sqlite3", calls=[], recognitions=[], history_calls=[],
        rows=[], history_override=None, execute_response=(True, "accepted"),
        before_recognize=None, before_preview=None, before_execute=None,
        metadata=MediaInfo(media_source=MediaSource("manko"), media_id="fixture_native_123",
                           type=MediaType.MOVIE, title="Synthetic fixture", year="2026"),
    )
    state.ledger = HandoffLedger(state.ledger_path)

    def recognize(context, meta):
        state.recognitions.append((context, meta))
        if state.before_recognize:
            state.before_recognize()
        return deepcopy(state.metadata)

    def history(**kwargs):
        state.history_calls.append(kwargs)
        if state.history_override:
            return state.history_override()
        return {"items": deepcopy(state.rows), "total": len(state.rows)}

    def transfer(**kwargs):
        state.calls.append(kwargs)
        if kwargs["preview"]:
            if state.before_preview:
                custom = state.before_preview(kwargs)
                if custom is not None:
                    return custom
            return True, {"items": [{"source": str(source), "source_storage": "local", "target": str(target), "success": True}]}
        assert state.ledger.get(state.key)["state"] == "submitting"
        if state.before_execute:
            state.before_execute(kwargs)
        return state.execute_response

    state.recognize, state.history, state.transfer = recognize, history, transfer
    state.coordinator = HandoffCoordinator(state.ledger, config, recognize, transfer=transfer, history=history)
    state.eligible = lambda _payload: True
    state.continue_ = lambda: True
    return state


def enqueue(state):
    record = state.coordinator.enqueue(state.torrent, state.source)
    assert record is not None
    state.key = record["key"]
    return record


def run(state):
    return state.coordinator.run_pending(state.eligible, state.continue_)


def history_row(state, *, identity=1, status=True):
    stat = state.source.stat()
    return {
        "id": identity, "status": status, "src": str(state.source), "src_storage": "local",
        "dest": str(state.target), "dest_storage": "local", "media_source": "manko",
        "media_id": "fixture_native_123", "downloader": state.torrent.downloader,
        "download_hash": state.torrent.hash, "mode": state.coordinator.config.operation,
        "src_fileitem": {"path": str(state.source), "storage": "local", "size": stat.st_size,
                         "modify_time": stat.st_mtime},
    }


def simulate_completed_transfer(state, identity=1):
    """只创建测试目标文件来模拟宿主副作用，不调用任何真实整理入口。"""
    state.target.write_bytes(state.source.read_bytes())
    state.rows.append(history_row(state, identity=identity))


def restart(state, config=None):
    return HandoffCoordinator(
        HandoffLedger(state.ledger_path), config or state.coordinator.config,
        state.recognize, transfer=state.transfer, history=state.history,
    )


@pytest.mark.parametrize("changes", [
    {"mode": "discover"}, {"mode": "unknown"}, {"operation": "move"}, {"operation": ""},
    {"ownership_ack": False}, {"auxiliary_ack": False}, {"sources": ()}, {"roots": ()},
    {"target_path": "/"}, {"target_path": "relative"}, {"target_path": ""},
])
def test_invalid_or_unapproved_execution_configuration_cannot_enqueue(handoff, changes):
    config = replace(handoff.config, **changes)
    assert config.target() is None
    coordinator = restart(handoff, config)
    assert coordinator.enqueue(handoff.torrent, handoff.source) is None
    assert handoff.calls == [] and handoff.recognitions == []
    assert HandoffConfig().target() is None


def test_target_must_exist_and_not_overlap_source_roots(handoff, tmp_path):
    child = handoff.inputs / "nested-output"
    child.mkdir()
    alias = tmp_path / "input-alias"
    alias.symlink_to(handoff.inputs, target_is_directory=True)
    for target in (handoff.inputs, child, tmp_path, alias, tmp_path / "missing", handoff.source):
        assert replace(handoff.config, target_path=str(target)).target() is None
    assert replace(handoff.config, target_path=str(handoff.output / ".." / "output")).target() is None


@pytest.mark.parametrize("operation", ["copy", "link"])
def test_preview_uses_real_models_and_never_executes(handoff, operation):
    handoff.coordinator.config = replace(handoff.config, mode="preview", operation=operation, ownership_ack=False, auxiliary_ack=False)
    enqueue(handoff)
    result = run(handoff)[0]
    assert result["state"] == "previewed"
    assert len(handoff.calls) == 1 and handoff.calls[0]["preview"] is True
    call = handoff.calls[0]
    assert isinstance(call["fileitem"], FileItem)
    assert isinstance(call["meta"], MetaBase)
    assert isinstance(call["mediainfo"], MediaInfo)
    assert call["fileitem"].path == str(handoff.source)
    assert call["fileitem"].storage == "local"
    assert call["transfer_type"] == operation
    assert call["target_path"] == handoff.output
    assert call["background"] is False and call["manual"] is False and call["force"] is False
    assert call["scrape"] is True and call["sync_extra_files"] is False
    assert call["library_type_folder"] is False and call["library_category_folder"] is False
    assert call["media_source"] == MediaSource("manko")
    assert call["media_id"] == "fixture_native_123"
    assert call["mediainfo"].scrape_source == "manko"
    directory = call["target_directory"]
    assert isinstance(directory, TransferDirectoryConf)
    assert directory.library_path == str(handoff.output)
    assert directory.library_storage == "local"
    assert directory.transfer_type == operation
    assert directory.overwrite_mode == "never"
    assert directory.renaming is True and directory.scraping is True and directory.notify is False
    assert directory.library_type_folder is False and directory.library_category_folder is False
    assert call["meta"].begin_season is None and call["meta"].begin_episode is None
    assert handoff.history_calls == []
    assert not handoff.target.exists()


def test_boolean_true_means_accepted_until_history_proves_completion(handoff):
    enqueue(handoff)
    result = run(handoff)[0]
    assert result["state"] == "accepted"
    assert [call["preview"] for call in handoff.calls] == [True, False]
    assert result["payload"]["baseline_history_ids"] == []
    assert len(handoff.history_calls) == 2
    assert handoff.history_calls[0]["filters"] == {"src": str(handoff.source), "download_hash": handoff.torrent.hash}
    assert not handoff.target.exists()
    restored = restart(handoff)
    assert restored.run_pending(handoff.eligible, handoff.continue_) == []
    assert restored.reconcile()[0]["state"] == "accepted"
    assert len(handoff.calls) == 2


def test_matching_new_public_history_and_actual_target_prove_completion(handoff):
    enqueue(handoff)
    handoff.before_execute = lambda _kwargs: simulate_completed_transfer(handoff)
    result = run(handoff)[0]
    assert result["state"] == "completed"
    assert result["payload"]["history_id"] == 1
    assert result["payload"]["reason"] == "verified_native_history"
    assert result["payload"]["completion_scope"] == "file_transfer"
    assert handoff.coordinator.enqueue(handoff.torrent, handoff.source) is None
    assert restart(handoff).run_pending(handoff.eligible, handoff.continue_) == []
    assert len(handoff.calls) == 2


def test_public_history_sdk_accepts_request_and_returns_real_snapshot_models(handoff, monkeypatch):
    from app.sdk import queries

    calls = []

    def read_history(filters, page):
        validated_filter = queries.TransferHistoryFilter.model_validate(filters)
        validated_page = queries.QueryPageRequest.model_validate(page)
        calls.append((validated_filter, validated_page))
        rows = [queries.TransferHistorySnapshot.model_validate(row) for row in handoff.rows]
        return queries.QueryPage[queries.TransferHistorySnapshot](items=rows, total=len(rows), page=1, count=200)

    monkeypatch.setattr(queries, "_service", lambda: SimpleNamespace(list_transfer_history=read_history))
    handoff.coordinator = HandoffCoordinator(handoff.ledger, handoff.config, handoff.recognize, transfer=handoff.transfer)
    enqueue(handoff)
    handoff.before_execute = lambda _kwargs: simulate_completed_transfer(handoff)
    assert run(handoff)[0]["state"] == "completed"
    assert len(calls) == 2
    assert calls[0][0].src == str(handoff.source)
    assert calls[0][0].download_hash == handoff.torrent.hash
    assert calls[0][1].count == 200


def test_duplicate_claim_and_parallel_coordinators_submit_only_once(handoff):
    enqueue(handoff)
    assert handoff.coordinator.enqueue(handoff.torrent, handoff.source) is None
    coordinators = [restart(handoff), restart(handoff)]
    barrier = Barrier(2)

    def worker(coordinator):
        barrier.wait()
        return coordinator.run_pending(handoff.eligible, handoff.continue_)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, coordinators))
    assert sum(len(value) for value in results) == 1
    assert [call["preview"] for call in handoff.calls] == [True, False]
    assert handoff.ledger.get(handoff.key)["state"] == "accepted"


def test_entire_torrent_batch_must_fit_before_any_file_can_execute(handoff, tmp_path):
    second = handoff.inputs / "TEST-124.mkv"
    second.write_bytes(b"second fixture")
    ledger = HandoffLedger(tmp_path / "limited.sqlite3", max_entries=1)
    coordinator = HandoffCoordinator(ledger, handoff.config, handoff.recognize, handoff.transfer, handoff.history)
    with pytest.raises(LedgerFullError):
        coordinator.enqueue_batch(handoff.torrent, (handoff.source, second))
    assert ledger.list_records() == []
    assert coordinator.run_pending(handoff.eligible, handoff.continue_) == []
    assert handoff.calls == [] and handoff.recognitions == []


def test_invalid_file_in_torrent_batch_cannot_leave_a_partial_claim(handoff, tmp_path):
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"outside fixture")
    assert handoff.coordinator.enqueue_batch(handoff.torrent, (handoff.source, outside)) == []
    assert handoff.ledger.list_records() == []


def test_legacy_incomplete_admission_is_held_without_resubmission(handoff):
    payload = {
        "path": str(handoff.source), "config_key": handoff.config.signature(),
        "admission_complete": False,
    }
    handoff.ledger.claim("legacy-incomplete", payload)
    assert run(handoff) == []
    assert handoff.ledger.get("legacy-incomplete")["state"] == "claimed"
    assert handoff.calls == []


@pytest.mark.parametrize("response", [(False, "unknown"), None, True, (1, "not bool")])
def test_nonconfirming_native_return_is_uncertain_and_never_retried(handoff, response):
    enqueue(handoff)
    handoff.execute_response = response
    assert run(handoff)[0]["state"] == "uncertain"
    assert restart(handoff).run_pending(handoff.eligible, handoff.continue_) == []
    assert restart(handoff).reconcile()[0]["state"] == "uncertain"
    assert len(handoff.calls) == 2


def test_native_exception_is_unknown_outcome_not_safe_to_retry(handoff):
    enqueue(handoff)

    def unavailable(_kwargs):
        raise ConnectionError("synthetic native response lost")

    handoff.before_execute = unavailable
    assert run(handoff)[0]["state"] == "uncertain"
    assert restart(handoff).run_pending(handoff.eligible, handoff.continue_) == []
    assert len(handoff.calls) == 2


@pytest.mark.parametrize("phase", ["preview", "before_commit", "after_commit"])
def test_process_crash_preserves_durable_ownership_without_resending(handoff, phase):
    enqueue(handoff)

    def crash(_kwargs):
        if phase == "after_commit":
            simulate_completed_transfer(handoff)
        raise SimulatedCrash()

    if phase == "preview":
        handoff.before_preview = crash
    else:
        handoff.before_execute = crash
    with pytest.raises(SimulatedCrash):
        run(handoff)
    assert handoff.ledger.get(handoff.key)["state"] == ("preparing" if phase == "preview" else "submitting")
    restored = restart(handoff)
    assert restored.run_pending(handoff.eligible, handoff.continue_) == []
    restored.reconcile()
    expected = {"preview": "preparing", "before_commit": "uncertain", "after_commit": "completed"}[phase]
    assert restored.ledger.get(handoff.key)["state"] == expected
    assert len(handoff.calls) == (1 if phase == "preview" else 2)


def test_existing_verified_native_history_skips_new_execution(handoff):
    enqueue(handoff)
    simulate_completed_transfer(handoff)
    result = run(handoff)[0]
    assert result["state"] == "completed"
    assert result["payload"]["reason"] == "verified_existing_native_history"
    assert result["payload"]["completion_scope"] == "file_transfer"
    assert [call["preview"] for call in handoff.calls] == [True]


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling_symlink", "directory"])
def test_occupied_destination_without_matching_history_is_preserved(handoff, kind):
    original = b"unrelated existing content"
    linked = handoff.output / "unrelated.bin"
    if kind == "file":
        handoff.target.write_bytes(original)
    elif kind == "symlink":
        linked.write_bytes(original)
        handoff.target.symlink_to(linked)
    elif kind == "dangling_symlink":
        handoff.target.symlink_to(linked)
    else:
        handoff.target.mkdir()
    enqueue(handoff)
    result = run(handoff)[0]
    assert result["state"] == "failed"
    assert [call["preview"] for call in handoff.calls] == [True]
    if kind == "file":
        assert handoff.target.read_bytes() == original
    elif kind == "symlink":
        assert handoff.target.is_symlink() and linked.read_bytes() == original
    elif kind == "dangling_symlink":
        assert handoff.target.is_symlink() and not linked.exists()
    else:
        assert handoff.target.is_dir()


@pytest.mark.parametrize(("field", "value"), [
    ("src", "/wrong/source.mkv"), ("src_storage", "remote"), ("dest", "/wrong/target.mkv"),
    ("dest_storage", "remote"), ("media_source", "other_source"), ("media_id", "other_id"),
    ("downloader", "other_downloader"), ("download_hash", "b" * 40), ("mode", "move"),
    ("id", True), ("id", "1"),
])
def test_mismatched_history_does_not_prove_completion(handoff, field, value):
    enqueue(handoff)
    assert run(handoff)[0]["state"] == "accepted"
    simulate_completed_transfer(handoff)
    handoff.rows[0][field] = value
    assert handoff.coordinator.reconcile()[0]["state"] == "accepted"
    assert len(handoff.calls) == 2


@pytest.mark.parametrize(("field", "value"), [
    ("path", "/wrong/source.mkv"), ("storage", "remote"), ("size", 999),
    ("modify_time", 1), ("modify_time", float("nan")), ("modify_time", "timestamp"),
])
def test_history_source_fingerprint_must_match(handoff, field, value):
    enqueue(handoff)
    run(handoff)
    simulate_completed_transfer(handoff)
    handoff.rows[0]["src_fileitem"][field] = value
    assert handoff.coordinator.reconcile()[0]["state"] == "accepted"


@pytest.mark.parametrize("target_state", ["missing", "wrong_size", "outside_symlink"])
def test_history_success_requires_a_matching_actual_target_file(handoff, tmp_path, target_state):
    enqueue(handoff)
    run(handoff)
    handoff.rows.append(history_row(handoff))
    if target_state == "wrong_size":
        handoff.target.write_bytes(b"wrong")
    elif target_state == "outside_symlink":
        outside = tmp_path / "elsewhere.mkv"
        outside.write_bytes(handoff.source.read_bytes())
        handoff.target.symlink_to(outside)
    assert handoff.coordinator.reconcile()[0]["state"] == "accepted"


def test_baseline_history_id_cannot_be_reused_as_new_completion(handoff):
    enqueue(handoff)
    old = history_row(handoff, identity=7, status=False)
    old["dest"] = str(handoff.output / "old-target.mkv")
    handoff.rows = [old]
    assert run(handoff)[0]["state"] == "accepted"
    handoff.rows = []
    simulate_completed_transfer(handoff, identity=7)
    assert handoff.coordinator.reconcile()[0]["state"] == "accepted"


def test_only_unambiguous_explicit_failed_history_proves_failure(handoff):
    enqueue(handoff)
    run(handoff)
    handoff.rows = [history_row(handoff, status=False)]
    result = handoff.coordinator.reconcile()[0]
    assert result["state"] == "failed"
    assert result["payload"]["reason"] == "verified_native_history"
    assert not handoff.target.exists()
    assert restart(handoff).run_pending(handoff.eligible, handoff.continue_) == []


@pytest.mark.parametrize("status", [None, "false", "true", 0, 1])
def test_nonboolean_history_status_is_unknown_not_a_terminal_result(handoff, status):
    enqueue(handoff)
    run(handoff)
    handoff.rows = [history_row(handoff, status=status)]
    assert handoff.coordinator.reconcile()[0]["state"] == "accepted"


def test_ambiguous_matching_history_does_not_settle_task(handoff):
    enqueue(handoff)
    run(handoff)
    simulate_completed_transfer(handoff, identity=1)
    handoff.rows.append(history_row(handoff, identity=2))
    assert handoff.coordinator.reconcile()[0]["state"] == "accepted"


@pytest.mark.parametrize("page", [None, {}, {"items": [], "total": None}, {"items": [], "total": True}, {"items": [], "total": 1}])
def test_unavailable_or_partial_history_preflight_prevents_execution(handoff, page):
    enqueue(handoff)
    handoff.history_override = lambda: page
    assert run(handoff)[0]["state"] == "failed"
    assert [call["preview"] for call in handoff.calls] == [True]


@pytest.mark.parametrize("changes", [
    {"target": "/outside/output.mkv"}, {"source": "/wrong/source.mkv"},
    {"source_storage": "remote"}, {"success": False},
])
def test_invalid_native_preview_cannot_execute(handoff, changes):
    enqueue(handoff)
    item = {"source": str(handoff.source), "source_storage": "local", "target": str(handoff.target), "success": True, **changes}
    handoff.before_preview = lambda _kwargs: (True, {"items": [item]})
    assert run(handoff)[0]["state"] == "failed"
    assert [call["preview"] for call in handoff.calls] == [True]


@pytest.mark.parametrize("preview", [(False, {}), (True, {}), (True, {"items": []}), (True, {"items": [{}, {}]})])
def test_native_preview_must_be_exactly_one_success(handoff, preview):
    enqueue(handoff)
    handoff.before_preview = lambda _kwargs: preview
    assert run(handoff)[0]["state"] == "failed"
    assert len(handoff.calls) == 1


@pytest.mark.parametrize("change", ["contents", "mtime", "missing", "year"])
def test_changed_source_or_filename_year_conflict_cannot_transfer(handoff, change):
    enqueue(handoff)
    if change == "contents":
        handoff.source.write_bytes(b"changed source fixture")
    elif change == "mtime":
        stat = handoff.source.stat()
        os.utime(handoff.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    elif change == "missing":
        handoff.source.unlink()
    else:
        handoff.metadata.year = "2025"
    assert run(handoff)[0]["state"] == "failed"
    assert handoff.calls == []


def test_source_symlink_retarget_cannot_reuse_same_stat_fingerprint(handoff):
    initial = handoff.inputs / "initial.mkv"
    handoff.source.rename(initial)
    handoff.source.symlink_to(initial)
    enqueue(handoff)
    other = handoff.inputs / "replacement.mkv"
    other.write_bytes(b"x" * initial.stat().st_size)
    stat = initial.stat()
    os.utime(other, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    handoff.source.unlink()
    handoff.source.symlink_to(other)
    assert run(handoff)[0]["state"] == "failed"
    assert handoff.calls == []


def test_replaced_inode_cannot_reuse_same_path_size_and_mtime(handoff):
    enqueue(handoff)
    stat = handoff.source.stat()
    replacement = handoff.inputs / "replacement.tmp"
    replacement.write_bytes(b"x" * stat.st_size)
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    replacement.replace(handoff.source)
    assert handoff.source.stat().st_ino != stat.st_ino
    assert run(handoff)[0]["state"] == "failed"
    assert handoff.calls == []


def test_target_symlink_retarget_cannot_change_enqueued_destination(handoff, tmp_path):
    alias = tmp_path / "output-alias"
    alias.symlink_to(handoff.output, target_is_directory=True)
    handoff.coordinator.config = replace(handoff.config, target_path=str(alias))
    enqueue(handoff)
    different = tmp_path / "different-output"
    different.mkdir()
    alias.unlink()
    alias.symlink_to(different, target_is_directory=True)
    assert run(handoff)[0]["state"] == "failed"
    assert handoff.calls == []


@pytest.mark.parametrize("state", ["submitting", "accepted", "uncertain", "completed"])
@pytest.mark.parametrize("change", ["target", "source_order", "hash", "rename", "hardlink", "operation"])
def test_physical_source_ownership_cannot_be_bypassed_by_mutable_intent(handoff, tmp_path, state, change):
    original = enqueue(handoff)
    assert handoff.ledger.transition(handoff.key, ("claimed",), state)
    config, torrent, path = handoff.config, handoff.torrent, handoff.source
    if change == "target":
        target = tmp_path / "new-output"
        target.mkdir()
        config = replace(config, target_path=str(target))
    elif change == "source_order":
        config = replace(config, sources=("fanza", "manko"))
    elif change == "hash":
        torrent = torrent.model_copy(update={"hash": "b" * 40})
    elif change == "rename":
        path = handoff.inputs / "renamed-TEST-123.mkv"
        handoff.source.rename(path)
    elif change == "hardlink":
        path = handoff.inputs / "hardlink-TEST-123.mkv"
        os.link(handoff.source, path)
    else:
        config = replace(config, operation="link")
    restored = restart(handoff, config)
    assert restored.enqueue(torrent, path) is None
    assert len(restored.ledger.list_records()) == 1
    assert restored.ledger.get(original["key"])["state"] == state
    assert restored.run_pending(handoff.eligible, handoff.continue_) == []
    assert handoff.calls == []


@pytest.mark.parametrize("stage", ["before_run", "after_metadata", "after_preview"])
def test_stop_callback_prevents_unsubmitted_work(handoff, stage):
    enqueue(handoff)
    alive = [True]
    handoff.continue_ = lambda: alive[0]
    if stage == "before_run":
        alive[0] = False
    elif stage == "after_metadata":
        handoff.before_recognize = lambda: alive.__setitem__(0, False)
    else:
        handoff.before_preview = lambda _kwargs: alive.__setitem__(0, False)
    result = run(handoff)
    assert all(call["preview"] for call in handoff.calls)
    assert handoff.ledger.get(handoff.key)["state"] == ("claimed" if stage == "before_run" else "failed")
    assert bool(result) is (stage != "before_run")


def test_changed_current_configuration_holds_old_claim_without_submitting(handoff):
    enqueue(handoff)
    changed = replace(handoff.config, operation="link")
    restored = restart(handoff, changed)
    assert restored.run_pending(handoff.eligible, handoff.continue_) == []
    assert restored.ledger.get(handoff.key)["state"] == "claimed"
    assert handoff.calls == [] and handoff.recognitions == []


def test_eligibility_is_rechecked_after_preview(handoff):
    enqueue(handoff)
    checks = []

    def eligible(_payload):
        checks.append(True)
        return len(checks) == 1

    handoff.eligible = eligible
    assert run(handoff)[0]["state"] == "failed"
    assert [call["preview"] for call in handoff.calls] == [True]


@pytest.mark.parametrize("change", ["stop", "source", "eligibility"])
def test_history_preflight_cannot_hide_a_change_before_submission(handoff, change):
    enqueue(handoff)
    alive, eligible = [True], [True]
    handoff.continue_ = lambda: alive[0]
    handoff.eligible = lambda _payload: eligible[0]

    def changed_during_history():
        if change == "stop":
            alive[0] = False
        elif change == "source":
            handoff.source.write_bytes(b"source changed during history read")
        else:
            eligible[0] = False
        return {"items": [], "total": 0}

    handoff.history_override = changed_during_history
    assert run(handoff)[0]["state"] == "failed"
    assert [call["preview"] for call in handoff.calls] == [True]


@pytest.mark.parametrize("source", ["themoviedb", "other_plugin"])
def test_unselected_recognition_source_cannot_enter_native_preview(handoff, source):
    enqueue(handoff)
    handoff.metadata.media_source = MediaSource(source)
    assert run(handoff)[0]["state"] == "failed"
    assert handoff.calls == []


@pytest.mark.parametrize("metadata", [None, MediaInfo(type=MediaType.TV), MediaInfo(type=MediaType.MOVIE)])
def test_unverified_metadata_cannot_reach_transfer(handoff, metadata):
    enqueue(handoff)
    handoff.metadata = metadata
    assert run(handoff)[0]["state"] == "failed"
    assert handoff.calls == []


@pytest.fixture
def integrated(handoff, tmp_path, monkeypatch):
    from app.plugins.xiaojiejiescraping import XiaojiejieScraping  # pylint: disable=import-error,no-name-in-module

    data_path = tmp_path / "plugin-data"
    data_path.mkdir()
    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _: data_path)
    monkeypatch.setattr(service_directory, "_config_loader", lambda _key, _model: [
        DownloaderConf(name="qbit-main", type="qbittorrent", enabled=True),
    ])
    plugin = XiaojiejieScraping()
    config = {"enabled": True, "discovery_enabled": True, "downloader": "qbit-main", "torrent_tag": "JAV",
              "recognize_dirs": [str(handoff.inputs)], "sources": ["manko"]}
    handoff.torrent = handoff.torrent.model_copy(update={
        "progress": 100, "state": "completed", "tags": "JAV", "content_path": str(handoff.source),
        "save_path": str(handoff.inputs),
    })
    handoff.query_calls = []
    handoff.raw_query_calls, handoff.raw_changes, handoff.raw_response = [], {}, None
    handoff.manifest_response = None

    def build_manifest(torrent):
        content = Path(torrent.content_path)
        files = (content,) if content.is_file() else sorted(content.rglob("*.mkv"))
        return [DownloaderFile(id=index, name=path.relative_to(Path(torrent.save_path)).as_posix(),
                               size=path.stat().st_size, priority=1, progress=1)
                for index, path in enumerate(files)]

    handoff.build_manifest = build_manifest

    def raw_completed(**kwargs):
        handoff.raw_query_calls.append(kwargs)
        assert isinstance(kwargs.get("ids"), list) and len(kwargs["ids"]) == 1
        assert kwargs.get("tags") is None
        if handoff.raw_response is not None:
            return handoff.raw_response()
        return [{"hash": handoff.torrent.hash, "state": "uploading", "progress": 1.0,
                 "amount_left": 0, "tags": handoff.torrent.tags,
                 "save_path": "/raw/downloader/container/path", **handoff.raw_changes}]

    service_module = SimpleNamespace(get_instances=lambda: {
        "qbit-main": SimpleNamespace(get_completed_torrents=raw_completed),
    })
    monkeypatch.setattr(service_directory, "_module_loader", lambda _kind: [service_module])

    def run_module(method, **kwargs):
        assert method in ("list_torrents", "torrent_files"), "poll must not mutate downloader or file state"
        handoff.query_calls.append((method, kwargs))
        if method == "torrent_files":
            assert kwargs == {"tid": handoff.torrent.hash, "downloader": "qbit-main"}
            return handoff.manifest_response() if handoff.manifest_response else build_manifest(handoff.torrent)
        return [handoff.torrent]

    monkeypatch.setattr(plugin.chain, "run_module", run_module)

    def configure(**changes):
        selected = {**config, **changes}
        plugin.init_plugin(selected)
        assert plugin._handoff is not None
        handoff.coordinator = plugin._handoff
        handoff.ledger = plugin._handoff.ledger
        plugin._handoff.recognize = handoff.recognize
        plugin._handoff.history = handoff.history

        def native_boundary(**kwargs):
            assert not plugin._lock._is_owned(), "native transfer must run outside the plugin lifecycle lock"
            records = handoff.ledger.list_records(limit=10000)
            assert len(records) == 1
            handoff.key = records[0]["key"]
            return handoff.transfer(**kwargs)

        plugin._handoff.transfer = native_boundary
        return selected

    configure()
    handoff.plugin, handoff.configure_plugin = plugin, configure
    yield handoff
    plugin.stop_service()


def execution_config(state):
    return {"handoff_mode": "execute", "target_path": str(state.output), "transfer_type": "copy",
            "ownership_ack": True, "auxiliary_ack": True}


def test_plugin_automatic_handoff_is_disabled_by_default(integrated):
    state = integrated
    defaults = state.plugin.get_form()[1]
    assert defaults["handoff_mode"] == "discover"
    assert defaults["ownership_ack"] is False and defaults["auxiliary_ack"] is False
    result = state.plugin.poll_completed()
    assert len(result) == 1 and result[0]["state"] == "discovered"
    assert state.ledger.list_records() == []
    assert state.calls == [] and state.recognitions == []
    assert not state.target.exists()


@pytest.mark.parametrize("changes", [
    {"target_path": ""}, {"ownership_ack": False}, {"auxiliary_ack": False},
    {"transfer_type": "move"}, {"target_path": "/"},
])
def test_plugin_missing_execution_permissions_fail_closed(integrated, changes):
    state = integrated
    state.configure_plugin(**{**execution_config(state), **changes})
    state.plugin.poll_completed()
    assert state.ledger.list_records() == []
    assert state.calls == [] and state.recognitions == []
    assert not state.target.exists()


def test_plugin_preview_records_plan_without_execution(integrated):
    state = integrated
    state.configure_plugin(handoff_mode="preview", target_path=str(state.output), transfer_type="copy")
    state.plugin.poll_completed()
    records = state.ledger.list_records()
    assert len(records) == 1 and records[0]["state"] == "previewed"
    assert [call["preview"] for call in state.calls] == [True]
    assert "仅预览" in str(state.plugin.get_page())
    assert not state.target.exists()


def test_plugin_handoff_executes_once_then_restarts_without_resubmission(integrated):
    state = integrated
    config = execution_config(state)
    state.configure_plugin(**config)
    state.before_execute = lambda _kwargs: simulate_completed_transfer(state)
    state.plugin.poll_completed()
    assert state.ledger.list_records()[0]["state"] == "completed"
    assert [call["preview"] for call in state.calls] == [True, False]
    assert state.calls[-1]["fileitem"].path == str(state.source)
    assert state.calls[-1]["target_storage"] == "local"
    assert "/raw/downloader/container" not in str(state.calls[-1]["fileitem"].path)
    assert "文件整理已核实（刮削独立）" in str(state.plugin.get_page())
    state.configure_plugin(**config)
    state.plugin.poll_completed()
    assert state.ledger.list_records()[0]["state"] == "completed"
    assert len(state.calls) == 2 and len(state.recognitions) == 1


def test_plugin_accepted_status_remains_distinct_and_does_not_resend(integrated):
    state = integrated
    config = execution_config(state)
    state.configure_plugin(**config)
    state.plugin.poll_completed()
    assert state.ledger.list_records()[0]["state"] == "accepted"
    assert "宿主已受理，待证据" in str(state.plugin.get_page())
    state.configure_plugin(**config)
    state.plugin.poll_completed()
    assert state.ledger.list_records()[0]["state"] == "accepted"
    assert len(state.calls) == 2


def test_previously_discovered_file_can_be_enqueued_after_valid_configuration(integrated):
    state = integrated
    state.configure_plugin(**{**execution_config(state), "ownership_ack": False})
    assert len(state.plugin.poll_completed()) == 1
    state.configure_plugin(**execution_config(state))
    assert state.plugin.poll_completed() == []
    assert state.ledger.list_records()[0]["state"] == "accepted"
    assert len(state.calls) == 2


def test_plugin_generation_stop_prevents_execution_after_preview(integrated):
    state = integrated
    state.configure_plugin(**execution_config(state))
    ledger = state.ledger
    state.before_preview = lambda _kwargs: state.plugin.stop_service()
    state.plugin.poll_completed()
    assert ledger.list_records()[0]["state"] == "failed"
    assert [call["preview"] for call in state.calls] == [True]
    assert not state.target.exists()


def test_plugin_batch_admission_failure_cannot_execute_a_partial_torrent(integrated):
    state = integrated
    second = state.inputs / "TEST-124.mkv"
    second.write_bytes(b"second fixture")
    state.torrent = state.torrent.model_copy(update={"content_path": str(state.inputs)})
    state.configure_plugin(**execution_config(state))
    limited = HandoffLedger(state.ledger.path, max_entries=1)
    state.ledger = state.plugin._handoff.ledger = limited
    state.plugin.poll_completed()
    assert limited.list_records() == []
    assert state.calls == [] and state.recognitions == []
    assert state.plugin._handoff_error


def test_plugin_rechecks_fresh_user_tag_after_preview(integrated):
    state = integrated
    state.configure_plugin(**execution_config(state))

    def remove_tag(_kwargs):
        state.torrent = state.torrent.model_copy(update={"tags": "other"})

    state.before_preview = remove_tag
    state.plugin.poll_completed()
    assert state.ledger.list_records()[0]["state"] == "failed"
    assert [call["preview"] for call in state.calls] == [True]
    assert any(kwargs.get("hashs") == [state.torrent.hash] for _, kwargs in state.query_calls)


def test_shared_content_directory_cannot_enqueue_files_absent_from_torrent_manifest(integrated):
    state = integrated
    other = state.inputs / "TEST-999 unrelated.mkv"
    other.write_bytes(b"belongs to another torrent")
    manifest = state.build_manifest(state.torrent)
    state.torrent = state.torrent.model_copy(update={"content_path": str(state.inputs)})
    state.manifest_response = lambda: manifest
    state.configure_plugin(**execution_config(state))
    state.plugin.poll_completed()
    records = state.ledger.list_records(limit=10000)
    assert len(records) == 1 and records[0]["payload"]["path"] == str(state.source)
    assert records[0]["state"] == "accepted"
    assert [call["fileitem"].path for call in state.calls] == [str(state.source), str(state.source)]
    assert other.read_bytes() == b"belongs to another torrent"


@pytest.mark.parametrize("change", ["path", "deselected", "unfinished", "missing"])
def test_manifest_revocation_after_preview_prevents_submission(integrated, change):
    state = integrated
    state.configure_plugin(**execution_config(state))

    def mutate_manifest(_kwargs):
        item = state.build_manifest(state.torrent)[0].model_dump()
        if change == "path":
            item["name"] = "different-owned-file.mkv"
        elif change == "deselected":
            item["priority"] = 0
        elif change == "unfinished":
            item["progress"] = 0.5
        state.manifest_response = lambda: None if change == "missing" else [item]

    state.before_preview = mutate_manifest
    state.plugin.poll_completed()
    assert state.ledger.list_records()[0]["state"] == "failed"
    assert [call["preview"] for call in state.calls] == [True]


def test_generation_change_during_manifest_read_cannot_commit_discovery_or_queue(integrated):
    state = integrated
    state.configure_plugin(**execution_config(state))
    ledger, journal = state.ledger, state.plugin._journal
    manifest = state.build_manifest(state.torrent)

    def stale_manifest():
        state.plugin.stop_service()
        return manifest

    state.manifest_response = stale_manifest
    assert state.plugin.poll_completed() == []
    assert ledger.list_records() == []
    assert journal.cache.get("poll_cursor") is None
    assert state.calls == [] and state.recognitions == []


@pytest.mark.parametrize("changes", [
    {"state": "error"}, {"state": "missingFiles"}, {"state": "checkingUP"}, {"state": "moving"},
    {"progress": 0.99}, {"progress": float("nan")}, {"progress": True},
    {"amount_left": 1}, {"amount_left": float("nan")}, {"amount_left": True},
    {"tags": "JAV-extra"}, {"hash": "b" * 40},
])
def test_raw_downloader_completion_proof_fails_closed(integrated, changes):
    state = integrated
    state.configure_plugin(**execution_config(state))
    state.raw_changes = changes
    state.plugin.poll_completed()
    assert state.ledger.list_records() == []
    assert state.calls == [] and state.recognitions == []
    assert state.raw_query_calls


@pytest.mark.parametrize("response", [None, [], {}, [None]])
def test_missing_raw_completion_capability_evidence_cannot_execute(integrated, response):
    state = integrated
    state.configure_plugin(**execution_config(state))
    state.raw_response = lambda: response
    state.plugin.poll_completed()
    assert state.ledger.list_records() == []
    assert state.calls == [] and state.recognitions == []
    assert state.raw_query_calls


@pytest.mark.parametrize(("raw_state", "expected"), [
    ("uploading", True), ("pausedUP", True), ("error", False),
    ("missingFiles", False), ("checkingUP", False), ("moving", False), ("futureUnknownState", False),
])
def test_official_qbittorrent_completed_filter_is_used_before_handoff(integrated, monkeypatch, raw_state, expected):
    from app.modules.qbittorrent.qbittorrent import Qbittorrent

    state = integrated
    calls = []

    def torrents_info(**kwargs):
        calls.append(kwargs)
        return [{"hash": state.torrent.hash, "state": raw_state, "progress": 1.0,
                 "amount_left": 0, "tags": "JAV"}]

    # 保留官方 get_torrents/get_completed_torrents，仅替换 qBittorrent API 客户端出口。
    client = object.__new__(Qbittorrent)
    client.qbc = SimpleNamespace(torrents_info=torrents_info)
    module = SimpleNamespace(get_instances=lambda: {"qbit-main": client})
    monkeypatch.setattr(service_directory, "_module_loader", lambda _kind: [module])
    assert state.plugin._safe_completed_hash("qbit-main", state.torrent.hash, "JAV") is expected
    assert calls == [{"torrent_hashes": [state.torrent.hash], "status_filter": "completed"}]


@pytest.mark.parametrize("remove_user_tag", [False, True])
def test_queued_second_file_survives_native_whole_torrent_settlement(integrated, monkeypatch, remove_user_tag):
    state = integrated
    second = state.inputs / "TEST-124 (2026).mkv"
    second.write_bytes(b"second synthetic media fixture")
    state.torrent = state.torrent.model_copy(update={"content_path": str(state.inputs)})
    state.configure_plugin(**execution_config(state))
    completed = []
    query_calls = []

    def downloader_query(method, **kwargs):
        if method == "torrent_files":
            return state.build_manifest(state.torrent)
        assert method == "list_torrents"
        query_calls.append(kwargs)
        if kwargs.get("hashs"):
            assert kwargs["hashs"] == [state.torrent.hash]
            assert kwargs["include_all_tags"] is True
            return [state.torrent]
        return [] if completed else [state.torrent]

    monkeypatch.setattr(state.plugin.chain, "run_module", downloader_query)

    def history_query(filters, page):
        rows = [row for row in state.rows if row["src"] == filters["src"] and row["download_hash"] == filters["download_hash"]]
        return {"items": deepcopy(rows), "total": len(rows)}

    def recognize(context, meta):
        identity = "fixture_123" if Path(context.path) == state.source else "fixture_124"
        return MediaInfo(media_source=MediaSource("manko"), media_id=identity, type=MediaType.MOVIE,
                         title="Synthetic fixture", year="2026")

    def native_boundary(**kwargs):
        assert not state.plugin._lock._is_owned()
        source = Path(kwargs["fileitem"].path)
        destination = state.output / source.name
        if kwargs["preview"]:
            return True, {"items": [{"source": str(source), "source_storage": "local", "target": str(destination), "success": True}]}
        record = next(row for row in state.ledger.list_records() if row["payload"]["path"] == str(source))
        assert record["state"] == "submitting"
        destination.write_bytes(source.read_bytes())
        stat = source.stat()
        state.rows.append({
            "id": len(state.rows) + 1, "status": True, "src": str(source), "src_storage": "local",
            "dest": str(destination), "dest_storage": "local", "media_source": str(kwargs["media_source"]),
            "media_id": kwargs["media_id"], "downloader": state.torrent.downloader,
            "download_hash": state.torrent.hash, "mode": "copy",
            "src_fileitem": {"path": str(source), "storage": "local", "size": stat.st_size, "modify_time": stat.st_mtime},
        })
        completed.append(source)
        state.torrent = state.torrent.model_copy(update={"tags": "已整理" if remove_user_tag else "JAV, 已整理"})
        return True, "accepted"

    state.plugin._handoff.recognize = recognize
    state.plugin._handoff.transfer = native_boundary
    state.plugin._handoff.history = history_query
    state.plugin.poll_completed()
    assert len(completed) == 1
    state.plugin.poll_completed()
    records = state.ledger.list_records()
    assert len(records) == 2
    if remove_user_tag:
        assert len(completed) == 1
        assert sorted(row["state"] for row in records) == ["completed", "failed"]
    else:
        assert set(completed) == {state.source, second}
        assert all(row["state"] == "completed" for row in records)
    state.plugin.poll_completed()
    assert len(completed) == (1 if remove_user_tag else 2)
    assert any(kwargs.get("hashs") == [state.torrent.hash] for kwargs in query_calls)


def test_400_plus_400_file_torrents_are_admitted_as_whole_batches(integrated, monkeypatch):
    state = integrated
    folders = [state.inputs / "batch-a", state.inputs / "batch-b"]
    for folder in folders:
        folder.mkdir()
        for index in range(400):
            (folder / f"TEST-123 part{index:03}.mkv").write_bytes(b"fixture")
    torrents = [state.torrent.model_copy(update={"hash": digit * 40, "content_path": str(folder)})
                for digit, folder in zip(("a", "b"), folders)]
    state.torrent = torrents[0]
    state.configure_plugin(**execution_config(state))
    state.raw_response = lambda: [
        {"hash": torrent.hash, "state": "uploading", "progress": 1.0,
         "amount_left": 0, "tags": "JAV"}
        for torrent in torrents
    ]
    submissions = []
    snapshots = []

    def query(method, **kwargs):
        if method == "torrent_files":
            torrent = next(item for item in torrents if item.hash == kwargs["tid"])
            return state.build_manifest(torrent)
        assert method == "list_torrents"
        if kwargs.get("hashs"):
            return [torrent for torrent in torrents if torrent.hash in kwargs["hashs"]]
        return torrents if not submissions else [torrents[1]]

    monkeypatch.setattr(state.plugin.chain, "run_module", query)

    def native(**kwargs):
        source = Path(kwargs["fileitem"].path)
        target = state.output / (source.parent.name + "-" + source.name)
        if kwargs["preview"]:
            return True, {"items": [{"source": str(source), "source_storage": "local", "target": str(target), "success": True}]}
        records = state.ledger.list_records(limit=10000)
        counts = [sum(Path(record["payload"]["path"]).parent == folder for record in records) for folder in folders]
        assert all(record["payload"]["admission_complete"] is True for record in records)
        snapshots.append(counts)
        submissions.append(source)
        return True, "accepted only"

    state.plugin._handoff.transfer = native
    state.plugin.poll_completed()
    assert snapshots == [[400, 0]]
    assert len(state.ledger.list_records(limit=10000)) == 400
    state.plugin.poll_completed()
    assert snapshots == [[400, 0], [400, 400]]
    assert len(state.ledger.list_records(limit=10000)) == 800
    assert len(submissions) == 2


def test_full_ledger_does_not_block_readonly_reconciliation_of_accepted_work(integrated):
    state = integrated
    state.configure_plugin(**execution_config(state))
    limited = HandoffLedger(state.ledger.path, max_entries=1)
    state.ledger = state.plugin._handoff.ledger = limited
    state.plugin.poll_completed()
    assert limited.list_records()[0]["state"] == "accepted"
    simulate_completed_transfer(state)
    new_file = state.inputs / "TEST-124 (2026).mkv"
    new_file.write_bytes(b"new unadmitted fixture")
    state.torrent = state.torrent.model_copy(update={"content_path": str(state.inputs)})
    state.plugin.poll_completed()
    records = limited.list_records()
    assert len(records) == 1 and records[0]["state"] == "completed"
    assert records[0]["payload"]["completion_scope"] == "file_transfer"
    assert len(state.calls) == 2
    assert state.plugin._handoff_error
