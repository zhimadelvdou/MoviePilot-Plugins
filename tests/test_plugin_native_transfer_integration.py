"""把真实插件识别结果交给未修改的 MoviePilot V3 原生整理入口。"""
from __future__ import annotations

from copy import deepcopy
import inspect
from pathlib import Path

import pytest

from app.chain.transfer import TransferChain  # pylint: disable=import-error,no-name-in-module
from app.chain.storage import StorageChain  # pylint: disable=import-error,no-name-in-module
from app.plugins.xiaojiejiescraping import XiaojiejieScraping  # pylint: disable=import-error,no-name-in-module
from app.schemas.file import FileItem
from app.schemas.system import TransferDirectoryConf
from app.schemas.types import MediaSource, MediaType
from app.sdk.media import MediaInfo, MetaInfo
from app.plugins.xiaojiejiescraping.handoff import native_transfer  # pylint: disable=import-error,no-name-in-module
from app.runtime.extensions.module.dispatcher import ModuleInvocationDispatcher


class OfflineMankoProvider:
    """仅替代插件的远程 metadata provider；识别与整理链保持真实。"""

    def __init__(self, payload: dict):
        self.payload = payload

    def search(self, number: str) -> dict:
        assert number == "TEST-123"
        return deepcopy(self.payload)

    def fetch(self, identity: str) -> dict:
        assert identity == "fixture_native_123"
        return deepcopy(self.payload)


class LocalModuleCatalog:
    """真实 FileManagerModule 仅处理官方本地存储适配器支持的方法。"""

    def __init__(self):
        from app.modules.filemanager.module import FileManagerModule

        self._module = FileManagerModule()
        self._module.init_module()

    def get_running_modules(self, _method: str):
        return (self._module,) if callable(getattr(self._module, _method, None)) else ()


class EmptyPluginCatalog:
    def __init__(self):
        self._plugin = None

    def set_plugin(self, plugin):
        self._plugin = plugin

    def get_plugin_modules(self):
        if self._plugin is None:
            return {}
        return {("XiaojiejieScraping", "Xiaojiejie-Scraping"): self._plugin.get_module()}


@pytest.fixture
def plugin_host_context(monkeypatch):
    """用真实模块 dispatcher 和仅本地文件查询适配器装配 V3 Chain。"""
    from dataclasses import MISSING, fields
    from types import SimpleNamespace

    from app.application.chain import context
    from app.application import configuration
    from app.application.configuration import ChainRuntimeConfig
    from app.db.adapters.chain import TransactionalChainDurableEventWriter
    from app.db.adapters.download import TransactionalDownloadFailureRepository
    from app.db.adapters.history.download import TransactionalDownloadHistoryRepository
    from app.db.adapters.history.transfer import TransactionalTransferHistoryRepository
    from app.db.adapters.mediaserver import TransactionalMediaServerRepository
    from app.db.adapters.transfer.admission import TransactionalTransferAdmissionRepository
    from app.db.adapters.transfer.execution import TransactionalTransferExecutionRepository
    from app.db.session import AsyncSessionFactory, SessionFactory
    from app.db.oper.systemconfig import SystemConfigOper
    from app.runtime.events import EventManager

    required = {
        field.name: None for field in fields(context.ChainRuntimeContext)
        if field.default is MISSING and field.default_factory is MISSING
    }
    required.update(
        module_manager=LocalModuleCatalog(),
        plugin_manager=EmptyPluginCatalog(),
        event_manager=EventManager(),
        download_history_repository=TransactionalDownloadHistoryRepository(
            sync_session=SessionFactory, async_session=AsyncSessionFactory,
        ),
        transfer_history_repository=TransactionalTransferHistoryRepository(
            sync_session=SessionFactory, async_session=AsyncSessionFactory,
        ),
        transfer_admission_repository=TransactionalTransferAdmissionRepository(SessionFactory),
        transfer_execution_repository=TransactionalTransferExecutionRepository(SessionFactory),
        download_failure_repository=TransactionalDownloadFailureRepository(SessionFactory),
        media_server_repository=TransactionalMediaServerRepository(SessionFactory),
        message_helper=SimpleNamespace(put=lambda **_kwargs: None),
        message_queue=SimpleNamespace(bind=lambda _callback: None),
        module_dispatcher_factory=ModuleInvocationDispatcher,
        configuration=ChainRuntimeConfig(media_extensions=(), video_extensions=(".mkv",)),
        durable_event_writer=TransactionalChainDurableEventWriter(SessionFactory),
    )
    runtime = context.ChainRuntimeContext(**required)
    monkeypatch.setattr(context, "_context_provider", lambda: runtime)
    monkeypatch.setattr(
        configuration, "_configured_system_config",
        configuration.SystemConfigService(SystemConfigOper()),
    )
    return runtime


@pytest.fixture
def offline_plugin(monkeypatch, tmp_path, sample_payload, plugin_host_context):
    """使用真实 V3 插件构造逻辑和明确的离线 provider 边界。"""
    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _self: tmp_path / "plugin-data")
    plugin = XiaojiejieScraping()
    plugin.init_plugin({"enabled": True, "image_hosts": []})
    plugin._provider = OfflineMankoProvider(sample_payload)
    plugin_host_context.plugin_manager.set_plugin(plugin)
    yield plugin
    plugin.stop_service()


