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
from .core import IDENTIFIER, exact_candidate, normalize_number, record_numbers
from .diagnostics import diagnose

API = "https://healertanker.com/swx/movie"
ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&()*+,./:;<=>?@[]^_`{|}~"'
_EQUIVALENT_TRANSPORT_FIELDS = frozenset({
    "_id", "category_id", "jable_hls", "missav_video", "mmtv_embed",
    "mmtv_m3u8", "mmtv_time", "njav_player", "upload18_player",
    "wasabi_embed_url", "wasabi_url_1",
})


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
    def _record_metadata_complete(record):
        """Require every metadata field that makes automatic equivalence useful."""
        titles = record.get("title_display_name")
        has_display_title = isinstance(titles, list) and any(
            isinstance(item, dict) and isinstance(item.get("title"), str)
            and item["title"].strip() and isinstance(item.get("language"), str)
            and item["language"].strip()
            for item in titles
        )
        if not has_display_title and not (isinstance(record.get("title"), str) and record["title"].strip()):
            return False

        share_date = record.get("share_date")
        if not isinstance(share_date, str) or len(share_date) < 10:
            return False
        try:
            date.fromisoformat(share_date[:10])
        except ValueError:
            return False

        cover = record.get("cover_image")
        if not isinstance(cover, str) or not cover.strip():
            return False
        try:
            parsed = urlsplit(cover)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                return False
        except ValueError:
            return False

        descriptions = record.get("discription")
        if not isinstance(descriptions, list) or not any(
            isinstance(item, dict) and isinstance(item.get("overview"), str)
            and item["overview"].strip() and isinstance(item.get("language"), str)
            and item["language"].strip()
            for item in descriptions
        ):
            return False
        return True

    @staticmethod
    def _equivalent_record_data(records):
        """Compare every raw field except native identity and observed transport fields."""
        values = []
        try:
            for record in records:
                comparable = {key: value for key, value in record.items()
                              if key not in _EQUIVALENT_TRANSPORT_FIELDS}
                values.append(MankoProvider._canonical_json(comparable))
        except (TypeError, ValueError):
            diagnose("manko", "search", "schema_invalid")
            return False
        return bool(values) and all(value == values[0] for value in values[1:])

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
            diagnose("manko", "search", "metadata_incomplete")
            return None
        try:
            MankoProvider._canonical_json(actors)
        except (TypeError, ValueError):
            diagnose("manko", "actors", "actors_invalid")
            return None
        return actors

    def _equivalent_search_result(self, matches):
        """Resolve equivalent multi-ID metadata only after strict raw and actor checks."""
        ordered = sorted(matches.items())
        records = [record for _, record in ordered]
        if not all(self._record_metadata_complete(record) for record in records):
            diagnose("manko", "search", "metadata_incomplete", count=len(records))
            return None
        if not self._equivalent_record_data(records):
            return None

        actor_lists = []
        for identity, _ in ordered:
            try:
                response = self._get("/actors/" + identity, {"size": 24, "page": 1})
            except Exception:
                diagnose("manko", "actors", "actors_invalid")
                actor_lists.append(None)
                continue
            actor_lists.append(self._complete_actor_list(response))
        if any(actors is None for actors in actor_lists):
            return None
        try:
            actor_keys = [self._canonical_json(actors) for actors in actor_lists]
        except (TypeError, ValueError):
            diagnose("manko", "actors", "actors_invalid")
            return None
        if not all(key == actor_keys[0] for key in actor_keys[1:]):
            return None
        diagnose("manko", "search", "equivalent_match", count=1)
        return {"record": ordered[0][1], "actors": actor_lists[0]}

    async def _async_equivalent_search_result(self, matches):
        """Async counterpart; actor calls stay on the provider's async transport."""
        ordered = sorted(matches.items())
        records = [record for _, record in ordered]
        if not all(self._record_metadata_complete(record) for record in records):
            diagnose("manko", "search", "metadata_incomplete", count=len(records))
            return None
        if not self._equivalent_record_data(records):
            return None

        actor_lists = []
        for identity, _ in ordered:
            try:
                response = await self._aget("/actors/" + identity, {"size": 24, "page": 1})
            except Exception:
                diagnose("manko", "actors", "actors_invalid")
                actor_lists.append(None)
                continue
            actor_lists.append(self._complete_actor_list(response))
        if any(actors is None for actors in actor_lists):
            return None
        try:
            actor_keys = [self._canonical_json(actors) for actors in actor_lists]
        except (TypeError, ValueError):
            diagnose("manko", "actors", "actors_invalid")
            return None
        if not all(key == actor_keys[0] for key in actor_keys[1:]):
            return None
        diagnose("manko", "search", "equivalent_match", count=1)
        return {"record": ordered[0][1], "actors": actor_lists[0]}

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
        return {"record": record, "number": next(iter(record_numbers(record))), "actors": self._actors(actors)}

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
        return {"record": record, "number": next(iter(record_numbers(record))), "actors": self._actors(actors)}

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
            equivalent = self._equivalent_search_result(matches)
            if equivalent is None:
                self._select(details, number)
                return None
            return {"record": equivalent["record"], "number": number, "actors": equivalent["actors"]}
        selected = self._select(details, number)
        if not isinstance(selected, dict):
            return None
        actors = self._get("/actors/" + selected.get("_id"), {"size": 24, "page": 1}).get("data", [])
        return {"record": selected, "number": number, "actors": self._actors(actors)}

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
            equivalent = await self._async_equivalent_search_result(matches)
            if equivalent is None:
                self._select(details, number)
                return None
            return {"record": equivalent["record"], "number": number, "actors": equivalent["actors"]}
        selected = self._select(details, number)
        if not isinstance(selected, dict):
            return None
        actors = (await self._aget("/actors/" + selected.get("_id"), {"size": 24, "page": 1})).get("data", [])
        return {"record": selected, "number": number, "actors": self._actors(actors)}

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
            reason = "exact_match" if len(matches) == 1 else "ambiguous_match" if matches else "no_exact_match"
            diagnose("manko", "search", reason, count=len(matches))
        packets = []
        for identity, record in matches.items():
            actors = self._get("/actors/" + identity, {"size": 24, "page": 1}).get("data", [])
            packets.append({"record": record, "number": number, "actors": self._actors(actors)})
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
            reason = "exact_match" if len(matches) == 1 else "ambiguous_match" if matches else "no_exact_match"
            diagnose("manko", "search", reason, count=len(matches))
        packets = []
        for identity, record in matches.items():
            actors = (await self._aget("/actors/" + identity, {"size": 24, "page": 1})).get("data", [])
            packets.append({"record": record, "number": number, "actors": self._actors(actors)})
        return packets
