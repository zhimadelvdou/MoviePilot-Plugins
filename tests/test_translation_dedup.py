"""Synthetic regression for Manko records differing only by translations."""
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


def test_translation_only_equivalent_records_merge_on_public_sync_and_async_search(translation_replay):
    state = translation_replay
    originals = deepcopy(state["records"])
    client = provider.MankoProvider(timeout=7)

    sync_search = client.search("TEST-775")
    async_search = asyncio.run(client.async_search("TEST-775"))
    sync_matches = client.search_matches("TEST-775")
    async_matches = asyncio.run(client.async_search_matches("TEST-775"))

    for packet in (sync_search, async_search):
        assert packet is not None
        assert packet["record"]["_id"] == state["ids"][0]
        assert packet["actors"] == state["actors"]
        assert packet["record"]["title_display_name"] == state["records"][state["ids"][1]]["title_display_name"]
        assert packet["record"]["discription"] == state["records"][state["ids"][1]]["discription"]
    for packets in (sync_matches, async_matches):
        assert len(packets) == 1
        assert packets[0]["record"]["_id"] == state["ids"][0]
        assert packets[0]["record"]["title_display_name"] == state["records"][state["ids"][1]]["title_display_name"]
        assert packets[0]["record"]["discription"] == state["records"][state["ids"][1]]["discription"]
    assert state["records"] == originals


@pytest.mark.parametrize("asynchronous", [False, True])
def test_equal_nonempty_translation_arrays_still_validate_duplicate_languages(translation_replay, asynchronous):
    state = translation_replay
    duplicate = [
        {"language": "en", "title": "Synthetic title"},
        {"language": "EN", "title": "Synthetic title"},
    ]
    records = [deepcopy(record) for record in state["records"].values()]
    for record in records:
        record["title_display_name"] = deepcopy(duplicate)
    _publish_records(state, records)
    client = provider.MankoProvider(timeout=7)
    if asynchronous:
        result = asyncio.run(client.async_search("TEST-775"))
        matches = asyncio.run(client.async_search_matches("TEST-775"))
    else:
        result = client.search("TEST-775")
        matches = client.search_matches("TEST-775")
    assert result is None
    assert len(matches) == 2


@pytest.mark.parametrize("asynchronous", [False, True])
def test_language_codes_casefold_before_same_language_conflict_check(translation_replay, asynchronous):
    state = translation_replay
    records = [deepcopy(record) for record in state["records"].values()]
    records[0]["discription"] = [
        {"language": "JA", "overview": "Shared Japanese overview."},
        {"language": "zh", "overview": "First Chinese overview."},
    ]
    records[1]["discription"] = [
        {"language": "ja", "overview": "Shared Japanese overview."},
        {"language": "ZH", "overview": "Different Chinese overview."},
    ]
    _publish_records(state, records)
    client = provider.MankoProvider(timeout=7)
    if asynchronous:
        result = asyncio.run(client.async_search("TEST-775"))
        matches = asyncio.run(client.async_search_matches("TEST-775"))
    else:
        result = client.search("TEST-775")
        matches = client.search_matches("TEST-775")
    assert result is None
    assert len(matches) == 2


