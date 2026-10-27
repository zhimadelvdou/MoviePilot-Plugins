"""独立 SQLite 交接账本的持久性、并发及失败关闭离线契约。"""
import hashlib
import json
import multiprocessing
import sqlite3
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from app.plugins.xiaojiejiescraping import (
    ledger,  # pylint: disable=import-error,no-name-in-module
)


def _process_claim(arguments):
    path, index = arguments
    store = ledger.HandoffLedger(path)
    return store.claim("process-shared", {"winner": index})


def test_claim_persists_full_record_and_returns_independent_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger.time, "time", lambda: 1000.5)
    path = tmp_path / "plugin-data" / "handoff.sqlite3"
    store = ledger.HandoffLedger(path)
    payload = {"download_hash": "synthetic-a", "intent": {"path": "/fixture/测试.mkv"}}
    record = store.claim("task-a", payload)
    assert record == {
        "key": "task-a", "state": "claimed", "payload": payload,
        "intent_fingerprint": ledger.task_key(payload), "created_at": 1000.5, "updated_at": 1000.5,
    }
    payload["intent"]["path"] = "changed caller"
    record["payload"]["intent"]["path"] = "changed return"
    restored = ledger.HandoffLedger(path)
    actual = restored.get("task-a")
    assert actual["payload"]["intent"]["path"] == "/fixture/测试.mkv"
    actual["payload"]["intent"]["path"] = "changed read"
    assert store.get("task-a")["payload"]["intent"]["path"] == "/fixture/测试.mkv"
    assert restored.claim("task-a", {"different": "intent"}) is None
    assert restored.get("missing") is None
    assert list(path.parent.iterdir()) == [path]
    assert path.read_bytes().startswith(b"SQLite format 3\x00")


def test_read_existing_records_is_read_only_and_never_creates_missing_ledger(tmp_path):
    missing = tmp_path / "missing" / "ledger.sqlite3"
    with pytest.raises(ledger.HandoffLedgerError):
        ledger.HandoffLedger.list_existing_records(missing, ("completed",))
    assert not missing.parent.exists()

    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path)
    store.claim("complete", {"kind": "fixture"})
    assert store.transition("complete", ("claimed",), "completed", {"completion_scope": "file_transfer"})
    before_bytes, before_mtime = path.read_bytes(), path.stat().st_mtime_ns
    rows = ledger.HandoffLedger.list_existing_records(path, ("completed",), limit=10001)
    assert [row["key"] for row in rows] == ["complete"]
    assert rows[0]["payload"]["completion_scope"] == "file_transfer"
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime


def test_task_key_uses_stable_canonical_json():
    first = {"path": "/合成/TEST-123.mkv", "intent": {"b": [2, 1], "a": True}}
    second = {"intent": {"a": True, "b": [2, 1]}, "path": "/合成/TEST-123.mkv"}
    canonical = json.dumps(first, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert ledger.task_key(first) == hashlib.sha256(canonical.encode()).hexdigest()
    assert ledger.task_key(second) == ledger.task_key(first)
    assert ledger.task_key({**first, "path": "/other.mkv"}) != ledger.task_key(first)


def test_compare_and_swap_merges_payload_preserving_original_intent(tmp_path, monkeypatch):
    store = ledger.HandoffLedger(tmp_path / "ledger.sqlite3")
    monkeypatch.setattr(ledger.time, "time", lambda: 100)
    initial = store.claim("task", {"intent": {"destination": "fixture"}, "keep": 1})
    monkeypatch.setattr(ledger.time, "time", lambda: 200)
    assert not store.transition("task", ("submitting",), "accepted", {"queue_id": "wrong"})
    assert not store.transition("missing", ("claimed",), "accepted")
    assert not store.transition("task", (), "accepted")
    assert store.get("task") == initial
    patch = {"queue_id": "fixture-1", "detail": {"count": 1}}
    assert store.transition("task", ("claimed", "failed"), "submitting", patch)
    patch["detail"]["count"] = 9
    updated = store.get("task")
    assert updated["payload"] == {"intent": {"destination": "fixture"}, "keep": 1,
                                   "queue_id": "fixture-1", "detail": {"count": 1}}
    assert updated["intent_fingerprint"] == initial["intent_fingerprint"]
    assert updated["created_at"] == 100 and updated["updated_at"] == 200
    monkeypatch.setattr(ledger.time, "time", lambda: 150)
    assert store.transition("task", ("submitting",), "accepted")
    assert store.get("task")["updated_at"] == 200


@pytest.mark.parametrize("state", sorted(ledger.STATES))
def test_all_states_survive_restart_without_expiration_or_retry(tmp_path, monkeypatch, state):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path)
    monkeypatch.setattr(ledger.time, "time", lambda: 1)
    store.claim("task", {"intent": "synthetic"})
    assert store.transition("task", ("claimed",), state)
    before = store.get("task")
    monkeypatch.setattr(ledger.time, "time", lambda: 10 ** 12)
    restarted = ledger.HandoffLedger(path)
    assert restarted.get("task") == before
    assert restarted.claim("task", {"intent": "synthetic"}) is None


