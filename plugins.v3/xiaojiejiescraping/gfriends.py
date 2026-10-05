"""GFriends 官方 Filetree.json 合同适配；只查清单，不下载演员图片。"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from urllib.parse import quote, urlsplit
import re
from app.sdk.config import settings
from .transport import AnonymousAsyncRequestUtils as AsyncRequestUtils, AnonymousRequestUtils as RequestUtils

INDEX_URL = "https://raw.githubusercontent.com/gfriends/gfriends/master/Filetree.json"
CONTENT_URL = "https://raw.githubusercontent.com/gfriends/gfriends/master/Content/"


def actor_image(manifest: dict, name: str) -> str | None:
    """演员名精确匹配；按官方质量升序合同选择最后一个匹配厂牌。"""
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return None
    if any(char in name for char in ("/", "\\", "\x00")) or name in (".", ".."):
        return None
    content = manifest.get("Content") if isinstance(manifest, dict) else None
    if not isinstance(content, dict) or len(content) > 1000:
        return None
    selected = None
    for company, files in content.items():
        if not isinstance(company, str) or not company or any(ord(c) < 32 or ord(c) == 127 for c in company) or company in (".", "..") or any(char in company for char in ("/", "\\", "\x00")):
            continue
        if not isinstance(files, dict) or len(files) > 300_000:
            continue
        filename = files.get(name + ".jpg")
        if not isinstance(filename, str):
            continue
        try:
            parsed = urlsplit(filename)
        except ValueError:
            continue
        if parsed.scheme or parsed.netloc or parsed.fragment or "/" in parsed.path or "\\" in parsed.path:
            continue
        if any(ord(c) < 32 or ord(c) == 127 for c in filename) or not parsed.path.lower().endswith(".jpg") or parsed.path in (".", ".."):
            continue
        # 官方别名可以指向同厂牌另一文件名；只准单一文件段及数值更新时间。
        if parsed.query and not re.fullmatch(r"t=[0-9]+", parsed.query):
            continue
        selected = CONTENT_URL + quote(company, safe="") + "/" + quote(parsed.path, safe="")
        if parsed.query:
            selected += "?" + parsed.query
    return selected


class GFriendsProvider:
    """清单查询使用 MoviePilot 现行系统代理与 TLS 验证。"""
    def fetch_index(self) -> dict | None:
        """读取公开索引；调用方负责持久缓存，避免每个演员重复抓取。"""
        response = RequestUtils(timeout=30, verify=True, proxies=settings.PROXY).get_json(
            INDEX_URL, allow_redirects=False,
        )
        return response if isinstance(response, dict) and isinstance(response.get("Content"), dict) else None

    async def async_fetch_index(self) -> dict | None:
        """异步索引读取，不调用同步 HTTP。"""
        response = await AsyncRequestUtils(timeout=30, verify=True, proxies=settings.PROXY).get_json(
            INDEX_URL, follow_redirects=False,
        )
        return response if isinstance(response, dict) and isinstance(response.get("Content"), dict) else None