@pytest.mark.parametrize("asynchronous", [False, True])
def test_translation_item_with_unrecognized_shape_stays_ambiguous(translation_replay, asynchronous):
    state = translation_replay
    records = [deepcopy(record) for record in state["records"].values()]
    for record in records:
        record["discription"][0]["unexpected"] = "Synthetic unknown field"
    _publish_records(state, records)
    client = provider.MankoProvider(timeout=7)
    if asynchronous:
        result = asyncio.run(client.async_search("TEST-775"))
        matches = asyncio.run(client.async_search_matches("TEST-775"))
    else:
        result = client.search("TEST-775")
        matches = client.search_matches("TEST-775")
    assert result is None
    assert len(matches) == 2


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("empty_shape", ["missing", "empty"])
def test_flat_title_fallback_preserves_missing_or_empty_title_translation_field(
        translation_replay, asynchronous, empty_shape):
    state = translation_replay
    records = [deepcopy(record) for record in state["records"].values()]
    for record in records:
        if empty_shape == "missing":
            record.pop("title_display_name")
        else:
            record["title_display_name"] = []
    _publish_records(state, records)
    client = provider.MankoProvider(timeout=7)
    if asynchronous:
        packet = asyncio.run(client.async_search("TEST-775"))
        matches = asyncio.run(client.async_search_matches("TEST-775"))
    else:
        packet = client.search("TEST-775")
        matches = client.search_matches("TEST-775")
    assert packet is not None and len(matches) == 1
    merged = packet["record"]
    if empty_shape == "missing":
        assert "title_display_name" not in merged
    else:
        assert merged["title_display_name"] == []
    assert merged["title"] == records[0]["title"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("title_shape", ["translated", "missing", "empty"])
def test_plugin_manual_search_and_recognition_keep_flat_title_fallback(
        translation_replay, monkeypatch, tmp_path, plugin_host_context, asynchronous, title_shape):
    state = translation_replay
    for identity, record in state["records"].items():
        if title_shape == "missing":
            record.pop("title_display_name")
        elif title_shape == "empty":
            record["title_display_name"] = []
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


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("group_conflict", ["no_common_language", "same_language_conflict"])
def test_group_wide_translation_consistency_is_required(translation_replay, asynchronous, group_conflict):
    state = translation_replay
    third_id = "000000000000000000000003"
    first_id, second_id = state["ids"]
    third = deepcopy(state["records"][first_id])
    third["_id"] = third_id
    if group_conflict == "no_common_language":
        state["records"][first_id]["discription"] = [
            {"language": "ja", "overview": "Synthetic Japanese overview."},
        ]
        state["records"][second_id]["discription"] = [
            {"language": "ja", "overview": "Synthetic Japanese overview."},
            {"language": "ko", "overview": "Synthetic Korean overview."},
        ]
        third["discription"] = [
            {"language": "ko", "overview": "Synthetic Korean overview."},
            {"language": "th", "overview": "Synthetic Thai overview."},
        ]
    else:
        state["records"][first_id]["discription"] = [
            {"language": "ja", "overview": "Synthetic Japanese overview."},
            {"language": "zh", "overview": "Synthetic Chinese overview."},
        ]
        state["records"][second_id]["discription"] = [
            {"language": "ja", "overview": "Synthetic Japanese overview."},
            {"language": "zh", "overview": "Synthetic Chinese overview."},
            {"language": "ko", "overview": "Synthetic Korean overview."},
        ]
        third["discription"] = [
            {"language": "ja", "overview": "Synthetic Japanese overview."},
            {"language": "zh", "overview": "Different synthetic Chinese overview."},
            {"language": "ko", "overview": "Synthetic Korean overview."},
        ]
    for identity in (first_id, second_id):
        state["responses"][f"/detail/{identity}"] = {"data": deepcopy(state["records"][identity])}
    state["records"][third_id] = third
    state["responses"]["/search"] = {
        "data": [{"_id": identity} for identity in (first_id, second_id, third_id)], "total": 3,
    }
    state["responses"][f"/detail/{third_id}"] = {"data": deepcopy(third)}
    state["responses"][f"/actors/{third_id}"] = {
        "data": deepcopy(state["actors"]), "total": len(state["actors"]),
    }
    client = provider.MankoProvider(timeout=7)
    if asynchronous:
        result = asyncio.run(client.async_search("TEST-775"))
        matches = asyncio.run(client.async_search_matches("TEST-775"))
    else:
        result = client.search("TEST-775")
        matches = client.search_matches("TEST-775")
    assert result is None
    assert len(matches) == 3
    assert {packet["record"]["_id"] for packet in matches} == {first_id, second_id, third_id}