def test_capacity_refuses_new_claim_but_never_removes_records_or_blocks_transitions(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path, max_entries=2)
    store.claim("old", {"index": 1})
    store.claim("new", {"index": 2})
    assert store.transition("old", ("claimed",), "completed")
    before = store.list_records()
    with pytest.raises(ledger.LedgerFullError):
        store.claim("overflow", {"index": 3})
    assert store.list_records() == before
    assert store.claim("old", {}) is None
    assert store.transition("new", ("claimed",), "uncertain")
    restarted = ledger.HandoffLedger(path, max_entries=1)
    assert len(restarted.list_records()) == 2
    assert restarted.transition("new", ("uncertain",), "failed")
    with pytest.raises(ledger.LedgerFullError):
        restarted.claim("another", {})


@pytest.mark.parametrize("shared", [False, True])
def test_atomic_claim_across_threads_and_instances(tmp_path, shared):
    path = tmp_path / "ledger.sqlite3"
    initial = ledger.HandoffLedger(path)
    stores = [initial if shared else ledger.HandoffLedger(path) for _ in range(12)]
    start = Barrier(len(stores))

    def attempt(index):
        start.wait()
        stores[index].claim(f"independent-{index}", {"index": index})
        return stores[index].claim("shared", {"winner": index})

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        claims = list(executor.map(attempt, range(len(stores))))
    winners = [record for record in claims if record is not None]
    assert len(winners) == 1
    assert initial.get("shared") == winners[0]
    assert len(initial.list_records()) == 13


