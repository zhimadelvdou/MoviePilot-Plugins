"""Synthetic offline tests for the bounded ATTACKERS metadata supplement."""
import asyncio

from app.plugins.xiaojiejiescraping.official_metadata import OfficialMetadataProvider
from app.plugins.xiaojiejiescraping.provider import MankoProvider


PAGE = """<!doctype html><html><head><title>AVメーカー ATTACKERS アタッカーズ 公式 Synthetic work</title></head><body>
<p>AVメーカー【ATTACKERS（アタッカーズ）】公式サイト</p>
<div class="p-workPage__table">
 <div class="item"><div class="th">品番</div><div class="td"><p><span>DVD</span> ADN000765</p></div></div>
 <div class="item"><div class="th">監督</div><div class="td"><p>Synthetic Director</p></div></div>
 <div class="item"><div class="th">収録時間</div><div class="td"><p><span>DVD</span> 120分</p></div></div>
</div></body></html>"""


def document(html=PAGE):
    from app.plugins.xiaojiejiescraping.direct_secondary import _document
    return _document(html)


def test_official_parser_extracts_only_exact_labeled_supplement():
    provider = OfficialMetadataProvider()

    result = provider._parse(document(), "ADN-000765")

    assert result == {
        "directors": [{"name": "Synthetic Director"}],
        "runtime_minutes": 120,
        "production_companies": [{"name": "アタッカーズ"}],
    }


def test_official_parser_rejects_catalog_mismatch_and_conflicting_ids():
    provider = OfficialMetadataProvider()
    mismatch = PAGE.replace("ADN000765", "ADN765")
    conflict = PAGE.replace("ADN000765", "ADN000765 ADN000766")
    suffix_collision = PAGE.replace("ADN000765", "ADN000765X")
    mixed_catalogs = PAGE.replace("ADN000765", "ADN000765 RBD-123")

    assert provider._parse(document(mismatch), "ADN-000765") is None
    assert provider._parse(document(conflict), "ADN-000765") is None
    assert provider._parse(document(suffix_collision), "ADN-000765") is None
    assert provider._parse(document(mixed_catalogs), "ADN-000765") is None


def test_official_parser_ignores_unlabeled_recommendations_and_unproven_studio():
    provider = OfficialMetadataProvider()
    page = PAGE.replace("<title>AVメーカー ATTACKERS アタッカーズ 公式 Synthetic work</title>", "<title>Synthetic work</title>")
    page = page.replace("<div class=\"item\"><div class=\"th\">監督</div><div class=\"td\"><p>Synthetic Director</p></div></div>",
                        "<div class=\"item\"><div class=\"th\">企画</div><div class=\"td\"><p>Fake Director</p></div></div>")

    assert provider._parse(document(page), "ADN-000765") == {"runtime_minutes": 120}


def test_official_parser_fails_closed_for_gate_or_missing_identity():
    provider = OfficialMetadataProvider()

    assert provider._parse(None, "ADN-000765") is None
    assert provider._parse(document(PAGE.replace("Synthetic work", "403 Forbidden")), "ADN-000765") is None
    assert provider._parse(document(), "ADN-765") is None
    assert provider._parse(document(), "ABP-000765") is None


def test_official_lookup_uses_fixed_path_and_preserves_leading_zeroes(monkeypatch):
    provider = OfficialMetadataProvider()
    calls = []
    monkeypatch.setattr(provider, "_load", lambda path: (calls.append(path) or document(), provider.base + path))

    result = provider.supplement("ADN-000765")

    assert calls == ["/works/detail/ADN000765"]
    assert result["runtime_minutes"] == 120


def test_official_supplement_projects_only_a_complete_single_actress_row(monkeypatch):
    provider = OfficialMetadataProvider()
    row = ('<div class="item"><div class="th">女優</div><div class="td">'
           '<a href="https://attackers.net/actress/detail/123">栗山莉緒</a>'
           '<a href="https://attackers.net/actress/detail/456">美園和花</a>'
           '</div></div>')
    monkeypatch.setattr(provider, "_load", lambda _path: (document(PAGE.replace('</div></body>', row + '</div></body>')), provider.base))

    result = provider.supplement("ADN-000765")

    assert result["_actress_evidence"] == {
        "source": "attackers-official", "number": "ADN000765", "names": ["栗山莉緒", "美園和花"],
    }


