"""用真实插件轮询与 MoviePilot 整理链验证自动触发匹配方式。"""
from __future__ import annotations

import hashlib
import inspect
from io import BytesIO
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.chain.transfer import TransferChain  # pylint: disable=import-error,no-name-in-module
from app.plugins.xiaojiejiescraping import XiaojiejieScraping  # pylint: disable=import-error,no-name-in-module
from app.schemas.transfer import DownloaderFile, DownloaderTorrent

# Reuse the established real plugin, dispatcher, SQLite history adapters, and local storage fixtures.
from test_plugin_native_transfer_integration import (  # noqa: F401
    OfflineMankoProvider,
    offline_plugin,
    plugin_host_context,
)


IMAGE_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63e051b2000000a4006798d0e0980000000049454e44ae426082"
)


class OfflineImageResponse:
    """HTTP 边界返回最小有效图片字节，真实图片校验与落盘仍由 MoviePilot 执行。"""

    status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, *, chunk_size: int):
        assert chunk_size > 0
        yield IMAGE_PNG


class OfflineImageClient:
    def __init__(self):
        self.urls = []

    def stream(self, url, **_kwargs):
        self.urls.append(url)
        assert url == "https://images.example.test/poster.png"
        return OfflineImageResponse()


@pytest.fixture
def native_host_lifecycle(plugin_host_context, offline_plugin):
    """Drain events and release ChainBase singletons around each injected host context."""
    from app.chain.media import MediaChain
    from app.chain.scraping import ScrapingChain
    from app.chain.transfer import TransferChain as HostTransferChain
    from app.runtime.events import EventManager
    from app.startup.initializers.modules import configure_host_event_handler_resolver

    event_manager = EventManager()

    def release_context_chains():
        for chain_type in (ScrapingChain, MediaChain, HostTransferChain):
            instance = chain_type.get_existing_instance()
            if instance is not None:
                chain_type.release_existing_instance(instance)

    def close_transfer_workers():
        instance = HostTransferChain.get_existing_instance()
        if instance is not None:
            assert instance.close_workers(), "TransferChain workers must settle before releasing their runtime context"

    # A previous case or test module can leave singleton chains holding its context.
    close_transfer_workers()
    event_manager.stop()
    release_context_chains()
    configure_host_event_handler_resolver()
    event_manager.start()
    yield
    close_transfer_workers()
    event_manager.stop()
    release_context_chains()


@pytest.fixture
def native_transfer_chain(plugin_host_context, native_host_lifecycle, offline_plugin):
    """Create the real singleton chain while its host context is active."""
    chain = TransferChain()
    yield chain


@pytest.fixture
def real_transfer_query_service():
    """Use the official query service and real SQLAlchemy adapter for handoff preflight/history."""
    from app.application.query import (
        DataQueryService,
        configure_data_query_service,
        reset_data_query_service,
    )
    from app.db.adapters.query import SqlAlchemyDataQueryAdapter
    from app.db.session import SessionFactory

    adapter = SqlAlchemyDataQueryAdapter(SessionFactory)
    configure_data_query_service(DataQueryService(
        subscriptions=adapter,
        histories=adapter,
        # The tested public history endpoint is synchronous; no worker is invoked.
        async_executor=SimpleNamespace(),
    ))
    try:
        yield
    finally:
        reset_data_query_service()


def _completed_torrent(source: Path, *, tag: str) -> DownloaderTorrent:
    return DownloaderTorrent(
        downloader="qbit-main",
        hash="a" * 40,
        tags=tag,
        progress=100,
        state="completed",
        content_path=str(source),
        save_path=str(source.parent),
    )


def _native_manifest(source: Path) -> list[DownloaderFile]:
    return [DownloaderFile(
        id=0, name=source.name, size=source.stat().st_size, priority=1, progress=1,
    )]


def _assert_nfo(path: Path):
    nfo = ET.parse(path).getroot()
    assert nfo.findtext("title") == '测试 & <合成> "标题"'
    assert nfo.findtext("year") == "2026"
    assert nfo.findtext("plot") == "Synthetic <plot> & text"
    assert [actor.findtext("name") for actor in nfo.findall("actor")] == [
        "Synthetic <Actor> & Name"
    ]
    uniqueid = nfo.find("uniqueid")
    assert uniqueid is not None
    assert uniqueid.attrib.get("type") == "manko"
    assert uniqueid.text == "fixture_native_123"