def test_concurrent_creation_never_exposes_uninitialized_database(tmp_path):
    path = tmp_path / "new" / "ledger.sqlite3"
    start = Barrier(12)

    def attempt(index):
        start.wait()
        store = ledger.HandoffLedger(path)
        return store.claim("single-task", {"winner": index})

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(attempt, range(12)))
    assert len([value for value in results if value is not None]) == 1
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="目标 Linux 的进程锁契约")
def test_independent_processes_share_atomic_claim_and_initialization(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    # 继承全局禁止网络夹具，仅通过 SQLite 文件与操作系统进程锁协调。
    with ProcessPoolExecutor(max_workers=8, mp_context=multiprocessing.get_context("fork")) as executor:
        results = list(executor.map(_process_claim, [(str(path), index) for index in range(16)]))
    winners = [record for record in results if record is not None]
    assert len(winners) == 1
    assert ledger.HandoffLedger(path).get("process-shared") == winners[0]
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_capacity_enforced_without_eviction(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    stores = [ledger.HandoffLedger(path, max_entries=3) for _ in range(12)]
    start = Barrier(len(stores))

    def attempt(index):
        start.wait()
        try:
            return stores[index].claim(str(index), {"index": index})
        except ledger.LedgerFullError:
            return None

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        results = list(executor.map(attempt, range(len(stores))))
    assert sum(result is not None for result in results) == 3
    assert len(stores[0].list_records()) == 3


def test_only_one_compare_and_swap_wins(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    stores = [ledger.HandoffLedger(path) for _ in range(12)]
    stores[0].claim("task", {})
    start = Barrier(len(stores))

    def attempt(index):
        start.wait()
        return stores[index].transition("task", ("claimed",), "preparing", {"winner": index})

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        results = list(executor.map(attempt, range(len(stores))))
    assert results.count(True) == 1
    assert stores[0].get("task")["payload"]["winner"] == results.index(True)
    assert stores[0].get("task")["state"] == "preparing"
    assert stores[results.index(True)].transition("task", ("preparing",), "submitting")
    assert not stores[0].transition("task", ("preparing",), "submitting")


def test_listing_filters_limits_and_stably_orders_equal_timestamps(tmp_path, monkeypatch):
    store = ledger.HandoffLedger(tmp_path / "ledger.sqlite3")
    monkeypatch.setattr(ledger.time, "time", lambda: 10)
    for key in ("c", "a", "b"):
        store.claim(key, {})
    store.transition("b", ("claimed",), "uncertain")
    assert [row["key"] for row in store.list_records()] == ["a", "b", "c"]
    assert [row["key"] for row in store.list_records(limit=2)] == ["a", "b"]
    assert [row["key"] for row in store.list_records(("claimed",))] == ["a", "c"]
    assert [row["key"] for row in store.list_records(("uncertain", "failed"))] == ["b"]
    assert store.list_records(()) == []
    assert store.list_records(limit=0) == []


@pytest.mark.parametrize("contents", [b"", b"not sqlite", b'{"schema":1,"entries":{}}'])
def test_corrupt_or_non_database_file_fails_closed_without_replacement(tmp_path, contents):
    path = tmp_path / "ledger.sqlite3"
    path.write_bytes(contents)
    with pytest.raises(ledger.HandoffLedgerError):
        ledger.HandoffLedger(path)
    assert path.read_bytes() == contents


@pytest.mark.parametrize("statement", [
    "PRAGMA user_version=999", "PRAGMA application_id=0", "DROP TABLE handoff_records",
    "CREATE TABLE other (value TEXT)",
    "CREATE TRIGGER unexpected AFTER INSERT ON handoff_records BEGIN DELETE FROM handoff_records; END",
])
def test_incompatible_database_never_reset_and_existing_instances_refuse_it(tmp_path, statement):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path)
    store.claim("existing", {})
    with sqlite3.connect(path) as connection:
        connection.execute(statement)
    before = path.read_bytes()
    with pytest.raises(ledger.HandoffLedgerError):
        ledger.HandoffLedger(path)
    with pytest.raises(ledger.HandoffLedgerError):
        store.claim("new", {})
    with pytest.raises(ledger.HandoffLedgerError):
        store.get("existing")
    assert path.read_bytes() == before


@pytest.mark.parametrize("column,value", [
    ("payload", "not json"), ("payload", "[]"), ("payload", '{"password":"synthetic-secret"}'),
    ("payload", '{"n":NaN}'), ("intent_fingerprint", "not-a-digest"),
    ("created_at", -1), ("updated_at", 0),
])
def test_malformed_persisted_record_is_never_ignored(tmp_path, column, value):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path)
    store.claim("task", {})
    with sqlite3.connect(path) as connection:
        connection.execute(f"UPDATE handoff_records SET {column}=?", (value,))
    before = path.read_bytes()
    with pytest.raises(ledger.HandoffLedgerError):
        ledger.HandoffLedger(path)
    for operation in (lambda: store.get("task"), store.list_records,
                      lambda: store.transition("task", ("claimed",), "submitting"),
                      lambda: store.claim("task", {})):
        with pytest.raises(ledger.HandoffLedgerError):
            operation()
    assert path.read_bytes() == before


def test_missing_file_is_not_recreated_by_open_instance(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path)
    store.claim("task", {})
    path.unlink()
    with pytest.raises(ledger.HandoffLedgerError):
        store.claim("task", {})
    with pytest.raises(ledger.HandoffLedgerError):
        store.get("task")
    assert not path.exists()


def test_io_error_never_looks_like_missing_record_and_preserves_disk(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path)
    store.claim("task", {})
    before = path.read_bytes()

    def unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic storage outage")

    monkeypatch.setattr(ledger.sqlite3, "connect", unavailable)
    for operation in (lambda: store.get("task"), store.list_records,
                      lambda: store.transition("task", ("claimed",), "submitting"),
                      lambda: store.claim("new", {})):
        with pytest.raises(ledger.HandoffLedgerError):
            operation()
    assert path.read_bytes() == before


def test_invalid_patch_rolls_back_state_and_preserves_prior_record(tmp_path):
    store = ledger.HandoffLedger(tmp_path / "ledger.sqlite3")
    before = store.claim("task", {"value": 1})
    with pytest.raises(ledger.HandoffLedgerError):
        store.transition("task", ("claimed",), "submitting", {"value": object()})
    assert store.get("task") == before


@pytest.mark.parametrize("operation", ["claim", "transition"])
def test_failed_commit_rolls_back_all_changes(tmp_path, monkeypatch, operation):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path)
    before = store.claim("task", {"value": 1})
    original_connect = ledger.sqlite3.connect

    class UnavailableCommit(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError("synthetic commit failure")

    with monkeypatch.context() as patched:
        patched.setattr(ledger.sqlite3, "connect", lambda *args, **kwargs: original_connect(
            *args, **kwargs, factory=UnavailableCommit,
        ))
        with pytest.raises(ledger.HandoffLedgerError):
            if operation == "claim":
                store.claim("new", {"value": 2})
            else:
                store.transition("task", ("claimed",), "submitting", {"value": 2})
    assert store.get("task") == before
    assert store.get("new") is None
    assert ledger.HandoffLedger(path).list_records() == [before]
    assert list(tmp_path.iterdir()) == [path]


def test_every_operation_closes_its_own_connection(tmp_path, monkeypatch):
    original_connect = ledger.sqlite3.connect
    active = []

    class TrackedConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            active.append(self)

        def close(self):
            active.remove(self)
            super().close()

    monkeypatch.setattr(ledger.sqlite3, "connect", lambda *args, **kwargs: original_connect(
        *args, **kwargs, factory=TrackedConnection,
    ))
    store = ledger.HandoffLedger(tmp_path / "ledger.sqlite3", max_entries=1)
    assert active == []
    operations = [lambda: store.claim("task", {}), lambda: store.get("task"), store.list_records,
                  lambda: store.transition("task", ("claimed",), "submitting"),
                  lambda: store.claim("task", {})]
    for operation in operations:
        operation()
        assert active == []
    with pytest.raises(ledger.LedgerFullError):
        store.claim("too-many", {})
    assert active == []


@pytest.mark.parametrize("payload", [
    [], None, {1: "bad-key"}, {"value": object()}, {"value": (1, 2)},
    {"value": float("nan")}, {"value": float("inf")}, {"value": "\ud800"},
    {"password": "synthetic-secret"}, {"nested": [{"API-Key": "synthetic-secret"}]},
    {"qbit_password": "synthetic-secret"}, {"download_token": "synthetic-secret"},
    {"value": "https://user:synthetic-secret@example.test/task"},
    {"value": "https://example.test/task?token=synthetic-secret"},
])
def test_invalid_or_secret_payload_never_written(tmp_path, payload):
    store = ledger.HandoffLedger(tmp_path / "ledger.sqlite3")
    with pytest.raises(ledger.LedgerValidationError):
        ledger.task_key(payload)
    with pytest.raises(ledger.LedgerValidationError):
        store.claim("task", payload)
    assert store.list_records() == []
    assert b"synthetic-secret" not in store.path.read_bytes()


def test_invalid_states_and_inputs_cannot_change_data(tmp_path):
    store = ledger.HandoffLedger(tmp_path / "ledger.sqlite3")
    before = store.claim("task", {})
    operations = [
        lambda: store.claim("", {}), lambda: store.claim(None, {}), lambda: store.claim("\ud800", {}),
        lambda: store.transition("task", ("claimed",), "unknown"),
        lambda: store.transition("task", ("unknown",), "accepted"),
        lambda: store.transition("task", "claimed", "accepted"),
        lambda: store.list_records(("unknown",)), lambda: store.list_records(limit=-1),
        lambda: store.list_records(limit=True), lambda: store.list_records(limit=1.5),
    ]
    for operation in operations:
        with pytest.raises(ledger.LedgerValidationError):
            operation()
    assert store.get("task") == before


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5, "10"])
def test_invalid_capacity_rejected_before_creating_file(tmp_path, capacity):
    path = tmp_path / "ledger.sqlite3"
    with pytest.raises(ledger.LedgerValidationError):
        ledger.HandoffLedger(path, max_entries=capacity)
    assert not path.exists()


