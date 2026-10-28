"""有范围的插件手动预览与原生整理入口。"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import os
import math
from pathlib import Path
import stat as stat_module
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field
from app.sdk.media import MetaInfo
from app.schemas.file import FileItem
from app.schemas.system import TransferDirectoryConf
from app.schemas.types import MediaType

from .core import NUMBER, normalize_number
from .discovery import VIDEO_EXTENSIONS
from .handoff import native_transfer, _fingerprint, _target_path
from .ledger import HandoffLedger, task_key
from .layout import actor_names, child_directory, child_directory_matches, safe_component
from .diagnostics import capture_query_diagnostics

MAX_SCAN_ENTRIES = 1000
MAX_VIDEO_FILES = 50
MAX_BROWSE_STATUS_RECORDS = 10000
PREVIEW_TTL = 15 * 60


class ManualOptions(BaseModel):
    enabled: bool
    sources: list[dict[str, str]]
    roots: list[str]
    default_target_path: str | None = None
    transfer_type: str = "link"
    actress_subfolder: bool = False


class ManualBrowseItem(BaseModel):
    name: str
    path: str
    type: str
    modify_time: float | None = None
    size: int | None = None
    organization_status: str | None = None
    organized_count: int | None = None
    video_count: int | None = None


class ManualBrowse(BaseModel):
    path: str
    parent: str | None = None
    items: list[ManualBrowseItem]


class ManualPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    target_path: str
    media_source: str
    transfer_type: str = "link"
    library_category_folder: bool = False
    actress_subfolder: bool | None = None
    actress_choices: dict[str, str] = Field(default_factory=dict)


class ManualPreviewItem(BaseModel):
    source: str
    target: str | None = None
    title: str | None = None
    media_source: str | None = None
    media_id: str | None = None
    poster_path: str | None = None
    success: bool
    message: str | None = None
    actor_candidates: list[str] = Field(default_factory=list)
    needs_actress_choice: bool = False
    selected_actress: str | None = None


class ManualSummary(BaseModel):
    total: int
    success: int
    failed: int


class ManualPreview(BaseModel):
    preview_id: str | None = None
    created_at: float | None = None
    expires_at: float | None = None
    items: list[ManualPreviewItem]
    summary: ManualSummary


class ManualExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str


class ManualExecuteItem(BaseModel):
    source: str
    target: str | None = None
    state: str
    message: str | None = None
    completion_scope: str | None = None


class ManualExecuteResponse(BaseModel):
    preview_id: str
    state: str
    items: list[ManualExecuteItem]
    summary: ManualSummary
    completion_scope: str | None = None


def _contained(path: Path, roots: tuple[Path, ...], *, existing: bool = True) -> Path | None:
    """词法路径、实际路径必须都在同一配置范围内。"""
    if not path.is_absolute() or ".." in path.parts:
        return None
    try:
        lexical = Path(os.path.normpath(str(path)))
        actual = path.resolve(strict=existing)
        for root in roots:
            resolved_root = root.resolve(strict=True)
            if lexical.is_relative_to(root) and actual.is_relative_to(resolved_root):
                return actual
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def _local_path(value: str, *, directory: bool | None = None) -> Path | None:
    """解析用户在手动流程中明确选定的既有本地路径。"""
    raw = Path(value)
    if not raw.is_absolute() or ".." in raw.parts:
        return None
    try:
        if Path(os.path.normpath(str(raw))) != raw or raw.resolve(strict=True) != raw or raw.is_symlink():
            return None
        actual = raw.resolve(strict=True)
        if directory is True and not actual.is_dir():
            return None
        if directory is False and not actual.is_file():
            return None
        return actual
    except (OSError, RuntimeError, ValueError):
        return None


def _target_root(value: str, source_roots: tuple[Path, ...] = ()) -> Path | None:
    """目标是既有本地目录，并与本次手动选择的源范围互不重叠。"""
    target = _local_path(value, directory=True)
    if target is None or target == Path("/"):
        return None
    for root in source_roots:
        resolved = root.resolve(strict=True)
        if target.is_relative_to(resolved) or resolved.is_relative_to(target):
            return None
    return target


def _directory_identity(path: Path) -> dict[str, int] | None:
    try:
        stat = path.stat()
        if not path.is_dir():
            return None
        return {"device": stat.st_dev, "inode": stat.st_ino}
    except OSError:
        return None


class ManualTransferWorkspace:
    """服务端保存识别身份和预览意图；客户端只拿不透明预览 ID。"""

    def __init__(self, plugin, data_path: Path, transfer=native_transfer):
        self.plugin = plugin
        self.data_path = Path(data_path)
        self.transfer = transfer
        self._ledger = None
        self._boot_id = uuid.uuid4().hex

    @property
    def roots(self) -> tuple[Path, ...]:
        with self.plugin._lock:
            if self.plugin._storage != "local":
                return ()
            return tuple(Path(root) for root in self.plugin._dirs)

    def _active(self) -> bool:
        return bool(self.plugin._enabled and self.plugin._storage == "local")

    def _configured_target(self) -> Path | None:
        configured = self.plugin._handoff.config.target_path if self.plugin._handoff else ""
        return _target_root(configured, self.roots) if configured else None

    def _selected_target(self, value: str, source_roots: tuple[Path, ...] = ()) -> Path | None:
        return _target_root(value, source_roots)

    def _config_signature(self, effective_actress_subfolder: bool | None = None) -> str:
        payload = {
            "enabled": self._active(),
            "storage": self.plugin._storage,
            "sources": list(self.plugin._active_sources),
        }
        if self.plugin._actress_subfolder:
            payload["actress_subfolder"] = True
        if (effective_actress_subfolder is not None
                and effective_actress_subfolder != self.plugin._actress_subfolder):
            payload["manual_actress_subfolder"] = effective_actress_subfolder
        return task_key(payload)

    def _source_matches(self, intent: dict) -> bool:
        path = Path(intent["source"])
        source_root = Path(intent["source_root"])
        roots = (source_root,)
        try:
            return (not source_root.is_symlink() and source_root.resolve(strict=True) == source_root
                    and _contained(path, roots) is not None
                    and _directory_identity(source_root) == intent.get("source_root_identity")
                    and _fingerprint(path) == intent["source_stat"])
        except OSError:
            return False

    def _target_matches(self, payload: dict, target: Path | None) -> bool:
        source_roots = tuple(Path(item["source_root"]) for item in payload.get("items", []))
        return bool(target and self._selected_target(payload.get("target_root", ""), source_roots) == target
                    and _directory_identity(target) == payload.get("selected_target_identity"))

    def _source_names(self) -> list[dict[str, str]]:
        from .registry import PROVIDERS
        statuses = {item.key: item.label for item in PROVIDERS}
        return [{"title": statuses.get(source, source), "value": source}
                for source in self.plugin._active_sources]

    def options(self) -> ManualOptions:
        roots = self.roots
        target_path = self._configured_target()
        return ManualOptions(enabled=self._active(), sources=self._source_names(),
                             roots=[str(root) for root in roots],
                             default_target_path=str(target_path) if target_path else None,
                             actress_subfolder=self.plugin._actress_subfolder)

    def _ledger_db(self) -> HandoffLedger:
        if self._ledger is None:
            self._ledger = HandoffLedger(self.data_path / "manual-handoff.sqlite3")
        return self._ledger

    def _organization_records(self) -> tuple[list[dict], bool]:
        """读取已核验完成记录；使用账本的只读入口和统一记录上限。"""
        ledgers: list[tuple[str, Path]] = []
        manual_path = self.data_path / "manual-handoff.sqlite3"
        if self._ledger is not None:
            ledgers.append(("manual", self._ledger.path))
        else:
            try:
                info = manual_path.lstat()
                if stat_module.S_ISREG(info.st_mode) and not stat_module.S_ISLNK(info.st_mode):
                    ledgers.append(("manual", manual_path))
                else:
                    return [], False
            except FileNotFoundError:
                pass
            except Exception:
                return [], False
        auto_ledger = getattr(getattr(self.plugin, "_handoff", None), "ledger", None)
        if auto_ledger is not None:
            auto_path = getattr(auto_ledger, "path", None)
            if auto_path is not None and all(Path(auto_path) != path for _, path in ledgers):
                ledgers.append(("automatic", Path(auto_path)))
        if not ledgers:
            return [], True

        budget_per_ledger = max(1, MAX_BROWSE_STATUS_RECORDS // len(ledgers))
        records, complete = [], True
        for kind, path in ledgers:
            try:
                rows = HandoffLedger.list_existing_records(
                    path, ("completed",), limit=budget_per_ledger + 1)
            except Exception:
                complete = False
                continue
            if len(rows) > budget_per_ledger:
                complete = False
                rows = rows[:budget_per_ledger]
            for record in rows:
                payload = record.get("payload") if isinstance(record, dict) else None
                if not isinstance(payload, dict) or payload.get("completion_scope") != "file_transfer":
                    continue
                if kind == "manual" and payload.get("kind") != "manual_file_intent":
                    continue
                source_value = payload.get("source") if kind == "manual" else payload.get("path")
                if not isinstance(source_value, str):
                    continue
                try:
                    source = Path(source_value)
                    if not source.is_absolute() or ".." in source.parts:
                        continue
                except (TypeError, ValueError):
                    continue
                records.append({"kind": kind, "record": record, "payload": payload, "source": source})
        return records, complete

    @staticmethod
    def _verified_automatic_transfer(payload: dict) -> bool:
        """Recheck the exact source version and current hard link for an auto completion."""
        try:
            if payload.get("operation") != "link":
                return False
            source = Path(payload["path"])
            source_stat = source.stat(follow_symlinks=False)
            if (source.is_symlink() or not stat_module.S_ISREG(source_stat.st_mode)
                    or str(source.resolve(strict=True)) != payload["real_path"]
                    or any(getattr(source_stat, attr) != payload[field] for attr, field in (
                        ("st_dev", "device"), ("st_ino", "inode"),
                        ("st_size", "size"), ("st_mtime_ns", "mtime_ns")))):
                return False
            root = Path(payload.get("actress_root") or payload["target_root"]).resolve(strict=True)
            target = _target_path(payload.get("preview_target"), root)
            if target is None or target.is_symlink() or not target.is_file():
                return False
            target_stat = target.stat(follow_symlinks=False)
            return (stat_module.S_ISREG(target_stat.st_mode)
                    and target_stat.st_dev == source_stat.st_dev
                    and target_stat.st_ino == source_stat.st_ino
                    and target_stat.st_size == source_stat.st_size
                    and target_stat.st_mode == source_stat.st_mode)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            return False

    def _organization_record_verified(self, record: dict) -> bool:
        try:
            if record["kind"] == "manual":
                return (self._source_matches(record["payload"])
                        and self._verify_manual_transfer(record["payload"], record["record"]))
            return self._verified_automatic_transfer(record["payload"])
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            return False

    def _annotate_organized_items(self, actual: Path, items: list[ManualBrowseItem]) -> None:
        records, history_complete = self._organization_records()
        if not records and history_complete:
            return
        file_items = {Path(item.path): item for item in items if item.type == "file"}
        directory_items = {Path(item.path): item for item in items if item.type == "dir"}
        direct_records: dict[Path, list[dict]] = {}
        directory_records: dict[Path, list[dict]] = {}
        for record in records:
            try:
                relative = record["source"].relative_to(actual)
            except ValueError:
                continue
            if len(relative.parts) == 1 and record["source"] in file_items:
                direct_records.setdefault(record["source"], []).append(record)
            elif relative.parts:
                top_directory = actual / relative.parts[0]
                if top_directory in directory_items:
                    directory_records.setdefault(top_directory, []).append(record)

        verified_cache: dict[int, bool] = {}

        def has_verified_record(path: Path, relevant: list[dict]) -> bool:
            for record in relevant:
                if record["source"] != path:
                    continue
                identity = id(record)
                if identity not in verified_cache:
                    verified_cache[identity] = self._organization_record_verified(record)
                if verified_cache[identity]:
                    return True
            return False

        for path, item in file_items.items():
            relevant = direct_records.get(path, [])
            if relevant and has_verified_record(path, relevant):
                item.organization_status = "organized"
            elif relevant or not history_complete:
                item.organization_status = "unknown"

        budget = {"entries": 0, "videos": 0, "exhausted": False}
        for directory, item in directory_items.items():
            if directory not in directory_records and not history_complete:
                item.organization_status = "unknown"
        for directory, relevant in directory_records.items():
            total_videos = organized_videos = 0
            scan_complete = history_complete
            if budget["exhausted"]:
                directory_items[directory].organization_status = "incomplete"
                continue
            try:
                directory_root = directory.resolve(strict=True)
                if directory.is_symlink() or not directory_root.is_dir() or directory_root != directory:
                    scan_complete = False
                    raise OSError("unsafe directory")
                pending = [directory_root]
                while pending and not budget["exhausted"]:
                    current_path = pending.pop()
                    try:
                        with os.scandir(current_path) as children:
                            for child_entry in children:
                                budget["entries"] += 1
                                if budget["entries"] > MAX_SCAN_ENTRIES:
                                    scan_complete = False
                                    budget["exhausted"] = True
                                    break
                                try:
                                    if child_entry.is_symlink():
                                        continue
                                    child = Path(child_entry.path)
                                    if child_entry.is_dir(follow_symlinks=False):
                                        pending.append(child)
                                        continue
                                    if (not child_entry.is_file(follow_symlinks=False)
                                            or child.suffix.lower() not in VIDEO_EXTENSIONS):
                                        continue
                                    if budget["videos"] >= MAX_VIDEO_FILES:
                                        scan_complete = False
                                        budget["exhausted"] = True
                                        break
                                    child_stat = child_entry.stat(follow_symlinks=False)
                                    resolved = child.resolve(strict=True)
                                    if (not stat_module.S_ISREG(child_stat.st_mode)
                                            or not resolved.is_relative_to(directory_root)):
                                        scan_complete = False
                                        continue
                                    budget["videos"] += 1
                                    total_videos += 1
                                    if has_verified_record(resolved, relevant):
                                        organized_videos += 1
                                except (OSError, RuntimeError, ValueError):
                                    scan_complete = False
                    except OSError:
                        scan_complete = False
                        break
            except (OSError, RuntimeError, ValueError):
                scan_complete = False

            item = directory_items[directory]
            if scan_complete and total_videos > 0 and organized_videos == total_videos:
                item.organization_status = "organized"
                item.organized_count = organized_videos
                item.video_count = total_videos
            elif scan_complete and organized_videos > 0:
                item.organization_status = "partial"
                item.organized_count = organized_videos
                item.video_count = total_videos
            elif organized_videos > 0:
                item.organization_status = "incomplete"
                item.organized_count = organized_videos
                item.video_count = total_videos
            else:
                item.organization_status = "unknown"

    def browse(self, value: str, kind: str, show_organized: bool = False) -> ManualBrowse:
        if kind not in ("source", "target"):
            raise ValueError("kind 必须是 source 或 target")
        if not self._active():
            raise ValueError("插件已禁用或当前仅支持本地存储")
        roots = self.roots
        if kind == "source":
            if not value:
                value = str(Path("/"))
        else:
            if not value:
                value = str(self._configured_target() or Path("/"))
        actual = _local_path(value, directory=True)
        if actual is None:
            raise ValueError("目录不存在或不是本地规范路径")
        items = []
        try:
            for child in sorted(actual.iterdir(), key=lambda item: item.name.casefold()):
                if child.is_symlink():
                    continue
                child_path = _local_path(str(child), directory=True)
                if child_path:
                    item_type = "dir"
                elif kind == "source" and _local_path(str(child), directory=False) and child.suffix.lower() in VIDEO_EXTENSIONS:
                    item_type = "file"
                else:
                    continue
                try:
                    child_stat = child.lstat()
                except OSError:
                    child_stat = None
                if child_stat is not None and stat_module.S_ISLNK(child_stat.st_mode):
                    continue
                modify_time = child_stat.st_mtime if child_stat and math.isfinite(child_stat.st_mtime) else None
                size = child_stat.st_size if item_type == "file" and child_stat and child_stat.st_size >= 0 else None
                items.append(ManualBrowseItem(name=child.name, path=str(child), type=item_type,
                                              modify_time=modify_time, size=size))
                if len(items) > MAX_SCAN_ENTRIES:
                    raise ValueError("目录条目超过限制")
        except OSError as exc:
            raise ValueError("无法读取该目录") from exc
        parent = str(actual.parent) if actual != Path("/") else None
        if kind == "source" and show_organized is True:
            try:
                self._annotate_organized_items(actual, items)
            except Exception:
                for item in items:
                    if item.type in ("file", "dir") and item.organization_status is None:
                        item.organization_status = "unknown"
        return ManualBrowse(path=str(actual), parent=parent, items=items)

    @staticmethod
    def _source_scope(source: Path) -> tuple[Path, tuple[Path, ...]]:
        actual = _local_path(str(source))
        if actual is None or actual == Path("/"):
            raise ValueError("源路径不存在或不是可选的本地路径")
        if actual.is_file():
            if actual.suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError("仅支持视频文件")
            return actual, (actual.parent,)
        if actual.is_dir():
            return actual, (actual,)
        raise ValueError("源路径必须是文件或目录")

    def _scan(self, source: Path, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        actual = _contained(source, roots)
        if actual is None:
            raise ValueError("源路径不存在或不是本次选择的本地范围")
        if actual.is_file():
            if actual.suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError("仅支持视频文件")
            return (actual,)
        if not actual.is_dir():
            raise ValueError("源路径必须是文件或目录")
        files: list[Path] = []
        count = 0
        for current, directories, filenames in os.walk(actual, followlinks=False):
            current_path = Path(current)
            directories[:] = [name for name in directories if not (current_path / name).is_symlink()]
            count += len(directories) + len(filenames)
            if count > MAX_SCAN_ENTRIES:
                raise ValueError("目录超过扫描上限 1000 项")
            for name in filenames:
                child = current_path / name
                if child.is_symlink() or child.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                if _contained(child, roots) is None:
                    raise ValueError("扫描发现超出识别目录的链接路径")
                files.append(child.resolve(strict=True))
                if len(files) > MAX_VIDEO_FILES:
                    raise ValueError("视频文件超过单次上限 50 个")
        return tuple(sorted(files, key=lambda item: str(item).casefold()))

    def _number_for(self, path: Path, roots: tuple[Path, ...]) -> str | None:
        file_numbers = {normalize_number(match.group()) for match in NUMBER.finditer(path.stem)}
        file_numbers.discard(None)
        if len(file_numbers) > 1:
            return None
        number = next(iter(file_numbers), None)
        parent_numbers = set()
        for parent in path.parents:
            if not any(parent == root.resolve() or parent.is_relative_to(root.resolve()) for root in roots):
                break
            found = {normalize_number(match.group()) for match in NUMBER.finditer(parent.name)}
            found.discard(None)
            if len(found) > 1:
                return None
            if found:
                parent_numbers.add(next(iter(found)))
        if number:
            return None if any(parent_number != number for parent_number in parent_numbers) else number
        return next(iter(parent_numbers)) if len(parent_numbers) == 1 else None

    @staticmethod
    def _meta(path: Path, number: str):
        meta = MetaInfo(path.name)
        meta.type = MediaType.MOVIE
        meta.org_string = number
        meta.begin_season = meta.begin_episode = meta.end_episode = None
        return meta

    @staticmethod
    def _value(media, key):
        value = getattr(media, key, None)
        return getattr(value, "value", value)

    def _native_request(self, path: Path, target: Path, source: str, number: str, media, preview: bool):
        meta = self._meta(path, number)
        media.scrape_source = source
        fingerprint = _fingerprint(path)
        fileitem = FileItem(storage="local", path=str(path), type="file", name=path.name,
                            basename=path.stem, extension=path.suffix.lstrip("."),
                            size=fingerprint["size"], modify_time=fingerprint["modify_time"])
        directory = TransferDirectoryConf(
            name="Xiaojiejie-Scraping 手动整理", library_path=str(target), library_storage="local",
            transfer_type="link", overwrite_mode="never", renaming=True, scraping=True,
            notify=False, library_type_folder=False, library_category_folder=False,
        )
        return dict(fileitem=fileitem, meta=meta, mediainfo=media, mtype=MediaType.MOVIE,
                    target_directory=directory, media_source=source, media_id=str(media.media_id),
                    target_storage="local", target_path=target, transfer_type="link", scrape=True,
                    library_type_folder=False, library_category_folder=False, background=False,
                    manual=False, force=False, sync_extra_files=False, preview=preview)

    def preview(self, request: ManualPreviewRequest) -> ManualPreview:
        if not self._active():
            raise ValueError("插件已禁用或当前仅支持本地存储")
        if request.transfer_type != "link" or request.library_category_folder:
            raise ValueError("首版仅支持硬链接，且不支持按媒体类别建目录")
        if request.media_source not in self.plugin._active_sources:
            raise ValueError("所选来源未启用")
        source, source_roots = self._source_scope(Path(request.path))
        target = self._selected_target(request.target_path, source_roots)
        if target is None:
            raise ValueError("目标必须是独立的既有本地目录，且不能与本次所选源范围重叠")
        if not source_roots or _directory_identity(target) is None:
            raise ValueError("源和目标必须是既有本地路径")
        files = self._scan(source, source_roots)
        now = time.time()
        preview_id = str(uuid.uuid4())
        actress_subfolder = (self.plugin._actress_subfolder if request.actress_subfolder is None
                             else request.actress_subfolder)
        config_signature = self._config_signature(actress_subfolder)
        if not isinstance(request.actress_choices, dict) or any(
                not isinstance(source_path, str) or not isinstance(name, str)
                for source_path, name in request.actress_choices.items()):
            raise ValueError("演员选择格式无效")
        items: list[ManualPreviewItem] = []
        intents = []
        for path in files:
            number = self._number_for(path, source_roots)
            if not number:
                items.append(ManualPreviewItem(source=str(path), success=False,
                                               message="文件名及允许范围内父目录未提取到唯一番号"))
                continue
            if path.stat().st_dev != target.stat().st_dev:
                items.append(ManualPreviewItem(source=str(path), success=False,
                                               message="源文件与目标目录不在同一卷，无法创建硬链接"))
                continue
            with capture_query_diagnostics() as query_events:
                media = self.plugin.recognize_media(meta=self._meta(path, number), mtype=MediaType.MOVIE,
                                                    media_source=request.media_source,
                                                    cache=not actress_subfolder)
            if (not media or str(self._value(media, "media_source")) != request.media_source
                    or not self._value(media, "media_id")):
                message = "所选来源未能确认唯一媒体身份"
                messages = {
                    "access_verification_required": "JavBus 返回访问验证页面，当前无法匿名识别",
                    "rate_limited": "JavBus 限流（HTTP 429），请稍后重试",
                    "login_required": "JavBus 页面要求登录；当前接入不使用账号登录",
                    "not_found": "JavBus 未找到该番号（HTTP 404）",
                    "network_unavailable": "JavBus 请求未获得响应，请稍后重试",
                    "upstream_unavailable": "JavBus 暂时不可用",
                    "access_restricted": "JavBus 当前访问受限",
                    "response_invalid": "JavBus 返回的页面无法确认媒体身份",
                }
                cause = next((reason for source, operation, reason, _ in query_events
                              if source == request.media_source and operation == "search"
                              and reason in messages), None)
                if cause:
                    message = messages[cause]
                items.append(ManualPreviewItem(source=str(path), success=False, message=message))
                continue
            fc2_identity = self.plugin._fc2_identity_matches(media, number)
            actor_folder = actress_subfolder and not fc2_identity
            selected_actress = None
            actress_choice_mode = None
            effective_target = target
            actress_identity = None
            candidates = []
            if actor_folder:
                trusted_names, allowed_trusted = self.plugin.actress_evidence(media, number)
                candidates = allowed_trusted if trusted_names is not None else actor_names(media)
                requested_actress = request.actress_choices.get(str(path))
                if requested_actress is not None:
                    if requested_actress in candidates:
                        selected_actress = requested_actress
                        actress_choice_mode = "manual"
                elif (trusted_names and allowed_trusted == trusted_names):
                    selected_actress = trusted_names[0]
                    actress_choice_mode = "source_flag_first"
                if selected_actress is None:
                    message = ("本次来源识别没有可信女主演来源证据；等待来源数据补齐后重试"
                               if not trusted_names else "请从本片可信女主演候选中选择后重新预览")
                    items.append(ManualPreviewItem(
                        source=str(path), title=self._value(media, "title"),
                        media_source=request.media_source, media_id=str(media.media_id),
                        poster_path=self._value(media, "poster_path"), success=False,
                        message=message, actor_candidates=candidates,
                        needs_actress_choice=bool(candidates)))
                    continue
                selected_actress = safe_component(selected_actress)
                child = child_directory(target, selected_actress) if selected_actress else None
                if child is None:
                    items.append(ManualPreviewItem(source=str(path), success=False,
                        message="女主演目录名或现有目录不安全；未调用原生预览"))
                    continue
                effective_target, actress_identity = child
            try:
                success, result = self.transfer(**self._native_request(
                    path, effective_target, request.media_source, number, media, preview=True))
            except Exception:
                success, result = False, None
            rows = result.get("items") if isinstance(result, dict) else None
            row = rows[0] if isinstance(rows, list) and len(rows) == 1 else None
            destination = _target_path(row.get("target"), effective_target) if isinstance(row, dict) else None
            ok = bool(success is True and row and row.get("success") is True
                      and row.get("source") == str(path) and row.get("source_storage") == "local"
                      and destination is not None and destination != path
                      and (not actor_folder or child_directory_matches(
                          target, selected_actress, actress_identity))
                      and not destination.exists() and not destination.is_symlink())
            items.append(ManualPreviewItem(
                source=str(path), target=str(destination) if destination else None,
                title=self._value(media, "title"), media_source=request.media_source,
                media_id=str(media.media_id), poster_path=self._value(media, "poster_path"),
                success=ok, message=None if ok else "原生预览未返回唯一且范围内的成功目标",
                needs_actress_choice=bool(actor_folder and len(candidates) > 1),
                selected_actress=selected_actress, actor_candidates=candidates))
            if ok:
                intents.append({"source": str(path), "target": str(destination),
                                "source_stat": _fingerprint(path), "source_root": str(source_roots[0]),
                                "source_root_identity": _directory_identity(source_roots[0]),
                                "media_source": request.media_source,
                                "media_id": str(media.media_id), "number": number,
                                "config_signature": config_signature,
                                "actress_subfolder": actress_subfolder,
                                "actress_name": selected_actress,
                                "actress_choice_mode": actress_choice_mode,
                                "actress_root": str(effective_target),
                                "actress_parent_root": str(target),
                                "actress_directory_identity": actress_identity})
        target_groups: dict[str, list[int]] = {}
        for index, item in enumerate(items):
            if item.success and item.target:
                target_groups.setdefault(item.target, []).append(index)
        duplicates = {path for path, indexes in target_groups.items() if len(indexes) > 1}
        if duplicates:
            for index, item in enumerate(items):
                if item.target in duplicates:
                    item.success = False
                    item.message = "多个源文件映射到同一目标，已阻止整个冲突目标"
            intents = [intent for intent in intents if intent["target"] not in duplicates]
        if intents:
            expires = now + PREVIEW_TTL
            payload = {"kind": "manual_preview", "preview_id": preview_id,
                       "created_at": now, "expires_at": expires,
                       "generation": self.plugin._generation, "boot_id": self._boot_id,
                       "config_signature": config_signature, "target_root": str(target),
                       "selected_target_identity": _directory_identity(target),
                       "items": intents}
            self._ledger_db().claim(preview_id, payload)
            self._ledger_db().transition(preview_id, ("claimed",), "previewed")
        else:
            expires = None
        return ManualPreview(preview_id=preview_id if intents else None, created_at=now if intents else None,
                             expires_at=expires, items=items,
                             summary=ManualSummary(total=len(items), success=sum(i.success for i in items),
                                                   failed=sum(not i.success for i in items)))

    def _get_preview(self, preview_id: str):
        record = self._ledger_db().get(preview_id)
        if not record or record["payload"].get("kind") != "manual_preview":
            raise ValueError("预览不存在")
        return record

    def status(self, preview_id: str) -> ManualExecuteResponse:
        record = self._get_preview(preview_id)
        state, payload = record["state"], record["payload"]
        items = []
        for item in payload["items"]:
            item_state, message, completion_scope = state, payload.get("result_message"), None
            key = item.get("intent_key")
            intent = self._ledger_db().get(key) if key else None
            if intent:
                item_state = intent["state"]
                message = intent["payload"].get("result_message")
                verified = self._verify_manual_transfer(item, intent)
                if item_state in ("accepted", "uncertain"):
                    if verified:
                        self._ledger_db().transition(key, (item_state,), "completed", {
                            "completion_scope": "file_transfer",
                            "result_message": "硬链接已核验；刮削请见原生历史",
                        })
                        intent = self._ledger_db().get(key)
                        item_state = intent["state"]
                        message = intent["payload"].get("result_message")
                if item_state == "completed" and verified:
                    completion_scope = intent["payload"].get("completion_scope")
                elif item_state == "completed":
                    item_state = "uncertain"
                    message = "硬链接回读未通过；请人工核对原生历史"
            items.append(ManualExecuteItem(source=item["source"], target=item["target"], state=item_state,
                                           message=message, completion_scope=completion_scope))
        if items and all(item.state == "completed" and item.completion_scope == "file_transfer" for item in items):
            if state != "completed":
                self._ledger_db().transition(preview_id, (state,), "completed", {
                    "completion_scope": "file_transfer",
                    "result_message": "所有硬链接已核验；刮削请见原生历史",
                })
            state = "completed"
        elif state == "completed":
            # Completed must always be backed by a fresh readback of every item.
            state = "accepted" if any(item.state in ("accepted", "completed") for item in items) else "uncertain"
        scope = "file_transfer" if state == "completed" else None
        return ManualExecuteResponse(preview_id=preview_id, state=state, items=items,
                                     completion_scope=scope,
                                     summary=ManualSummary(total=len(items),
                                                           success=sum(i.state in ("accepted", "completed") for i in items),
                                                           failed=sum(i.state == "failed" for i in items)))

    @staticmethod
    def _verified_fc2_without_actress(payload: dict) -> bool:
        """Recognize only a persisted canonical FC2 identity with no actress child."""
        try:
            source = payload.get("media_source")
            number = payload.get("number")
            if (payload.get("actress_subfolder") is not True
                    or payload.get("actress_name") is not None
                    or payload.get("actress_directory_identity") is not None
                    or source not in {"fc2", "fc2hub", "fc2ppvdb"}
                    or not isinstance(number, str)
                    or normalize_number(number) != number
                    or not number.startswith("FC2-PPV-")):
                return False
            identity = number.removeprefix("FC2-PPV-")
            expected_id = "id" + identity if source == "fc2hub" else identity
            if str(payload.get("media_id")) != expected_id:
                return False
            target = Path(payload["target_root"])
            return (target == Path(payload["actress_root"])
                    and target == Path(payload["actress_parent_root"])
                    and target.resolve(strict=True).is_dir())
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            return False

    @staticmethod
    def _verify_manual_transfer(item: dict, intent: dict) -> bool:
        """Only confirm a stable regular hard link whose source version still matches."""
        try:
            source = Path(item["source"])
            target = Path(item["target"])
            source_stat = source.stat(follow_symlinks=False)
            recorded = intent["payload"]["source_stat"]
            if (source.is_symlink() or not source.is_file()
                    or any(getattr(source_stat, attr) != recorded[key] for attr, key in (
                        ("st_dev", "device"), ("st_ino", "inode"),
                        ("st_size", "size"), ("st_mtime_ns", "mtime_ns")))):
                return False
            root = Path(intent["payload"]["target_root"]).resolve(strict=True)
            intent_payload = intent["payload"]
            if intent_payload.get("actress_subfolder"):
                if not ManualTransferWorkspace._verified_fc2_without_actress(intent_payload):
                    if (not intent_payload.get("actress_name") or not child_directory_matches(
                            Path(intent_payload["actress_parent_root"]), intent_payload["actress_name"],
                            intent_payload.get("actress_directory_identity"), allow_created=True)):
                        return False
            if (not target.is_absolute() or ".." in target.parts or target == root
                    or not target.is_relative_to(root) or not target.resolve(strict=True).is_relative_to(root)
                    or target.is_symlink() or not target.is_file()):
                return False
            target_stat = target.stat(follow_symlinks=False)
            return (target_stat.st_dev == source_stat.st_dev and target_stat.st_ino == source_stat.st_ino
                    and target_stat.st_mode == source_stat.st_mode)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            return False

    def execute(self, preview_id: str) -> ManualExecuteResponse:
        record = self._get_preview(preview_id)
        if record["state"] != "previewed":
            return self.status(preview_id)
        if not self._ledger_db().transition(preview_id, ("previewed",), "preparing"):
            return self.status(preview_id)
        payload = record["payload"]
        now = time.time()
        target = self._selected_target(payload["target_root"])
        failed = (not self._active() or not target
                  or payload.get("generation") != self.plugin._generation
                  or payload.get("boot_id") != self._boot_id
                  or payload.get("config_signature") != self._config_signature(
                      payload["items"][0].get("actress_subfolder") if payload.get("items") else None)
                  or not self._target_matches(payload, target)
                  or now >= payload.get("expires_at", 0))
        if not failed:
            for intent in payload["items"]:
                path = Path(intent["source"])
                if (intent.get("media_source") not in self.plugin._active_sources
                        or intent.get("config_signature") != payload.get("config_signature")
                        or not self._source_matches(intent)):
                    failed = True
                    break
                try:
                    media = self.plugin.recognize_media(meta=self._meta(path, intent["number"]),
                        mtype=MediaType.MOVIE, media_source=intent["media_source"],
                        media_id=intent["media_id"], cache=False)
                    if (not media or str(self._value(media, "media_source")) != intent["media_source"]
                            or str(self._value(media, "media_id")) != intent["media_id"]):
                        failed = True
                        break
                    fc2_identity = self.plugin._fc2_identity_matches(media, intent["number"])
                    actor_folder = intent.get("actress_subfolder") and not fc2_identity
                    effective_target = Path(intent.get("actress_root") or target)
                    if not actor_folder and effective_target != target:
                        failed = True
                        break
                    if actor_folder:
                        trusted_names, allowed = self.plugin.actress_evidence(media, intent["number"])
                        choice_mode = intent.get("actress_choice_mode")
                        if choice_mode == "source_flag_unique":
                            valid_name = (trusted_names is not None and len(trusted_names) == 1
                                          and allowed == trusted_names
                                          and trusted_names[0] == intent.get("actress_name"))
                        elif choice_mode == "source_flag_first":
                            valid_name = (trusted_names is not None and bool(trusted_names)
                                          and allowed == trusted_names
                                          and trusted_names[0] == intent.get("actress_name"))
                        else:
                            candidates_now = allowed if trusted_names is not None else actor_names(media)
                            valid_name = intent.get("actress_name") in candidates_now
                        if (not valid_name or not child_directory_matches(
                                target, intent["actress_name"], intent.get("actress_directory_identity"))
                                or child_directory(target, intent["actress_name"])[0] != effective_target):
                            failed = True
                            break
                    preview_ok, preview = self.transfer(**self._native_request(
                        path, effective_target, intent["media_source"], intent["number"], media, preview=True))
                    rows = preview.get("items") if isinstance(preview, dict) else None
                    if (preview_ok is not True or not isinstance(rows, list) or len(rows) != 1
                            or rows[0].get("success") is not True or rows[0].get("target") != intent["target"]
                            or Path(intent["target"]).exists() or Path(intent["target"]).is_symlink()):
                        failed = True
                        break
                except Exception:
                    failed = True
                    break
        if failed:
            self._ledger_db().transition(preview_id, ("preparing",), "failed",
                                         {"result_message": "预览过期或源文件、配置、身份、目标已变化；未提交"})
            return self.status(preview_id)
        file_entries = []
        for intent in payload["items"]:
            version = {key: intent["source_stat"][key] for key in ("device", "inode", "size", "mtime_ns")}
            key = task_key({"kind": "manual_file_intent", "source": intent["source"],
                            "source_version": version, "media_source": intent["media_source"],
                            "media_id": intent["media_id"], "target": intent["target"]})
            intent["intent_key"] = key
            file_entries.append((key, {"kind": "manual_file_intent",
                                       "target_root": intent.get("actress_root") or str(target), **intent}))
        if not self._ledger_db().transition(preview_id, ("preparing",), "preparing",
                                            {"items": payload["items"]}):
            return self.status(preview_id)
        try:
            claimed = self._ledger_db().claim_batch(file_entries)
        except Exception:
            claimed = []
        if len(claimed) != len(file_entries):
            self._ledger_db().transition(preview_id, ("preparing",), "failed",
                                         {"result_message": "该文件版本与目标已有提交意图，拒绝重复提交"})
            return self.status(preview_id)
        if not self._ledger_db().transition(preview_id, ("preparing",), "submitting",
                                            {"submitted_at": now, "items": payload["items"]}):
            return self.status(preview_id)
        for key, _ in file_entries:
            if not self._ledger_db().transition(key, ("claimed",), "submitting"):
                self._ledger_db().transition(preview_id, ("submitting",), "uncertain",
                                             {"result_message": "文件提交意图状态不一致；禁止自动重试"})
                return self.status(preview_id)
        try:
            for intent in payload["items"]:
                path = Path(intent["source"])
                if (not self._active() or payload.get("generation") != self.plugin._generation
                        or intent["media_source"] not in self.plugin._active_sources
                        or intent["config_signature"] != self._config_signature(
                            intent.get("actress_subfolder"))
                        or payload.get("boot_id") != self._boot_id
                        or not self._source_matches(intent)
                        or not self._target_matches(payload, target)
                        or (intent.get("actress_subfolder") and intent.get("actress_name")
                            and not child_directory_matches(target, intent["actress_name"],
                                                            intent.get("actress_directory_identity")))
                        or Path(intent["target"]).exists() or Path(intent["target"]).is_symlink()):
                    raise RuntimeError("源文件提交前发生变化")
                media = self.plugin.recognize_media(meta=self._meta(path, intent["number"]),
                    mtype=MediaType.MOVIE, media_source=intent["media_source"],
                    media_id=intent["media_id"], cache=False)
                if (not media or str(self._value(media, "media_source")) != intent["media_source"]
                        or str(self._value(media, "media_id")) != intent["media_id"]):
                    raise RuntimeError("提交时原生身份回查不一致")
                effective_target = Path(intent.get("actress_root") or target)
                fc2_identity = self.plugin._fc2_identity_matches(media, intent["number"])
                actor_folder = intent.get("actress_subfolder") and not fc2_identity
                if not actor_folder and effective_target != target:
                    raise RuntimeError("FC2 目标不应包含演员目录")
                if actor_folder:
                    trusted_names, allowed = self.plugin.actress_evidence(media, intent["number"])
                    choice_mode = intent.get("actress_choice_mode")
                    if choice_mode == "source_flag_unique":
                        valid_name = (trusted_names is not None and len(trusted_names) == 1
                                      and allowed == trusted_names
                                      and trusted_names[0] == intent.get("actress_name"))
                    elif choice_mode == "source_flag_first":
                        valid_name = (trusted_names is not None and bool(trusted_names)
                                      and allowed == trusted_names
                                      and trusted_names[0] == intent.get("actress_name"))
                    else:
                        candidates_now = allowed if trusted_names is not None else actor_names(media)
                        valid_name = intent.get("actress_name") in candidates_now
                    if (not valid_name or not child_directory_matches(
                            target, intent["actress_name"], intent.get("actress_directory_identity"))):
                        raise RuntimeError("提交时女主演证据或子目录已变化")
                if (payload.get("generation") != self.plugin._generation
                        or not self._active() or intent["media_source"] not in self.plugin._active_sources
                        or intent["config_signature"] != self._config_signature(
                            intent.get("actress_subfolder"))
                        or not self._source_matches(intent) or not self._target_matches(payload, target)):
                    raise RuntimeError("提交前插件代次或交接范围已变化")
                def continue_callback(intent=intent, payload=payload, target=target, path=path,
                                      generation=payload["generation"], actor_folder=actor_folder):
                    child_ok = (not actor_folder or child_directory_matches(
                        target, intent["actress_name"], intent.get("actress_directory_identity")))
                    return (child_ok and self._active() and payload.get("generation") == generation
                            and self.plugin._generation == generation
                            and intent["media_source"] in self.plugin._active_sources
                            and payload.get("boot_id") == self._boot_id
                            and intent["config_signature"] == self._config_signature(
                                intent.get("actress_subfolder"))
                            and self._source_matches(intent)
                            and self._target_matches(payload, target))
                result = self.transfer(**self._native_request(path, effective_target, intent["media_source"],
                                                               intent["number"], media, preview=False),
                                       continue_callback=continue_callback)
                if not isinstance(result, tuple) or not result or result[0] is not True:
                    raise RuntimeError("宿主未确认整理请求")
                self._ledger_db().transition(intent["intent_key"], ("submitting",), "accepted",
                                             {"result_message": "宿主已收到整理请求"})
        except Exception:
            for intent in payload["items"]:
                self._ledger_db().transition(intent["intent_key"], ("submitting",), "uncertain",
                                             {"result_message": "结果不确定；永久禁止重复提交"})
            self._ledger_db().transition(preview_id, ("submitting",), "uncertain",
                                         {"result_message": "宿主整理结果不确定；不会自动重提"})
            return self.status(preview_id)
        self._ledger_db().transition(preview_id, ("submitting",), "accepted",
                                     {"result_message": "宿主已收到整理请求；实际完成待公开证据核验"})
        return self.status(preview_id)