def _assert_valid_fixture_image():
    from PIL import Image

    with Image.open(BytesIO(IMAGE_PNG)) as image:
        image.verify()


def _configure_qbit_boundary(monkeypatch, plugin, torrent, source):
    """仅替换 qBittorrent SDK 结果；轮询、映射、账本和整理仍走真实实现。"""
    plugin._is_qbittorrent = lambda _name: True
    plugin.chain.list_torrents = lambda **_kwargs: [torrent]
    plugin.chain.torrent_files = lambda **_kwargs: _native_manifest(source)
    monkeypatch.setattr(XiaojiejieScraping, "_raw_completed_row", staticmethod(lambda _downloader, _hash: {
        "hash": torrent.hash,
        "state": "uploading",
        "progress": 1.0,
        "amount_left": 0,
        "tags": torrent.tags,
        "completion_on": int(time.time()),
    }))


def test_tag_match_outside_configured_paths_runs_real_native_hardlink_and_scrape(
    offline_plugin, tmp_path, monkeypatch, sample_payload, real_transfer_query_service,
    native_host_lifecycle, native_transfer_chain,
):
    """精确标签授权单个已完成任务；任务局部 source scope 不扩大插件全局目录。"""
    assert "recognize_source" not in inspect.signature(TransferChain.do_transfer).parameters

    source_root = tmp_path / "task-local-source"
    source_root.mkdir()
    source = source_root / "TEST-123.mkv"
    source.write_bytes(b"synthetic video bytes; no media decoding")
    original = source.stat()
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    configured_root = tmp_path / "configured-but-not-matching-path"
    configured_root.mkdir()
    target_root = tmp_path / "library"
    target_root.mkdir()
    sample_payload["record"]["cover_image"] = "https://images.example.test/poster.png"
    plugin_config = {
        "enabled": True,
        "sources": ["manko"],
        "storage": "local",
        "discovery_enabled": True,
        "automatic_match_mode": "tag",
        "auto_new_only": False,
        "downloader": "qbit-main",
        "torrent_tag": "MANKO",
        "recognize_dirs": [str(configured_root)],
        "handoff_mode": "execute",
        "target_path": str(target_root),
        "transfer_type": "link",
        "ownership_ack": True,
        "auxiliary_ack": True,
        "handoff_limit": 1,
        "image_hosts": ["images.example.test"],
    }
    offline_plugin.init_plugin(plugin_config)
    offline_plugin._provider = OfflineMankoProvider(sample_payload)
    assert source_root not in offline_plugin._dirs

    torrent = _completed_torrent(source, tag="reviewed, MANKO")
    _configure_qbit_boundary(monkeypatch, offline_plugin, torrent, source)

    import app.chain.scraping as scraping_module

    _assert_valid_fixture_image()
    image_client = OfflineImageClient()
    monkeypatch.setattr(scraping_module, "_scraping_http_snapshot", lambda: image_client)

    discovered = offline_plugin.poll_completed()
    assert len(discovered) == 1
    assert discovered[0]["path"] == str(source)
    records = offline_plugin._handoff.ledger.list_records(limit=10)
    assert len(records) == 1
    assert records[0]["state"] in ("accepted", "completed", "uncertain")
    assert records[0]["payload"]["path"] == str(source)
    assert records[0]["payload"]["media_source"] == "manko"
    assert records[0]["payload"]["media_id"] == "fixture_native_123"

    target = Path(records[0]["payload"]["preview_target"])
    deadline = time.monotonic() + 10
    status = offline_plugin._handoff.ledger.get(records[0]["key"])
    while time.monotonic() < deadline:
        status = offline_plugin._handoff.reconcile_record(records[0]["key"])
        nfo_files = list(target.parent.glob("*.nfo"))
        image_files = [path for path in target.parent.rglob("*") if path.is_file()
                       and path.suffix.lower() in (".jpg", ".png")]
        if target.exists() and nfo_files and len(image_files) >= 2 and status["state"] == "completed":
            break
        time.sleep(0.05)

    assert status["state"] == "completed", status
    assert target.is_file()
    target_stat = target.stat()
    assert (target_stat.st_dev, target_stat.st_ino) == (original.st_dev, original.st_ino)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == original_hash
    after = source.stat()
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
        original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns,
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash

    nfo_files = list(target.parent.glob("*.nfo"))
    assert len(nfo_files) == 1
    _assert_nfo(nfo_files[0])
    image_files = sorted(path.name for path in target.parent.rglob("*") if path.is_file()
                         and path.suffix.lower() in (".jpg", ".png"))
    assert image_files == ["backdrop.jpg", "fanart.jpg", "poster.jpg"]
    assert all((target.parent / name).read_bytes() == IMAGE_PNG for name in image_files)
    assert len(image_client.urls) == 3
    assert offline_plugin._dirs == (configured_root,)


