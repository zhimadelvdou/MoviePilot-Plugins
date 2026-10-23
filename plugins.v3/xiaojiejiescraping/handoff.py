"""经公开 MoviePilot 入口交接单文件；账本与历史证据决定状态，不自行搬运。"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass
import copy
import math
from pathlib import Path
import time
from typing import Callable

from app.sdk.media import MetaInfo
from app.sdk.queries import list_transfer_history
from app.schemas.file import FileItem
from app.schemas.system import TransferDirectoryConf
from app.schemas.types import MediaType

from .ledger import HandoffLedger, task_key
from .scope import RecognitionContext, matches_scope
from .layout import child_directory, child_directory_matches


@dataclass(frozen=True, slots=True)
class HandoffConfig:
    """冻结用户明确设置的目标与方式；默认发现，不产生整理操作。"""
    mode: str = "discover"
    target_path: str = ""
    operation: str = ""
    roots: tuple[Path, ...] = ()
    sources: tuple[str, ...] = ()
    ownership_ack: bool = False
    auxiliary_ack: bool = False
    actress_subfolder: bool = False

    def target(self, roots: tuple[Path, ...] | None = None) -> Path | None:
        """目标必须为既有本地目录，且与所有输入目录双向不重叠。"""
        if self.mode not in ("preview", "execute") or self.operation not in ("copy", "link"):
            return None
        if self.mode == "execute" and not (self.ownership_ack and self.auxiliary_ack):
            return None
        if not isinstance(self.target_path, str):
            return None
        raw = Path(self.target_path)
        roots = self.roots if roots is None else roots
        if not raw.is_absolute() or raw == Path("/") or ".." in raw.parts or not roots or not self.sources:
            return None
        try:
            target = raw.resolve(strict=True)
            if not target.is_dir() or target == Path("/"):
                return None
            for root in roots:
                resolved = root.resolve(strict=True)
                if target.is_relative_to(resolved) or resolved.is_relative_to(target):
                    return None
            return target
        except (OSError, RuntimeError, ValueError):
            return None

    def signature(self) -> str:
        """配置变化不让旧排队记录沿用已撤销的目标或权限。"""
        payload = {"mode": self.mode, "target": self.target_path, "operation": self.operation,
                   "roots": [str(root) for root in self.roots], "sources": list(self.sources),
                   "ownership_ack": self.ownership_ack, "auxiliary_ack": self.auxiliary_ack}
        if self.actress_subfolder:
            payload["actress_subfolder"] = True
        return task_key(payload)


def native_transfer(**kwargs):
    """只使用文档明确支持插件调用的公开 do_transfer，不调用内部 workflow。"""
    # 宿主通过 __getattr__ 惰性导出这个稳定公开 ABI，静态检查不可见。
    from app.chain.transfer import TransferChain  # pylint: disable=no-name-in-module
    return TransferChain().do_transfer(**kwargs)


def _value(record, key, default=None):
    return record.get(key, default) if isinstance(record, dict) else getattr(record, key, default)


def _fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "modify_time": stat.st_mtime,
            "device": stat.st_dev, "inode": stat.st_ino}


def _target_path(value, root: Path) -> Path | None:
    if not isinstance(value, str):
        return None
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path == root:
        return None
    try:
        return path if path.is_relative_to(root) and path.resolve().is_relative_to(root) else None
    except (OSError, RuntimeError, ValueError):
        return None


class HandoffCoordinator:
    """单文件预览/交接与只读核实；不重提已进入宿主或结果不确定的任务。"""
    def __init__(self, ledger: HandoffLedger, config: HandoffConfig, recognize: Callable,
                 transfer: Callable = native_transfer, history: Callable = list_transfer_history,
                 actress_evidence: Callable | None = None):
        self.ledger, self.config = ledger, config
        self.recognize, self.transfer, self.history = recognize, transfer, history
        self.actress_evidence = actress_evidence

    def _admission(self, torrent, path: Path, scope_roots: tuple[Path, ...] | None = None,
                   match_mode: str | None = None):
        """构造源文件版本与完整意图，交由单一事务统一准入。"""
        scope_roots = self.config.roots if scope_roots is None else scope_roots
        target = self.config.target(scope_roots)
        context = RecognitionContext("transfer", "local", str(path), scope_roots)
        if target is None or not matches_scope(context, "local", scope_roots):
            return None
        fingerprint = _fingerprint(path)
        payload = {"path": str(path), "source_storage": "local", "real_path": str(path.resolve()),
                   "downloader": torrent.downloader, "download_hash": torrent.hash,
                   "target_root": str(target), "scope_roots": [str(root) for root in scope_roots],
                   "operation": self.config.operation,
                   "mode": self.config.mode, "config_key": self.config.signature(), **fingerprint}
        if match_mode:
            payload["match_mode"] = match_mode
        # 实际执行的所有权必须独立于目标/模式选择/来源顺序等可变配置。
        # 同一物理文件版本即使改名、改 qbit hash 或换目标，也不能绕过未知结果重新提交。
        source_version = {field: fingerprint[field] for field in ("device", "inode", "size", "mtime_ns")}
        ownership = {"source_version": source_version,
                     "purpose": "execute" if self.config.mode == "execute" else "preview:" + self.config.signature()}
        payload["source_version"] = source_version
        payload["admission_complete"] = True
        return task_key(ownership), payload

    def enqueue_batch(self, torrent, paths: tuple[Path, ...], scope_roots: tuple[Path, ...] | None = None,
                      match_mode: str | None = None) -> list[dict]:
        """同一 eligible 种子的所有文件在一个事务中入队；容量失败不能留半批。"""
        if not paths:
            return []
        entries = [self._admission(torrent, path, scope_roots, match_mode) for path in paths]
        if any(entry is None for entry in entries):
            return []
        return self.ledger.claim_batch(entries)

    def enqueue(self, torrent, path: Path) -> dict | None:
        """显式单文件调用也使用完整的一文件批次事务。"""
        records = self.enqueue_batch(torrent, (path,))
        return records[0] if records else None

    def run_pending(self, eligible: Callable[[dict], bool], can_continue: Callable[[], bool], limit: int = 1) -> list[dict]:
        """从持久队列取有限个当前配置任务；未知/已提交记录永远不重新执行。"""
        results = []
        for record in self.ledger.list_records(("claimed",), limit=10000):
            if len(results) >= limit or not can_continue():
                break
            if (record["payload"].get("config_key") != self.config.signature()
                    or record["payload"].get("admission_complete") is not True):
                continue
            if not self.ledger.transition(record["key"], ("claimed",), "preparing"):
                continue
            results.append(self._prepare(record["key"], eligible, can_continue))
        return results

    def _finish(self, key: str, state: str, reason: str, **facts) -> dict:
        if state == "completed":
            facts["completion_scope"] = "file_transfer"
        self.ledger.transition(key, ("preparing", "submitting", "accepted", "uncertain"), state,
                               {"reason": reason, **facts})
        return self.ledger.get(key)

    def _source_valid(self, payload: dict) -> bool:
        # Old ledger records predate per-task scopes and remain bound to configured roots.
        scope_roots = tuple(Path(root) for root in payload.get(
            "scope_roots", tuple(str(root) for root in self.config.roots),
        ))
        context = RecognitionContext("transfer", "local", payload["path"], scope_roots)
        if not scope_roots or not matches_scope(context, "local", scope_roots):
            return False
        try:
            current = _fingerprint(Path(payload["path"]))
            return (str(Path(payload["path"]).resolve()) == payload["real_path"]
                    and all(current[field] == payload[field] for field in ("size", "mtime_ns", "device", "inode")))
        except OSError:
            return False

    def _prepare(self, key: str, eligible: Callable, can_continue: Callable) -> dict:
        payload = self.ledger.get(key)["payload"]
        scope_roots = tuple(Path(root) for root in payload.get(
            "scope_roots", tuple(str(root) for root in self.config.roots),
        ))
        target = self.config.target(scope_roots)
        try:
            if (target is None or str(target) != payload["target_root"] or not eligible(payload)
                    or not self._source_valid(payload) or not can_continue()):
                return self._finish(key, "failed", "source_scope_or_configuration_changed")
            path = Path(payload["path"])
            meta = MetaInfo(path.name)
            recognize_options = {"cache": False} if self.config.actress_subfolder else {}
            media = self.recognize(RecognitionContext("transfer", "local", str(path), scope_roots),
                                   meta=meta, **recognize_options)
            if (not media or media.type != MediaType.MOVIE or str(media.media_source) not in self.config.sources
                    or not media.media_id or str(media.media_id) == "0"):
                return self._finish(key, "failed", "no_verified_metadata")
            if meta.year and media.year and str(meta.year) != str(media.year):
                return self._finish(key, "failed", "filename_year_conflict")
            meta.type = MediaType.MOVIE
            meta.begin_season = meta.begin_episode = meta.end_episode = None
            meta.year = meta.year or media.year
            media.scrape_source = str(media.media_source)
            effective_target = target
            actress_name = None
            actress_identity = None
            if self.config.actress_subfolder:
                evidence, allowed = self.actress_evidence(media, None) if self.actress_evidence else (None, [])
                if evidence is None or len(evidence) != 1 or allowed != evidence:
                    return self._finish(key, "failed", "primary_actress_choice_required")
                actress_name = evidence[0]
                child = child_directory(target, actress_name)
                if child is None:
                    return self._finish(key, "failed", "primary_actress_directory_invalid")
                effective_target, actress_identity = child
            item = FileItem(storage="local", path=str(path), type="file", name=path.name,
                            basename=path.stem, extension=path.suffix.lstrip("."),
                            size=payload["size"], modify_time=payload["modify_time"])
            def continuation():
                child_valid = (not self.config.actress_subfolder or child_directory_matches(
                    target, actress_name, actress_identity))
                return (child_valid and can_continue() and self._source_valid(payload)
                        and self.config.target(scope_roots) == target)
            directory = TransferDirectoryConf(
                name="Xiaojiejie-Scraping", library_path=str(effective_target), library_storage="local",
                transfer_type=self.config.operation, overwrite_mode="never", renaming=True,
                scraping=True, notify=False, library_type_folder=False, library_category_folder=False,
            )
            request = dict(fileitem=item, meta=meta, mediainfo=media, mtype=MediaType.MOVIE,
                           target_directory=directory,
                           media_source=media.media_source, media_id=media.media_id,
                           target_storage="local", target_path=effective_target, transfer_type=self.config.operation,
                           scrape=True, library_type_folder=False, library_category_folder=False,
                           downloader=payload["downloader"], download_hash=payload["download_hash"],
                           background=False, manual=False, force=False, sync_extra_files=False,
                           continue_callback=continuation)
            if not can_continue():
                return self._finish(key, "failed", "stopped_before_preview")
            success, preview = self.transfer(**copy.deepcopy({k: v for k, v in request.items() if k != "continue_callback"}),
                                             continue_callback=continuation, preview=True)
            items = preview.get("items") if isinstance(preview, dict) else None
            if success is not True or not isinstance(items, list) or len(items) != 1:
                return self._finish(key, "failed", "native_preview_not_single_success")
            result = items[0]
            destination = _target_path(result.get("target"), effective_target)
            if (result.get("success") is not True or result.get("source") != str(path)
                    or result.get("source_storage") != "local" or destination is None
                    or destination.resolve() == path.resolve()):
                return self._finish(key, "failed", "native_preview_outside_intent")
            facts = {"media_source": str(media.media_source), "media_id": str(media.media_id),
                     "preview_target": str(destination), "actress_name": actress_name,
                     "actress_root": str(effective_target),
                     "actress_directory_identity": actress_identity}
            self.ledger.transition(key, ("preparing",), "preparing", facts)
            if self.config.mode == "preview":
                return self._finish(key, "previewed", "preview_only_no_file_transfer")
            if not can_continue() or not eligible(payload) or not self._source_valid(payload):
                return self._finish(key, "failed", "stopped_or_changed_before_submission")
            payload = self.ledger.get(key)["payload"]
            history = self._history(payload)
            if history is None:
                return self._finish(key, "failed", "history_preflight_unavailable")
            existing = self._matching_history(payload, history, require_new=False)
            if existing is not None:
                state = "completed" if _value(existing, "status") is True else "failed"
                return self._finish(key, state, "verified_existing_native_history", history_id=_value(existing, "id"))
            if destination.exists() or destination.is_symlink():
                return self._finish(key, "failed", "occupied_target_requires_manual_review")
            if any(_value(row, "status") is True for row in history):
                return self._finish(key, "failed", "other_native_history_requires_manual_review")
            if not continuation() or not eligible(payload):
                return self._finish(key, "failed", "stopped_after_history_preflight")
            baseline = [int(_value(row, "id")) for row in history if isinstance(_value(row, "id"), int)]
            if not self.ledger.transition(key, ("preparing",), "submitting", {"baseline_history_ids": baseline,
                                                                                 "submitted_at": time.time()}):
                return self.ledger.get(key)
            if not continuation():
                return self._finish(key, "failed", "stopped_before_native_call")
            try:
                response = self.transfer(**request, preview=False)
            except Exception:
                return self._finish(key, "uncertain", "native_call_result_unknown")
            # True 只能视为宿主受理，不能用返回文字猜测是否完成。
            returned_true = isinstance(response, tuple) and len(response) == 2 and response[0] is True
            self.ledger.transition(key, ("submitting",), "accepted" if returned_true else "uncertain",
                                   {"reason": "awaiting_public_history", "native_return_success": returned_true})
            return self.reconcile_record(key)
        except Exception:
            current = self.ledger.get(key)
            if current and current["state"] in ("submitting", "accepted", "uncertain"):
                return self._finish(key, "uncertain", "handoff_evidence_unavailable")
            return self._finish(key, "failed", "preparation_failed_no_submission")

    def _history(self, payload: dict) -> list | None:
        try:
            page = self.history(filters={"src": payload["path"], "download_hash": payload["download_hash"]},
                                page={"page": 1, "count": 200, "sort": {"field": "id", "direction": "desc"}})
            rows = _value(page, "items")
            total = _value(page, "total")
            if not isinstance(rows, list) or type(total) is not int or total != len(rows):
                return None
            return rows
        except Exception:
            return None

    @staticmethod
    def _matching_history(payload: dict, rows: list, require_new: bool = True):
        candidates = []
        for row in rows:
            identity = _value(row, "id")
            if type(_value(row, "status")) is not bool:
                continue
            source = _value(row, "src_fileitem")
            if type(identity) is not int or (require_new and identity in payload.get("baseline_history_ids", [])):
                continue
            if (_value(row, "src") != payload["path"] or _value(row, "src_storage") != "local"
                    or str(_value(row, "media_source")) != payload.get("media_source")
                    or str(_value(row, "media_id")) != payload.get("media_id")
                    or _value(row, "downloader") != payload["downloader"]
                    or _value(row, "download_hash") != payload["download_hash"]
                    or _value(row, "mode") != payload["operation"]
                    or _value(row, "dest_storage") != "local"
                    or _value(row, "dest") != payload.get("preview_target") or not isinstance(source, dict)):
                continue
            modified = source.get("modify_time")
            if (source.get("path") != payload["path"] or source.get("storage") != "local"
                    or source.get("size") != payload["size"] or not isinstance(modified, (int, float))
                    or not math.isfinite(modified) or not math.isclose(modified, payload["modify_time"], abs_tol=1e-6, rel_tol=0)):
                continue
            if _value(row, "status") is True:
                try:
                    target = _target_path(payload["preview_target"],
                                          Path(payload.get("actress_root") or payload["target_root"]))
                    if target is None or not target.is_file() or target.stat().st_size != payload["size"]:
                        continue
                except OSError:
                    continue
            candidates.append(row)
        return candidates[0] if len(candidates) == 1 else None

    def reconcile_record(self, key: str) -> dict:
        """只读核对公共历史，未取得证据时保留非终态且绝不重提。"""
        record = self.ledger.get(key)
        if not record or record["state"] not in ("submitting", "accepted", "uncertain"):
            return record
        payload = record["payload"]
        history = self._history(payload)
        match = self._matching_history(payload, history) if history is not None else None
        if match is not None:
            return self._finish(key, "completed" if _value(match, "status") is True else "failed",
                                "verified_native_history", history_id=_value(match, "id"))
        state = "uncertain" if record["state"] == "submitting" else record["state"]
        self.ledger.transition(key, (record["state"],), state, {"last_checked": time.time()})
        return self.ledger.get(key)

    def reconcile(self, limit: int = 20) -> list[dict]:
        """按最近核实时间公平检查旧任务；恢复阶段只读，不调用整理。"""
        records = self.ledger.list_records(("submitting", "accepted", "uncertain"), limit=10000)
        records.sort(key=lambda row: (row["updated_at"], row["key"]))
        return [self.reconcile_record(row["key"]) for row in records[:limit]]
