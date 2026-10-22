"""番号、原生身份、JSON 缓存及安全刮削输出的离线契约。"""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from xml.etree import ElementTree as ET

import pytest

# 生产命名空间由共享测试引导动态扩展，静态分析无法发现仓库外插件。
from app.plugins.xiaojiejiescraping import core  # pylint: disable=import-error,no-name-in-module


@pytest.mark.parametrize(("raw", "expected"), [
    ("test-123", "TEST-123"), (" TEST_00123 ", "TEST-00123"),
    ("test 123", "TEST-123"), ("FC2-1234567", "FC2-PPV-1234567"),
    ("FC2_PPV_1234567", "FC2-PPV-1234567"),
    ("FC21234567", "FC2-PPV-1234567"),
    ("TEST-123/title", None), ("TEST-123 TEST-124", None),
    ("", None), (None, None), ("ordinary title 2026", None),
])
def test_normalize_complete_number(raw, expected):
    assert core.normalize_number(raw) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("[synthetic] TEST-123.1080p.mkv", "TEST-123"),
    ("TEST-123 test_123.mkv", "TEST-123"),
    ("TEST-123 TEST-124.mkv", None), ("Ordinary Movie (2026).mkv", None),
    ("XTEST-123Y.mkv", None),
])
def test_extract_number_requires_unambiguous_token(text, expected):
    assert core.extract_number(text) == expected


def test_exact_candidate_preserves_native_id_and_allows_duplicate_rows():
    record = {"_id": "native_alpha", "title": "TEST-123 fixture"}
    assert core.exact_candidate([record, dict(record)], "TEST-123") == record
    assert core.exact_candidate([{"_id": "TEST-123"}], "TEST-123") is None


@pytest.mark.parametrize("records", [
    [{"_id": "one", "title": "TEST-123"}, {"_id": "two", "title": "TEST-123"}],
    [{"_id": "one", "number": "TEST-123", "title": "TEST-124"}],
    [{"_id": "one", "number": "TEST-123", "title": "TEST-123 TEST-124"}],
    [{"_id": "../outside", "number": "TEST-123"}],
    [{"_id": "one", "number": "TEST-0123"}],
])
def test_exact_candidate_rejects_ambiguous_or_untrusted_records(records):
    assert core.exact_candidate(records, "TEST-123") is None


def test_json_cache_reload_deep_copy_and_expiration(tmp_path, monkeypatch):
    monkeypatch.setattr(core.time, "time", lambda: 1000)
    path = tmp_path / "cache.json"
    cache = core.JsonCache(path, ttl=10)
    original = {"media_source": "manko", "media_id": "native_123", "nested": {"value": 1}}
    cache.put("TEST-123", original)
    original["nested"]["value"] = 9
    first = cache.get("TEST-123")
    assert first["nested"]["value"] == 1
    first["nested"]["value"] = 8
    assert cache.get("TEST-123")["nested"]["value"] == 1
    restored = core.JsonCache(path, ttl=10)
    assert restored.get("TEST-123") == cache.get("TEST-123")
    assert json.loads(path.read_text())["schema"] == 1
    monkeypatch.setattr(core.time, "time", lambda: 1010)
    assert restored.get("TEST-123") is None


def test_json_cache_bounds_and_removes_temporary_files(tmp_path):
    path = tmp_path / "data" / "cache.json"
    cache = core.JsonCache(path, max_entries=2)
    for index in range(3):
        cache.put(str(index), {"index": index})
    assert cache.get("0") is None
    assert core.JsonCache(path).get("2") == {"index": 2}
    assert list(path.parent.iterdir()) == [path]


