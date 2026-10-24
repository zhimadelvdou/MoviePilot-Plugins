"""查询诊断只输出固定词表和有限整数，绝不记录用户输入或上游文本。"""
# SPDX-License-Identifier: GPL-3.0-only
from contextlib import contextmanager
from contextvars import ContextVar

from app.sdk.logging import logger

from .registry import PROVIDERS

_query_events = ContextVar("xiaojiejiescraping_query_events", default=None)


@contextmanager
def capture_query_diagnostics():
    """Capture a bounded, sanitized diagnostic slice for one caller context."""
    events = []
    token = _query_events.set(events)
    try:
        yield events
    finally:
        _query_events.reset(token)

_SOURCES = frozenset(status.key for status in PROVIDERS) | {"plugin", "other"}
_OPERATIONS = frozenset({"configure", "request", "search", "fetch", "detail", "actors", "accept"})
_REASONS = frozenset({
    "enabled", "disabled", "source_unselected", "cache_unavailable", "scope_rejected",
    "media_type_rejected", "provider_unavailable", "invalid_id", "number_not_unique",
    "cache_hit", "cache_miss", "empty_result", "query_exception", "accepted",
    "source_mismatch", "identity_mismatch", "result_invalid", "configuration_changed",
    "transport_unavailable", "request_exception", "http_response", "http_error",
    "http_redirect", "invalid_json", "payload_invalid", "schema_invalid",
    "search_incomplete", "no_candidates", "candidates", "no_exact_match",
    "ambiguous_match", "exact_match", "equivalent_match", "metadata_incomplete",
    "number_evidence_invalid", "actors_invalid", "access_verification_required",
    "rate_limited", "login_required", "not_found", "network_unavailable",
    "upstream_unavailable", "access_restricted", "response_invalid",
})
_MISSING_FIELDS = ("title", "descriptions", "date", "cover", "background", "actors")


def diagnose(source, operation, reason, *, status=None, count=None, fields=None):
    """字段须命中固定词表；状态仅接收真实响应的整数，不猜测异常消息。

    不接受 URL、请求参数、身份、番号、路径、标题、响应正文或异常对象。
    日志设施异常不能改变原有查询/授权结果。
    """
    source = source if type(source) is str and source in _SOURCES else "other"
    operation = operation if type(operation) is str and operation in _OPERATIONS else "request"
    reason = reason if type(reason) is str and reason in _REASONS else "result_invalid"
    message = f"Xiaojiejie-Scraping diagnostic source={source} operation={operation} reason={reason}"
    if type(status) is int and 100 <= status <= 599:
        message += f" http_status={status}"
    if type(count) is int and 0 <= count <= 10000:
        message += f" count={count}"
    if isinstance(fields, (list, tuple, set, frozenset)):
        missing = [field for field in _MISSING_FIELDS if field in fields]
        if missing:
            message += " missing=" + ",".join(missing)
    events = _query_events.get()
    if isinstance(events, list) and len(events) < 16:
        events.append((source, operation, reason, status if type(status) is int and 100 <= status <= 599 else None))
    try:
        logger.info(message)
    except Exception:
        pass
