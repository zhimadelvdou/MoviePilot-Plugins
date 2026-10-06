"""Manko API 适配。端点/编码依据 JAVRecognize 1.1；不进行真人站点测试。"""
# SPDX-License-Identifier: GPL-3.0-only
# Derived API contract and base91 decoder: hyuan280/MoviePilot-Plugins (GPLv3).
from __future__ import annotations

import json
from .transport import AnonymousAsyncRequestUtils as AsyncRequestUtils, AnonymousRequestUtils as RequestUtils
from app.sdk.config import settings
from .core import IDENTIFIER, exact_candidate, normalize_number, record_numbers
from .diagnostics import diagnose

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
            matches = {str(row.get("_id") or "") for row in details
                       if record_numbers(row) == {number} and IDENTIFIER.fullmatch(str(row.get("_id") or ""))}
            reason = "exact_match" if selected is not None else "ambiguous_match" if len(matches) > 1 else "no_exact_match"
            diagnose("manko", "search", reason, count=len(matches))
        return selected

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
        selected = self._select(details, number)
        if not isinstance(selected, dict):
            return None
        actors = (await self._aget("/actors/" + selected.get("_id"), {"size": 24, "page": 1})).get("data", [])
        return {"record": selected, "number": number, "actors": self._actors(actors)}
