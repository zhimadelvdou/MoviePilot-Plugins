"""插件独立的持久交接账本；不淘汰记录，也不自动重试未知结果。"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

STATES = frozenset({
    "claimed", "preparing", "submitting", "accepted", "previewed", "completed", "failed", "uncertain",
})
_VERSION = 1
_APPLICATION_ID = 0x584A4A4C
_TABLE = """CREATE TABLE handoff_records (
    key TEXT PRIMARY KEY NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'claimed', 'preparing', 'submitting', 'accepted', 'previewed', 'completed', 'failed', 'uncertain'
    )),
    payload TEXT NOT NULL,
    intent_fingerprint TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)"""
_SECRET_FIELDS = frozenset({
    "password", "passwd", "pwd", "token", "accesstoken", "refreshtoken", "apikey",
    "secret", "clientsecret", "authorization", "proxyauthorization", "cookie", "cookies",
    "credential", "credentials", "privatekey", "sessionid", "authkey", "passkey",
})
_URL = re.compile(r"(?:https?|ftp|socks5?)://[^\s<>\"']+", re.IGNORECASE)


class HandoffLedgerError(RuntimeError):
    """账本不可安全使用时停止交接，不将错误视为空账本。"""


class LedgerFullError(HandoffLedgerError):
    """容量已满，保留全部历史并拒绝新的占位。"""


class LedgerValidationError(HandoffLedgerError, ValueError):
    """拒绝无效状态、非 JSON 数据或明显包含凭据的数据。"""


def _secret_field(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    return normalized in _SECRET_FIELDS or normalized.endswith((
        "password", "passwd", "token", "apikey", "secret", "credential", "credentials", "passkey",
    ))


def _validate_json(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or _secret_field(key):
                raise LedgerValidationError("账本字段必须为字符串且不能包含凭据字段")
            _validate_json(child)
    elif isinstance(value, list):
        for child in value:
            _validate_json(child)
    elif isinstance(value, str):
        for match in _URL.finditer(value):
            try:
                parsed = urlsplit(match.group())
                if parsed.username is not None or parsed.password is not None:
                    raise LedgerValidationError("账本不能保存带凭据的 URL")
                if any(_secret_field(key) for key, _ in parse_qsl(parsed.query)):
                    raise LedgerValidationError("账本不能保存带密钥的 URL")
            except ValueError as exc:
                raise LedgerValidationError("账本 URL 无效或包含凭据") from exc
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise LedgerValidationError("账本仅接受普通 JSON 数据")
    elif isinstance(value, float) and not math.isfinite(value):
        raise LedgerValidationError("账本不接受非有限数值")


def _canonical(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise LedgerValidationError("账本载荷必须为 JSON 对象")
    try:
        _validate_json(payload)
        result = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), allow_nan=False)
        result.encode("utf-8")
        return result
    except (ValueError, TypeError, RecursionError) as exc:
        raise LedgerValidationError("账本载荷无效或包含凭据") from exc


def task_key(payload: dict) -> str:
    """用严格、排序的 UTF-8 JSON 生成稳定 SHA-256；调用方只提供无秘密的意图。"""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _check_key(key):
    if not isinstance(key, str) or not key or "\x00" in key:
        raise LedgerValidationError("交接键必须为非空字符串")
    try:
        key.encode("utf-8")
    except UnicodeError as exc:
        raise LedgerValidationError("交接键必须是合法 UTF-8 字符串") from exc


def _check_states(states):
    if not isinstance(states, tuple) or any(not isinstance(s, str) or s not in STATES for s in states):
        raise LedgerValidationError("交接状态必须是已知状态组成的元组")


def _check_schema(connection):
    if (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
            or connection.execute("PRAGMA user_version").fetchone()[0] != _VERSION):
        raise HandoffLedgerError("交接账本标识或版本不兼容；禁止重建")
    objects = connection.execute(
        "SELECT type, name, sql FROM sqlite_master ORDER BY name"
    ).fetchall()
    expected = [
        ("table", "handoff_records", _TABLE),
        ("index", "sqlite_autoindex_handoff_records_1", None),
    ]
    if [tuple(row) for row in objects] != expected:
        raise HandoffLedgerError("交接账本结构不兼容；禁止重建")


def _decode(row):
    if row is None:
        return None
    record = dict(row)
    try:
        _check_key(record["key"])
        _check_states((record["state"],))
        payload = json.loads(record["payload"])
        if _canonical(payload) != record["payload"]:
            raise ValueError("载荷不是规范 JSON")
        if not re.fullmatch(r"[0-9a-f]{64}", record["intent_fingerprint"]):
            raise ValueError("指纹格式错误")
        created, updated = record["created_at"], record["updated_at"]
        if (not isinstance(created, (int, float)) or not isinstance(updated, (int, float))
                or not math.isfinite(created) or not math.isfinite(updated)
                or not 0 <= created <= updated):
            raise ValueError("时间戳格式错误")
        record["payload"] = payload
        return record
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        raise HandoffLedgerError("交接账本记录损坏；禁止忽略或清空") from exc


class HandoffLedger:
    """每次操作独立连接的 SQLite 账本，跨线程及进程通过事务串行占位。

    记录格式为 key、state、payload、intent_fingerprint、created_at、updated_at。
    时间戳为 UTC Unix 秒；指纹固定为首次载荷的 task_key。调用方只应写入必要的
    无秘密交接证据，不能传入下载器配置、原始异常或认证内容。常见凭据会额外拒绝。
    构造及读写异常均抛出 HandoffLedgerError，不能捕获后改用内存记录继续提交。
    """

    def __init__(self, path: str | Path, max_entries: int = 10000):
        """打开专用本地文件；已有空文件、损坏文件或其它版本一律不重置。"""
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise LedgerValidationError("账本容量必须为正整数")
        if not str(path) or str(path) == ":memory:":
            raise LedgerValidationError("账本必须使用持久文件路径")
        self.max_entries = max_entries
        try:
            self.path = Path(path).expanduser().resolve()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.path.exists():
                self._create_new()
            with self._transaction() as connection:
                if [tuple(row) for row in connection.execute("PRAGMA quick_check")] != [("ok",)]:
                    raise HandoffLedgerError("交接账本完整性检查失败；禁止重建")
                for row in connection.execute("SELECT * FROM handoff_records"):
                    _decode(row)
        except (OSError, sqlite3.Error) as exc:
            raise HandoffLedgerError("无法安全打开交接账本") from exc

    def _create_new(self):
        # 先完整初始化临时库，再用硬链接原子发布；不覆盖已有库，也不暴露空库窗口。
        descriptor, temporary = tempfile.mkstemp(prefix=".handoff-", suffix=".sqlite3", dir=self.path.parent)
        os.close(descriptor)
        try:
            connection = sqlite3.connect(temporary, isolation_level=None)
            try:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(_TABLE)
                connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={_VERSION}")
                connection.commit()
            finally:
                connection.close()
            try:
                os.link(temporary, self.path)
            except FileExistsError:
                pass  # 另一个实例先发布；随后仍须验证其完整结构和版本。
            else:
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @contextmanager
    def _transaction(self, write=False):
        connection = None
        try:
            # mode=rw 防止运行期间文件丢失后被静默创建为空账本。
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                         timeout=10, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            _check_schema(connection)
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError) as exc:
            raise HandoffLedgerError("交接账本读写失败；必须停止交接") from exc
        finally:
            if connection is not None:
                try:
                    if connection.in_transaction:
                        connection.rollback()
                finally:
                    connection.close()

    def claim(self, key: str, payload: dict) -> dict | None:
        """原子创建 claimed 记录；重复键返回空，满额抛错且绝不淘汰历史。"""
        records = self.claim_batch([(key, payload)])
        return records[0] if records else None

    def claim_batch(self, entries: list[tuple[str, dict]]) -> list[dict]:
        """一次事务接纳全部新文件，容量不足或任何失败时全部回滚。

        输入先完整校验；批内同键同载荷合并，载荷冲突则拒绝整批。已有键只验证、
        不改写，不额外占用容量。按输入首次出现顺序返回新建的 claimed 记录。
        调用方应先持久接纳整个种子的所有合格文件，再执行任何文件交接。
        """
        if not isinstance(entries, list):
            raise LedgerValidationError("批量交接必须为键与载荷元组组成的列表")
        prepared = {}
        for entry in entries:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise LedgerValidationError("每个批量交接项必须为键与载荷的二元组")
            key, payload = entry
            _check_key(key)
            encoded = _canonical(payload)
            if key in prepared and prepared[key] != encoded:
                raise LedgerValidationError("同批交接键存在冲突载荷；拒绝整批接纳")
            prepared[key] = encoded
        with self._transaction(write=True) as connection:
            new_entries = []
            for key, encoded in prepared.items():
                existing = connection.execute("SELECT * FROM handoff_records WHERE key=?", (key,)).fetchone()
                if existing is None:
                    new_entries.append((key, encoded))
                else:
                    _decode(existing)
            count = connection.execute("SELECT COUNT(*) FROM handoff_records").fetchone()[0]
            if new_entries and count + len(new_entries) > self.max_entries:
                raise LedgerFullError("交接账本容量不足；历史记录保留，拒绝整批新增交接")
            now = time.time()
            records = []
            for key, encoded in new_entries:
                fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                connection.execute(
                    "INSERT INTO handoff_records VALUES (?, 'claimed', ?, ?, ?, ?)",
                    (key, encoded, fingerprint, now, now),
                )
                records.append(_decode(connection.execute(
                    "SELECT * FROM handoff_records WHERE key=?", (key,),
                ).fetchone()))
            return records

    def get(self, key: str) -> dict | None:
        """读取独立副本；不存在返回空，损坏或不可读则抛错。"""
        _check_key(key)
        with self._transaction() as connection:
            return _decode(connection.execute("SELECT * FROM handoff_records WHERE key=?", (key,)).fetchone())

    def transition(self, key: str, expected: tuple[str, ...], state: str, patch: dict | None = None) -> bool:
        """仅在当前状态匹配时更新，patch 浅合并载荷；保留首次指纹及创建时间。"""
        _check_key(key)
        _check_states(expected)
        _check_states((state,))
        if patch is not None:
            _canonical(patch)
        with self._transaction(write=True) as connection:
            record = _decode(connection.execute("SELECT * FROM handoff_records WHERE key=?", (key,)).fetchone())
            if record is None or record["state"] not in expected:
                return False
            payload = record["payload"]
            if patch is not None:
                payload.update(patch)
            encoded = _canonical(payload)
            connection.execute(
                "UPDATE handoff_records SET state=?, payload=?, updated_at=? WHERE key=? AND state=?",
                (state, encoded, max(time.time(), record["updated_at"]), key, record["state"]),
            )
            return True

    def list_records(self, states: tuple[str, ...] | None = None, limit: int = 100) -> list[dict]:
        """按创建时间、交接键稳定列出记录；不隐式筛除旧记录或终态。"""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise LedgerValidationError("查询数量必须为非负整数")
        if states is not None:
            _check_states(states)
        with self._transaction() as connection:
            if states == () or limit == 0:
                return []
            parameters = list(states or ())
            where = " WHERE state IN (" + ",".join("?" for _ in parameters) + ")" if states else ""
            rows = connection.execute(
                "SELECT * FROM handoff_records" + where + " ORDER BY created_at, key LIMIT ?",
                (*parameters, limit),
            )
            return [_decode(row) for row in rows]

    @staticmethod
    def list_existing_records(path: str | Path, states: tuple[str, ...] | None = None,
                              limit: int = 100) -> list[dict]:
        """只读列出已存在账本；不创建路径/数据库，供旁路状态摘要使用。"""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 10001:
            raise LedgerValidationError("只读查询数量超出允许范围")
        if states is not None:
            _check_states(states)
        try:
            target = Path(path).expanduser()
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise HandoffLedgerError("只读账本路径不是普通文件")
            target = target.resolve(strict=True)
            if states == () or limit == 0:
                return []
            connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True,
                                         timeout=10, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("BEGIN")
                _check_schema(connection)
                parameters = list(states or ())
                where = " WHERE state IN (" + ",".join("?" for _ in parameters) + ")" if states else ""
                rows = connection.execute(
                    "SELECT * FROM handoff_records" + where + " ORDER BY created_at, key LIMIT ?",
                    (*parameters, limit),
                )
                records = [_decode(row) for row in rows]
                connection.commit()
                return records
            finally:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()
        except HandoffLedgerError:
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
            raise HandoffLedgerError("只读账本查询失败") from exc
