"""Xiaojiejie-Scraping V3：直连多来源精确识别与原生刮削扩展。"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import asyncio
import copy
import math
from pathlib import Path
import threading
from app.sdk.plugin.base import _PluginBase
from app.sdk.media import MediaInfo
from app.sdk.logging import logger
from app.sdk.services import DownloaderHelper
from app.schemas.types import MediaSource, MediaType
from .core import SOURCE, IDENTIFIER, JsonCache, extract_number, image_map, project_record, record_numbers, render_nfo
from .provider import MankoProvider
from .direct_primary import FANZAProvider, MGStageProvider, DUGAProvider, SODProvider
from .direct_secondary import JavBusProvider, Jav321Provider, FC2HubProvider
from .fc2ppvdb import FC2PPVDBProvider
from .scope import RecognitionContext, matches_scope
from .registry import PROVIDERS
from .discovery import DiscoveryJournal, exact_tags, owned_torrent_files, torrent_files
from .gfriends import GFriendsProvider, actor_image
from .handoff import HandoffConfig, HandoffCoordinator
from .ledger import HandoffLedger, HandoffLedgerError, LedgerFullError
from app.schemas.types import TorrentStatus


class XiaojiejieScraping(_PluginBase):
    """默认关闭；显式来源查询与自动任务发现分离，原生整理仍有辅助补充风险。"""
    plugin_name = "Xiaojiejie-Scraping"
    plugin_desc = "直连多来源刮削、原生手动来源入口与 qBittorrent 只读发现（实验版）"
    plugin_icon = ""
    plugin_version = "2.0.0"
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
        self._provider = None
        self._providers = {}
        self._active_sources = (SOURCE,)
        self._cache = None
        self._discovery = False
        self._downloader = ""
        self._tag = ""
        self._journal = None
        self._actor_images = False
        self._actor_index = None
        self._gfriends = GFriendsProvider()
        self._poll_lock = threading.Lock()
        self._handoff = None
        self._handoff_error = ""
        self._handoff_limit = 1

    def init_plugin(self, config: dict | None = None) -> None:
        """每次重载替换实例状态，不联网、不扫描目录、不调度旧记录修复。"""
        config = config or {}
        with self._lock:
            self._generation += 1
            self._enabled = config.get("enabled") is True
            self._storage = str(config.get("storage") or "local")
            self._discovery = config.get("discovery_enabled") is True
            self._downloader = str(config.get("downloader") or "").strip()
            self._tag = str(config.get("torrent_tag") or "").strip()
            self._dirs = tuple(Path(v) for v in self._lines(config.get("recognize_dirs")) if Path(v).is_absolute() and Path(v) != Path("/") and ".." not in Path(v).parts)
            self._actor_images = config.get("actor_images") is True
            self._actor_index = JsonCache(self.get_data_path() / "actors-v1.json", max_entries=1, max_bytes=32 * 1024 * 1024) if self._enabled and self._actor_images else None
            self._image_hosts = tuple(v.lower() for v in self._lines(config.get("image_hosts")))
            self._provider = MankoProvider() if self._enabled else None
            self._providers = {"fanza": FANZAProvider(), "mgstage": MGStageProvider(), "duga": DUGAProvider(),
                               "sod": SODProvider(), "javbus": JavBusProvider(), "jav321": Jav321Provider(),
                               "fc2hub": FC2HubProvider(), "fc2ppvdb": FC2PPVDBProvider()} if self._enabled else {}
            requested = self._lines(config.get("sources", [SOURCE]))
            self._active_sources = tuple(source for source in requested if source in self._adapters())
            self._cache = JsonCache(self.get_data_path() / "metadata-v1.json") if self._enabled else None
            self._journal = DiscoveryJournal(self.get_data_path() / "discovery-v1.json") if self._enabled and self._discovery else None
            self._handoff, self._handoff_error = None, ""
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
                    )
                    self._handoff = HandoffCoordinator(ledger, handoff_config, self.recognize_scoped)
                except HandoffLedgerError:
                    self._handoff_error = "交接账本不可用，自动交接已阻止；不会重置或删除旧状态"


    @staticmethod
    def _lines(value) -> tuple[str, ...]:
        values = value.splitlines() if isinstance(value, str) else value if isinstance(value, list) else []
        return tuple(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))

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
            self._handoff = None

    def get_service(self) -> list[dict]:
        """宿主管理五分钟轮询；默认只读，整理须另选执行模式及完整权限。"""
        if not (self._enabled and self._discovery and self._downloader and self._tag and self._dirs and self._storage == "local"):
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
    def _safe_completed_hash(downloader: str, download_hash: str, tag: str) -> bool:
        """经 SDK 服务目录调用公开完成状态过滤；缺少该能力时保守停止。

        普通 hashs 查询把某些原始错误状态归一为 completed，不能单独作为执行证据。
        不读取私有客户端/凭据，也不自行转换下载器原始路径。
        """
        try:
            service = DownloaderHelper().get_service(name=downloader, type_filter="qbittorrent")
            getter = getattr(getattr(service, "instance", None), "get_completed_torrents", None)
            if not service or service.type != "qbittorrent" or not callable(getter):
                return False
            # SDK ServiceInfo.instance 为动态能力；上方已显式验证 callable。
            rows = getter(ids=[download_hash], tags=None)  # pylint: disable=not-callable
            if not isinstance(rows, list):
                return False
            matches = [row for row in rows if isinstance(row, dict) and row.get("hash") == download_hash]
            if len(matches) != 1:
                return False
            row = matches[0]
            progress, left = row.get("progress"), row.get("amount_left")
            safe_states = {"uploading", "stalledup", "forcedup", "queuedup", "pausedup", "stoppedup"}
            return (str(row.get("state") or "").lower() in safe_states
                    and type(progress) in (int, float) and math.isfinite(progress) and progress >= 1
                    and type(left) in (int, float) and math.isfinite(left) and left == 0
                    and tag in exact_tags(row.get("tags")))
        except Exception:
            return False

    def _manifest_files(self, torrent, candidates):
        try:
            manifest = self.chain.torrent_files(tid=torrent.hash, downloader=torrent.downloader)
            return owned_torrent_files(torrent, candidates, manifest)
        except Exception:
            return None

    def poll_completed(self) -> list[dict]:
        """复用下载器连接；先发现入队，再按显式模式预览或交给宿主整理。"""
        if not self.get_service() or not self._poll_lock.acquire(blocking=False):
            return []
        try:
            with self._lock:
                generation, journal = self._generation, self._journal
                downloader, tag, roots = self._downloader, self._tag, self._dirs
                handoff, handoff_limit = self._handoff, self._handoff_limit
            if not self._is_qbittorrent(downloader):
                return []
            torrents = self.chain.list_torrents(status=TorrentStatus.TRANSFER, downloader=downloader, include_all_tags=True) or []
            results = []
            eligible = [torrent for torrent in torrents if getattr(torrent, "downloader", None) == downloader and tag in exact_tags(getattr(torrent, "tags", None))]
            with self._lock:
                if not self._enabled or self._generation != generation or not journal:
                    return []
                batch = journal.next_batch(eligible)
            considered = 0
            for torrent in batch:
                files = torrent_files(torrent, downloader=downloader, tag=tag, roots=roots)
                if handoff and handoff.config.mode in ("preview", "execute"):
                    owned = self._manifest_files(torrent, files)
                    if owned is None:
                        with self._lock:
                            if not self._enabled or self._generation != generation:
                                return []
                            self._handoff_error = "种子文件清单缺失/不完整或路径映射不能唯一对应，已阻止该批次自动交接"
                            journal.advance(torrent)
                        continue
                    files = owned
                # 不拆开种子入队：首文件结算可能让宿主为整条种子加已整理标签。
                if considered + len(files) > 500:
                    break
                with self._lock:
                    if not self._enabled or self._generation != generation or not journal:
                        return []
                    results.extend(journal.discover(torrent, files[:max(0, 500 - len(results))]))
                    if handoff and handoff.config.mode in ("preview", "execute"):
                        try:
                            handoff.enqueue_batch(torrent, files)
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
                if not self._safe_completed_hash(downloader, payload["download_hash"], tag) or not can_continue():
                    return False
                candidates = torrent_files(matches[0], downloader=downloader, tag=tag, roots=roots)
                owned = self._manifest_files(matches[0], candidates)
                return can_continue() and owned is not None and Path(payload["path"]) in owned

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
        return [{"name": next((status.label for status in PROVIDERS if status.key == source), source) + "（离线适配）", "media_source": source, "media_types": [MediaType.MOVIE.value]} for source in self._active_sources] if self._enabled else []

    def recognize_media(self, meta=None, mtype=None, media_source=None, media_id=None, episode_group=None, cache=True):
        """显式来源查询用于手动操作及已有身份回查；不推断调用方是否手动。"""
        request = self._request(None, meta, mtype, media_source, media_id, explicit_source=True)
        return self._recognize_request(request, cache)

    async def async_recognize_media(self, meta=None, mtype=None, media_source=None, media_id=None, episode_group=None, cache=True):
        """异步显式来源查询；没有选择来源时总是退出。"""
        request = self._request(None, meta, mtype, media_source, media_id, explicit_source=True)
        return await self._async_recognize_request(request, cache)

    def search_medias(self, meta, media_source=None) -> list[MediaInfo]:
        """原生搜索只接受本来源及唯一可证明番号，不给宽泛标题猜测结果。"""
        sources = media_source if isinstance(media_source, (tuple, list)) else (media_source,)
        results = []
        for source in dict.fromkeys(str(value) for value in sources):
            result = self.recognize_media(meta=meta, media_source=source)
            if result:
                results.append(result)
        return results

    async def async_search_medias(self, meta, media_source=None) -> list[MediaInfo]:
        """异步原生来源搜索，不混合不同来源的原生身份。"""
        sources = media_source if isinstance(media_source, (tuple, list)) else (media_source,)
        results = []
        for source in dict.fromkeys(str(value) for value in sources):
            result = await self.async_recognize_media(meta=meta, media_source=source)
            if result:
                results.append(result)
        return results

    def _request(self, context, meta, mtype, media_source, media_id, explicit_source=False):
        with self._lock:
            if not self._enabled or not self._cache:
                return None
            source = str(media_source) if media_source is not None else None
            if explicit_source:
                if source not in self._active_sources:
                    return None
            elif not matches_scope(context, self._storage, self._dirs):
                return None
            source = source or (self._active_sources[0] if self._active_sources else None)
            if source not in self._active_sources or mtype not in (None, MediaType.MOVIE, MediaType.UNKNOWN):
                return None
            if getattr(meta, "type", None) in (MediaType.TV, MediaType.MUSIC):
                return None
            provider = self._adapters().get(source)
            if provider is None:
                return None
            if media_id is not None:
                identity = str(media_id)
                if not media_source or not IDENTIFIER.fullmatch(identity):
                    return None
                return self._generation, provider, self._cache, source + ":id:" + identity, identity, None, source
            text = str(getattr(meta, "org_string", None) or getattr(meta, "name", None) or "")
            number = extract_number(text)
            if not number:
                return None
            return self._generation, provider, self._cache, source + ":number:" + number, None, number, source

    def _accept(self, request, packet: dict | None, persist: bool = True) -> MediaInfo | None:
        if not packet:
            return None
        generation, _, cache, key, identity, number, source = request
        try:
            record, actual_number = packet["record"], packet["number"]
            if record.get("_source", source) != source:
                return None
            if not IDENTIFIER.fullmatch(str(record["_id"])) or record_numbers(record) != {actual_number}:
                return None
            if (identity and str(record["_id"]) != identity) or (number and actual_number != number):
                return None
            with self._lock:
                if not self._enabled or generation != self._generation:
                    return None
                projected = project_record(record, actual_number, self._current_actors(packet), self._image_hosts, source=source)
                cached = {"packet": packet, "projected": projected}
                if persist:
                    cache.put(key, cached)
                    cache.put(source + ":id:" + str(record["_id"]), cached)
            result = MediaInfo()
            result.from_dict(projected)
            return result
        except (OSError, TypeError, ValueError, KeyError, AttributeError):
            logger.warning("Xiaojiejie-Scraping: 来源结果或缓存无效，保留原生回退")
            return None

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
        _, provider, store, key, identity, number, _ = request
        try:
            cached = store.get(key) if cache else None
            packet = cached.get("packet") if cached else provider.fetch(identity) if identity else provider.search(number)
            if packet and not cached:
                packet = self._with_actor_images(packet)
            return self._accept(request, packet, persist=not bool(cached))
        except Exception:
            logger.warning("Xiaojiejie-Scraping: 来源暂不可用，保留原生回退")
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
        _, provider, store, key, identity, number, _ = request
        try:
            cached = await asyncio.to_thread(store.get, key) if cache else None
            packet = cached.get("packet") if cached else await provider.async_fetch(identity) if identity else await provider.async_search(number)
            if packet and not cached:
                packet = await self._async_with_actor_images(packet)
            return await asyncio.to_thread(self._accept, request, packet, persist=not bool(cached))
        except Exception:
            logger.warning("Xiaojiejie-Scraping: 异步来源暂不可用，保留原生回退")
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

    def _owned(self, mediainfo):
        source = str(getattr(mediainfo, "media_source", None))
        with self._lock:
            if not self._enabled or source not in self._active_sources or not self._cache:
                return None
            data = self._cache.get(source + ":id:" + str(mediainfo.media_id))
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
        return image_map(data["packet"]["record"], self._image_hosts) or None

    def obtain_images(self, mediainfo) -> MediaInfo | None:
        """只补自己的封面字段，不改写其他来源对象。"""
        data = self._owned(mediainfo)
        if not data:
            return None
        cover = image_map(data["packet"]["record"], self._image_hosts).get("poster.jpg")
        mediainfo.poster_path = cover
        mediainfo.backdrop_path = cover
        return mediainfo

    async def async_obtain_images(self, mediainfo) -> MediaInfo | None:
        """异步图片入口不进行同步磁盘操作。"""
        return await asyncio.to_thread(self.obtain_images, mediainfo)

    def get_api(self) -> list:
        """不额外开放外部查询接口。"""
        return []

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
                    text = f"{labels.get(record['state'], record['state'])}：{payload.get('path', '')}；{payload.get('reason', '')}"
                    rows.append({"component": "VAlert", "props": {"type": "info", "text": text}})
            except HandoffLedgerError:
                rows.append({"component": "VAlert", "props": {"type": "error", "text": "账本无法安全读取，请勿删除后盲目重试"}})
        return rows or [{"component": "VAlert", "props": {"type": "info", "text": "尚无自动交接记录；默认不会整理文件"}}]

    def get_form(self) -> tuple[list, dict]:
        """配置页明确授权范围及尚未隔离的宿主补充路径。"""
        fields = [
            {"component": "VAlert", "props": {"type": "warning", "text": "实验版：手动整理/刮削可明确选择来源。自动模式默认只读发现；预览会查询来源及宿主缓存但不搬文件，执行须完整配置。来源与真实整理尚未实站验收。"}},
            {"component": "VSwitch", "props": {"model": "enabled", "label": "启用已完成的来源能力（继承 MoviePilot 系统代理）"}},
            {"component": "VSelect", "props": {"model": "sources", "multiple": True, "chips": True, "label": "元数据来源（选择顺序即查询顺序，均未实站验收）", "items": [{"title": status.label, "value": status.key} for status in PROVIDERS if status.purpose == "metadata" and status.state == "offline_adapter"]}},
            {"component": "VSelect", "props": {"model": "storage", "label": "存储", "items": [{"title": "本地存储", "value": "local"}]}},
            {"component": "VTextarea", "props": {"model": "recognize_dirs", "label": "触发目录：每行一个绝对路径，含子目录；仅真实文件上下文生效"}},
            {"component": "VSwitch", "props": {"model": "discovery_enabled", "label": "每五分钟检查 qBittorrent 已完成任务（具体动作取决于下方模式）"}},
            {"component": "VTextField", "props": {"model": "downloader", "label": "MoviePilot 已配置的 qBittorrent 下载器名称"}},
            {"component": "VTextField", "props": {"model": "torrent_tag", "label": "专用标签，精确匹配且必须同时符合触发目录"}},
            {"component": "VSelect", "props": {"model": "handoff_mode", "label": "自动流程模式", "items": [{"title": "只读发现（默认）", "value": "discover"}, {"title": "预览：查询来源/缓存，不搬文件", "value": "preview"}, {"title": "执行：交 MoviePilot 整理并刮削", "value": "execute"}]}},
            {"component": "VTextField", "props": {"model": "target_path", "label": "明确目标：MoviePilot 容器内既有目录，不能与输入目录重叠"}},
            {"component": "VSelect", "props": {"model": "transfer_type", "label": "明确整理方式（保留 qBittorrent 源文件）", "items": [{"title": "复制", "value": "copy"}, {"title": "硬链接", "value": "link"}]}},
            {"component": "VSwitch", "props": {"model": "ownership_ack", "label": "我已将相同来源排除在 MoviePilot 原生自动监控之外，避免双重处理"}},
            {"component": "VSwitch", "props": {"model": "auxiliary_ack", "label": "我理解宿主仍可能补充 TMDB 辅助字段；已核对预览并接受该原生行为"}},
            {"component": "VTextField", "props": {"model": "handoff_limit", "type": "number", "label": "每轮最多处理的新文件（1–5，默认1）"}},
            {"component": "VSwitch", "props": {"model": "actor_images", "label": "GFriends 演员缩略图（还需批准 raw.githubusercontent.com 图片主机）"}},
            {"component": "VTextarea", "props": {"model": "image_hosts", "label": "批准的图片 HTTPS 主机，每行一个；空值不提供图片地址"}},
            {"component": "VAlert", "props": {"type": "info", "text": "; ".join(status.label + ("：离线适配，未实站验收" if status.state == "offline_adapter" else "：暂不可用，现行接口待核实" if status.state == "blocked" else "：待实现") for status in PROVIDERS)}},
        ]
        return fields, {"enabled": False, "sources": ["manko"], "storage": "local", "recognize_dirs": "", "image_hosts": "", "discovery_enabled": False, "downloader": "", "torrent_tag": "", "actor_images": False, "handoff_mode": "discover", "target_path": "", "transfer_type": "", "ownership_ack": False, "auxiliary_ack": False, "handoff_limit": 1}
