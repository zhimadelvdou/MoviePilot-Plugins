"""Public-provider regressions for complete same-number record selection."""
import asyncio
from copy import deepcopy

import pytest

from app.plugins.xiaojiejiescraping import XiaojiejieScraping  # pylint: disable=import-error,no-name-in-module
from app.plugins.xiaojiejiescraping import provider  # pylint: disable=import-error,no-name-in-module
from app.sdk.network import AsyncRequestUtils, RequestUtils
from app.schemas.types import MediaType
from types import SimpleNamespace


class ReplayResponse:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def json(self):
        return deepcopy(self.payload)

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


@pytest.fixture
def translation_replay(monkeypatch):
    ids = ("000000000000000000000001", "000000000000000000000002")
    shared_descriptions = [
        {"language": "ja", "overview": "Synthetic Japanese overview."},
        {"language": "ko", "overview": "Synthetic Korean overview."},
        {"language": "en", "overview": "Synthetic English overview."},
        {"language": "zh", "overview": "Synthetic Chinese overview."},
    ]
    common = {
        "number": "TEST-775",
        "title": "TEST-775 synthetic title",
        "title_display_name": [
            {"language": "ja", "title": "Synthetic Japanese title"},
            {"language": "en", "title": "TEST-775 synthetic title"},
        ],
        "discription": deepcopy(shared_descriptions),
        "share_date": "2026-05-01",
        "cover_image": "https://images.example.test/synthetic-cover.jpg",
        "backdrop_images": ["https://images.example.test/synthetic-backdrop.jpg"],
        "video_duration": 120,
        "tags": ["synthetic-tag"],
        "rating": "synthetic-rating",
    }
    records = {
        ids[0]: {**deepcopy(common), "_id": ids[0]},
        ids[1]: {
            **deepcopy(common),
            "_id": ids[1],
            "title_display_name": deepcopy(common["title_display_name"]) + [
                {"language": "zh", "title": "Synthetic Chinese title"}
            ],
            "discription": deepcopy(shared_descriptions) + [
                {"language": "th", "overview": "Synthetic Thai overview."}
            ],
        },
    }
    actors = [{"name": "Synthetic Actor", "aliases": ["Synthetic Alias"]}]
    responses = {
        "/search": {"data": [{"_id": identity} for identity in ids], "total": len(ids)},
        **{f"/detail/{identity}": {"data": deepcopy(record)} for identity, record in records.items()},
        **{f"/actors/{identity}": {"data": deepcopy(actors), "total": len(actors)} for identity in ids},
    }
    state = {"ids": ids, "records": records, "actors": actors, "responses": responses, "calls": [], "opened": []}

    def replay(client, method, url, **kwargs):
        assert method == "get"
        assert url.startswith(provider.API + "/")
        suffix = url.removeprefix(provider.API)
        assert suffix in state["responses"], f"unexpected HTTP request: {suffix}"
        response = ReplayResponse(state["responses"][suffix])
        state["calls"].append(suffix)
        state["opened"].append(response)
        return response

    def sync_request(client, method, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        return replay(client, method, url, **kwargs)

    async def async_request(client, method, url, **kwargs):
        assert kwargs.get("follow_redirects") is False
        return replay(client, method, url, **kwargs)

    monkeypatch.setattr(RequestUtils, "request", sync_request)
    monkeypatch.setattr(AsyncRequestUtils, "request", async_request)
    yield state
    assert all(response.closed for response in state["opened"])


def _publish_records(state, records):
    state["records"] = {record["_id"]: deepcopy(record) for record in records}
    state["responses"]["/search"] = {
        "data": [{"_id": identity} for identity in state["records"]],
        "total": len(state["records"]),
    }
    for identity, record in state["records"].items():
        state["responses"][f"/detail/{identity}"] = {"data": deepcopy(record)}
        state["responses"][f"/actors/{identity}"] = {
            "data": deepcopy(state["actors"]), "total": len(state["actors"]),
        }


def test_all_public_search_entrypoints_choose_first_complete_api_match_without_merging(translation_replay):
    state = translation_replay
    first_id, second_id = state["ids"]
    first_record = state["records"][second_id]
    first_record.update({
        "title": "First API row has its own title",
        "title_display_name": [{"language": "en", "title": "First API row title"}],
        "discription": [{"language": "en", "overview": "First API row overview"}],
        "share_date": "2026-05-02",
        "cover_image": "https://images.example.test/first-api-cover.jpg",
        "backdrop_images": ["https://images.example.test/first-api-background.jpg"],
    })
    state["responses"][f"/detail/{second_id}"] = {"data": deepcopy(first_record)}
    first_actors = [{"name": "First API actor", "avdbs_actor": True}]
    state["responses"]["/search"] = {
        "data": [{"_id": second_id}, {"_id": first_id}], "total": 2,
    }
    state["responses"][f"/actors/{second_id}"] = {"data": deepcopy(first_actors), "total": 1}
    client = provider.MankoProvider(timeout=7)

    sync_search = client.search("TEST-775")
    async_search = asyncio.run(client.async_search("TEST-775"))
    sync_matches = client.search_matches("TEST-775")
    async_matches = asyncio.run(client.async_search_matches("TEST-775"))

    for packet in (sync_search, async_search):
        assert packet is not None
        assert packet["record"] == first_record
        assert packet["actors"] == first_actors
    for packets in (sync_matches, async_matches):
        assert len(packets) == 1
        assert packets[0]["record"] == first_record
        assert packets[0]["actors"] == first_actors


def _call_public_entrypoint(client, asynchronous, entrypoint):
    if asynchronous:
        method = getattr(client, "async_" + entrypoint)
        return asyncio.run(method("TEST-775"))
    return getattr(client, entrypoint)("TEST-775")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("entrypoint", ["search", "search_matches"])
@pytest.mark.parametrize("missing_field", ["title", "descriptions", "date", "cover", "background", "actors"])
def test_incomplete_first_match_falls_back_to_next_complete_packet(
        translation_replay, asynchronous, entrypoint, missing_field):
    state = translation_replay
    first_id, second_id = state["ids"]
    first = deepcopy(state["records"][first_id])
    if missing_field == "title":
        first["title_display_name"] = [
            {"language": "en", "title": "Valid row cannot hide malformed row"},
            {"language": "ja"},
        ]
    elif missing_field == "descriptions":
        first["discription"] = [
            {"language": "en", "overview": "Valid row cannot hide malformed row"},
            {"overview": "Missing language"},
        ]
    elif missing_field == "date":
        first["share_date"] = "not-an-ISO-date"
    elif missing_field == "cover":
        first["cover_image"] = "http://images.example.test/insecure.jpg"
    elif missing_field == "background":
        first["backdrop_images"] = [first["cover_image"]]
    else:
        state["responses"][f"/actors/{first_id}"] = {"data": [], "total": 0}
    state["responses"][f"/detail/{first_id}"] = {"data": first}
    client = provider.MankoProvider(timeout=7)

    result = _call_public_entrypoint(client, asynchronous, entrypoint)

    packet = result[0] if entrypoint == "search_matches" and result else result
    assert packet is not None
    assert packet["record"]["_id"] == second_id
    assert packet["record"] == state["records"][second_id]
    assert packet["actors"] == state["actors"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("entrypoint", ["search", "search_matches"])
def test_no_complete_match_returns_none_and_fixed_missing_fields(
        translation_replay, caplog, asynchronous, entrypoint):
    state = translation_replay
    records = [deepcopy(record) for record in state["records"].values()]
    for record in records:
        record["share_date"] = "not-an-ISO-date"
    _publish_records(state, records)
    client = provider.MankoProvider(timeout=7)

    result = _call_public_entrypoint(client, asynchronous, entrypoint)

    assert result is None if entrypoint == "search" else result == []
    assert "missing=date" in caplog.text


@pytest.mark.parametrize("asynchronous", [False, True])
def test_multi_match_with_incomplete_search_total_still_fails_closed(translation_replay, asynchronous):
    state = translation_replay
    state["responses"]["/search"]["total"] = 3
    client = provider.MankoProvider(timeout=7)

    assert _call_public_entrypoint(client, asynchronous, "search") is None
    assert _call_public_entrypoint(client, asynchronous, "search_matches") == []
    assert all(not path.startswith("/detail/") for path in state["calls"])


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("empty_shape", ["missing", "empty", "null"])
def test_flat_title_fallback_preserves_missing_or_empty_translation_field(
        translation_replay, asynchronous, empty_shape):
    state = translation_replay
    records = [deepcopy(record) for record in state["records"].values()]
    for record in records:
        if empty_shape == "missing":
            record.pop("title_display_name")
        elif empty_shape == "empty":
            record["title_display_name"] = []
        else:
            record["title_display_name"] = None
    _publish_records(state, records)
    client = provider.MankoProvider(timeout=7)
    if asynchronous:
        packet = asyncio.run(client.async_search("TEST-775"))
        matches = asyncio.run(client.async_search_matches("TEST-775"))
    else:
        packet = client.search("TEST-775")
        matches = client.search_matches("TEST-775")
    assert packet is not None and len(matches) == 1
    selected = packet["record"]
    if empty_shape == "missing":
        assert "title_display_name" not in selected
    elif empty_shape == "empty":
        assert selected["title_display_name"] == []
    else:
        assert selected["title_display_name"] is None
    assert selected["title"] == records[0]["title"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("title_shape", ["translated", "missing", "empty", "null"])
def test_plugin_manual_search_and_recognition_keep_flat_title_fallback(
        translation_replay, monkeypatch, tmp_path, plugin_host_context, asynchronous, title_shape):
    state = translation_replay
    for identity, record in state["records"].items():
        if title_shape == "missing":
            record.pop("title_display_name")
        elif title_shape == "empty":
            record["title_display_name"] = []
        elif title_shape == "null":
            record["title_display_name"] = None
        state["responses"][f"/detail/{identity}"] = {"data": deepcopy(record)}

    monkeypatch.setattr(XiaojiejieScraping, "get_data_path", lambda _self: tmp_path / "plugin-data")
    plugin = XiaojiejieScraping()
    plugin.init_plugin({"enabled": True, "sources": ["manko"]})
    media_meta = SimpleNamespace(org_string="TEST-775 synthetic.mkv", type=MediaType.MOVIE)
    try:
        if asynchronous:
            manual = asyncio.run(plugin.async_search_medias(media_meta, media_source="manko"))
            recognized = asyncio.run(plugin.async_recognize_media(
                meta=media_meta, mtype=MediaType.MOVIE, media_source="manko", cache=False))
        else:
            manual = plugin.search_medias(media_meta, media_source="manko")
            recognized = plugin.recognize_media(
                meta=media_meta, mtype=MediaType.MOVIE, media_source="manko", cache=False)
        assert len(manual) == 1
        assert manual[0].media_id == state["ids"][0]
        assert manual[0].title
        assert recognized is not None
        assert recognized.media_id == state["ids"][0]
    finally:
        plugin.stop_service()
