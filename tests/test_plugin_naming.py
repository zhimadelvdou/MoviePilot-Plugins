"""JavBus file naming changes only the host formatter's owned metadata copy."""
from types import SimpleNamespace

import pytest

from app.plugins.xiaojiejiescraping import XiaojiejieScraping
from app.schemas.types import MediaType
from test_plugin_native_transfer_integration import plugin_host_context  # noqa: F401


class FakeEventManager:
    def __init__(self):
        self.listeners = {}

    def add_event_listener(self, event_type, handler, **_kwargs):
        self.listeners.setdefault(event_type, []).append(handler)

    def remove_event_listener(self, event_type, handler):
        self.listeners[event_type].remove(handler)
        if not self.listeners[event_type]:
            del self.listeners[event_type]


def _rename_event(media, **overrides):
    rename_dict = {
        "title": "SYN-123 Synthetic JavBus Work Title",
        "year": "2026",
        "part": "Part 2",
        "edition": "Extended",
        "videoFormat": "1080p",
        "__mediainfo__": media,
        "__meta__": object(),
    }
    rename_dict.update(overrides)
    return {"template_string": "{{title}}/{{title}}({{year}})-{{part}}-{{edition}}.mkv",
            "rename_dict": rename_dict, "source_path": "synthetic.mkv", "source_item": object()}


def _event_wrapper(payload):
    return SimpleNamespace(event_data=payload)


def _owned_packet(identity="SYN-123", number="SYN-123", source="javbus"):
    return {"packet": {"number": number, "record": {
        "_id": identity, "number": number, "_source": source,
    }}}


def test_javbus_rename_changes_only_formatter_title_and_preserves_metadata(
        monkeypatch, plugin_host_context):
    plugin = XiaojiejieScraping()
    plugin._enabled = True
    plugin._active_sources = ("manko", "javbus")
    media = SimpleNamespace(media_source="javbus", media_id="SYN-123", type=MediaType.MOVIE,
                            title="SYN-123 Synthetic JavBus Work Title",
                            original_title="SYN-123 Synthetic JavBus Work Title")
    monkeypatch.setattr(plugin, "_owned", lambda _media: _owned_packet())
    event = _rename_event(media)
    before = {key: value for key, value in event["rename_dict"].items() if key != "title"}

    plugin._on_transfer_rename_build(_event_wrapper(event))

    assert event["rename_dict"]["title"] == "SYN-123"
    assert {key: value for key, value in event["rename_dict"].items() if key != "title"} == before
    assert media.title == "SYN-123 Synthetic JavBus Work Title"
    assert media.original_title == "SYN-123 Synthetic JavBus Work Title"


@pytest.mark.parametrize("case", [
    "disabled", "source_unselected", "wrong_source", "wrong_type", "unowned", "ownership_exception",
    "stopped_during_ownership",
    "identity_mismatch", "record_source_mismatch", "missing_mediainfo", "missing_payload",
])
def test_javbus_rename_hook_fails_closed_for_out_of_scope_events(
        monkeypatch, case, plugin_host_context):
    plugin = XiaojiejieScraping()
    plugin._enabled = case != "disabled"
    plugin._active_sources = ("manko",) if case == "source_unselected" else ("manko", "javbus")
    source = "manko" if case == "wrong_source" else "javbus"
    media_type = MediaType.TV if case == "wrong_type" else MediaType.MOVIE
    media = SimpleNamespace(media_source=source, media_id="SYN-123", type=media_type,
                            title="SYN-123 Synthetic title", original_title="SYN-123 Synthetic title")
    if case == "identity_mismatch":
        packet = _owned_packet(identity="SYN-123", number="SYN-456")
    elif case == "record_source_mismatch":
        packet = _owned_packet(source="manko")
    else:
        packet = None if case == "unowned" else _owned_packet()
    def owned(_media):
        if case == "ownership_exception":
            raise OSError("synthetic cache read failure")
        if case == "stopped_during_ownership":
            plugin.stop_service()
        return packet

    monkeypatch.setattr(plugin, "_owned", owned)
    event = _rename_event(None if case == "missing_mediainfo" else media)
    before = dict(event["rename_dict"])

    plugin._on_transfer_rename_build(_event_wrapper(None if case == "missing_payload" else event))

    assert event["rename_dict"] == before


def test_javbus_rename_hook_registration_is_idempotent_and_removed_on_stop(
        monkeypatch, tmp_path, plugin_host_context):
    plugin = XiaojiejieScraping()
    event_manager = FakeEventManager()
    monkeypatch.setattr(plugin, "get_data_path", lambda: tmp_path)
    monkeypatch.setattr(plugin, "eventmanager", event_manager, raising=False)
    config = {"enabled": True, "sources": ["javbus"], "javbus_api_url": "http://fixture.example.test"}

    plugin.init_plugin(config)
    assert sum(map(len, event_manager.listeners.values())) == 1
    from app.schemas.types import ChainEventType
    assert list(event_manager.listeners) == [ChainEventType.TransferRenameBuild]
    plugin.init_plugin(config)
    assert sum(map(len, event_manager.listeners.values())) == 1
    assert list(event_manager.listeners) == [ChainEventType.TransferRenameBuild]

    plugin.stop_service()
    assert event_manager.listeners == {}
    plugin.init_plugin({"enabled": True, "sources": ["manko"]})
    assert event_manager.listeners == {}
    plugin.stop_service()


