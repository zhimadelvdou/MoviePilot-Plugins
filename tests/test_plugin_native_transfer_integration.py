"""把真实插件识别结果交给未修改的 MoviePilot V3 原生整理入口。"""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
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


def _synthetic_jpeg(color, size=(800, 534)):
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


NATIVE_IMAGES = {
    "https://images.example.test/poster.jpg": _synthetic_jpeg((220, 20, 20), (500, 750)),
    "https://art.example.test/backdrop.jpg": _synthetic_jpeg((20, 20, 220)),
}


class OfflineImageResponse:
    status_code = 200

    def __init__(self, content, content_type="image/jpeg"):
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size=8192):
        assert chunk_size > 0
        yield self.content


class OfflineImageClient:
    def __init__(self):
        self.urls = []

    def stream(self, url, **_kwargs):
        self.urls.append(url)
        assert url in NATIVE_IMAGES
        return OfflineImageResponse(NATIVE_IMAGES[url])


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
    from app.chain.scraping import ScrapingChain
    from app.plugins.xiaojiejiescraping.transport import AnonymousRequestUtils

    def release_scraping_chain():
        instance = ScrapingChain.get_existing_instance()
        if instance is not None:
            ScrapingChain.release_existing_instance(instance)

    # Every test installs a new ChainRuntimeContext. ScrapingChain is a process
    # singleton and otherwise keeps the prior test's module/plugin dispatcher.
    release_scraping_chain()

    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _self: tmp_path / "plugin-data")
    monkeypatch.setattr(
        AnonymousRequestUtils, "get_res",
        lambda _client, _url, **_kwargs: OfflineImageResponse(_synthetic_jpeg((20, 20, 220))),
    )
    plugin = XiaojiejieScraping()
    plugin.init_plugin({"enabled": True, "image_hosts": []})
    plugin._provider = OfflineMankoProvider(sample_payload)
    plugin_host_context.plugin_manager.set_plugin(plugin)
    yield plugin
    plugin.stop_service()
    release_scraping_chain()


@pytest.fixture
def bind_current_plugin_event_handler(offline_plugin, plugin_host_context):
    """通过 SDK resolver 接口把当前测试插件实例绑定给正式事件分发器。"""
    from app.runtime.event.binding import EventHandlerBinding

    resolver_name = "xiaojiejie-native-test"
    event_manager = plugin_host_context.event_manager

    def resolve(owner_class):
        if owner_class is type(offline_plugin):
            return EventHandlerBinding(
                instance=offline_plugin,
                owner_name=offline_plugin.get_name(),
                run_sync_in_threadpool=True,
            )
        return None

    event_manager.register_handler_instance_resolver(resolver_name, resolve)
    try:
        yield
    finally:
        event_manager.unregister_handler_instance_resolver(resolver_name)


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