def test_memory_database_rejected():
    with pytest.raises(ledger.LedgerValidationError):
        ledger.HandoffLedger(":memory:")


def test_alias_paths_share_the_same_durable_database(tmp_path):
    directory = tmp_path / "real"
    directory.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    first = ledger.HandoffLedger(directory / "ledger.sqlite3")
    second = ledger.HandoffLedger(alias / "ledger.sqlite3")
    assert first.claim("task", {"path": "synthetic"}) is not None
    assert second.claim("task", {"path": "synthetic"}) is None
    assert first.path == second.path
    assert isinstance(first.path, Path)


def test_batch_claim_persists_all_files_and_deduplicates_identical_input(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path)
    monkeypatch.setattr(ledger.time, "time", lambda: 100)
    payload = {"admission_complete": True, "intent": {"path": "second.mkv", "size": 1}}
    equivalent = {"intent": {"size": 1, "path": "second.mkv"}, "admission_complete": True}
    first = {"admission_complete": True, "intent": {"path": "first.mkv"}}
    records = store.claim_batch([("second", payload), ("first", first), ("second", equivalent)])
    assert [record["key"] for record in records] == ["second", "first"]
    for record in records:
        assert record["state"] == "claimed"
        assert record["created_at"] == record["updated_at"] == 100
        assert record["intent_fingerprint"] == ledger.task_key(record["payload"])
        assert record["payload"]["admission_complete"] is True
    payload["intent"]["path"] = "caller mutation"
    records[0]["payload"]["intent"]["path"] = "return mutation"
    assert ledger.HandoffLedger(path).get("second")["payload"]["intent"]["path"] == "second.mkv"
    assert store.claim_batch([]) == []
    assert store.claim_batch([("second", equivalent), ("first", first)]) == []


