"""按真实文件上下文检查范围；不从标题、标签或缓存身份推断路径。"""
# SPDX-License-Identifier: GPL-3.0-only
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RecognitionContext:
    """未来宿主边界应从真实任务 FileItem 构建的不可变请求上下文。"""
    operation: str
    storage: str
    path: str
    scope_roots: tuple[Path, ...] | None = None


def matches_scope(context: RecognitionContext | None, storage: str, roots: tuple[Path, ...]) -> bool:
    """仅支持本地路径；词法位置及符号链接实际目标都必须在同一白名单内。"""
    if not isinstance(context, RecognitionContext) or context.operation != "transfer":
        return False
    if context.storage != storage or storage != "local":
        return False
    if not isinstance(context.path, str) or not context.path:
        return False
    raw = Path(context.path)
    if not raw.is_absolute() or ".." in raw.parts:
        return False
    try:
        lexical = Path(os.path.normpath(str(raw)))
        actual = raw.resolve(strict=True)
        if not actual.is_file():
            return False
        for root in (context.scope_roots if context.scope_roots is not None else roots):
            if not root.is_absolute() or root == Path("/") or ".." in root.parts:
                continue
            try:
                resolved_root = root.resolve(strict=True)
                if resolved_root.is_dir() and lexical.is_relative_to(root) and actual.is_relative_to(resolved_root):
                    return True
            except (OSError, RuntimeError, ValueError):
                continue
    except (OSError, RuntimeError, ValueError):
        return False
    return False
