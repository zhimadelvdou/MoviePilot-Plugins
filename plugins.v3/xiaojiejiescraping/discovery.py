"""只读轮询 qBittorrent 完成任务；不整理、不改标签、不发送媒体给来源。"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
from typing import Iterable
from .core import JsonCache
from .scope import RecognitionContext, matches_scope

VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v", ".ts"})


def exact_tags(value: str | None) -> set[str]:
    """使用 qBittorrent 逗号分隔标签语义，禁止字符串子串匹配。"""
    return {tag.strip() for tag in str(value or "").split(",") if tag.strip()}


def torrent_files(torrent, *, downloader: str, tag: str | None, roots: tuple[Path, ...], max_files: int = 500) -> tuple[Path, ...]:
    """使用宿主已映射的 content_path/path；绝不再拼 raw save_path 或二次映射。"""
    if not downloader or (tag and "," in tag) or getattr(torrent, "downloader", None) != downloader:
        return ()
    if tag and tag not in exact_tags(getattr(torrent, "tags", None)):
        return ()
    progress = getattr(torrent, "progress", None)
    if not isinstance(progress, (int, float)) or not math.isfinite(progress) or progress < 100:
        return ()
    if getattr(torrent, "state", None) not in ("completed", "paused"):
        return ()
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", str(getattr(torrent, "hash", ""))):
        return ()
    mapped_path = getattr(torrent, "content_path", None) or getattr(torrent, "path", None)
    if not isinstance(mapped_path, (str, Path)):
        return ()
    path = Path(mapped_path)
    if not path.is_absolute() or ".." in path.parts:
        return ()
    try:
        if path.is_file():
            found: Iterable[Path] = (path,)
        elif path.is_dir() and not path.is_symlink():
            # 先验证目录所在范围，避免遍历范围外目录后才过滤文件。
            actual = path.resolve(strict=True)
            allowed = False
            for root in roots:
                try:
                    if root != Path("/") and path.is_relative_to(root) and actual.is_relative_to(root.resolve(strict=True)):
                        allowed = True
                        break
                except (OSError, RuntimeError, ValueError):
                    continue
            if not allowed:
                return ()
            files = []
            visits = 0
            for directory, children, names in os.walk(path, followlinks=False):
                children[:] = sorted(child for child in children if not (Path(directory) / child).is_symlink())
                visits += 1
                if visits > 1000:
                    return ()
                for name in sorted(names):
                    item = Path(directory) / name
                    if item.suffix.lower() in VIDEO_EXTENSIONS:
                        files.append(item)
                        if len(files) > max_files:
                            return ()
            found = files
        else:
            return ()
        return tuple(file for file in found if file.suffix.lower() in VIDEO_EXTENSIONS and matches_scope(
            RecognitionContext("transfer", "local", str(file)), "local", roots,
        ))
    except (OSError, RuntimeError, ValueError):
        return ()


class DiscoveryJournal:
    """发现记录防重复通知；不代表整理成功，也不用于代替宿主提交事务。"""
    def __init__(self, path: Path):
        self.cache = JsonCache(path, ttl=365 * 86400, max_entries=5000)

    def next_batch(self, torrents: list, limit: int = 100) -> list:
        """持久轮转游标保证已发现/无效旧任务不会永久饿死第 101 个任务。"""
        def key(torrent):
            return str(getattr(torrent, "downloader", "")) + ":" + str(getattr(torrent, "hash", ""))
        ordered = sorted(torrents, key=key)
        if not ordered:
            return []
        after = (self.cache.get("poll_cursor") or {}).get("after", "")
        start = next((index for index, torrent in enumerate(ordered) if key(torrent) > after), 0)
        batch = (ordered[start:] + ordered[:start])[:limit]
        return batch

    def advance(self, torrent) -> None:
        """仅在实际处理过任务后推进游标，文件数上限不能跳过未处理任务。"""
        key = str(getattr(torrent, "downloader", "")) + ":" + str(getattr(torrent, "hash", ""))
        self.cache.put("poll_cursor", {"after": key})

    def discover(self, torrent, files: tuple[Path, ...]) -> list[dict]:
        """以下载器/hash/实际路径/大小/时间戳记录版本变化，原子持久化。"""
        results = []
        for path in files:
            try:
                stat = path.stat()
                item = {"downloader": torrent.downloader, "hash": torrent.hash,
                        "path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                        "state": "discovered"}
                raw = "\x00".join(str(item[k]) for k in ("downloader", "hash", "path", "size", "mtime_ns"))
                key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                if self.cache.put_if_absent(key, item):
                    results.append(item)
            except OSError:
                continue
        return results


def owned_torrent_files(torrent, candidates: tuple[Path, ...], manifest) -> tuple[Path, ...] | None:
    """用公开文件清单证明 hash 所有权；仅筛选已有合规路径，不生成新扫描路径。

    save_path 是 MoviePilot DownloaderTorrent 已映射字段，不是原始下载器路径；
    无法把清单相对名与该锚点唯一对应时拒绝，不猜根目录或再做 Docker 映射。
    """
    mapped_save = getattr(torrent, "save_path", None)
    if not isinstance(mapped_save, str) or not mapped_save or not isinstance(manifest, list) or not manifest:
        return None
    base = Path(mapped_save)
    if not base.is_absolute() or base == Path("/") or ".." in base.parts or not base.is_dir() or len(manifest) > 10000:
        return None
    try:
        candidate_map = {path.relative_to(base).as_posix(): path for path in candidates}
    except ValueError:
        return None
    if len(candidate_map) != len(candidates):
        return None
    names, identities, selected = set(), set(), []
    for item in manifest:
        read = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
        name, identity = read("name"), read("id")
        size, priority, progress = read("size"), read("priority"), read("progress")
        if (not isinstance(name, str) or not name or "\\" in name or "//" in name
                or any(ord(char) < 32 or ord(char) == 127 for char in name)
                or Path(name).is_absolute() or any(part in ("", ".", "..") for part in name.split("/"))):
            return None
        if type(identity) is not int or identity < 0 or identity in identities or name in names:
            return None
        if type(size) is not int or size < 0 or type(priority) is not int or priority < 0:
            return None
        if type(progress) not in (int, float) or not math.isfinite(progress) or not 0 <= progress <= 1:
            return None
        identities.add(identity)
        names.add(name)
        if priority == 0:
            continue
        if progress != 1:
            return None
        if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        path = candidate_map.get(name)
        if path is None:
            return None
        try:
            if path.stat().st_size != size:
                return None
        except OSError:
            return None
        selected.append(path)
    if identities != set(range(len(manifest))) or len(selected) > 500:
        return None
    return tuple(selected)
