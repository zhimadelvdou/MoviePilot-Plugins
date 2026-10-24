"""Manko API 适配。端点/编码依据 JAVRecognize 1.1；不进行真人站点测试。"""
# SPDX-License-Identifier: GPL-3.0-only
# Derived API contract and base91 decoder: hyuan280/MoviePilot-Plugins (GPLv3).
from __future__ import annotations

import json
import math
from datetime import date
from urllib.parse import urlsplit
from .transport import AnonymousAsyncRequestUtils as AsyncRequestUtils, AnonymousRequestUtils as RequestUtils
from app.sdk.config import settings
from .core import IDENTIFIER, exact_candidate, normalize_number, record_numbers, safe_image_url
from .diagnostics import diagnose
from .official_metadata import OfficialMetadataProvider

API = "https://healertanker.com/swx/movie"
ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&()*+,./:;<=>?@[]^_`{|}~"'
def decode_payload(response: dict | None) -> dict:
    """接受原生 JSON 或上游 base91 数据；畸形和超大载荷失败关闭。"""
    if not isinstance(response, dict):
        return {}
    data = response.get("data")
    if isinstance(data, str):
        if len(data) > 2_000_000:
            return {}
        value, bits, count, output = -1, 0, 0, bytearray()
        for char in data:
            if char not in ALPHABET:
                return {}
            digit = ALPHABET.index(char)
            if value == -1:
                value = digit
                continue
            value += digit * 91
            bits |= value << count
            count += 13 if (value & 8191) > 88 else 14
            while count > 7:
                output.append(bits & 255)
                bits >>= 8
                count -= 8
            value = -1
        if value != -1:
            output.append((bits | value << count) & 255)
        try:
            data = json.loads(output.decode("utf-8"))
        except (ValueError, UnicodeError):
            return {}
    return {**response, "data": data} if isinstance(data, (list, dict)) else {}


class MankoProvider:
    """同步与异步共用校验规则；只发送提取的番号或已知原生 ID。"""
    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self._official_metadata = OfficialMetadataProvider(timeout=timeout)

    def _supplement(self, packet: dict, number: str) -> dict:
        """Add only validated missing fields; official lookup failures are nonfatal."""
        result = json.loads(json.dumps(packet))
        try:
            extra = self._official_metadata.supplement(number)
        except Exception:
            return result
        self._merge_supplement(result, extra)
        return result

    async def _async_supplement(self, packet: dict, number: str) -> dict:
        result = json.loads(json.dumps(packet))
        try:
            extra = await self._official_metadata.async_supplement(number)
        except Exception:
            return result
        self._merge_supplement(result, extra)
        return result

    @staticmethod
    def _merge_supplement(packet: dict, extra: dict | None) -> None:
        if not isinstance(extra, dict):
            return
        record = packet.get("record")
        if not isinstance(record, dict):
            return
        for key in ("directors", "production_companies"):
            current = record.get(key)
            if not isinstance(current, list) or not any(isinstance(item, dict) and item.get("name") for item in current):
                value = extra.get(key)
                if isinstance(value, list):
                    record[key] = value
        evidence = extra.get("_actress_evidence")
        expected = "ADN" + str(packet.get("number", "")).removeprefix("ADN-")
        names = evidence.get("names") if isinstance(evidence, dict) else None
        if (isinstance(evidence, dict) and evidence.get("source") == "attackers-official"
                and evidence.get("number") == expected and isinstance(names, list)
                and names and all(isinstance(name, str) and name.strip() and len(name) <= 200
                                  for name in names)):
            packet["_actress_evidence"] = {
                "source": "attackers-official", "number": expected,
                "names": list(dict.fromkeys(name.strip() for name in names)),
            }
        source_runtime = record.get("video_duration")
        fallback_runtime = record.get("runtime_minutes")
        source_valid = type(source_runtime) is int and 1 <= source_runtime <= 999
        fallback_valid = type(fallback_runtime) is int and 1 <= fallback_runtime <= 999
        if not source_valid and not fallback_valid:
            runtime = extra.get("runtime_minutes")
            if type(runtime) is int and 1 <= runtime <= 999:
                record["runtime_minutes"] = runtime

    @staticmethod
    def _decode(response, operation):
        decoded = decode_payload(response)
        if response is not None and not decoded:
            diagnose("manko", operation, "payload_invalid")
        return decoded

    def _get(self, suffix: str, params: dict | None = None) -> dict:
        operation = suffix.split("/")[1]
        response = RequestUtils(timeout=self.timeout, verify=True, proxies=settings.PROXY).get_json(
            API + suffix, params=params, allow_redirects=False,
            diagnostic_source="manko", diagnostic_operation=operation,
        )
        return self._decode(response, operation)

    async def _aget(self, suffix: str, params: dict | None = None) -> dict:
        operation = suffix.split("/")[1]
        response = await AsyncRequestUtils(timeout=self.timeout, verify=True, proxies=settings.PROXY).get_json(
            API + suffix, params=params, follow_redirects=False,
            diagnostic_source="manko", diagnostic_operation=operation,
        )
        return self._decode(response, operation)

    @staticmethod
    def _records(response: dict) -> list[dict]:
        data = response.get("data")
        total = response.get("total")
        if not isinstance(data, list) or len(data) > 24:
            if response:
                diagnose("manko", "search", "schema_invalid")
            return []
        # 截断列表无法证明唯一，不把第一页首项作为最终结果。
        if type(total) is not int or total < 0 or total != len(data):
            diagnose("manko", "search", "search_incomplete", count=len(data))
            return []
        if not all(isinstance(row, dict) for row in data):
            diagnose("manko", "search", "schema_invalid")
            return []
        diagnose("manko", "search", "candidates" if data else "no_candidates", count=len(data))
        return data

    @staticmethod
    def _detail(response: dict, identity: str) -> dict | None:
        data = response.get("data")
        if not isinstance(data, dict):
            if response:
                diagnose("manko", "detail", "schema_invalid")
            return None
        if str(data.get("_id", identity)) != identity:
            diagnose("manko", "detail", "identity_mismatch")
            return None
        return {**data, "_id": identity}

    @staticmethod
    def _select(details, number):
        """仍由原有精确匹配函数决定结果；只统计同一规则下的不同 ID。"""
        selected = exact_candidate(details, number)
        if details:
            matches = MankoProvider._matching_records(details, number)
            reason = "exact_match" if selected is not None else "ambiguous_match" if len(matches) > 1 else "no_exact_match"
            diagnose("manko", "search", reason, count=len(matches))
        return selected

    @staticmethod
    def _matching_records(details, number):
        matches = {}
        for row in details:
            identity = str(row.get("_id") or "")
            if record_numbers(row) == {number} and IDENTIFIER.fullmatch(identity):
                matches.setdefault(identity, row)
        return matches

    @staticmethod
    def _canonical_json(value):
        """Use JSON type semantics (so True differs from 1) for upstream equality."""
        def validate(node):
            if node is None or type(node) in (str, bool, int):
                return
            if type(node) is float:
                if not math.isfinite(node):
                    raise ValueError("non_finite_number")
                return
            if isinstance(node, list):
                for item in node:
                    validate(item)
                return
            if isinstance(node, dict):
                if not all(type(key) is str for key in node):
                    raise TypeError("non_string_key")
                for item in node.values():
                    validate(item)
                return
            raise TypeError("non_json_value")

        validate(value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _translation_array_valid(value, text_key):
        """Validate every supplied translation row; no language is mandatory."""
        if not isinstance(value, list) or not value:
            return False
        languages = set()
        for entry in value:
            if not isinstance(entry, dict):
                return False
            language, text = entry.get("language"), entry.get(text_key)
            if (not isinstance(language, str) or not language.strip()
                    or not isinstance(text, str) or not text.strip()):
                return False
            key = language.strip().casefold()
            if key in languages:
                return False
            languages.add(key)
        return True

    @staticmethod
    def _valid_image_url(value):
        """Check URL safety without applying a user's optional image host allowlist."""
        if not isinstance(value, str):
            return False
        try:
            host = urlsplit(value).hostname
        except ValueError:
            return False
        return bool(host and safe_image_url(value, (host.lower(),)) == value)

    @staticmethod
    def _record_missing_fields(record):
        """Return only fixed field labels needed for complete same-number selection."""
        missing = []
        titles = record.get("title_display_name")
        if isinstance(titles, list) and titles:
            has_title = MankoProvider._translation_array_valid(titles, "title")
        else:
            has_title = isinstance(record.get("title"), str) and bool(record["title"].strip())
        if not has_title:
            missing.append("title")

        descriptions = record.get("discription")
        if not MankoProvider._translation_array_valid(descriptions, "overview"):
            missing.append("descriptions")

        share_date = record.get("share_date")
        try:
            valid_date = isinstance(share_date, str) and len(share_date) >= 10
            if valid_date:
                date.fromisoformat(share_date[:10])
        except ValueError:
            valid_date = False
        if not valid_date:
            missing.append("date")

        cover = record.get("cover_image")
        valid_cover = MankoProvider._valid_image_url(cover)
        if not valid_cover:
            missing.append("cover")

        backgrounds = record.get("backdrop_images")
        valid_background = (isinstance(backgrounds, list) and valid_cover and any(
            MankoProvider._valid_image_url(url) and url != cover for url in backgrounds
        ))
        if not valid_background:
            missing.append("background")
        return missing

    @staticmethod
    def _complete_actor_list(response):
        """Return actor data only when its response is valid and not evidently truncated."""
        if not isinstance(response, dict) or not isinstance(response.get("data"), list):
            diagnose("manko", "actors", "actors_invalid")
            return None
        actors = response["data"]
        if len(actors) > 24 or not all(isinstance(actor, dict) for actor in actors):
            diagnose("manko", "actors", "actors_invalid")
            return None
        if "total" in response:
            total = response["total"]
            if type(total) is not int or total != len(actors):
                diagnose("manko", "actors", "actors_invalid")
                return None
        elif len(actors) == 24:
            # A full page without a total cannot prove that more actors do not exist.
            diagnose("manko", "actors", "actors_invalid")
            return None
        # The observed actor endpoint is keyed by work ID and currently returns the same
        # set even when size/page change. Treat that response set as the work's actors,
        # not as proof of a complete upstream actor catalog.
        if not actors or not all(isinstance(actor.get("name"), str) and actor["name"].strip()
                                 for actor in actors):
            return None
        try:
            MankoProvider._canonical_json(actors)
        except (TypeError, ValueError):
            diagnose("manko", "actors", "actors_invalid")
            return None
        return actors

    def _select_complete_search_match(self, matches):
        """Choose the first complete exact-number packet in API order, without merging."""
        missing_fields = set()
        for identity, record in matches.items():
            missing = set(self._record_missing_fields(record))
            try:
                actors = self._complete_actor_list(
                    self._get("/actors/" + identity, {"size": 24, "page": 1}))
            except Exception:
                diagnose("manko", "actors", "actors_invalid")
                actors = None
            if actors is None:
                missing.add("actors")
            if missing:
                missing_fields.update(missing)
                continue
            diagnose("manko", "search", "exact_match", count=1)
            return {"record": record, "actors": actors}
        diagnose("manko", "search", "metadata_incomplete", count=len(matches),
                 fields=missing_fields)
        return None

    async def _async_select_complete_search_match(self, matches):
        """Async counterpart, preserving API candidate order and packet boundaries."""
        missing_fields = set()
        for identity, record in matches.items():
            missing = set(self._record_missing_fields(record))
            try:
                response = await self._aget("/actors/" + identity, {"size": 24, "page": 1})
                actors = self._complete_actor_list(response)
            except Exception:
                diagnose("manko", "actors", "actors_invalid")
                actors = None
            if actors is None:
                missing.add("actors")
            if missing:
                missing_fields.update(missing)
                continue
            diagnose("manko", "search", "exact_match", count=1)
            return {"record": record, "actors": actors}
        diagnose("manko", "search", "metadata_incomplete", count=len(matches),
                 fields=missing_fields)
        return None

    @staticmethod
    def _actors(actors):
        if not isinstance(actors, list):
            diagnose("manko", "actors", "actors_invalid")
            return []
        return actors

    def fetch(self, identity: str) -> dict | None:
        """按原生 ID 回查，结果仍需具备唯一番号证据。"""
        if not IDENTIFIER.fullmatch(identity):
            diagnose("manko", "fetch", "invalid_id")
            return None
        record = self._detail(self._get("/detail/" + identity), identity)
        if not record:
            return None
        if len(record_numbers(record)) != 1:
            diagnose("manko", "fetch", "number_evidence_invalid")
            return None
        actors = self._get("/actors/" + identity, {"size": 24, "page": 1}).get("data", [])
        number = next(iter(record_numbers(record)))
        return self._supplement({"record": record, "number": number, "actors": self._actors(actors)}, number)

    async def async_fetch(self, identity: str) -> dict | None:
        """异步 ID 查询，禁止在事件循环中执行同步 HTTP。"""
        if not IDENTIFIER.fullmatch(identity):
            diagnose("manko", "fetch", "invalid_id")
            return None
        record = self._detail(await self._aget("/detail/" + identity), identity)
        if not record:
            return None
        if len(record_numbers(record)) != 1:
            diagnose("manko", "fetch", "number_evidence_invalid")
            return None
        actors = (await self._aget("/actors/" + identity, {"size": 24, "page": 1})).get("data", [])
        number = next(iter(record_numbers(record)))
        return await self._async_supplement({"record": record, "number": number, "actors": self._actors(actors)}, number)

    def search(self, number: str) -> dict | None:
        """核对全部有限候选详情后才返回唯一精确命中。"""
        number = normalize_number(number)
        if not number:
            diagnose("manko", "search", "number_not_unique")
            return None
        records = self._records(self._get("/search", {"keyword": number, "size": 24, "page": 1}))
        details = []
        for row in records:
            identity = str(row.get("_id") or "")
            if IDENTIFIER.fullmatch(identity):
                detail = self._detail(self._get("/detail/" + identity), identity)
                if not detail:
                    return None
                details.append(detail)
            else:
                diagnose("manko", "search", "invalid_id")
                return None
        matches = self._matching_records(details, number)
        if len(matches) > 1:
            selected = self._select_complete_search_match(matches)
            if selected is None:
                return None
            return self._supplement({**selected, "number": number}, number)
        selected = self._select(details, number)
        if not isinstance(selected, dict):
            return None
        actors = self._get("/actors/" + selected.get("_id"), {"size": 24, "page": 1}).get("data", [])
        return self._supplement({"record": selected, "number": number, "actors": self._actors(actors)}, number)

    async def async_search(self, number: str) -> dict | None:
        """异步搜索保持与同步入口一致的歧义拒绝语义。"""
        number = normalize_number(number)
        if not number:
            diagnose("manko", "search", "number_not_unique")
            return None
        records = self._records(await self._aget("/search", {"keyword": number, "size": 24, "page": 1}))
        details = []
        for row in records:
            identity = str(row.get("_id") or "")
            if IDENTIFIER.fullmatch(identity):
                detail = self._detail(await self._aget("/detail/" + identity), identity)
                if not detail:
                    return None
                details.append(detail)
            else:
                diagnose("manko", "search", "invalid_id")
                return None
        matches = self._matching_records(details, number)
        if len(matches) > 1:
            selected = await self._async_select_complete_search_match(matches)
            if selected is None:
                return None
            return await self._async_supplement({**selected, "number": number}, number)
        selected = self._select(details, number)
        if not isinstance(selected, dict):
            return None
        actors = (await self._aget("/actors/" + selected.get("_id"), {"size": 24, "page": 1})).get("data", [])
        return await self._async_supplement({"record": selected, "number": number, "actors": self._actors(actors)}, number)

    def search_matches(self, number: str) -> list[dict]:
        """为显式手动搜索返回所有逐条精确匹配的不同原生 ID。"""
        number = normalize_number(number)
        if not number:
            diagnose("manko", "search", "number_not_unique")
            return []
        rows = self._records(self._get("/search", {"keyword": number, "size": 24, "page": 1}))
        details = {}
        for row in rows:
            identity = str(row.get("_id") or "")
            if not IDENTIFIER.fullmatch(identity):
                diagnose("manko", "search", "invalid_id")
                return []
            if identity in details:
                continue
            detail = self._detail(self._get("/detail/" + identity), identity)
            if not detail:
                return []
            details[identity] = detail
        matches = self._matching_records(list(details.values()), number)
        if details:
            if len(matches) > 1:
                selected = self._select_complete_search_match(matches)
                if selected is not None:
                    return [self._supplement({**selected, "number": number}, number)]
                return []
            else:
                reason = "exact_match" if matches else "no_exact_match"
                diagnose("manko", "search", reason, count=len(matches))
        packets = []
        for identity, record in matches.items():
            actors = self._get("/actors/" + identity, {"size": 24, "page": 1}).get("data", [])
            packets.append(self._supplement({"record": record, "number": number, "actors": self._actors(actors)}, number))
        return packets

    async def async_search_matches(self, number: str) -> list[dict]:
        """异步手动搜索，与同步逐条匹配并保持相同候选顺序。"""
        number = normalize_number(number)
        if not number:
            diagnose("manko", "search", "number_not_unique")
            return []
        rows = self._records(await self._aget("/search", {"keyword": number, "size": 24, "page": 1}))
        details = {}
        for row in rows:
            identity = str(row.get("_id") or "")
            if not IDENTIFIER.fullmatch(identity):
                diagnose("manko", "search", "invalid_id")
                return []
            if identity in details:
                continue
            detail = self._detail(await self._aget("/detail/" + identity), identity)
            if not detail:
                return []
            details[identity] = detail
        matches = self._matching_records(list(details.values()), number)
        if details:
            if len(matches) > 1:
                selected = await self._async_select_complete_search_match(matches)
                if selected is not None:
                    return [await self._async_supplement({**selected, "number": number}, number)]
                return []
            else:
                reason = "exact_match" if matches else "no_exact_match"
                diagnose("manko", "search", reason, count=len(matches))
        packets = []
        for identity, record in matches.items():
            actors = (await self._aget("/actors/" + identity, {"size": 24, "page": 1})).get("data", [])
            packets.append(await self._async_supplement({"record": record, "number": number, "actors": self._actors(actors)}, number))
        return packets