@pytest.mark.parametrize("capacity", [1, 2, 3])
def test_batch_capacity_checks_aggregate_before_admitting_any_file(tmp_path, capacity):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path, max_entries=capacity)
    before = store.claim("existing", {"value": 1})
    with pytest.raises(ledger.LedgerFullError):
        store.claim_batch([("first", {}), ("second", {}), ("third", {})])
    assert ledger.HandoffLedger(path).list_records() == [before]


@pytest.mark.parametrize("state", ["preparing", "submitting", "accepted", "uncertain", "completed"])
def test_batch_preserves_existing_unknown_or_settled_records_without_using_capacity(tmp_path, state):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path, max_entries=2)
    store.claim("existing", {"original": True})
    store.transition("existing", ("claimed",), state, {"evidence": "preserved"})
    before = store.get("existing")
    created = store.claim_batch([
        ("existing", {"admission_complete": True}), ("new", {"admission_complete": True}),
        ("existing", {"admission_complete": True}),
    ])
    assert [record["key"] for record in created] == ["new"]
    assert store.get("existing") == before
    smaller = ledger.HandoffLedger(path, max_entries=1)
    assert smaller.claim_batch([("existing", {}), ("new", {})]) == []
    assert smaller.get("existing") == before
    assert len(smaller.list_records()) == 2