def test_official_supplement_rejects_partial_and_duplicate_actress_rows(monkeypatch):
    provider = OfficialMetadataProvider()
    partial = ('<div class="item"><div class="th">女優</div><div class="td">'
               '<a href="https://attackers.net/actress/detail/123">栗山莉緒</a>'
               '<a>Unknown</a></div></div>')
    duplicate = ('<div class="item"><div class="th">女優</div><div class="td">'
                 '<a href="https://attackers.net/actress/detail/123">栗山莉緒</a></div></div>'
                 '<div class="item"><div class="th">女優</div><div class="td">'
                 '<a href="https://attackers.net/actress/detail/123">栗山莉緒</a></div></div>')
    for rows in (partial, duplicate):
        monkeypatch.setattr(provider, "_load", lambda _path, rows=rows: (
            document(PAGE.replace('</div></body>', rows + '</div></body>')), provider.base))
        result = provider.supplement("ADN-000765")
        assert "_actress_evidence" not in result


def test_malformed_actress_label_row_plus_valid_row_does_not_create_unique_evidence(monkeypatch):
    provider = OfficialMetadataProvider()
    malformed = ('<div class="item"><div class="th">女優</div><div class="th">監督</div></div>')
    valid = ('<div class="item"><div class="th">女優</div><div class="td">'
             '<a href="https://attackers.net/actress/detail/123">栗山莉緒</a></div></div>')
    rows = malformed + valid
    monkeypatch.setattr(provider, "_load", lambda _path: (
        document(PAGE.replace('</div></body>', rows + '</div></body>')), provider.base))

    result = provider.supplement("ADN-000765")

    assert "_actress_evidence" not in result


def test_official_detail_redirect_policy_rejects_other_hosts_and_catalog_families():
    provider = OfficialMetadataProvider()

    assert provider._safe_detail_url("https://example.test/works/detail/ADN000765") is None
    assert provider._safe_detail_url("/works/detail/RBD000765") is None
    assert provider._safe_detail_url("/works/detail/ADN000765?next=https://example.test") is None


def test_non_adn_and_async_lookup_never_call_sync_transport(monkeypatch):
    provider = OfficialMetadataProvider()
    monkeypatch.setattr(provider, "_load", lambda _path: (_ for _ in ()).throw(AssertionError("sync transport")))
    calls = []

    assert provider.supplement("ABP-000765") is None

    async def load(path):
        calls.append(path)
        return document(), provider.base + path

    monkeypatch.setattr(provider, "_aload", load)
    result = asyncio.run(provider.async_supplement("ADN-000765"))
    assert calls == ["/works/detail/ADN000765"]
    assert result["runtime_minutes"] == 120


def test_supplement_is_nonfatal_and_does_not_mutate_manko_identity_or_raw_record():
    client = MankoProvider()
    raw = {"_id": "fixture-id", "number": "ADN-000765", "title": "Synthetic title"}
    packet = {"record": raw, "number": "ADN-000765", "actors": []}

    class FailedOfficialLookup:
        def supplement(self, _number):
            raise OSError("offline")

    client._official_metadata = FailedOfficialLookup()
    result = client._supplement(packet, "ADN-000765")
    assert result["record"] == raw
    assert packet["record"] == raw

    class OfficialLookup:
        def supplement(self, _number):
            return {"directors": [{"name": "Synthetic Director"}], "runtime_minutes": 120}

    client._official_metadata = OfficialLookup()
    result = client._supplement(packet, "ADN-000765")
    assert result["record"]["_id"] == "fixture-id"
    assert result["record"]["number"] == "ADN-000765"
    assert result["record"]["title"] == "Synthetic title"
    assert result["record"]["directors"] == [{"name": "Synthetic Director"}]
    assert result["record"]["runtime_minutes"] == 120
    assert "directors" not in packet["record"]


def test_async_supplement_uses_only_async_official_lookup():
    client = MankoProvider()

    class AsyncOfficialLookup:
        def supplement(self, _number):
            raise AssertionError("sync official transport called")

        async def async_supplement(self, _number):
            return {"runtime_minutes": 120}

    client._official_metadata = AsyncOfficialLookup()
    packet = {"record": {"_id": "fixture-id", "number": "ADN-000765"}, "number": "ADN-000765", "actors": []}

    result = asyncio.run(client._async_supplement(packet, "ADN-000765"))
    assert result["record"]["runtime_minutes"] == 120