def test_manual_arbitrary_source_and_target_preview_execute_status_with_images(
    offline_plugin, tmp_path, monkeypatch, sample_payload, native_host_lifecycle,
    native_transfer_chain,
):
    """手动入口可单独选择自动范围外的源与目标，并验证整理、NFO 和真实图片落盘。"""
    import time

    auto_root = tmp_path / "automatic-source"
    auto_root.mkdir()
    auto_target = tmp_path / "automatic-target"
    auto_target.mkdir()
    manual_root = tmp_path / "manual-source"
    manual_root.mkdir()
    source = manual_root / "TEST-123.mkv"
    source.write_bytes(b"manual synthetic video")
    original = source.stat()
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    manual_target = tmp_path / "manual-library"
    manual_target.mkdir()
    sample_payload["record"]["cover_image"] = "https://images.example.test/poster.png"
    offline_plugin.init_plugin({
        "enabled": True,
        "sources": ["manko"],
        "storage": "local",
        "recognize_dirs": [str(auto_root)],
        "handoff_mode": "discover",
        "target_path": str(auto_target),
        "image_hosts": ["images.example.test"],
    })
    offline_plugin._provider = OfflineMankoProvider(sample_payload)
    assert offline_plugin._manual is not None
    assert manual_root not in offline_plugin._dirs
    assert manual_target != auto_target
    assert not source.is_relative_to(auto_root)

    _assert_valid_fixture_image()
    image_client = OfflineImageClient()
    import app.chain.scraping as scraping_module

    monkeypatch.setattr(scraping_module, "_scraping_http_snapshot", lambda: image_client)

    from app.plugins.xiaojiejiescraping.manual import ManualPreviewRequest
    preview = offline_plugin._manual.preview(ManualPreviewRequest(
        path=str(source), target_path=str(manual_target), media_source="manko",
        transfer_type="link", library_category_folder=False,
    ))
    assert preview.summary.total == 1
    assert preview.summary.success == 1, preview
    assert preview.preview_id
    target = Path(preview.items[0].target)
    assert target.parent != manual_target
    assert not target.exists()
    assert target.is_relative_to(manual_target)

    execution = offline_plugin._manual.execute(preview.preview_id)
    assert execution.state in ("accepted", "completed"), execution
    deadline = time.monotonic() + 10
    status = offline_plugin._manual.status(preview.preview_id)
    while time.monotonic() < deadline:
        nfo_files = list(target.parent.glob("*.nfo"))
        images = [path for path in target.parent.rglob("*") if path.is_file()
                  and path.suffix.lower() in (".jpg", ".png")]
        if target.exists() and nfo_files and len(images) >= 2 and status.state == "completed":
            break
        status = offline_plugin._manual.status(preview.preview_id)
        time.sleep(0.05)

    assert status.state == "completed", status
    assert status.items[0].state == "completed"
    target_stat = target.stat()
    assert (target_stat.st_dev, target_stat.st_ino) == (original.st_dev, original.st_ino)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == original_hash
    after = source.stat()
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
        original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns,
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    _assert_nfo(next(target.parent.glob("*.nfo")))
    images = sorted(path for path in target.parent.rglob("*") if path.is_file()
                    and path.suffix.lower() in (".jpg", ".png"))
    assert [path.name for path in images] == ["backdrop.jpg", "fanart.jpg", "poster.jpg"]
    assert all(path.read_bytes() == IMAGE_PNG for path in images)
    assert len(image_client.urls) == 3