def test_native_manual_source_titles_produce_consistent_filename(
    offline_plugin, bind_current_plugin_event_handler, tmp_path, monkeypatch,
):
    """Compare source-dependent titles at the real native formatter preview seam."""
    from app.plugins.xiaojiejiescraping.manual import ManualPreviewRequest
    from app.sdk.config import settings

    monkeypatch.setattr(
        settings,
        "MOVIE_RENAME_FORMAT",
        "{{title}}{% if year %} ({{year}}){% endif %}/{{title}}{% if year %}({{year}}){% endif %}{% if part %}-{{part}}{% endif %}{% if edition %}-{{edition}}{% endif %}{% if videoFormat %}-{{videoFormat}}{% endif %}{% if videoCodec %}-{{videoCodec}}{% endif %}{% if audioCodec %}-{{audioCodec}}{% endif %}{% if effect %}-{{effect}}{% endif %}{{fileExt}}",
    )

    number, year = "SYN-123", "2026"
    javbus_title = f"{number} Synthetic JavBus Work Title"
    source = tmp_path / "downloads" / f"{number}.mkv"
    source.parent.mkdir()
    source.write_bytes(b"synthetic filename fixture; no media decoding")
    library = tmp_path / "library"
    library.mkdir()

    manko_packet = {
        "number": number,
        "record": {
            "_id": "fixture_manko_SYN123", "number": number,
            "title": "Synthetic Manko description kept outside filename projection",
            "title_display_name": [{"language": "en", "title": number}],
            "discription": [{"language": "en", "overview": "Synthetic Manko overview"}],
            "share_date": f"{year}-02-03T00:00:00Z",
        },
        "actors": [],
    }
    javbus_packet = {
        "number": number,
        "record": {
            "_id": number, "number": number,
            "title": javbus_title,
            "discription": [{"language": "en", "overview": "Synthetic JavBus overview"}],
            "share_date": f"{year}-02-03T00:00:00Z",
        },
        "actors": [],
    }

    class OfflinePacketProvider:
        def __init__(self, packet):
            self.packet = packet

        def search(self, found_number):
            assert found_number == number
            return deepcopy(self.packet)

        def fetch(self, _identity):
            return deepcopy(self.packet)

    offline_plugin.init_plugin({
        "enabled": True,
        "sources": ["manko", "javbus"],
        "javbus_api_url": "http://127.0.0.1:9",
        "storage": "local",
        "recognize_dirs": str(source.parent),
        "target_path": str(library),
        "transfer_type": "link",
        "handoff_mode": "discover",
        "image_hosts": [],
        "actress_subfolder": False,
    })
    offline_plugin._provider = OfflinePacketProvider(manko_packet)
    offline_plugin._providers["javbus"] = OfflinePacketProvider(javbus_packet)
    assert offline_plugin._manual is not None
    from app.schemas.types import ChainEventType
    assert offline_plugin._rename_event_manager is offline_plugin.eventmanager
    assert offline_plugin._rename_event_type is ChainEventType.TransferRenameBuild

    target_by_source = {}
    for media_source in ("manko", "javbus"):
        preview = offline_plugin._manual.preview(ManualPreviewRequest(
            path=str(source), target_path=str(library), media_source=media_source,
            transfer_type="link", library_category_folder=False,
            actress_subfolder=False,
        ))
        assert preview.summary.success == 1, preview
        assert len(preview.items) == 1 and preview.items[0].target
        target = Path(preview.items[0].target)
        assert target.parent.parent == library
        assert not target.exists()
        target_by_source[media_source] = target

        if media_source == "javbus":
            # Filename normalization must not erase the title used by preview/NFO metadata.
            assert preview.items[0].title == javbus_title
            media = offline_plugin.recognize_media(
                meta=offline_plugin._manual._meta(source, number),
                mtype=MediaType.MOVIE,
                media_source="javbus",
                cache=False,
            )
            assert isinstance(media, MediaInfo)
            assert media.title == javbus_title
            assert media.original_title == javbus_title
            import xml.etree.ElementTree as ET
            nfo = ET.fromstring(offline_plugin.metadata_nfo(mediainfo=media))
            assert nfo.findtext("title") == javbus_title

    manko_target = target_by_source["manko"]
    javbus_target = target_by_source["javbus"]
    # The same system template renders the Manko number/year form. This expectation
    # intentionally goes red on current JavBus title-driven naming.
    assert manko_target.parent.name == f"{number} ({year})"
    assert manko_target.stem == f"{number}({year})"
    assert javbus_target == manko_target
    assert not list(library.rglob("*.nfo"))


