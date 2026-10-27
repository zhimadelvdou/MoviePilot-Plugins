"""供插件路由注册的管理 API。"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from typing import Literal

from fastapi import Depends, HTTPException
from app.api.endpoints.plugin import get_current_active_superuser

from .manual import (
    ManualBrowse, ManualExecuteRequest, ManualExecuteResponse, ManualOptions,
    ManualPreview, ManualPreviewRequest, ManualTransferWorkspace,
)


class ManualPluginAPI:
    """只提供已认证的插件管理 API，不读取客户端凭据。"""

    def __init__(self, workspace: ManualTransferWorkspace):
        self.workspace = workspace

    def options(self, _user=Depends(get_current_active_superuser)) -> ManualOptions:
        return self.workspace.options()

    def browse(self, path: str = "", kind: Literal["source", "target"] = "source",
               show_organized: bool = False,
               _user=Depends(get_current_active_superuser)) -> ManualBrowse:
        try:
            return self.workspace.browse(path, kind, show_organized=show_organized)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def preview(self, request: ManualPreviewRequest,
                _user=Depends(get_current_active_superuser)) -> ManualPreview:
        try:
            return self.workspace.preview(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def execute(self, request: ManualExecuteRequest,
                _user=Depends(get_current_active_superuser)) -> ManualExecuteResponse:
        try:
            return self.workspace.execute(request.preview_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def status(self, preview_id: str,
               _user=Depends(get_current_active_superuser)) -> ManualExecuteResponse:
        try:
            return self.workspace.status(preview_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


def api_routes(workspace: ManualTransferWorkspace) -> list[dict]:
    """返回 MoviePilot 插件 API 注册器接受的显式路由定义。"""
    api = ManualPluginAPI(workspace)
    return [
        {"path": "/manual/options", "endpoint": api.options, "methods": ["GET"], "auth": "bear",
         "summary": "读取手动整理选项", "response_model": ManualOptions},
        {"path": "/manual/browse", "endpoint": api.browse, "methods": ["GET"], "auth": "bear",
         "summary": "浏览允许的手动整理目录", "response_model": ManualBrowse},
        {"path": "/manual/preview", "endpoint": api.preview, "methods": ["POST"], "auth": "bear",
         "summary": "预览手动整理", "response_model": ManualPreview},
        {"path": "/manual/execute", "endpoint": api.execute, "methods": ["POST"], "auth": "bear",
         "summary": "提交已确认的手动整理预览", "response_model": ManualExecuteResponse},
        {"path": "/manual/status", "endpoint": api.status, "methods": ["GET"], "auth": "bear",
         "summary": "读取手动整理状态", "response_model": ManualExecuteResponse},
    ]