@pytest.mark.parametrize("entries", [
    None, (), [("missing-payload",)], [("extra", {}, {})], [["not-a-tuple", {}]],
    [("good", {}), ("", {})], [("good", {}), ("bad", {"password": "synthetic-secret"})],
    [("conflict", {"value": 1}), ("conflict", {"value": 2})],
])
def test_batch_validates_every_input_before_opening_a_transaction(tmp_path, monkeypatch, entries):
    store = ledger.HandoffLedger(tmp_path / "ledger.sqlite3")

    def forbidden_transaction(*_args, **_kwargs):
        raise AssertionError("输入未全部验证就打开了事务")

    with monkeypatch.context() as patched:
        patched.setattr(store, "_transaction", forbidden_transaction)
        with pytest.raises(ledger.LedgerValidationError):
            store.claim_batch(entries)
    assert store.list_records() == []


def test_batch_conflicting_input_is_rejected_even_if_key_already_exists(tmp_path):
    store = ledger.HandoffLedger(tmp_path / "ledger.sqlite3")
    before = store.claim("existing", {})
    with pytest.raises(ledger.LedgerValidationError):
        store.claim_batch([("new", {}), ("existing", {"a": 1}), ("existing", {"a": 2})])
    assert store.list_records() == [before]


@pytest.mark.parametrize("failure", ["second-insert", "commit"])
def test_batch_storage_failure_never_persists_a_partial_admission(tmp_path, monkeypatch, failure):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path)
    store.claim("existing", {})
    store.transition("existing", ("claimed",), "uncertain")
    before = store.get("existing")
    original_connect = ledger.sqlite3.connect

    class StorageFailure(sqlite3.Connection):
        inserts = 0

        def execute(self, sql, parameters=()):
            if sql.startswith("INSERT INTO handoff_records"):
                self.inserts += 1
                if failure == "second-insert" and self.inserts == 2:
                    raise sqlite3.OperationalError("synthetic second insert failure")
            return super().execute(sql, parameters)

        def commit(self):
            if failure == "commit":
                raise sqlite3.OperationalError("synthetic commit failure")
            return super().commit()

    with monkeypatch.context() as patched:
        patched.setattr(ledger.sqlite3, "connect", lambda *args, **kwargs: original_connect(
            *args, **kwargs, factory=StorageFailure,
        ))
        with pytest.raises(ledger.HandoffLedgerError):
            store.claim_batch([("first", {}), ("existing", {}), ("second", {}), ("third", {})])
    assert ledger.HandoffLedger(path).list_records() == [before]
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("capacity,successes", [(8, 3), (30, 8)])
def test_parallel_overlapping_batches_obey_capacity_without_partial_groups(tmp_path, capacity, successes):
    path = tmp_path / "ledger.sqlite3"
    stores = [ledger.HandoffLedger(path, max_entries=capacity) for _ in range(8)]
    start = Barrier(len(stores))

    def attempt(index):
        start.wait()
        try:
            return stores[index].claim_batch([
                ("shared", {"admission_complete": True}),
                (f"first-{index}", {"admission_complete": True}),
                (f"second-{index}", {"admission_complete": True}),
            ])
        except ledger.LedgerFullError:
            return None

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        outcomes = list(executor.map(attempt, range(len(stores))))
    records = ledger.HandoffLedger(path).list_records()
    keys = {record["key"] for record in records}
    assert sum(outcome is not None for outcome in outcomes) == successes
    assert len(records) == 1 + 2 * successes <= capacity
    returned_keys = [record["key"] for outcome in outcomes if outcome is not None for record in outcome]
    assert len(returned_keys) == len(set(returned_keys)) == len(records)
    for index, outcome in enumerate(outcomes):
        assert (f"first-{index}" in keys) == (outcome is not None)
        assert (f"second-{index}" in keys) == (outcome is not None)


def test_batch_refuses_corrupt_existing_evidence_without_admitting_other_files(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    store = ledger.HandoffLedger(path)
    store.claim("existing", {})
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE handoff_records SET payload='invalid' WHERE key='existing'")
    with pytest.raises(ledger.HandoffLedgerError):
        store.claim_batch([("first", {}), ("existing", {}), ("second", {})])
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT key FROM handoff_records").fetchall() == [("existing",)]