def test_json_cache_failed_atomic_replace_preserves_previous_disk_data(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    cache = core.JsonCache(path)
    cache.put("verified", {"value": 1})
    before = path.read_bytes()

    def unavailable(*_args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(core.os, "replace", unavailable)
    with pytest.raises(OSError, match="synthetic disk failure"):
        cache.put("new", {"value": 2})
    assert path.read_bytes() == before
    assert core.JsonCache(path).get("verified") == {"value": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_cache_instances_share_resolved_path_lock_and_reload_other_writes(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    first = core.JsonCache(real / "cache.json")
    second = core.JsonCache(alias / "cache.json")
    assert first._lock is second._lock
    first.put("first", {"value": 1})
    second.put("second", {"value": 2})
    assert first.get("second") == {"value": 2}
    assert second.get("first") == {"value": 1}
    assert set(json.loads((real / "cache.json").read_text())["entries"]) == {"first", "second"}


def test_same_process_cache_instances_cannot_lose_parallel_keys_or_duplicate_claims(tmp_path):
    path = tmp_path / "cache.json"
    stores = [core.JsonCache(path) for _ in range(8)]
    start = Barrier(len(stores))

    def write(index):
        start.wait()
        stores[index].put(str(index), {"index": index})
        return stores[index].put_if_absent("claimed", {"winner": index})

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        claims = list(executor.map(write, range(len(stores))))
    assert claims.count(True) == 1
    restored = core.JsonCache(path)
    assert all(restored.get(str(index)) == {"index": index} for index in range(len(stores)))
    assert restored.get("claimed")["winner"] in range(len(stores))
    assert restored.put_if_absent("claimed", {"winner": 99}) is False


def test_updating_cursor_keeps_it_recent_during_bounded_eviction(tmp_path):
    cache = core.JsonCache(tmp_path / "cache.json", max_entries=3)
    cache.put("poll_cursor", {"after": "first"})
    cache.put("old", {"value": 1})
    cache.put("second", {"value": 2})
    cache.put("poll_cursor", {"after": "latest"})
    cache.put("third", {"value": 3})
    assert cache.get("poll_cursor") == {"after": "latest"}
    assert cache.get("old") is None


@pytest.mark.parametrize("contents", [
    b"not JSON", b"\x80\x04pickle is not a supported cache", b"[]", b"null",
    b'{"schema":999,"entries":{}}',
    b'{"schema":1,"entries":{"x":{"expires":"never","value":{}}}}',
])
def test_json_cache_corrupt_or_legacy_data_fails_closed(tmp_path, contents):
    path = tmp_path / "cache.json"
    path.write_bytes(contents)
    cache = core.JsonCache(path)
    assert cache.get("x") is None


def test_projection_nfo_escapes_text_and_never_fakes_builtin_ids(sample_payload):
    record = sample_payload["record"]
    record["title_display_name"][0]["title"] += "\x01"
    info = core.project_record(record, "TEST-123", sample_payload["actors"], ("images.example.test",))
    assert info["media_source"] == "manko"
    assert info["media_id"] == "fixture_native_123"
    assert not any(info.get(key) for key in ("tmdb_id", "imdb_id", "douban_id"))
    assert info["year"] == "2026"
    info["tmdb_id"] = 123456
    info["imdb_id"] = "tt123456"
    nfo = core.render_nfo(info, "TEST-123")
    document = ET.fromstring(nfo)
    assert document.tag == "movie"
    assert document.findtext("title") == '测试 & <合成> "标题"'
    assert document.findtext("plot") == "Synthetic <plot> & text"
    assert document.findtext("actor/name") == "Synthetic <Actor> & Name"
    assert document.findtext("num") == "TEST-123"
    assert document.find("uniqueid").attrib == {"type": "manko", "default": "true"}
    assert document.findtext("uniqueid") == "fixture_native_123"
    assert "tmdb" not in nfo and "imdb" not in nfo and "\x01" not in nfo


def test_image_map_uses_fixed_safe_leaf_names(sample_payload):
    urls = core.image_map(sample_payload["record"], ("images.example.test",))
    assert set(urls) == {"poster.jpg"}
    assert all(Path(name).name == name for name in urls)
    assert urls["poster.jpg"] == sample_payload["record"]["cover_image"]


@pytest.mark.parametrize("url", [
    "http://images.example.test/image.jpg", "https://evil.example/image.jpg",
    "https://images.example.test.evil.example/image.jpg",
    "https://user:pass@images.example.test/image.jpg",
    "https://images.example.test:444/image.jpg", "https://127.0.0.1/image.jpg",
    "https://[::1]/image.jpg", "https://localhost/image.jpg",
    "https://host.local/image.jpg", "file:///etc/passwd", "data:image/png;base64,abc",
    "https://images.example.test/image.jpg#fragment", "https://images.example.test:bad/image.jpg",
])
def test_images_reject_unsafe_urls_even_if_host_is_allowlisted(url):
    hosts = ("images.example.test", "127.0.0.1", "::1", "localhost", "host.local")
    assert core.safe_image_url(url, hosts) is None
    assert core.image_map({"cover_image": url}, hosts) == {}


@pytest.mark.parametrize("url", [
    " https://images.example.test/image.jpg", "https://images.example.test/image.jpg ",
    "https://images.example.test/im age.jpg", "https://images.example.test/im\tage.jpg",
    "https://images.example.test/im\nage.jpg", "https://images.example.test/im\rage.jpg",
    "https://images.example.test/im\x00age.jpg", "https://images.example.test/im\x1fage.jpg",
    "https://images.example.test/im\x7fage.jpg", "https://images.example.test/im\x85age.jpg",
    "https://images.example.test/im\u00a0age.jpg", "https://images.example.test/im\\age.jpg",
])
def test_raw_invalid_image_urls_are_rejected_before_parser_normalization(sample_payload, url):
    allowed = ("images.example.test",)
    assert core.safe_image_url(url, allowed) is None
    info = core.project_record(sample_payload["record"], "TEST-123", [{"name": "Synthetic actor", "profile_path": url}], allowed)
    assert info["actors"][0]["profile_path"] is None
    assert ET.fromstring(core.render_nfo(info, "TEST-123")).find("actor/thumb") is None


def test_nfo_sanitizes_all_xml_text_fields_including_actor_thumb_and_identity():
    info = {
        "title": "A & <B>\x00", "media_source": "testsource\x01", "media_id": "native\x02",
        "actors": [{"name": "Synthetic\x03 Actor", "profile_path": "https://images.example.test/a\x00.jpg?x=1&y=2"}],
    }
    document = ET.fromstring(core.render_nfo(info, "TEST-123"))
    assert document.findtext("title") == "A & <B>"
    assert document.find("uniqueid").attrib["type"] == "testsource"
    assert document.findtext("uniqueid") == "native"
    assert document.findtext("actor/name") == "Synthetic Actor"
    assert document.findtext("actor/thumb") == "https://images.example.test/a.jpg?x=1&y=2"
