"""Offline tests for bounded, allowlisted landscape-backdrop selection."""
import asyncio
from io import BytesIO
import struct
import threading
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

# Reuse the established real V3 plugin fixture and its offline FakeProvider.
pytest_plugins = ("test_javrecognize_plugin",)

from app.plugins.xiaojiejiescraping.transport import AnonymousRequestUtils


COVER = "https://images.example.test/poster.png"
PORTRAIT = "https://art.example.test/portrait.png"
LANDSCAPE = "https://art.example.test/landscape.png"
UNAPPROVED = "https://not-approved.example.test/landscape.png"


def _png_header(width, height):
    def chunk(kind, data):
        return (struct.pack("!I", len(data)) + kind + data
                + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanline = b"\x00" + b"\x20\x40\x60" * width
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(scanline))


def _jpeg(width, height):
    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (width, height), (40, 80, 120)).save(image, format="JPEG")
    return image.getvalue()


class ProbeResponse:
    status_code = 200

    def __init__(self, content, content_type="image/png"):
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size > 0
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        self.closed = True


def prepare_plugin(plugin, sample_payload, scope_context, monkeypatch, backdrops,
                   responses=None, hosts=("images.example.test", "art.example.test")):
    plugin._provider.payload["record"]["cover_image"] = COVER
    plugin._provider.payload["record"]["backdrop_images"] = list(backdrops)
    plugin._image_hosts = hosts
    payloads = responses or {}
    calls = []
    response_objects = []

    def get_res(_client, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        assert kwargs.get("stream") is True
        calls.append(url)
        content, content_type = payloads.get(url, (_png_header(800, 534), "image/png"))
        response = ProbeResponse(content, content_type)
        response_objects.append(response)
        return response

    monkeypatch.setattr(AnonymousRequestUtils, "get_res", get_res)
    metadata = SimpleNamespace(org_string="TEST-123 synthetic", type=None)
    media = plugin.recognize_scoped(scope_context, meta=metadata, media_source="manko", cache=True)
    assert media is not None
    return media, calls, response_objects


def test_callbacks_select_landscape_after_cover_and_portrait_and_share_probe_memo(
    plugin, sample_payload, scope_context, monkeypatch,
):
    media, calls, responses = prepare_plugin(
        plugin, sample_payload, scope_context, monkeypatch, [COVER, PORTRAIT, LANDSCAPE],
        {PORTRAIT: (_png_header(147, 200), "image/png"), LANDSCAPE: (_png_header(800, 534), "image/png")},
    )

    images = plugin.metadata_img(media)
    plugin.obtain_images(media)

    assert images == {"poster.jpg": COVER, "fanart.jpg": LANDSCAPE}
    assert media.poster_path == COVER
    assert media.backdrop_path == LANDSCAPE
    assert calls == [PORTRAIT, LANDSCAPE]
    assert all(response.closed for response in responses)


def test_real_jpeg_headers_skip_portrait_and_select_horizontal_image(
    plugin, sample_payload, scope_context, monkeypatch,
):
    portrait = "https://art.example.test/portrait.jpg"
    landscape = "https://art.example.test/landscape.jpg"
    media, calls, _responses = prepare_plugin(
        plugin, sample_payload, scope_context, monkeypatch, [portrait, landscape],
        {portrait: (_jpeg(600, 900), "image/jpeg"),
         landscape: (_jpeg(1280, 720), "image/jpeg")},
    )

    assert plugin.metadata_img(media)["fanart.jpg"] == landscape
    assert calls == [portrait, landscape]


@pytest.mark.parametrize(("backdrops", "responses", "expected_calls"), [
    ([PORTRAIT], {PORTRAIT: (_png_header(147, 200), "image/png")}, [PORTRAIT]),
    ([LANDSCAPE], {LANDSCAPE: (_png_header(800, 534), "text/html")}, [LANDSCAPE]),
    ([LANDSCAPE], {LANDSCAPE: (_png_header(9000, 5000), "image/png")}, [LANDSCAPE]),
    ([UNAPPROVED], {}, []),
])
def test_no_eligible_or_unsafe_candidate_means_poster_only(
    plugin, sample_payload, scope_context, monkeypatch, backdrops, responses, expected_calls,
):
    media, calls, _responses = prepare_plugin(
        plugin, sample_payload, scope_context, monkeypatch, backdrops, responses,
    )

    assert plugin.metadata_img(media) == {"poster.jpg": COVER}
    assert calls == expected_calls
    assert all("not-approved.example.test" not in url for url in calls)


def test_probe_memo_is_scoped_to_config_and_host_revocation_revalidates(
    plugin, sample_payload, scope_context, monkeypatch,
):
    media, calls, _responses = prepare_plugin(
        plugin, sample_payload, scope_context, monkeypatch, [PORTRAIT, LANDSCAPE],
        {PORTRAIT: (_png_header(147, 200), "image/png"), LANDSCAPE: (_png_header(800, 534), "image/png")},
    )
    assert plugin.metadata_img(media)["fanart.jpg"] == LANDSCAPE
    assert plugin.metadata_img(media)["fanart.jpg"] == LANDSCAPE
    assert calls == [PORTRAIT, LANDSCAPE]

    payload = plugin._provider.payload
    plugin.init_plugin({"enabled": True, "recognize_dirs": [str(Path(scope_context.path).parent)],
                        "image_hosts": ["images.example.test"]})
    from test_javrecognize_plugin import FakeProvider
    plugin._provider = FakeProvider(payload)
    plugin._provider.payload["record"]["cover_image"] = COVER
    plugin._provider.payload["record"]["backdrop_images"] = [LANDSCAPE]
    metadata = SimpleNamespace(org_string="TEST-123 synthetic", type=None)
    reloaded = plugin.recognize_scoped(scope_context, meta=metadata, media_source="manko", cache=True)

    assert reloaded is not None
    assert plugin.metadata_img(reloaded) == {"poster.jpg": COVER}
    assert calls == [PORTRAIT, LANDSCAPE]


def test_host_revocation_during_probe_does_not_return_stale_artwork(
    plugin, sample_payload, scope_context, monkeypatch,
):
    media, calls, _responses = prepare_plugin(
        plugin, sample_payload, scope_context, monkeypatch, [LANDSCAPE],
    )
    original_get_res = AnonymousRequestUtils.get_res

    def revoke_during_probe(client, url, **kwargs):
        response = original_get_res(client, url, **kwargs)
        with plugin._lock:
            plugin._generation += 1
            plugin._image_hosts = ("images.example.test",)
        return response

    monkeypatch.setattr(AnonymousRequestUtils, "get_res", revoke_during_probe)

    assert plugin.metadata_img(media) is None
    assert calls == [LANDSCAPE]


def test_async_obtain_images_runs_header_probes_off_the_event_loop_thread(
    plugin, sample_payload, scope_context, monkeypatch,
):
    media, calls, _responses = prepare_plugin(
        plugin, sample_payload, scope_context, monkeypatch, [LANDSCAPE],
    )
    loop_thread = threading.get_ident()
    probe_threads = []
    # Preserve the same HTTP-boundary fixture while recording the executing thread.
    def fixture_get_res(_client, url, **kwargs):
        assert kwargs.get("allow_redirects") is False and kwargs.get("stream") is True
        probe_threads.append(threading.get_ident())
        return ProbeResponse(_png_header(800, 534))

    monkeypatch.setattr(AnonymousRequestUtils, "get_res", fixture_get_res)
    result = asyncio.run(plugin.async_obtain_images(media))

    assert result.backdrop_path == LANDSCAPE
    assert probe_threads and all(thread != loop_thread for thread in probe_threads)


def test_candidate_probe_count_is_bounded(plugin, sample_payload, scope_context, monkeypatch):
    candidates = [f"https://art.example.test/{index}.png" for index in range(12)]
    responses = {url: (_png_header(147, 200), "image/png") for url in candidates}
    media, calls, _responses = prepare_plugin(
        plugin, sample_payload, scope_context, monkeypatch, candidates, responses,
    )

    assert plugin.metadata_img(media) == {"poster.jpg": COVER}
    assert calls == candidates[:8]
