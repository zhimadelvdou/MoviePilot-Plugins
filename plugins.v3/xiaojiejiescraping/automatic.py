"""新任务自动触发边界；独立于文件交接账本的控制状态。"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import math
import time
from pathlib import Path

from .ledger import HandoffLedger, HandoffLedgerError

_CONTROL_KEY = "auto-trigger-control-v1"


class AutoTriggerGuard:
    """持久保存新任务开关边界，损坏时停止而不重置。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.error = ""
        self._record = None

    @property
    def armed(self) -> bool:
        return bool(self._record and self._record["payload"].get("armed") is True)

    @property
    def started_at(self) -> int | None:
        if not self.armed:
            return None
        value = self._record["payload"].get("started_at")
        return value if type(value) is int else None

    @property
    def scope_signature(self) -> str | None:
        return self._record["payload"].get("scope_signature") if self._record else None

    def configure(self, *, active: bool, new_only: bool, scope_signature: str) -> None:
        """Only create a file for an active new-only configuration."""
        self.error = ""
        should_arm = active and new_only
        if not should_arm and not self.path.exists():
            self._record = None
            return
        try:
            ledger = HandoffLedger(self.path)
            record = ledger.get(_CONTROL_KEY)
            if should_arm:
                if record is not None:
                    self._validate(record)
                if (record and record["payload"]["armed"] is True
                        and record["payload"]["scope_signature"] == scope_signature):
                    self._record = record
                    return
                previous_start = record["payload"].get("started_at") if record else None
                start_at = math.ceil(time.time())
                if type(previous_start) is int:
                    start_at = max(start_at, previous_start + 1)
                payload = {"kind": "auto_trigger_control", "armed": True,
                           "scope_signature": scope_signature, "started_at": start_at}
                self._write(ledger, record, payload)
            elif record is not None:
                self._validate(record)
                if record["payload"]["armed"] is True:
                    payload = dict(record["payload"])
                    payload.update(armed=False)
                    self._write(ledger, record, payload)
                else:
                    self._record = record
            else:
                self._record = None
        except (HandoffLedgerError, OSError, ValueError, TypeError):
            self._record = None
            self.error = "新任务边界账本不可用；自动触发已阻止"

    @staticmethod
    def _validate(record: dict) -> None:
        payload = record["payload"]
        if (payload.get("kind") != "auto_trigger_control" or type(payload.get("armed")) is not bool
                or (payload.get("armed") is True and
                    (type(payload.get("started_at")) is not int or payload.get("started_at") <= 0))
                or (payload.get("armed") is False and payload.get("started_at") is not None
                    and (type(payload.get("started_at")) is not int or payload.get("started_at") <= 0))
                or not isinstance(payload.get("scope_signature"), str)):
            raise HandoffLedgerError("新任务边界记录无效")

    def _write(self, ledger: HandoffLedger, record: dict | None, payload: dict) -> None:
        if record is None:
            saved = ledger.claim(_CONTROL_KEY, payload)
            if saved is None:
                saved = ledger.get(_CONTROL_KEY)
                if saved is None:
                    raise HandoffLedgerError("新任务边界无法保存")
                self._validate(saved)
            self._record = saved
            return
        if not ledger.transition(_CONTROL_KEY, (record["state"],), record["state"], payload):
            raise HandoffLedgerError("新任务边界并发变化")
        self._record = ledger.get(_CONTROL_KEY)
        if self._record is None:
            raise HandoffLedgerError("新任务边界保存后缺失")


def is_valid_completion_time(value, started_at: int, now: float | None = None) -> bool:
    """Require a finite numeric raw completion timestamp inside the armed window."""
    current = time.time() if now is None else now
    return (type(started_at) is int and started_at > 0
            and type(value) in (int, float) and math.isfinite(value) and value > 0
            and value >= started_at and value <= current)