def test_javbus_rename_hook_skips_hosts_without_the_optional_event(
        monkeypatch, plugin_host_context):
    from app.schemas import types as schema_types

    plugin = XiaojiejieScraping()
    event_manager = FakeEventManager()
    plugin._enabled = True
    plugin._active_sources = ("javbus",)
    monkeypatch.setattr(plugin, "eventmanager", event_manager, raising=False)
    monkeypatch.delattr(schema_types, "ChainEventType")

    assert plugin._register_javbus_rename_hook() is False
    assert event_manager.listeners == {}


def test_fc2_rename_uses_owned_canonical_number_and_preserves_full_metadata(
        monkeypatch, plugin_host_context):
    plugin = XiaojiejieScraping()
    plugin._enabled = True
    plugin._active_sources = ("fc2",)
    media = SimpleNamespace(media_source="fc2", media_id="1234567", type=MediaType.MOVIE,
                            title="FC2-PPV-1234567 Complete product title",
                            original_title="FC2-PPV-1234567 Complete product title",
                            names=["FC2-PPV-1234567"])
    packet = {"packet": {"source": "fc2", "number": "FC2-PPV-1234567", "record": {
        "_id": "1234567", "_source": "fc2", "number": "FC2-PPV-1234567"}},
        "projected": {"year": "2024"}}
    monkeypatch.setattr(plugin, "_owned", lambda _media: packet)
    event = _rename_event(media)
    plugin._on_transfer_rename_build(_event_wrapper(event))
    assert event["rename_dict"]["title"] == "FC2-PPV-1234567"
    assert event["rename_dict"]["year"] == "2024"
    assert media.title == "FC2-PPV-1234567 Complete product title"
    assert media.original_title == "FC2-PPV-1234567 Complete product title"


def test_fc2_rename_requires_owned_same_source_and_number(monkeypatch, plugin_host_context):
    plugin = XiaojiejieScraping()
    plugin._enabled = True
    plugin._active_sources = ("fc2",)
    media = SimpleNamespace(media_source="fc2", media_id="1234567", type=MediaType.MOVIE,
                            names=["FC2-PPV-1234567"])
    packet = {"packet": {"source": "fc2", "number": "FC2-PPV-1234568", "record": {
        "_id": "1234567", "_source": "fc2", "number": "FC2-PPV-1234567"}}}
    monkeypatch.setattr(plugin, "_owned", lambda _media: packet)
    event = _rename_event(media)
    before = dict(event["rename_dict"])
    plugin._on_transfer_rename_build(_event_wrapper(event))
    assert event["rename_dict"] == before


def test_fc2_rename_does_not_infer_missing_year(monkeypatch, plugin_host_context):
    plugin = XiaojiejieScraping()
    plugin._enabled = True
    plugin._active_sources = ("fc2",)
    media = SimpleNamespace(media_source="fc2", media_id="1234567", type=MediaType.MOVIE,
                            names=["FC2-PPV-1234567"])
    packet = {"packet": {"source": "fc2", "number": "FC2-PPV-1234567", "record": {
        "_id": "1234567", "_source": "fc2", "number": "FC2-PPV-1234567"}},
        "projected": {"year": None}}
    monkeypatch.setattr(plugin, "_owned", lambda _media: packet)
    event = _rename_event(media)
    plugin._on_transfer_rename_build(_event_wrapper(event))
    assert event["rename_dict"]["title"] == "FC2-PPV-1234567"
    assert event["rename_dict"]["year"] == ""


def test_fc2_source_registers_formatter_hook(monkeypatch, tmp_path, plugin_host_context):
    plugin = XiaojiejieScraping()
    event_manager = FakeEventManager()
    monkeypatch.setattr(plugin, "get_data_path", lambda: tmp_path)
    monkeypatch.setattr(plugin, "eventmanager", event_manager, raising=False)
    plugin.init_plugin({"enabled": True, "sources": ["fc2"]})
    assert sum(map(len, event_manager.listeners.values())) == 1
    plugin.stop_service()
    assert event_manager.listeners == {}


def test_fc2_identity_matcher_reads_owned_canonical_number_not_alias_order(monkeypatch, plugin_host_context):
    plugin = XiaojiejieScraping()
    plugin._enabled = True
    plugin._active_sources = ("fc2",)
    media = SimpleNamespace(media_source="fc2", media_id="1234567",
                            names=["1234567", "FC2-PPV-1234567"])
    packet = {"packet": {"number": "FC2-PPV-1234567", "record": {
        "_id": "1234567", "_source": "fc2", "number": "FC2-PPV-1234567"}}}
    monkeypatch.setattr(plugin, "_owned", lambda _media: packet)
    assert plugin._fc2_identity_matches(media, None)
    assert plugin._fc2_identity_matches(media, "FC2-PPV-1234567")
    assert not plugin._fc2_identity_matches(media, "1234567")