def test_native_manual_execution_links_renames_and_scrapes_plugin_metadata(
    offline_plugin, tmp_path, request, sample_payload, monkeypatch,
):
    """真实原生链写入插件 NFO 和彼此不同的合成海报/背景图。"""
    import hashlib
    import time
    import xml.etree.ElementTree as ET

    assert "recognize_source" not in inspect.signature(TransferChain.do_transfer).parameters

    source = tmp_path / "downloads" / "TEST-123.mkv"
    source.parent.mkdir()
    source.write_bytes(b"synthetic video bytes; no media decoding")
    source_nfo = source.with_suffix(".nfo")
    source_nfo.write_text("<movie><runtime>118</runtime></movie>", encoding="utf-8")
    source_nfo_before = source_nfo.read_bytes()
    original = source.stat()
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    payload = deepcopy(sample_payload)
    payload["record"].update({
        "video_duration": None,
        "runtime_minutes": 120,
        "directors": [{"name": "Synthetic Director"}],
        "production_companies": [{"name": "アタッカーズ"}],
        "backdrop_images": ["https://art.example.test/backdrop.jpg"],
        "cover_image": "https://images.example.test/poster.jpg",
    })

    import app.chain.scraping as scraping_module

    image_client = OfflineImageClient()
    monkeypatch.setattr(scraping_module, "_scraping_http_snapshot", lambda: image_client)
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
        "image_hosts": ["images.example.test", "art.example.test"],
    })
    offline_plugin._provider = OfflineMankoProvider(payload)
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
        image_files = [path for path in expected_target.parent.glob("*.jpg") if path.is_file()]
        if expected_target.exists() and nfo_files and len(image_files) >= 2 and status.state == "completed":
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
    assert nfo.findtext("director") == "Synthetic Director"
    assert nfo.findtext("studio") == "アタッカーズ"
    assert nfo.findtext("runtime") == "120"
    assert [actor.findtext("name") for actor in nfo.findall("actor")] == [
        "Synthetic <Actor> & Name"
    ]
    uniqueid = nfo.find("uniqueid")
    assert uniqueid is not None
    assert uniqueid.attrib.get("type") == "manko"
    assert uniqueid.text == "fixture_native_123"
    assert nfo.findtext("num") == "TEST-123"
    assert source_nfo.read_bytes() == source_nfo_before
    target_images = {path.name: path.read_bytes() for path in target.parent.glob("*.jpg")}
    assert "poster.jpg" in target_images
    background_name = "backdrop.jpg" if "backdrop.jpg" in target_images else "fanart.jpg"
    assert background_name in target_images
    assert target_images["poster.jpg"] == NATIVE_IMAGES["https://images.example.test/poster.jpg"]
    assert target_images[background_name] == NATIVE_IMAGES["https://art.example.test/backdrop.jpg"]
    assert target_images["poster.jpg"] != target_images[background_name]
    assert set(image_client.urls) == set(NATIVE_IMAGES)


def test_actress_folder_preview_without_source_flag_waits_for_trusted_marker(
    offline_plugin, tmp_path, sample_payload,
):
    """没有原始 true 标记时不把普通 cast 提供为女主演候选。"""
    from app.plugins.xiaojiejiescraping.manual import ManualPreviewRequest

    source = tmp_path / "downloads" / "TEST-123.mkv"
    source.parent.mkdir()
    source.write_bytes(b"synthetic video bytes")
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
        "actress_subfolder": True,
    })
    offline_plugin._provider = OfflineMankoProvider(sample_payload)
    calls = []
    offline_plugin._manual.transfer = lambda **kwargs: calls.append(kwargs)

    preview = offline_plugin._manual.preview(ManualPreviewRequest(
        path=str(source), target_path=str(library), media_source="manko",
        transfer_type="link", library_category_folder=False,
    ))

    assert calls == []
    assert preview.summary.success == 0
    item = preview.items[0]
    assert item.needs_actress_choice is False
    assert item.actor_candidates == []
    assert "可信女主演来源证据" in item.message


def test_only_true_flagged_actors_are_candidates_and_false_actor_cannot_be_chosen(
    offline_plugin, tmp_path, monkeypatch,
):
    """多个 true 标记只提供这些候选，false actor 和旧官方名单不能扩大候选范围。"""
    from app.plugins.xiaojiejiescraping.manual import ManualPreviewRequest

    source = tmp_path / "downloads" / "ADN-000775.mkv"
    source.parent.mkdir()
    source.write_bytes(b"synthetic video bytes")
    library = tmp_path / "library"
    library.mkdir()
    packet = {
        "number": "ADN-000775",
        "record": {"_id": "fixture_native_123", "number": "ADN-000775", "title": "Synthetic Work"},
        "actors": [{"name": "Synthetic Actress", "avdbs_actor": True},
                   {"name": "Other Trusted Actress", "avdbs_actor": True},
                   {"name": "Unmarked Actor", "avdbs_actor": False}],
        "_actress_evidence": {"source": "attackers-official", "number": "ADN000775",
                              "names": ["Unmarked Actor"]},
    }

    class ActressProvider:
        def search(self, _number):
            return deepcopy(packet)

        def fetch(self, _identity):
            return deepcopy(packet)

    offline_plugin.init_plugin({"enabled": True, "sources": ["manko"], "storage": "local",
        "recognize_dirs": str(source.parent), "target_path": str(library),
        "transfer_type": "link", "handoff_mode": "discover"})
    offline_plugin._provider = ActressProvider()
    calls = []

    def transfer(**kwargs):
        calls.append(kwargs)
        destination = Path(kwargs["target_path"]) / source.name
        return True, {"items": [{"success": True, "source": str(source), "source_storage": "local",
                                  "target": str(destination)}]}

    offline_plugin._manual.transfer = transfer
    preview = offline_plugin._manual.preview(ManualPreviewRequest(
        path=str(source), target_path=str(library), media_source="manko", actress_subfolder=True,
        actress_choices={str(source): "Unmarked Actor"}))

    assert calls == []
    assert preview.summary.success == 0
    assert preview.items[0].needs_actress_choice is True
    assert preview.items[0].actor_candidates == ["Synthetic Actress", "Other Trusted Actress"]
    assert "女主演候选" in preview.items[0].message

    selected = offline_plugin._manual.preview(ManualPreviewRequest(
        path=str(source), target_path=str(library), media_source="manko", actress_subfolder=True,
        actress_choices={str(source): "Other Trusted Actress"}))
    assert selected.summary.success == 1
    assert selected.items[0].selected_actress == "Other Trusted Actress"
    assert calls[0]["target_path"] == library / "Other Trusted Actress"