def test_native_manual_preview_accepts_plugin_native_identity(offline_plugin, tmp_path, monkeypatch, request):
    """插件号码识别出的原生身份可通过旧 SDK 签名进入真实整理预览。"""
    assert "recognize_source" not in inspect.signature(TransferChain.do_transfer).parameters

    source = tmp_path / "downloads" / "TEST-123.mkv"
    source.parent.mkdir()
    source.write_bytes(b"synthetic video bytes; no media decoding")
    original = source.stat()
    library = tmp_path / "library"
    library.mkdir()
    meta = MetaInfo(source.name)
    meta.type = MediaType.MOVIE

    media = offline_plugin.recognize_media(meta=meta, mtype=MediaType.MOVIE, media_source="manko")

    assert isinstance(media, MediaInfo)
    assert media.media_source == MediaSource("manko")
    assert media.media_id == "fixture_native_123"

    directory = TransferDirectoryConf(
        name="synthetic-library",
        library_path=str(library),
        library_storage="local",
        transfer_type="link",
        overwrite_mode="never",
        renaming=False,
        scraping=False,
        notify=False,
    )
    item = StorageChain().get_file_item(storage="local", path=source)
    assert isinstance(item, FileItem)
    assert item.path == str(source)
    assert item.type == "file"
    assert item.name == source.name
    assert item.extension == "mkv"
    assert item.size == original.st_size
    chain_probe = TransferChain()
    assert ".mkv" in chain_probe._media_exts
    assert chain_probe._is_media_file(item, MediaType.MOVIE) is True
    request.addfinalizer(chain_probe.close_workers)
    preview_ok, preview = native_transfer(
        fileitem=item,
        meta=meta,
        mediainfo=media,
        mtype=MediaType.MOVIE,
        media_source=media.media_source,
        media_id=media.media_id,
        target_directory=directory,
        target_storage="local",
        target_path=library,
        transfer_type="link",
        scrape=False,
        background=False,
        manual=True,
        force=True,
        min_filesize=0,
        preview=True,
    )

    assert preview_ok is True, preview
    assert isinstance(preview, dict)
    rows = preview.get("items")
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert row["success"] is True
    assert row["source"] == str(source)
    target = Path(row["target"])
    assert target.parent == library
    assert not target.exists()
    after = source.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns, after.st_nlink) == (
        original.st_ino,
        original.st_size,
        original.st_mtime_ns,
        original.st_nlink,
    )


def test_native_manual_execution_links_renames_and_scrapes_plugin_metadata(
    offline_plugin, tmp_path, request, sample_payload,
):
    """真实插件 preview→execute/status 经原生链写入硬链接目标与插件 NFO。"""
    import hashlib
    import time
    import xml.etree.ElementTree as ET

    assert "recognize_source" not in inspect.signature(TransferChain.do_transfer).parameters

    source = tmp_path / "downloads" / "TEST-123.mkv"
    source.parent.mkdir()
    source.write_bytes(b"synthetic video bytes; no media decoding")
    original = source.stat()
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    library = tmp_path / "library"
    library.mkdir()
    offline_plugin.init_plugin({
        "enabled": True,
        "sources": ["manko"],
        "storage": "local",
        "recognize_dirs": str(source.parent),
        "target_path": str(library),
        "transfer_type": "link",
        "handoff_mode": "discover",
        "image_hosts": [],
    })
    offline_plugin._provider = OfflineMankoProvider(sample_payload)
    assert offline_plugin._manual is not None

    from app.plugins.xiaojiejiescraping.manual import ManualPreviewRequest

    preview = offline_plugin._manual.preview(ManualPreviewRequest(
        path=str(source), target_path=str(library), media_source="manko",
        transfer_type="link", library_category_folder=False,
    ))
    assert preview.summary.total == 1
    assert preview.summary.success == 1, preview
    assert preview.preview_id
    expected_target = Path(preview.items[0].target)
    assert expected_target.parent != library
    assert expected_target.stem != source.stem
    assert not expected_target.exists()

    from app.runtime.events import EventManager
    from app.startup.initializers.modules import configure_host_event_handler_resolver

    event_manager = EventManager()
    configure_host_event_handler_resolver()
    event_manager.start()
    request.addfinalizer(event_manager.stop)
    chain_probe = TransferChain()
    request.addfinalizer(chain_probe.close_workers)
    execution = offline_plugin._manual.execute(preview.preview_id)
    assert execution.preview_id == preview.preview_id
    assert execution.state in ("accepted", "completed"), execution
    assert len(execution.items) == 1
    assert execution.items[0].state in ("accepted", "completed"), execution.items[0]

    deadline = time.monotonic() + 10
    status = offline_plugin._manual.status(preview.preview_id)
    while time.monotonic() < deadline:
        nfo_files = list(expected_target.parent.glob("*.nfo"))
        if expected_target.exists() and nfo_files and status.state == "completed":
            break
        status = offline_plugin._manual.status(preview.preview_id)
        time.sleep(0.05)

    assert expected_target.exists(), status
    assert status.state == "completed", status
    target = expected_target
    assert target.is_file()
    target_stat = target.stat()
    assert (target_stat.st_dev, target_stat.st_ino) == (original.st_dev, original.st_ino)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == original_hash
    after = source.stat()
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
        original.st_dev,
        original.st_ino,
        original.st_size,
        original.st_mtime_ns,
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash

    nfo_files = list(target.parent.glob("*.nfo"))
    assert len(nfo_files) == 1
    nfo = ET.parse(nfo_files[0]).getroot()
    assert nfo.findtext("title") == "测试 & <合成> \"标题\""
    assert nfo.findtext("year") == "2026"
    assert nfo.findtext("plot") == "Synthetic <plot> & text"
    assert [actor.findtext("name") for actor in nfo.findall("actor")] == [
        "Synthetic <Actor> & Name"
    ]
    uniqueid = nfo.find("uniqueid")
    assert uniqueid is not None
    assert uniqueid.attrib.get("type") == "manko"
    assert uniqueid.text == "fixture_native_123"
    assert nfo.findtext("num") == "TEST-123"
