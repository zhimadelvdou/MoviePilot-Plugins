"""管理员专用自动触发状态和只读条件检查 API。"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from pydantic import BaseModel
from fastapi import Depends
from app.api.endpoints.plugin import get_current_active_superuser


class AutomaticStatus(BaseModel):
    enabled: bool
    mode: str
    match_mode: str
    new_only: bool
    armed: bool
    started_at: int | None = None
    interval_seconds: int = 300
    downloader: str = ""
    torrent_tag: str = ""
    roots: list[str]
    target_path: str = ""
    last_poll_at: float | None = None
    counts: dict[str, int]
    error: str | None = None


class AutomaticCheck(BaseModel):
    ready: bool
    message: str
    match_mode: str
    downloader_type: str
    completion_capability: bool
    completion_rows_checked: int
    completion_time_rows: int
    native_monitor_overlap: bool | None = None
    native_monitor_count: int
    directories_available: bool
    errors: list[str]


class AutomaticPluginAPI:
    def __init__(self, plugin):
        self.plugin = plugin

    def status(self, _user=Depends(get_current_active_superuser)) -> AutomaticStatus:
        return AutomaticStatus(**self.plugin.automatic_status())

    def check(self, _user=Depends(get_current_active_superuser)) -> AutomaticCheck:
        return AutomaticCheck(**self.plugin.automatic_check())


def api_routes(plugin) -> list[dict]:
    api = AutomaticPluginAPI(plugin)
    return [
        {"path": "/automatic/status", "endpoint": api.status, "methods": ["GET"], "auth": "bear",
         "summary": "读取自动触发状态", "response_model": AutomaticStatus},
        {"path": "/automatic/check", "endpoint": api.check, "methods": ["POST"], "auth": "bear",
         "summary": "只读检查自动触发条件", "response_model": AutomaticCheck},
    ]