def test_manual_preview_is_invalidated_when_selected_source_flag_is_removed(
    offline_plugin, tmp_path,
):
    """预览时被选演员失去 true 标记，必须停止且不调用原生整理。"""
    from app.plugins.xiaojiejiescraping.manual import ManualPreviewRequest

    source = tmp_path / "downloads" / "ADN-000775.mkv"
    source.parent.mkdir()
    source.write_bytes(b"synthetic video bytes")
    library = tmp_path / "library"
    library.mkdir()
    packet = {
        "number": "ADN-000775",
        "record": {"_id": "fixture_native_123", "number": "ADN-000775", "title": "Synthetic Work"},
        "actors": [{"name": "Synthetic Actress", "avdbs_actor": True}],
    }

    class ActressProvider:
        def search(self, _number):
            return deepcopy(packet)

        def fetch(self, _identity):
            return deepcopy(packet)

    offline_plugin.init_plugin({"enabled": True, "sources": ["manko"], "storage": "local",
        "recognize_dirs": str(source.parent), "target_path": str(library),
        "transfer_type": "link", "handoff_mode": "discover"})
    offline_plugin._provider = ActressProvider()
    calls = []

    def transfer(**kwargs):
        calls.append(kwargs)
        if not kwargs["preview"]:
            return True, "accepted"
        target = Path(kwargs["target_path"]) / "Synthetic Work.mkv"
        return True, {"items": [{"source": str(source), "source_storage": "local",
            "target": str(target), "success": True}]}

    offline_plugin._manual.transfer = transfer
    preview = offline_plugin._manual.preview(ManualPreviewRequest(
        path=str(source), target_path=str(library), media_source="manko", actress_subfolder=True))
    assert preview.summary.success == 1, preview
    assert preview.items[0].selected_actress == "Synthetic Actress"

    packet["actors"][0]["avdbs_actor"] = False
    execution = offline_plugin._manual.execute(preview.preview_id)

    assert execution.state == "failed", execution
    assert len(calls) == 1 and calls[0]["preview"] is True


