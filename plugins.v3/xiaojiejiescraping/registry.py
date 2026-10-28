"""直连来源的扩展合同与可用性清单；不依赖或调用 MetaTube。"""
# SPDX-License-Identifier: GPL-3.0-only
from dataclasses import dataclass
from typing import Protocol


class MetadataProvider(Protocol):
    """每个直连适配器自行取得并核对来源原生身份，返回纯数据包。"""
    def search(self, number: str) -> dict | None:
        """精确番号查询；歧义或证据不足返回空。"""
        ...

    async def async_search(self, number: str) -> dict | None:
        """异步精确查询。"""
        ...

    def fetch(self, identity: str) -> dict | None:
        """来源原生 ID 回查，不跨来源使用 ID。"""
        ...

    async def async_fetch(self, identity: str) -> dict | None:
        """异步来源 ID 回查。"""
        ...


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """区分已实现的离线合同与尚未实现的来源，不将占位列为可用。"""
    key: str
    label: str
    state: str
    purpose: str = "metadata"


PROVIDERS = (
    ProviderStatus("manko", "Manko", "offline_adapter"),
    ProviderStatus("fanza", "FANZA", "offline_adapter"),
    ProviderStatus("mgstage", "MGStage", "offline_adapter"),
    ProviderStatus("duga", "DUGA", "offline_adapter"),
    ProviderStatus("sod", "SOD", "offline_adapter"),
    ProviderStatus("javbus", "JavBus", "offline_adapter"),
    ProviderStatus("jav321", "Jav321", "offline_adapter"),
    ProviderStatus("fc2hub", "FC2Hub", "offline_adapter"),
    ProviderStatus("fc2", "FC2官方", "limited_live"),
    ProviderStatus("fc2ppvdb", "FC2PPVDB / FC2CMADB", "offline_adapter"),
    ProviderStatus("gfriends", "GFriends", "offline_adapter", "actor_images"),
)


class ProviderRegistry:
    """仅调度真正注册的适配器；错误不变成假成功，来源 ID 不混用。"""
    def __init__(self, adapters: dict[str, MetadataProvider]):
        self.adapters = dict(adapters)

    def search(self, number: str, ordered_sources: tuple[str, ...]) -> tuple[str, dict] | None:
        """按用户顺序查精确结果；第一个成功来源为主身份，不合并冲突 ID。"""
        for source in dict.fromkeys(ordered_sources):
            adapter = self.adapters.get(source)
            if adapter is None:
                continue
            try:
                result = adapter.search(number)
            except Exception:
                continue
            if result:
                return source, result
        return None

    async def async_search(self, number: str, ordered_sources: tuple[str, ...]) -> tuple[str, dict] | None:
        """异步调度遵循相同来源顺序，不运行同步抓取。"""
        for source in dict.fromkeys(ordered_sources):
            adapter = self.adapters.get(source)
            if adapter is None:
                continue
            try:
                result = await adapter.async_search(number)
            except Exception:
                continue
            if result:
                return source, result
        return None