@pytest.mark.parametrize(("source_inside", "download_tags", "expected"), [
    (False, "MANKO", True),   # tag-only side of OR
    (True, "", True),         # path-only side of OR
    (True, "MANKO", True),    # both sides may match
    (False, "", False),       # neither side matches
])
def test_tag_or_path_poll_matches_either_side_and_rejects_neither(
    offline_plugin, tmp_path, monkeypatch, source_inside, download_tags, expected,
):
    """OR 规则的标签分支与路径分支各自可独立触发，二者均不匹配时严格拒绝。"""
    configured_root = tmp_path / "configured-root"
    configured_root.mkdir()
    source_root = configured_root if source_inside else tmp_path / "outside-root"
    source_root.mkdir(exist_ok=True)
    source = source_root / "TEST-123.mkv"
    source.write_bytes(b"synthetic video bytes")
    original = (source.read_bytes(), source.stat())
    plugin_config = {
        "enabled": True,
        "sources": ["manko"],
        "storage": "local",
        "discovery_enabled": True,
        "automatic_match_mode": "tag_or_path",
        "auto_new_only": False,
        "downloader": "qbit-main",
        "torrent_tag": "MANKO",
        "recognize_dirs": [str(configured_root)],
        "handoff_mode": "discover",
    }
    offline_plugin.init_plugin(plugin_config)
    torrent = _completed_torrent(source, tag=download_tags)
    _configure_qbit_boundary(monkeypatch, offline_plugin, torrent, source)

    result = offline_plugin.poll_completed()

    assert bool(result) is expected
    if expected:
        assert len(result) == 1
        assert result[0]["path"] == str(source)
    else:
        assert result == []
        assert source.read_bytes() == original[0]
        after = source.stat()
        assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
            original[1].st_ino, original[1].st_size, original[1].st_mtime_ns,
        )


@pytest.mark.parametrize(("match_mode", "tag", "inside_root", "expected"), [
    ("path", "", True, True),
    ("tag_and_path", "", True, False),
])
def test_path_match_mode_requires_path_without_tag_and_negative_leaves_files_untouched(
    offline_plugin, tmp_path, monkeypatch, sample_payload, match_mode, tag, inside_root, expected,
    real_transfer_query_service,
    native_host_lifecycle, native_transfer_chain,
):
    """路径模式可不设标签；AND 模式的缺标签任务不进入账本或改动文件。"""
    source_root = tmp_path / "allowed-source"
    source_root.mkdir()
    outside = tmp_path / "unmatched-source"
    outside.mkdir()
    source = (source_root if inside_root else outside) / "TEST-123.mkv"
    source.write_bytes(b"synthetic video bytes")
    before = (source.read_bytes(), source.stat())
    target_root = tmp_path / "library"
    target_root.mkdir()
    plugin_config = {
        "enabled": True,
        "sources": ["manko"],
        "storage": "local",
        "discovery_enabled": True,
        "automatic_match_mode": match_mode,
        "auto_new_only": False,
        "downloader": "qbit-main",
        "torrent_tag": "" if match_mode == "path" else "MANKO",
        "recognize_dirs": [str(source_root)],
        "handoff_mode": "execute",
        "target_path": str(target_root),
        "transfer_type": "link",
        "ownership_ack": True,
        "auxiliary_ack": True,
        "image_hosts": [],
    }
    offline_plugin.init_plugin(plugin_config)
    offline_plugin._provider = OfflineMankoProvider(sample_payload)
    torrent = _completed_torrent(source, tag="")
    _configure_qbit_boundary(monkeypatch, offline_plugin, torrent, source)

    result = offline_plugin.poll_completed()

    records = offline_plugin._handoff.ledger.list_records(limit=10)
    assert bool(result) is expected
    assert bool(records) is expected
    if expected:
        assert records[0]["payload"]["path"] == str(source)
        target = Path(records[0]["payload"]["preview_target"])
        deadline = time.monotonic() + 10
        status = offline_plugin._handoff.reconcile_record(records[0]["key"])
        while time.monotonic() < deadline:
            nfo_files = list(target.parent.glob("*.nfo"))
            if target.exists() and nfo_files and status["state"] == "completed":
                break
            status = offline_plugin._handoff.reconcile_record(records[0]["key"])
            time.sleep(0.05)
        assert status["state"] == "completed", status
        original = before[1]
        assert (target.stat().st_dev, target.stat().st_ino) == (original.st_dev, original.st_ino)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == hashlib.sha256(before[0]).hexdigest()
        after = source.stat()
        assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
            original.st_ino, original.st_size, original.st_mtime_ns,
        )
        _assert_nfo(next(target.parent.glob("*.nfo")))
    else:
        assert list(target_root.iterdir()) == []
        assert source.read_bytes() == before[0]
        after = source.stat()
        assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
            before[1].st_ino, before[1].st_size, before[1].st_mtime_ns,
        )