def test_native_manual_actress_subfolder_preview_execute_and_scrapes(
    offline_plugin, tmp_path, request, monkeypatch, sample_payload,
):
    """真实 SDK 在缺失女主演子目录时仍可预览，并将硬链接与刮削放入该目录。"""
    import hashlib
    import time
    import xml.etree.ElementTree as ET

    from app.plugins.xiaojiejiescraping.manual import ManualPreviewRequest

    number = "ADN-000775"
    actress = "Synthetic Actress"
    source = tmp_path / "downloads" / f"{number}.mkv"
    source.parent.mkdir()
    source.write_bytes(b"synthetic actress-folder video bytes")
    source_before = source.stat()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    library = tmp_path / "library"
    library.mkdir()
    packet = deepcopy(sample_payload)
    packet["number"] = number
    packet["record"].update({
        "_id": "fixture_native_775", "number": number, "title": "Synthetic Actress Work",
        "title_display_name": [{"language": "en", "title": "Synthetic Actress Work"}],
        "discription": [{"language": "en", "overview": "Synthetic actress folder plot"}],
        "share_date": "2026-02-03T00:00:00Z", "runtime_minutes": 98,
        "directors": [{"name": "Synthetic Director"}],
        "production_companies": [{"name": "Synthetic Studio"}],
        "backdrop_images": ["https://art.example.test/backdrop.jpg"],
        "cover_image": "https://images.example.test/poster.jpg",
    })
    packet["actors"] = [{"name": actress, "avdbs_actor": True},
                        {"name": "Unmarked Cast Member", "avdbs_actor": False}]

    class ActressProvider:
        def search(self, found_number):
            assert found_number == number
            return deepcopy(packet)

        def fetch(self, identity):
            assert identity == "fixture_native_775"
            return deepcopy(packet)

    import app.chain.scraping as scraping_module

    image_client = OfflineImageClient()
    monkeypatch.setattr(scraping_module, "_scraping_http_snapshot", lambda: image_client)
    offline_plugin.init_plugin({
        "enabled": True, "sources": ["manko"], "storage": "local",
        "recognize_dirs": str(source.parent), "target_path": str(library),
        "transfer_type": "link", "handoff_mode": "discover",
        "image_hosts": ["images.example.test", "art.example.test"],
        "actress_subfolder": False,
    })
    offline_plugin._provider = ActressProvider()

    preview = offline_plugin._manual.preview(ManualPreviewRequest(
        path=str(source), target_path=str(library), media_source="manko",
        actress_subfolder=True,
    ))
    assert preview.summary.success == 1, preview
    assert preview.preview_id
    expected_target = Path(preview.items[0].target)
    assert expected_target.is_relative_to(library / actress)
    assert preview.items[0].selected_actress == actress
    assert not (library / actress).exists()
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
    assert execution.state in ("accepted", "completed"), execution

    deadline = time.monotonic() + 10
    status = offline_plugin._manual.status(preview.preview_id)
    while time.monotonic() < deadline:
        nfo_files = list(expected_target.parent.glob("*.nfo"))
        image_files = [path for path in expected_target.parent.glob("*.jpg") if path.is_file()]
        if expected_target.exists() and nfo_files and len(image_files) >= 2 and status.state == "completed":
            break
        status = offline_plugin._manual.status(preview.preview_id)
        time.sleep(0.05)
    assert status.state == "completed", status
    assert expected_target.is_file()
    after = source.stat()
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
        source_before.st_dev, source_before.st_ino, source_before.st_size, source_before.st_mtime_ns,
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    target_stat = expected_target.stat()
    assert (target_stat.st_dev, target_stat.st_ino) == (source_before.st_dev, source_before.st_ino)
    nfo = ET.parse(next(expected_target.parent.glob("*.nfo"))).getroot()
    assert nfo.findtext("director") == "Synthetic Director"
    assert nfo.findtext("studio") == "Synthetic Studio"
    assert nfo.findtext("runtime") == "98"
    assert [actor.findtext("name") for actor in nfo.findall("actor")] == [
        actress, "Unmarked Cast Member",
    ]
    images = {path.name: path.read_bytes() for path in expected_target.parent.glob("*.jpg")}
    assert images["poster.jpg"] == NATIVE_IMAGES["https://images.example.test/poster.jpg"]
    background = "backdrop.jpg" if "backdrop.jpg" in images else "fanart.jpg"
    assert images[background] == NATIVE_IMAGES["https://art.example.test/backdrop.jpg"]



