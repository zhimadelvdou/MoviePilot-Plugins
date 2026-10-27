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
