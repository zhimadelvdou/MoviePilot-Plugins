"""Xiaojiejie-Scraping V3：直连多来源精确识别与原生刮削扩展。"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import asyncio
import copy
import math
import time
from collections import OrderedDict
from pathlib import Path
import threading
import weakref
from app.sdk.plugin.base import _PluginBase
from app.sdk.media import MediaInfo
from app.sdk.services import DownloaderHelper
from app.schemas.types import MediaSource, MediaType, SystemConfigKey
from .core import (SOURCE, IDENTIFIER, JsonCache, extract_number, image_map, normalize_number, project_record,
                   project_actress_evidence, project_javbus_actress_evidence, record_numbers, render_nfo)
from .artwork import backdrop_candidates, is_usable_landscape
from .provider import MankoProvider
from .diagnostics import diagnose
from .direct_primary import FANZAProvider, MGStageProvider, DUGAProvider, SODProvider
from .direct_secondary import JavBusProvider, Jav321Provider, FC2HubProvider
from .fc2ppvdb import FC2PPVDBProvider
from .fc2_official import FC2OfficialProvider
from .scope import RecognitionContext, matches_scope
from .registry import PROVIDERS
from .discovery import DiscoveryJournal, exact_tags, owned_torrent_files, torrent_files
from .gfriends import GFriendsProvider, actor_image
from .handoff import HandoffConfig, HandoffCoordinator
from .ledger import HandoffLedger, HandoffLedgerError, LedgerFullError, task_key
from .automatic import AutoTriggerGuard, is_valid_completion_time
from .automatic_api import api_routes as automatic_api_routes
from app.schemas.types import TorrentStatus
from .manual import ManualTransferWorkspace
from .manual_api import api_routes as manual_api_routes
from .layout import safe_component
from .artwork_relay import JavBusArtworkRelay, api_routes as artwork_api_routes

MATCH_MODES = frozenset({"tag", "path", "tag_or_path", "tag_and_path", "category"})


class XiaojiejieScraping(_PluginBase):
    """默认关闭；显式来源查询与自动任务发现分离，原生整理仍有辅助补充风险。"""
    plugin_name = "Xiaojiejie-Scraping"
    plugin_desc = "直连多来源刮削、插件独立手动整理入口与 qBittorrent 发现"
    plugin_icon = ""
    plugin_version = "0.8.19"
    plugin_author = "zhimadelvdou（基于 hyuan280 方案重写）"
    author_url = "https://github.com/zhimadelvdou/MoviePilot-Plugins"
    plugin_config_prefix = "xiaojiejiescraping_"
    plugin_order = 25
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._lock = threading.RLock()
        self._generation = 0
        self._enabled = False
        self._storage = "local"
        self._dirs = ()
        self._image_hosts = ()
        self._artwork_lock = threading.RLock()
        self._artwork_cache = OrderedDict()
        self._artwork_relay = JavBusArtworkRelay(self._artwork_state)
        self._rename_event_manager = None
        self._rename_event_type = None
        self._rename_event_handler = self._on_transfer_rename_build
        self._provider = None
        self._providers = {}
        self._active_sources = (SOURCE,)
        self._cache = None
        self._actress_evidence = {}
        self._discovery = False
        self._downloader = ""
        self._tag = ""
        self._category = ""
        self._journal = None
        self._actor_images = False
        self._actress_subfolder = False
        self._actor_index = None
        self._gfriends = GFriendsProvider()
        self._poll_lock = threading.Lock()
        self._handoff = None
        self._handoff_error = ""
        self._handoff_limit = 1
        self._manual = None
        self._auto_new_only = False
        self._match_mode = "tag_and_path"
        self._auto_guard = None
        self._auto_error = ""
        self._last_poll_at = None
        self._auto_counts = {"checked": 0, "new_only_rejected": 0, "discovered": 0, "enqueued": 0}

    def init_plugin(self, config: dict | None = None) -> None:
        """每次重载替换实例状态，不联网、不扫描目录、不调度旧记录修复。"""
        config = config or {}
        self._remove_javbus_rename_hook()
        with self._lock:
            self._generation += 1
            self._artwork_relay.rotate(self._generation)
            self._actress_evidence.clear()
            self._enabled = config.get("enabled") is True
            self._storage = str(config.get("storage") or "local")
            self._discovery = config.get("discovery_enabled") is True
            self._auto_new_only = config.get("auto_new_only") is True
            requested_match_mode = config.get("automatic_match_mode", "tag_and_path")
            self._match_mode = (requested_match_mode
                                if isinstance(requested_match_mode, str) and requested_match_mode in MATCH_MODES
                                else "invalid")
            self._downloader = str(config.get("downloader") or "").strip()
            self._tag = str(config.get("torrent_tag") or "").strip()
            raw_category = config.get("torrent_category")
            self._category = raw_category.strip() if isinstance(raw_category, str) else ""
            self._dirs = tuple(Path(v) for v in self._lines(config.get("recognize_dirs")) if Path(v).is_absolute() and Path(v) != Path("/") and ".." not in Path(v).parts)
            self._actor_images = config.get("actor_images") is True
            self._actress_subfolder = config.get("actress_subfolder") is True
            self._actor_index = JsonCache(self.get_data_path() / "actors-v1.json", max_entries=1, max_bytes=32 * 1024 * 1024) if self._enabled and self._actor_images else None
            self._image_hosts = tuple(v.lower() for v in self._lines(config.get("image_hosts")))
            with self._artwork_lock:
                self._artwork_cache.clear()
            self._provider = MankoProvider() if self._enabled else None
            self._providers = {"fanza": FANZAProvider(), "mgstage": MGStageProvider(), "duga": DUGAProvider(),
                               "sod": SODProvider(), "javbus": JavBusProvider(api_url=config.get("javbus_api_url") or ""), "jav321": Jav321Provider(),
                               "fc2hub": FC2HubProvider(), "fc2ppvdb": FC2PPVDBProvider(),
                               "fc2": FC2OfficialProvider()} if self._enabled else {}
            requested = self._lines(config.get("sources", [SOURCE]))
            self._active_sources = tuple(source for source in requested if source in self._adapters())
            self._cache = JsonCache(self.get_data_path() / "metadata-v2.json") if self._enabled else None
            self._journal = DiscoveryJournal(self.get_data_path() / "discovery-v1.json") if self._enabled and self._discovery else None
            self._handoff, self._handoff_error = None, ""
            self._manual = ManualTransferWorkspace(self, self.get_data_path()) if self._enabled else None
            limit = config.get("handoff_limit", 1)
            self._handoff_limit = min(5, max(1, limit)) if type(limit) is int else 1
            if self._enabled:
                try:
                    ledger = HandoffLedger(self.get_data_path() / "handoff.sqlite3")
                    handoff_config = HandoffConfig(
                        mode=str(config.get("handoff_mode") or "discover"),
                        target_path=str(config.get("target_path") or ""),
                        operation=str(config.get("transfer_type") or ""), roots=self._dirs,
                        sources=self._active_sources, ownership_ack=config.get("ownership_ack") is True,
                        auxiliary_ack=config.get("auxiliary_ack") is True,
                        actress_subfolder=self._actress_subfolder,
                    )
                    self._handoff = HandoffCoordinator(ledger, handoff_config, self.recognize_scoped,
                                                       actress_evidence=self.actress_evidence,
                                                       fc2_identity_matches=self._fc2_identity_matches)
                except HandoffLedgerError:
                    self._handoff_error = "交接账本不可用，自动交接已阻止；不会重置或删除旧状态"
            scope_payload = {
                "downloader": self._downloader, "torrent_tag": self._tag,
                "storage": self._storage,
                "roots": [str(root) for root in self._dirs],
                "target_path": str(config.get("target_path") or ""),
                "sources": list(self._active_sources),
                "operation": str(config.get("transfer_type") or ""),
                "match_mode": self._match_mode,
            }
            if self._match_mode == "category":
                scope_payload["torrent_category"] = self._category
            if self._actress_subfolder:
                scope_payload["actress_subfolder"] = True
            scope_signature = task_key(scope_payload)
            self._auto_guard = AutoTriggerGuard(self.get_data_path() / "auto-trigger.sqlite3")
            self._auto_guard.configure(active=self._enabled and self._discovery and self._storage == "local",
                                        new_only=self._auto_new_only,
                                        scope_signature=scope_signature)
            self._auto_error = self._auto_guard.error
            diagnose("plugin", "configure", "enabled" if self._enabled else "disabled", count=len(self._active_sources))
            for source in self._active_sources:
                diagnose(source, "configure", "enabled")
        self._register_javbus_rename_hook()


    @staticmethod
    def _lines(value) -> tuple[str, ...]:
        values = value.splitlines() if isinstance(value, str) else value if isinstance(value, list) else []
        return tuple(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))

    @staticmethod
    def _id_cache_key(source: str, identity: str) -> str:
        namespace = ":artwork-v1" if source == "javbus" else ""
        return f"{source}:id{namespace}:{identity}"

    @staticmethod
    def _number_cache_key(source: str, number: str) -> str:
        namespace = ":artwork-v1" if source == "javbus" else ""
        return f"{source}:number{namespace}:{number}"

    def _artwork_state(self):
        with self._lock:
            return (self._enabled, "javbus" in self._active_sources,
                    self._generation, self._image_hosts)

    def _register_javbus_rename_hook(self) -> bool:
        """Register the host rename extension only while a supported source is enabled."""
        with self._lock:
            active = self._enabled and bool({"javbus", "fc2", "fc2hub", "fc2ppvdb"}.intersection(self._active_sources))
        if not active or self._rename_event_manager is not None:
            return False
        try:
            from app.schemas.types import ChainEventType

            event_type = ChainEventType.TransferRenameBuild
            manager = getattr(self, "eventmanager", None)
            add_listener = getattr(manager, "add_event_listener", None)
            remove_listener = getattr(manager, "remove_event_listener", None)
            if not event_type or not callable(add_listener) or not callable(remove_listener):
                raise AttributeError("rename event API unavailable")
            add_listener(event_type, self._rename_event_handler)
        except Exception:
            diagnose("javbus", "configure", "provider_unavailable")
            return False
        self._rename_event_manager = manager
        self._rename_event_type = event_type
        return True

    def _remove_javbus_rename_hook(self) -> bool:
        manager = self._rename_event_manager
        event_type = self._rename_event_type
        if manager is None or not event_type:
            return True
        remove_listener = getattr(manager, "remove_event_listener", None)
        if not callable(remove_listener):
            diagnose("javbus", "configure", "provider_unavailable")
            return False
        try:
            remove_listener(event_type, self._rename_event_handler)
        except Exception:
            diagnose("javbus", "configure", "provider_unavailable")
            return False
        self._rename_event_manager = None
        self._rename_event_type = None
        return True

    def _on_transfer_rename_build(self, event_data) -> None:
        """Use a validated packet number for the formatter copy, never raw media metadata."""
        payload = getattr(event_data, "event_data", None)
        if payload is None and isinstance(event_data, dict):
            payload = event_data
        if isinstance(payload, dict):
            rename_dict = payload.get("rename_dict")
        else:
            rename_dict = getattr(payload, "rename_dict", None)
        if not isinstance(rename_dict, dict):
            return
        mediainfo = rename_dict.get("__mediainfo__")
        if mediainfo is None:
            return
        source = str(getattr(mediainfo, "media_source", None))
        supported = {"javbus", "fc2", "fc2hub", "fc2ppvdb"}
        if source not in supported:
            return
        with self._lock:
            if not self._enabled or source not in self._active_sources:
                return
            generation = self._generation
        media_type = getattr(mediainfo, "type", None)
        media_type = getattr(media_type, "value", media_type)
        if media_type not in (MediaType.MOVIE, MediaType.MOVIE.value):
            return
        try:
            owned = self._owned(mediainfo)
        except Exception:
            return
        if not owned:
            return
        try:
            packet = owned["packet"]
            record = packet["record"]
            number = packet["number"]
            identity = str(getattr(mediainfo, "media_id", ""))
            if (record.get("_source", source) != source
                    or str(record.get("_id")) != identity
                    or not isinstance(number, str)
                    or normalize_number(number) != number
                    or record_numbers(record) != {number}):
                return
            if source == "javbus" and normalize_number(identity) != number:
                return
            if source in {"fc2", "fc2hub", "fc2ppvdb"} and not number.startswith("FC2-PPV-"):
                return
        except Exception:
            return
        with self._lock:
            if (not self._enabled or source not in self._active_sources
                    or generation != self._generation):
                return
            rename_dict["title"] = number
            if source in {"fc2", "fc2hub", "fc2ppvdb"}:
                year = owned.get("projected", {}).get("year")
                rename_dict["year"] = year if isinstance(year, str) and len(year) == 4 and year.isdigit() else ""

    def get_state(self) -> bool:
        """返回实例是否已启用。"""
        return self._enabled

    def stop_service(self) -> None:
        """撤销进行中的结果提交；不保留跨实例客户端或退出事件。"""
        with self._lock:
            self._generation += 1
            self._enabled = False
            self._provider = None
            self._providers = {}
            self._cache = None
            self._journal = None
            self._actor_index = None
            self._actress_evidence.clear()
            self._handoff = None
            self._manual = None
            # Reload teardown revokes memory only; persistent new-task boundary is preserved.
            self._auto_guard = None
        self._remove_javbus_rename_hook()

    def get_service(self) -> list[dict]:
        """宿主管理五分钟轮询；默认只读，整理须另选执行模式及完整权限。"""
        uses_tag = self._match_mode in ("tag", "tag_or_path", "tag_and_path")
        uses_path = self._match_mode in ("path", "tag_or_path", "tag_and_path")
        valid_scope = (self._match_mode in MATCH_MODES
                       and (bool(self._category) if self._match_mode == "category" else
                            ((bool(self._tag) or bool(self._dirs)) if self._match_mode == "tag_or_path"
                             else (not uses_tag or bool(self._tag)) and (not uses_path or bool(self._dirs)))))
        if not (self._enabled and self._discovery and self._downloader and valid_scope and self._storage == "local"):
            return []
        return [{"id": "xiaojiejiescraping_discovery", "name": "Xiaojiejie-Scraping 完成任务轮询", "trigger": "interval",
                 "func": self.poll_completed, "kwargs": {"seconds": 300}}]

    @staticmethod
    def _is_qbittorrent(name: str) -> bool:
        try:
            config = DownloaderHelper().get_config(name)
            return bool(config and config.type == "qbittorrent")
        except Exception:
            return False

    @staticmethod
    def _raw_completed_row(downloader: str, download_hash: str) -> dict | None:
        """Return exactly one raw row from the public qBittorrent service capability."""
        try:
            service = DownloaderHelper().get_service(name=downloader, type_filter="qbittorrent")
            getter = getattr(getattr(service, "instance", None), "get_completed_torrents", None)
            if not service or service.type != "qbittorrent" or not callable(getter):
                return None
            rows = getter(ids=[download_hash], tags=None)  # pylint: disable=not-callable
            if not isinstance(rows, list):
                return None
            matches = [row for row in rows if isinstance(row, dict) and row.get("hash") == download_hash]
            return matches[0] if len(matches) == 1 else None
        except Exception:
            return None

    @staticmethod
    def _safe_completed_row(row: dict | None, download_hash: str, tag: str | None,
                            *, category: str | None = None) -> bool:
        if not isinstance(row, dict) or row.get("hash") != download_hash:
            return False
        progress, left = row.get("progress"), row.get("amount_left")
        safe_states = {"uploading", "stalledup", "forcedup", "queuedup", "pausedup", "stoppedup"}
        return (str(row.get("state") or "").lower() in safe_states
                and type(progress) in (int, float) and math.isfinite(progress) and progress >= 1
                and type(left) in (int, float) and math.isfinite(left) and left == 0
                and (not tag or tag in exact_tags(row.get("tags")))
                and (category is None or row.get("category") == category))

    @staticmethod
    def _safe_completed_hash(downloader: str, download_hash: str, tag: str | None) -> bool:
        """经 SDK 服务目录调用公开完成状态过滤；缺少该能力时保守停止。

        普通 hashs 查询把某些原始错误状态归一为 completed，不能单独作为执行证据。
        不读取私有客户端/凭据，也不自行转换下载器原始路径。
        """
        return XiaojiejieScraping._safe_completed_row(
            XiaojiejieScraping._raw_completed_row(downloader, download_hash), download_hash, tag,
        )

    def _completed_candidate_allowed(self, downloader: str, download_hash: str,
                                     tag: str | None, started_at: int | None,
                                     *, require_new: bool, category: str | None = None) -> bool:
        """Use one fresh raw row for status and optional new-only time proof."""
        row = self._raw_completed_row(downloader, download_hash)
        if not self._safe_completed_row(row, download_hash, tag, category=category):
            return False
        return (not require_new or (started_at is not None
                                    and is_valid_completion_time(row.get("completion_on"), started_at)))

    def _manifest_files(self, torrent, candidates):
        try:
            manifest = self.chain.torrent_files(tid=torrent.hash, downloader=torrent.downloader)
            return owned_torrent_files(torrent, candidates, manifest)
        except Exception:
            return None

    @staticmethod
    def _task_scope_roots(torrent) -> tuple[Path, ...]:
        """Bound tag-only reads to the SDK-mapped task path, never raw save_path."""
        value = getattr(torrent, "content_path", None) or getattr(torrent, "path", None)
        if not isinstance(value, (str, Path)):
            return ()
        path = Path(value)
        if not path.is_absolute() or path == Path("/") or ".." in path.parts or path.is_symlink():
            return ()
        try:
            actual = path.resolve(strict=True)
            if actual != path:
                return ()
            root = path.parent if path.is_file() else path if path.is_dir() else None
            return (root,) if root and root != Path("/") else ()
        except (OSError, RuntimeError, ValueError):
            return ()

    def _native_monitor_paths(self) -> tuple[Path, ...] | None:
        """None means an unreadable rule set; an absent/empty setting means no rules."""
        try:
            directories = self.systemconfig.get(SystemConfigKey.Directories)
            if directories is None:
                return ()
            if isinstance(directories, dict):
                directories = list(directories.values())
            if not isinstance(directories, list):
                return None
            paths = []
            for item in directories:
                read = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
                raw_type = read("monitor_type", "")
                monitor_type = str(getattr(raw_type, "value", raw_type) or "").lower()
                raw_storage = read("storage", "")
                storage = str(getattr(raw_storage, "value", raw_storage) or "").lower()
                raw_path = read("download_path")
                if storage != "local" or monitor_type not in ("downloader", "monitor"):
                    continue
                if not isinstance(raw_path, str):
                    return None
                path = Path(raw_path)
                if not path.is_absolute() or ".." in path.parts:
                    return None
                paths.append(path.resolve(strict=False))
            return tuple(paths)
        except Exception:
            return None

    def _candidate_monitor_overlap(self, files: tuple[Path, ...]) -> bool | None:
        monitors = self._native_monitor_paths()
        if monitors is None:
            return None
        for file in files:
            try:
                actual = file.resolve(strict=True)
                if any(actual.is_relative_to(root) for root in monitors):
                    return True
            except (OSError, RuntimeError, ValueError):
                return None
        return False

    def _candidate_files(self, torrent, mode: str, tag: str, roots: tuple[Path, ...],
                         category: str | None = None):
        """Return mapped candidates, task scope, and whether a public manifest is required."""
        if mode == "category":
            category_match = bool(category and getattr(torrent, "category", None) == category)
            task_roots = self._task_scope_roots(torrent) if category_match else ()
            category_files = (torrent_files(torrent, downloader=self._downloader, tag=None, roots=task_roots)
                              if task_roots else ())
            return ((category_files, task_roots, True, None)
                    if category_files else ((), (), True, None))
        tag_match = bool(tag and tag in exact_tags(getattr(torrent, "tags", None)))
        path_files = (torrent_files(torrent, downloader=self._downloader, tag=None, roots=roots)
                      if roots else ())
        task_roots = self._task_scope_roots(torrent) if tag_match else ()
        tag_files = (torrent_files(torrent, downloader=self._downloader, tag=tag, roots=task_roots)
                     if task_roots else ())
        if mode == "tag":
            return (tag_files, task_roots, True, tag) if tag_files else ((), (), True, tag)
        if mode == "path":
            return (path_files, roots, False, None) if path_files else ((), (), False, None)
        if mode == "tag_and_path":
            if tag_match and path_files:
                return path_files, roots, True, tag
            return (), (), True, tag
        if mode == "tag_or_path":
            if tag_files:
                return tag_files, task_roots, True, tag
            return (path_files, roots, False, None) if path_files else ((), (), False, None)
        return (), (), False, None

    def _torrent_scope_choice(self, torrent, mode: str, tag: str, roots: tuple[Path, ...],
                              category: str | None = None):
        """Choose the matching branch without walking files before the bounded journal batch."""
        if mode == "category":
            if not category or getattr(torrent, "category", None) != category:
                return None
            task_roots = self._task_scope_roots(torrent)
            return (task_roots, True, None) if task_roots else None
        tag_match = bool(tag and tag in exact_tags(getattr(torrent, "tags", None)))
        task_roots = self._task_scope_roots(torrent) if tag_match else ()
        mapped = getattr(torrent, "content_path", None) or getattr(torrent, "path", None)
        path_match = False
        if roots and isinstance(mapped, (str, Path)):
            path = Path(mapped)
            try:
                actual = path.resolve(strict=True)
                path_match = path.is_absolute() and ".." not in path.parts and not path.is_symlink() and actual == path
                if path_match:
                    path_match = False
                    for root in roots:
                        try:
                            if (root.is_absolute() and root != Path("/") and ".." not in root.parts
                                    and not root.is_symlink() and path.is_relative_to(root)
                                    and actual.is_relative_to(root.resolve(strict=True))):
                                path_match = True
                                break
                        except (OSError, RuntimeError, ValueError):
                            continue
            except (OSError, RuntimeError, ValueError):
                path_match = False
        if mode == "tag" and tag_match and task_roots:
            return task_roots, True, tag
        if mode == "path" and path_match:
            return roots, False, None
        if mode == "tag_and_path" and tag_match and task_roots and path_match:
            return roots, True, tag
        if mode == "tag_or_path":
            if tag_match and task_roots:
                return task_roots, True, tag
            if path_match:
                return roots, False, None
        return None

    def poll_completed(self) -> list[dict]:
        """复用下载器连接；先发现入队，再按显式模式预览或交给宿主整理。"""
        if not self.get_service() or not self._poll_lock.acquire(blocking=False):
            return []
        try:
            with self._lock:
                self._last_poll_at = time.time()
                self._auto_counts = {"checked": 0, "new_only_rejected": 0, "discovered": 0, "enqueued": 0}
                generation, journal = self._generation, self._journal
                downloader, tag, roots = self._downloader, self._tag, self._dirs
                category = self._category if self._match_mode == "category" else None
                match_mode = self._match_mode
                handoff, handoff_limit = self._handoff, self._handoff_limit
                new_only = self._auto_new_only
                guard = self._auto_guard
                started_at = guard.started_at if guard else None
            if not self._is_qbittorrent(downloader):
                return []
            torrents = self.chain.list_torrents(status=TorrentStatus.TRANSFER, downloader=downloader, include_all_tags=True) or []
            results = []
            eligible = []
            candidates_by_key = {}
            for torrent in torrents:
                if getattr(torrent, "downloader", None) != downloader:
                    continue
                choice = self._torrent_scope_choice(torrent, match_mode, tag, roots, category)
                if choice is None:
                    continue
                candidates_by_key[(torrent.downloader, torrent.hash)] = choice
                eligible.append(torrent)
            with self._lock:
                if not self._enabled or self._generation != generation or not journal:
                    return []
                batch = journal.next_batch(eligible)
            considered = 0
            for torrent in batch:
                choice = candidates_by_key.get((torrent.downloader, torrent.hash))
                if choice is None:
                    journal.advance(torrent)
                    continue
                scope_roots, used_tag, effective_tag = choice
                with self._lock:
                    self._auto_counts["checked"] += 1
                if not self._completed_candidate_allowed(downloader, torrent.hash, effective_tag,
                                                        started_at, require_new=new_only,
                                                        category=category):
                    with self._lock:
                        if not self._enabled or self._generation != generation or not journal:
                            return []
                        if new_only:
                            self._auto_counts["new_only_rejected"] += 1
                        journal.advance(torrent)
                    continue
                files = torrent_files(torrent, downloader=downloader, tag=effective_tag, roots=scope_roots)
                if not files:
                    journal.advance(torrent)
                    continue
                if (used_tag or match_mode == "category"
                        or (handoff and handoff.config.mode in ("preview", "execute"))):
                    owned = self._manifest_files(torrent, files)
                    if owned is None:
                        with self._lock:
                            if not self._enabled or self._generation != generation:
                                return []
                            self._handoff_error = "种子文件清单缺失/不完整或路径映射不能唯一对应，已阻止该批次自动交接"
                            journal.advance(torrent)
                        continue
                    files = owned
                overlap = self._candidate_monitor_overlap(files)
                if overlap is not False:
                    with self._lock:
                        if not self._enabled or self._generation != generation:
                            return []
                        self._handoff_error = ("原生监控目录与候选来源存在重叠；已阻止该批次"
                                               if overlap is True else "原生监控目录规则不可读；已阻止该批次")
                        journal.advance(torrent)
                    continue
                # 不拆开种子入队：首文件结算可能让宿主为整条种子加已整理标签。
                if considered + len(files) > 500:
                    break
                with self._lock:
                    if not self._enabled or self._generation != generation or not journal:
                        return []
                    results.extend(journal.discover(torrent, files[:max(0, 500 - len(results))]))
                    self._auto_counts["discovered"] += len(files)
                    if handoff and handoff.config.mode in ("preview", "execute"):
                        if not self._completed_candidate_allowed(
                                downloader, torrent.hash, effective_tag, started_at, require_new=new_only,
                                category=category):
                            if new_only:
                                self._auto_counts["new_only_rejected"] += 1
                            journal.advance(torrent)
                            continue
                        try:
                            handoff.enqueue_batch(torrent, files, scope_roots=scope_roots,
                                                  match_mode=match_mode)
                            self._auto_counts["enqueued"] += len(files)
                        except LedgerFullError:
                            self._handoff_error = "交接账本容量已满，拒绝新批次；既有队列和只读核实继续，不删除历史"
                            break
                        except (HandoffLedgerError, OSError):
                            self._handoff_error = "自动交接入队失败，保留账本且未重置；请检查插件状态"
                            return results
                    considered += len(files)
                    journal.advance(torrent)
                    if len(results) >= 500 or considered >= 500:
                        break
            def can_continue():
                return self._enabled and self._generation == generation

            def current_candidate(payload):
                # 每次执行边界都重新按 hash 读取，既看见标签/映射变化，也能继续
                # 本插件既有队列；宿主给整条种子加“已整理”不等于本插件全部文件完成。
                if not can_continue():
                    return False
                try:
                    current = self.chain.list_torrents(hashs=[payload["download_hash"]], downloader=downloader,
                                                       include_all_tags=True) or []
                except Exception:
                    return False
                matches = [torrent for torrent in current if torrent.hash == payload["download_hash"]
                           and torrent.downloader == payload["downloader"]]
                if len(matches) != 1 or not can_continue():
                    return False
                if payload.get("match_mode") not in (None, match_mode):
                    return False
                candidate_info = self._candidate_files(matches[0], match_mode, tag, roots, category)
                candidates, candidate_roots, used_tag, effective_tag = candidate_info
                if (not candidates or not self._completed_candidate_allowed(
                        downloader, payload["download_hash"], effective_tag, started_at,
                        require_new=new_only, category=category)
                        or not can_continue()):
                    return False
                if (used_tag or match_mode == "category"
                        or (handoff and handoff.config.mode in ("preview", "execute"))):
                    candidates = self._manifest_files(matches[0], candidates)
                if candidates is None:
                    return False
                overlap = self._candidate_monitor_overlap(candidates)
                return (can_continue() and overlap is False
                        and Path(payload["path"]) in candidates)

            if handoff and can_continue() and handoff.config.mode in ("preview", "execute"):
                try:
                    handoff.reconcile()
                    if can_continue():
                        handoff.run_pending(current_candidate, can_continue, limit=handoff_limit)
                except HandoffLedgerError:
                    self._handoff_error = "自动交接账本写入/核实失败，已停止；未知结果不会重复提交"
            return results
        finally:
            self._poll_lock.release()

    def _adapters(self) -> dict:
        return {SOURCE: self._provider, **self._providers} if self._provider else dict(self._providers)

    def get_module(self) -> dict:
        """仅接管明确选择本来源的元数据查询，不做无来源的全局番号捕获。"""
        if not self._enabled:
            return {}
        return {name: getattr(self, name) for name in (
            "recognize_media", "async_recognize_media", "search_medias", "async_search_medias",
            "metadata_nfo", "metadata_img", "obtain_images", "async_obtain_images",
        )}

    def get_media_source(self) -> list[dict]:
        """供宿主原生手动整理/刮削选择，不代表已开启自动任务触发。"""
        statuses = {status.key: status for status in PROVIDERS}
        return [{"name": statuses[source].label + ("（有限商品页实测）" if statuses[source].state == "limited_live" else "（离线适配）"),
                 "media_source": source, "media_types": [MediaType.MOVIE.value]}
                for source in self._active_sources] if self._enabled else []

    def recognize_media(self, meta=None, mtype=None, media_source=None, media_id=None, episode_group=None, cache=True):
        """显式来源查询用于手动操作及已有身份回查；不推断调用方是否手动。"""
        request = self._request(None, meta, mtype, media_source, media_id, explicit_source=True)
        return self._recognize_request(request, cache)

    async def async_recognize_media(self, meta=None, mtype=None, media_source=None, media_id=None, episode_group=None, cache=True):
        """异步显式来源查询；没有选择来源时总是退出。"""
        request = self._request(None, meta, mtype, media_source, media_id, explicit_source=True)
        return await self._async_recognize_request(request, cache)

    def search_medias(self, meta, media_source=None) -> list[MediaInfo]:
        """原生手动搜索可展示 Manko 的全部精确原生身份，不给宽泛标题猜测结果。"""
        sources = media_source if isinstance(media_source, (tuple, list)) else (media_source,)
        results = []
        for source in dict.fromkeys(str(value) for value in sources):
            if source == SOURCE:
                request = self._request(None, meta, None, source, None, explicit_source=True)
                results.extend(self._manual_manko_results(request))
            else:
                result = self.recognize_media(meta=meta, media_source=source)
                if result:
                    results.append(result)
        return results

    async def async_search_medias(self, meta, media_source=None) -> list[MediaInfo]:
        """异步原生手动搜索，与同步入口保持来源及候选语义一致。"""
        sources = media_source if isinstance(media_source, (tuple, list)) else (media_source,)
        results = []
        for source in dict.fromkeys(str(value) for value in sources):
            if source == SOURCE:
                request = self._request(None, meta, None, source, None, explicit_source=True)
                results.extend(await self._async_manual_manko_results(request))
            else:
                result = await self.async_recognize_media(meta=meta, media_source=source)
                if result:
                    results.append(result)
        return results

    def _manual_manko_results(self, request) -> list[MediaInfo]:
        if request is None:
            return []
        generation, provider, cache, _, _, number, source = request
        try:
            diagnose(source, "search", "cache_miss")
            packets = provider.search_matches(number)
            results = []
            for packet in packets:
                identity = str(packet["record"]["_id"])
                candidate = (generation, provider, cache, self._id_cache_key(source, identity), identity, number, source)
                result = self._accept(candidate, self._with_actor_images(packet), persist=False)
                if result is None:
                    return []
                results.append(result)
            return results
        except Exception:
            diagnose(source, "search", "query_exception")
            return []

    async def _async_manual_manko_results(self, request) -> list[MediaInfo]:
        if request is None:
            return []
        generation, provider, cache, _, _, number, source = request
        try:
            diagnose(source, "search", "cache_miss")
            packets = await provider.async_search_matches(number)
            results = []
            for packet in packets:
                identity = str(packet["record"]["_id"])
                candidate = (generation, provider, cache, self._id_cache_key(source, identity), identity, number, source)
                enriched = await self._async_with_actor_images(packet)
                result = await asyncio.to_thread(self._accept, candidate, enriched, persist=False)
                if result is None:
                    return []
                results.append(result)
            return results
        except Exception:
            diagnose(source, "search", "query_exception")
            return []

    def _request(self, context, meta, mtype, media_source, media_id, explicit_source=False):
        with self._lock:
            if not self._enabled or not self._cache:
                diagnose(media_source, "request", "disabled" if not self._enabled else "cache_unavailable")
                return None
            source = str(media_source) if media_source is not None else None
            if explicit_source:
                if source not in self._active_sources:
                    diagnose(source, "request", "source_unselected")
                    return None
            elif not matches_scope(context, self._storage, self._dirs):
                diagnose(source, "request", "scope_rejected")
                return None
            source = source or (self._active_sources[0] if self._active_sources else None)
            if source not in self._active_sources or mtype not in (None, MediaType.MOVIE, MediaType.UNKNOWN):
                diagnose(source, "request", "source_unselected" if source not in self._active_sources else "media_type_rejected")
                return None
            if getattr(meta, "type", None) in (MediaType.TV, MediaType.MUSIC):
                diagnose(source, "request", "media_type_rejected")
                return None
            provider = self._adapters().get(source)
            if provider is None:
                diagnose(source, "request", "provider_unavailable")
                return None
            if media_id is not None:
                identity = str(media_id)
                if not media_source or not IDENTIFIER.fullmatch(identity):
                    diagnose(source, "fetch", "invalid_id")
                    return None
                return self._generation, provider, self._cache, self._id_cache_key(source, identity), identity, None, source
            text = str(getattr(meta, "org_string", None) or getattr(meta, "name", None) or "")
            number = extract_number(text)
            if not number:
                diagnose(source, "search", "number_not_unique")
                return None
            cache_key = (source + ":number:complete-first-v1:" + number
                         if source == SOURCE else self._number_cache_key(source, number))
            return self._generation, provider, self._cache, cache_key, None, number, source

    def _accept(self, request, packet: dict | None, persist: bool = True) -> MediaInfo | None:
        if not packet:
            diagnose(request[-1], "accept", "empty_result")
            return None
        generation, _, cache, key, identity, number, source = request
        try:
            record, actual_number = packet["record"], packet["number"]
            if record.get("_source", source) != source:
                diagnose(source, "accept", "source_mismatch")
                return None
            if not IDENTIFIER.fullmatch(str(record["_id"])) or record_numbers(record) != {actual_number}:
                diagnose(source, "accept", "number_evidence_invalid")
                return None
            if (identity and str(record["_id"]) != identity) or (number and actual_number != number):
                diagnose(source, "accept", "identity_mismatch")
                return None
            with self._lock:
                if not self._enabled or generation != self._generation:
                    diagnose(source, "accept", "configuration_changed")
                    return None
                projected = project_record(record, actual_number, self._current_actors(packet), self._image_hosts, source=source)
                actress_evidence = (project_actress_evidence(packet.get("_actress_evidence"), actual_number)
                                    if source == SOURCE else
                                    project_javbus_actress_evidence(
                                        packet.get("_actress_evidence"), actual_number,
                                        str(record["_id"]), packet.get("actors", []))
                                    if source == "javbus" else None)
                cached = {"packet": packet, "projected": projected,
                          "actress_evidence": actress_evidence}
                if persist:
                    cache.put(key, cached)
                    cache.put(self._id_cache_key(source, str(record["_id"])), cached)
                display_projected = (self._artwork_relay.project_media_info(
                    projected, generation, self._image_hosts) if source == "javbus" else projected)
            result = MediaInfo()
            result.from_dict(display_projected)
            if source == SOURCE:
                flagged_names = []
                for actor in packet.get("actors", []) if isinstance(packet.get("actors"), list) else []:
                    if not isinstance(actor, dict) or actor.get("avdbs_actor") is not True:
                        continue
                    name = actor.get("name")
                    safe_name = safe_component(name) if isinstance(name, str) else None
                    if safe_name == name and safe_name not in flagged_names:
                        flagged_names.append(safe_name)
                evidence = {
                    "source": source, "media_id": str(record["_id"]),
                    "number": actual_number, "names": flagged_names,
                }
                self._remember_actress_evidence(result, evidence, generation)
            elif source == "javbus" and isinstance(cached["actress_evidence"], dict):
                self._remember_actress_evidence(result, cached["actress_evidence"], generation)
            diagnose(source, "accept", "accepted")
            return result
        except (OSError, TypeError, ValueError, KeyError, AttributeError):
            diagnose(source, "accept", "result_invalid")
            return None

    def actress_evidence(self, media, number: str | None = None) -> tuple[list[str] | None, list[str]]:
        """Return this MediaInfo's current source-owned actress candidates."""
        from .layout import actor_names
        source = str(getattr(getattr(media, "media_source", None), "value", getattr(media, "media_source", "")))
        identity = str(getattr(media, "media_id", "") or "")
        if source == "javbus":
            with self._lock:
                entry = self._actress_evidence.get(id(media))
                evidence = (entry[2] if entry is not None and entry[0]() is media
                            and entry[1] == self._generation else None)
            try:
                expected = normalize_number(number) if number is not None else None
                if (not IDENTIFIER.fullmatch(identity) or not isinstance(evidence, dict)
                        or evidence.get("source") != source or evidence.get("media_id") != identity
                        or not isinstance(evidence.get("number"), str)
                        or normalize_number(evidence["number"]) != evidence["number"]
                        or (number is not None and (expected is None or evidence["number"] != expected))
                        or not isinstance(evidence.get("names"), list)):
                    return [], []
                names = [value for value in evidence["names"] if isinstance(value, str)
                         and safe_component(value) == value]
                actors = actor_names(media)
                allowed = [name for name in names if name in actors]
                return list(dict.fromkeys(names)), list(dict.fromkeys(allowed))
            except Exception:
                return [], []
        if source != SOURCE:
            return None, actor_names(media)
        with self._lock:
            entry = self._actress_evidence.get(id(media))
            evidence = (entry[2] if entry is not None and entry[0]() is media
                        and entry[1] == self._generation else None)
        try:
            expected = normalize_number(number) if number is not None else None
            if (not IDENTIFIER.fullmatch(identity) or not isinstance(evidence, dict)
                    or evidence.get("source") != source or evidence.get("media_id") != identity
                    or not isinstance(evidence.get("number"), str)
                    or (number is not None and (expected is None or evidence.get("number") != expected))
                    or not isinstance(evidence.get("names"), list)):
                return [], []
            names = [value for value in evidence["names"] if isinstance(value, str)
                     and safe_component(value) == value]
            actors = actor_names(media)
            allowed = [name for name in names if name in actors]
            return list(dict.fromkeys(names)), allowed
        except Exception:
            return [], []

    def _remember_actress_evidence(self, media, evidence: dict, generation: int) -> None:
        """Keep source flags outside MediaInfo serialization and bound to object identity."""
        key = id(media)
        plugin_ref = weakref.ref(self)

        def discard(reference):
            plugin = plugin_ref()
            if plugin is None:
                return
            with plugin._lock:
                current = plugin._actress_evidence.get(key)
                if current is not None and current[0] is reference:
                    plugin._actress_evidence.pop(key, None)

        try:
            reference = weakref.ref(media, discard)
        except TypeError:
            return
        with self._lock:
            if self._enabled and self._generation == generation:
                self._actress_evidence[key] = (reference, generation, evidence)

    def recognize_scoped(self, context: RecognitionContext, meta=None, mtype=None, media_source=None, media_id=None, episode_group=None, cache=True) -> MediaInfo | None:
        """供插件筛选 qBittorrent 文件后调用；自动流程的 ID 回查也须在范围内。"""
        if media_id is not None and media_source is None:
            return None
        for source in (media_source,) if media_source else self._active_sources:
            request = self._request(context, meta, mtype, source, media_id)
            result = self._recognize_request(request, cache)
            if result:
                return result
        return None

    def _recognize_request(self, request, cache=True):
        if request is None:
            return None
        _, provider, store, key, identity, number, source = request
        try:
            cached = store.get(key) if cache else None
            diagnose(source, "fetch" if identity else "search", "cache_hit" if cached else "cache_miss")
            packet = cached.get("packet") if cached else provider.fetch(identity) if identity else provider.search(number)
            if packet and not cached:
                packet = self._with_actor_images(packet)
            return self._accept(request, packet, persist=not bool(cached))
        except Exception:
            diagnose(source, "fetch" if identity else "search", "query_exception")
            return None

    async def async_recognize_scoped(self, context: RecognitionContext, meta=None, mtype=None, media_source=None, media_id=None, episode_group=None, cache=True) -> MediaInfo | None:
        """HTTP 使用异步 SDK；缓存文件读写移至线程，不阻塞事件循环。"""
        if media_id is not None and media_source is None:
            return None
        for source in (media_source,) if media_source else self._active_sources:
            request = self._request(context, meta, mtype, source, media_id)
            result = await self._async_recognize_request(request, cache)
            if result:
                return result
        return None

    async def _async_recognize_request(self, request, cache=True):
        if request is None:
            return None
        _, provider, store, key, identity, number, source = request
        try:
            cached = await asyncio.to_thread(store.get, key) if cache else None
            diagnose(source, "fetch" if identity else "search", "cache_hit" if cached else "cache_miss")
            packet = cached.get("packet") if cached else await provider.async_fetch(identity) if identity else await provider.async_search(number)
            if packet and not cached:
                packet = await self._async_with_actor_images(packet)
            return await asyncio.to_thread(self._accept, request, packet, persist=not bool(cached))
        except Exception:
            diagnose(source, "fetch" if identity else "search", "query_exception")
            return None

    def _actor_options(self):
        with self._lock:
            if not self._enabled or not self._actor_images or "raw.githubusercontent.com" not in self._image_hosts:
                return None
            return self._actor_index, self._generation

    @staticmethod
    def _apply_actor_index(packet, manifest):
        result = copy.deepcopy(packet)
        if isinstance(manifest, dict):
            for actor in result.get("actors", []):
                if isinstance(actor, dict) and (url := actor_image(manifest, actor.get("name"))):
                    actor["profile_path"] = url
                    actor["_image_source"] = "gfriends"
        return result

    def _store_actor_index(self, index, generation, manifest):
        with self._lock:
            if self._enabled and generation == self._generation and index is self._actor_index:
                index.put("gfriends", manifest)

    def _with_actor_images(self, packet):
        options = self._actor_options()
        if options is None or not packet.get("actors"):
            return packet
        index, generation = options
        try:
            manifest = index.get("gfriends")
            if manifest is None:
                manifest = self._gfriends.fetch_index()
                if manifest:
                    self._store_actor_index(index, generation, manifest)
            return self._apply_actor_index(packet, manifest)
        except Exception:
            return packet

    async def _async_with_actor_images(self, packet):
        options = self._actor_options()
        if options is None or not packet.get("actors"):
            return packet
        index, generation = options
        try:
            manifest = await asyncio.to_thread(index.get, "gfriends")
            if manifest is None:
                manifest = await self._gfriends.async_fetch_index()
                if manifest:
                    await asyncio.to_thread(self._store_actor_index, index, generation, manifest)
            return self._apply_actor_index(packet, manifest)
        except Exception:
            return packet

    def _current_actors(self, packet):
        actors = copy.deepcopy(packet.get("actors", []))
        if not self._actor_images:
            for actor in actors:
                if isinstance(actor, dict) and actor.get("_image_source") == "gfriends":
                    actor.pop("profile_path", None)
        return actors

    def _fc2_identity_matches(self, mediainfo, number: str | None = None) -> bool:
        """Accept only this plugin's verified same-source packet and canonical FC2 number."""
        try:
            source = str(getattr(mediainfo, "media_source", ""))
            identity = str(getattr(mediainfo, "media_id", ""))
            if source not in {"fc2", "fc2hub", "fc2ppvdb"}:
                return False
            owned = self._owned(mediainfo)
            if not owned:
                return False
            packet = owned["packet"]
            record = packet["record"]
            canonical = packet.get("number")
            if (not isinstance(canonical, str) or normalize_number(canonical) != canonical
                    or not canonical.startswith("FC2-PPV-")
                    or (number is not None and number != canonical)):
                return False
            return (packet.get("source", source) == source
                    and record.get("_source", source) == source
                    and str(record.get("_id")) == identity
                    and record_numbers(record) == {canonical}
                    and canonical in set(getattr(mediainfo, "names", []) or []))
        except (AttributeError, KeyError, TypeError, ValueError):
            return False

    def _owned(self, mediainfo):
        source = str(getattr(mediainfo, "media_source", None))
        with self._lock:
            if not self._enabled or source not in self._active_sources or not self._cache:
                return None
            data = self._cache.get(self._id_cache_key(source, str(mediainfo.media_id)))
            if not data:
                return None
            try:
                packet = data["packet"]
                record = packet["record"]
                if str(record["_id"]) != str(mediainfo.media_id) or record.get("_source", source) != source:
                    return None
                if record_numbers(record) != {packet["number"]}:
                    return None
                # 图片主机撤销必须立即生效，不能复用旧投影中的演员 URL。
                data["projected"] = project_record(record, packet["number"], self._current_actors(packet), self._image_hosts, source=source)
                return data
            except (TypeError, KeyError, AttributeError, ValueError):
                return None

    def metadata_nfo(self, meta=None, mediainfo=None, season=None, episode=None, **kwargs) -> str | None:
        """生成内容交由宿主保存；不预删 NFO，不接管文件 I/O。"""
        data = self._owned(mediainfo)
        if not data or season is not None or episode is not None:
            return None
        return render_nfo(data["projected"], data["packet"]["number"])

    def metadata_img(self, mediainfo, season=None, episode=None) -> dict[str, str] | None:
        """只返回已批准主机的固定图片文件名；由宿主负责下载。"""
        data = self._owned(mediainfo)
        if not data or season is not None or episode is not None:
            return None
        return self._selected_artwork(data) or None

    def _selected_artwork(self, data):
        """Use one bounded probe result for both host artwork callbacks."""
        record = data["packet"]["record"]
        with self._lock:
            generation = self._generation
            hosts = self._image_hosts
        images = image_map(record, hosts)
        images.pop("fanart.jpg", None)
        candidates = backdrop_candidates(record, hosts)
        cover = images.get("poster.jpg", "")
        key = (generation, tuple(hosts), str(record.get("_source") or SOURCE),
               str(record.get("_id") or ""), cover, candidates)
        with self._artwork_lock:
            if key in self._artwork_cache:
                selected = self._artwork_cache.pop(key)
                self._artwork_cache[key] = selected
                cache_hit = True
            else:
                selected = None
                cache_hit = False
        if not cache_hit:
            for candidate in candidates:
                try:
                    if is_usable_landscape(candidate):
                        selected = candidate
                        break
                except Exception:
                    continue
            with self._lock:
                current = (self._enabled and generation == self._generation
                           and hosts == self._image_hosts)
            if current:
                with self._artwork_lock:
                    self._artwork_cache[key] = selected
                    self._artwork_cache.move_to_end(key)
                    while len(self._artwork_cache) > 128:
                        self._artwork_cache.popitem(last=False)
        with self._lock:
            if (not self._enabled or generation != self._generation
                    or hosts != self._image_hosts):
                return {}
            if selected:
                images["fanart.jpg"] = selected
            return images

    def obtain_images(self, mediainfo) -> MediaInfo | None:
        """只补自己的封面字段，不改写其他来源对象。"""
        data = self._owned(mediainfo)
        if not data:
            return None
        images = self._selected_artwork(data)
        fields = {"poster_path": images.get("poster.jpg"),
                  "backdrop_path": images.get("fanart.jpg")}
        if str(getattr(mediainfo, "media_source", None)) == "javbus":
            with self._lock:
                generation, hosts = self._generation, self._image_hosts
            fields = self._artwork_relay.project_media_info(fields, generation, hosts)
        mediainfo.poster_path = fields["poster_path"]
        mediainfo.backdrop_path = fields["backdrop_path"]
        return mediainfo

    async def async_obtain_images(self, mediainfo) -> MediaInfo | None:
        """异步图片入口不进行同步磁盘操作。"""
        return await asyncio.to_thread(self.obtain_images, mediainfo)

    def automatic_status(self) -> dict:
        """Return safe runtime status without querying the downloader or writing state."""
        guard = self._auto_guard
        mode = self._handoff.config.mode if self._handoff else "discover"
        error = self._auto_error or self._handoff_error or None
        counts = dict(self._auto_counts)
        pending_actress_choices = []
        if self._handoff:
            try:
                pending_actress_choices = [
                    {"path": str(record["payload"].get("path", "")), "reason": "primary_actress_choice_required"}
                    for record in self._handoff.ledger.list_records(("failed",), limit=10000)
                    if record["payload"].get("reason") == "primary_actress_choice_required"
                ][:20]
                counts["primary_actress_choice_required"] = len(pending_actress_choices)
            except HandoffLedgerError:
                counts["primary_actress_choice_required"] = -1
        return {
            "enabled": bool(self._enabled and self._discovery and self._storage == "local"),
            "mode": mode,
            "match_mode": self._match_mode,
            "new_only": self._auto_new_only,
            "armed": bool(guard and guard.armed and self._enabled and self._discovery and self._storage == "local"),
            "started_at": guard.started_at if guard and self._enabled and self._discovery and self._storage == "local" else None,
            "interval_seconds": 300,
            "downloader": self._downloader,
            "torrent_tag": self._tag,
            "torrent_category": self._category,
            "roots": [str(root) for root in self._dirs],
            "target_path": str(self._handoff.config.target_path) if self._handoff else "",
            "last_poll_at": self._last_poll_at,
            "counts": counts,
            "pending_actress_choices": pending_actress_choices,
            "error": error,
        }

    def automatic_check(self) -> dict:
        """Read public downloader and system-directory configuration only; never enqueue or transfer."""
        errors = []
        downloader_type, completion_capability = "unknown", False
        completion_rows_checked = completion_time_rows = 0
        try:
            config = DownloaderHelper().get_config(self._downloader)
            downloader_type = str(getattr(config, "type", "unknown") or "unknown") if config else "unknown"
            service = DownloaderHelper().get_service(name=self._downloader, type_filter="qbittorrent")
            getter = getattr(getattr(service, "instance", None), "get_completed_torrents", None)
            if service and getattr(service, "type", None) == "qbittorrent" and callable(getter):
                completion_capability = True
                rows = getter(ids=None, tags=None)  # pylint: disable=not-callable
                if isinstance(rows, list):
                    sample = rows[:100]
                    completion_rows_checked = len(sample)
                    server_now = time.time()
                    completion_time_rows = sum(
                        1 for row in sample if isinstance(row, dict)
                        and type(row.get("completion_on")) in (int, float)
                        and math.isfinite(row["completion_on"])
                        and row["completion_on"] > 0 and row["completion_on"] <= server_now
                    )
                else:
                    completion_capability = False
                    errors.append("完成任务返回格式不可用")
            else:
                errors.append("完成任务读取能力不可用")
        except Exception:
            errors.append("下载器读取失败")

        monitors = self._native_monitor_paths()
        directories_available = monitors is not None
        native_monitor_count = len(monitors) if monitors is not None else 0
        roots = tuple(Path(root) for root in self._dirs
                      if self._match_mode not in ("tag", "category")
                      and Path(root).is_absolute() and ".." not in Path(root).parts)
        native_monitor_overlap: bool | None = None
        if monitors is None:
            errors.append("原生监控目录规则不可读")
        elif not roots:
            # Tag/category-selected roots come from each mapped torrent path; a setup check cannot infer them.
            native_monitor_overlap = False if not monitors else None
        else:
            native_monitor_overlap = any(
                path == root or path.is_relative_to(root) or root.is_relative_to(path)
                for path in monitors for root in roots
            )
        if native_monitor_overlap is True:
            errors.append("原生监控目录与插件来源目录重叠")
        elif native_monitor_overlap is None:
            errors.append("自动来源路径由任务映射决定，无法静态核实原生监控重叠")
        if completion_rows_checked == 0 or completion_time_rows == 0:
            errors.append("未观察到带有效完成时间的 qBittorrent 行")
        uses_tag = self._match_mode in ("tag", "tag_or_path", "tag_and_path")
        uses_path = self._match_mode in ("path", "tag_or_path", "tag_and_path")
        scope_ready = (bool(self._category) if self._match_mode == "category" else
                       ((bool(self._tag) or bool(roots)) if self._match_mode == "tag_or_path"
                        else (not uses_tag or bool(self._tag)) and (not uses_path or bool(roots))))
        config_ready = self._match_mode in MATCH_MODES and scope_ready and self._storage == "local"
        if not config_ready:
            errors.append("匹配模式所需的分类、标签、路径或本地存储配置不完整")
        ready = bool(config_ready and downloader_type == "qbittorrent" and completion_capability
                     and completion_rows_checked > 0 and completion_time_rows > 0
                     and directories_available and native_monitor_overlap is False)
        message = ("条件检查通过；新任务边界仍由配置和启动时间控制" if ready
                   else (errors[0] if errors else "条件尚未满足"))
        return {
            "ready": ready,
            "message": message,
            "match_mode": self._match_mode,
            "downloader_type": downloader_type,
            "completion_capability": completion_capability,
            "completion_rows_checked": completion_rows_checked,
            "completion_time_rows": completion_time_rows,
            "native_monitor_overlap": native_monitor_overlap,
            "native_monitor_count": native_monitor_count,
            "directories_available": directories_available,
            "errors": errors,
        }

    def get_api(self) -> list:
        """Register enabled plugin management APIs even when scheduled discovery is paused."""
        if not self._enabled:
            return []
        routes = (manual_api_routes(self._manual) if self._manual else []) + automatic_api_routes(self)
        if "javbus" in self._active_sources:
            routes += artwork_api_routes(self._artwork_relay)
        return routes

    @staticmethod
    def get_render_mode() -> tuple[str, str]:
        """启用官方 Vue 联邦页面。"""
        return "vue", "dist/assets"

    def get_sidebar_nav(self) -> list[dict]:
        """仅向管理员显示插件自有的手动整理页面入口。"""
        if not self._enabled:
            return []
        return [{
            "nav_key": "manual",
            "title": "Manko 整理",
            "icon": "mdi-folder-move",
            "section": "organize",
            "permission": "manage",
            "order": 50,
        }]

    def get_page(self):
        """展示持久交接状态；受理、预览和未知结果不会冒充完成。"""
        labels = {"claimed": "已排队", "preparing": "准备中（重启后需复核）", "submitting": "提交中",
                  "accepted": "宿主已受理，待证据", "previewed": "仅预览", "completed": "文件整理已核实（刮削独立）",
                  "failed": "失败，未自动重提", "uncertain": "结果不确定，禁止重提"}
        rows = []
        if self._handoff_error:
            rows.append({"component": "VAlert", "props": {"type": "error", "text": self._handoff_error}})
        if self._handoff:
            if self._handoff.config.mode in ("preview", "execute") and self._handoff.config.target() is None:
                rows.append({"component": "VAlert", "props": {"type": "warning", "text": "自动交接被阻止：请填写既有目标目录、整理方式、输入目录和来源；执行模式还需两项确认"}})
            try:
                records = self._handoff.ledger.list_records(limit=10000)
                for record in sorted(records, key=lambda item: item["updated_at"], reverse=True)[:20]:
                    payload = record["payload"]
                    reason = payload.get("reason", "")
                    if reason == "primary_actress_choice_required":
                        text = f"待人工选择女主演（自动准备已阻止，不会重试）：{payload.get('path', '')}"
                    elif reason == "primary_actress_directory_invalid":
                        text = f"女主演目录不安全，自动准备已阻止：{payload.get('path', '')}"
                    else:
                        text = f"{labels.get(record['state'], record['state'])}：{payload.get('path', '')}；{reason}"
                    rows.append({"component": "VAlert", "props": {"type": "info", "text": text}})
            except HandoffLedgerError:
                rows.append({"component": "VAlert", "props": {"type": "error", "text": "账本无法安全读取，请勿删除后盲目重试"}})
        return rows or [{"component": "VAlert", "props": {"type": "info", "text": "尚无自动交接记录；默认不会整理文件"}}]

    def get_form(self) -> tuple[list, dict]:
        """配置页明确授权范围及尚未隔离的宿主补充路径。"""
        fields = [
            {"component": "VAlert", "props": {"type": "warning", "text": "实验版：手动整理/刮削可明确选择来源。自动模式默认只读发现；预览会查询来源及宿主缓存但不搬文件，执行须完整配置。来源与真实整理尚未实站验收。"}},
            {"component": "VSwitch", "props": {"model": "enabled", "label": "启用已完成的来源能力（JavBus API 局域网直连；其他来源沿用既有代理）"}},
            {"component": "VSelect", "props": {"model": "sources", "multiple": True, "chips": True, "label": "元数据来源（选择顺序即查询顺序；FC2官方仅有有限商品页实测）", "items": [{"title": status.label, "value": status.key} for status in PROVIDERS if status.purpose == "metadata" and status.state in ("offline_adapter", "limited_live")]}},
            {"component": "VTextField", "props": {"model": "javbus_api_url", "label": "JavBus API 基址（局域网直连；例 http://NAS地址:8922；留空则 JavBus 不可用）"}},
            {"component": "VSelect", "props": {"model": "storage", "label": "存储", "items": [{"title": "本地存储", "value": "local"}]}},
            {"component": "VTextarea", "props": {"model": "recognize_dirs", "label": "触发目录：每行一个绝对路径，含子目录；仅真实文件上下文生效"}},
            {"component": "VSelect", "props": {"model": "automatic_match_mode", "label": "自动匹配方式", "items": [
                {"title": "专用标签", "value": "tag"}, {"title": "配置路径", "value": "path"},
                {"title": "标签或路径", "value": "tag_or_path"}, {"title": "标签且路径（旧配置兼容）", "value": "tag_and_path"},
                {"title": "qBittorrent 分类（独立筛选）", "value": "category"},
            ]}},
            {"component": "VSwitch", "props": {"model": "discovery_enabled", "label": "每五分钟检查 qBittorrent 已完成任务（具体动作取决于下方模式）"}},
            {"component": "VSwitch", "props": {"model": "auto_new_only", "label": "只处理本次启用后新完成的任务；重载保留时间边界"}},
            {"component": "VTextField", "props": {"model": "downloader", "label": "MoviePilot 已配置的 qBittorrent 下载器名称"}},
            {"component": "VTextField", "props": {"model": "torrent_tag", "label": "专用标签（仅在所选匹配方式需要时使用，精确匹配）"}},
            {"component": "VTextField", "props": {"model": "torrent_category", "label": "qBittorrent 分类（仅分类模式需要，精确匹配）"}},
            {"component": "VSelect", "props": {"model": "handoff_mode", "label": "自动流程模式", "items": [{"title": "只读发现（默认）", "value": "discover"}, {"title": "预览：查询来源/缓存，不搬文件", "value": "preview"}, {"title": "执行：交 MoviePilot 整理并刮削", "value": "execute"}]}},
            {"component": "VTextField", "props": {"model": "target_path", "label": "明确目标：MoviePilot 容器内既有目录，不能与输入目录重叠"}},
            {"component": "VSelect", "props": {"model": "transfer_type", "label": "明确整理方式（保留 qBittorrent 源文件）", "items": [{"title": "复制", "value": "copy"}, {"title": "硬链接", "value": "link"}]}},
            {"component": "VSwitch", "props": {"model": "ownership_ack", "label": "我已将相同来源排除在 MoviePilot 原生自动监控之外，避免双重处理"}},
            {"component": "VSwitch", "props": {"model": "auxiliary_ack", "label": "我理解宿主仍可能补充 TMDB 辅助字段；已核对预览并接受该原生行为"}},
            {"component": "VTextField", "props": {"model": "handoff_limit", "type": "number", "label": "每轮最多处理的新文件（1–5，默认1）"}},
            {"component": "VSwitch", "props": {"model": "actor_images", "label": "GFriends 演员缩略图（还需批准 raw.githubusercontent.com 图片主机）"}},
            {"component": "VSwitch", "props": {"model": "actress_subfolder", "label": "按女主演分目录（默认来源首位；手动预览可改选）"}},
            {"component": "VTextarea", "props": {"model": "image_hosts", "label": "批准的图片 HTTPS 主机，每行一个；空值不提供图片地址"}},
            {"component": "VAlert", "props": {"type": "info", "text": "; ".join(status.label + ("：仅有限商品页实测，旧内容及部署环境未覆盖" if status.state == "limited_live" else "：离线适配，未实站验收" if status.state == "offline_adapter" else "：暂不可用，现行接口待核实" if status.state == "blocked" else "：待实现") for status in PROVIDERS)}},
        ]
        return fields, {"enabled": False, "sources": ["manko"], "javbus_api_url": "", "storage": "local", "recognize_dirs": "", "automatic_match_mode": "tag_or_path", "image_hosts": "", "discovery_enabled": False, "auto_new_only": True, "downloader": "", "torrent_tag": "", "torrent_category": "", "actor_images": False, "actress_subfolder": False, "handoff_mode": "discover", "target_path": "", "transfer_type": "", "ownership_ack": False, "auxiliary_ack": False, "handoff_limit": 1}