@pytest.mark.parametrize(("share_date", "expected_year"), [
    ("2024-01-02", "2024"),
    ("", None),
])
def test_native_fc2_number_layout_preserves_nfo_without_actor_folder(
        offline_plugin, bind_current_plugin_event_handler, tmp_path, request, monkeypatch,
        share_date, expected_year,
):
    """Run FC2 through the real native formatter, hardlink, NFO and image write paths."""
    import hashlib
    import time
    import xml.etree.ElementTree as ET

    from app.plugins.xiaojiejiescraping.manual import ManualPreviewRequest
    from app.sdk.config import settings

    number = "FC2-PPV-1234567"
    title = "FC2-PPV-1234567 Complete synthetic FC2 title"
    overview = "Complete synthetic FC2 overview"
    source = tmp_path / "downloads" / f"{number}.mkv"
    source.parent.mkdir()
    source.write_bytes(b"synthetic FC2 video bytes; no subtitles or media decoding")
    source_before = source.stat()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    library = tmp_path / "library"
    library.mkdir()
    packet = {
        "number": number,
        "record": {
            "_source": "fc2", "_id": "1234567", "number": number,
            "title": title, "share_date": share_date,
            "discription": [{"language": "en", "overview": overview}],
            "cover_image": "https://images.example.test/poster.jpg",
            "backdrop_images": ["https://art.example.test/backdrop.jpg"],
        },
        "actors": [],
    }

    class FC2Provider:
        def search(self, found_number):
            assert found_number == number
            return deepcopy(packet)

        def fetch(self, identity):
            assert identity == "1234567"
            return deepcopy(packet)

    monkeypatch.setattr(
        settings, "MOVIE_RENAME_FORMAT",
        "{{title}}{% if year %} ({{year}}){% endif %}/{{title}}{% if year %}({{year}}){% endif %}{{fileExt}}",
    )
    import app.chain.scraping as scraping_module
    image_client = OfflineImageClient()
    monkeypatch.setattr(scraping_module, "_scraping_http_snapshot", lambda: image_client)
    offline_plugin.init_plugin({
        "enabled": True, "sources": ["fc2"], "storage": "local",
        "recognize_dirs": str(source.parent), "target_path": str(library),
        "transfer_type": "link", "handoff_mode": "discover",
        "image_hosts": ["images.example.test", "art.example.test"],
        "actress_subfolder": True,
    })
    offline_plugin._providers["fc2"] = FC2Provider()
    assert offline_plugin._manual is not None

    preview = offline_plugin._manual.preview(ManualPreviewRequest(
        path=str(source), target_path=str(library), media_source="fc2",
        transfer_type="link", library_category_folder=False,
    ))
    assert preview.summary.success == 1, preview
    assert preview.preview_id
    target = Path(preview.items[0].target)
    assert target.parent.parent == library
    assert target.parent.name == (f"{number} ({expected_year})" if expected_year else number)
    assert target.name == (f"{number}({expected_year}).mkv" if expected_year else f"{number}.mkv")
    assert preview.items[0].title == title
    assert not target.exists()

    from app.runtime.events import EventManager
    from app.startup.initializers.modules import configure_host_event_handler_resolver
    event_manager = EventManager()
    configure_host_event_handler_resolver()
    event_manager.start()
    request.addfinalizer(event_manager.stop)
    chain_probe = TransferChain()
    request.addfinalizer(chain_probe.close_workers)
    execution = offline_plugin._manual.execute(preview.preview_id)
    assert execution.state in ("accepted", "completed"), execution

    deadline = time.monotonic() + 10
    status = offline_plugin._manual.status(preview.preview_id)
    while time.monotonic() < deadline:
        nfo_files = list(target.parent.glob("*.nfo"))
        image_files = list(target.parent.glob("*.jpg"))
        if target.exists() and nfo_files and len(image_files) >= 2 and status.state == "completed":
            break
        status = offline_plugin._manual.status(preview.preview_id)
        time.sleep(0.05)
    assert status.state == "completed", status

    target_stat = target.stat()
    assert (target_stat.st_dev, target_stat.st_ino) == (source_before.st_dev, source_before.st_ino)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == source_hash
    source_after = source.stat()
    assert (source_after.st_dev, source_after.st_ino, source_after.st_size, source_after.st_mtime_ns) == (
        source_before.st_dev, source_before.st_ino, source_before.st_size, source_before.st_mtime_ns,
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash

    nfo_files = list(target.parent.glob("*.nfo"))
    assert len(nfo_files) == 1
    nfo = ET.parse(nfo_files[0]).getroot()
    assert nfo.findtext("num") == number
    assert nfo.findtext("uniqueid") == "1234567"
    assert nfo.find("uniqueid").attrib.get("type") == "fc2"
    assert nfo.findtext("title") == title
    assert nfo.findtext("originaltitle") == title
    assert nfo.findtext("plot") == overview
    if expected_year:
        assert nfo.findtext("year") == expected_year
    else:
        assert nfo.find("year") is None
    assert nfo.findall("actor") == []
    assert not source.with_suffix(".nfo").exists()

    images = {path.name: path.read_bytes() for path in target.parent.glob("*.jpg")}
    assert images["poster.jpg"] == NATIVE_IMAGES["https://images.example.test/poster.jpg"]
    background_names = {"backdrop.jpg", "fanart.jpg"}.intersection(images)
    assert background_names
    assert all(images[name] == NATIVE_IMAGES["https://art.example.test/backdrop.jpg"]
               for name in background_names)
    assert images["poster.jpg"] != images[next(iter(background_names))]
    from PIL import Image
    for image_bytes in images.values():
        with Image.open(BytesIO(image_bytes)) as image:
            assert image.format == "JPEG"
            image.verify()
    assert set(image_client.urls) == set(NATIVE_IMAGES)
