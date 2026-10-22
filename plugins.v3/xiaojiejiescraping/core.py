"""多来源元数据的纯校验、精确匹配及安全输出；不操作媒体文件。"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unicodedata
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

_PATH_LOCKS = {}
_PATH_LOCKS_GUARD = threading.Lock()

SOURCE = "manko"
NUMBER = re.compile(r"(?<![A-Z0-9])(?:FC2[-_ ]?(?:PPV[-_ ]?)?\d{5,9}|(?:[0-9]{1,4})?[A-Z]{2,12}[-_ ]\d{2,7})(?![A-Z0-9])", re.I)
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def normalize_number(value: str) -> str | None:
    """仅接受完整番号，不用标题相似度或补零猜测。"""
    value = str(value or "").strip().upper()
    if not NUMBER.fullmatch(value):
        return None
    value = re.sub(r"[-_ ]+", "-", value)
    if value.startswith("FC2"):
        return "FC2-PPV-" + re.fullmatch(r"FC2-?(?:PPV-?)?(\d{5,9})", value).group(1)
    return value


def extract_number(text: str) -> str | None:
    """多个不同番号返回空，避免合集或歧义文件被误归类。"""
    found = {normalize_number(m.group()) for m in NUMBER.finditer(str(text or ""))}
    return next(iter(found)) if len(found) == 1 else None


def record_numbers(record: dict) -> set[str]:
    """从已知编号及多语标题字段取证，不把服务端原生 ID 当作番号。"""
    texts = [record.get(key) for key in ("number", "code", "serial_number", "jav_id", "title", "name")]
    texts.extend(item.get("title") for item in record.get("title_display_name", []) if isinstance(item, dict))
    return {normalize_number(match.group()) for text in texts if isinstance(text, str) for match in NUMBER.finditer(text)}


def exact_candidate(records: list[dict], number: str) -> dict | None:
    """必须只有一个原生 ID 精确命中；重复行可合并，不选第一条。"""
    matches = {}
    for record in records:
        if not isinstance(record, dict) or record_numbers(record) != {number}:
            continue
        identity = str(record.get("_id") or "")
        if IDENTIFIER.fullmatch(identity):
            matches[identity] = record
    return next(iter(matches.values())) if len(matches) == 1 else None


def safe_image_url(value: str, allowed_hosts: tuple[str, ...]) -> str | None:
    """图片仅允许用户配置的精确 HTTPS 主机，禁止凭据、IP 和本地地址。"""
    if not isinstance(value, str) or not value or len(value) > 8192 or "\\" in value:
        return None
    if any(char.isspace() or unicodedata.category(char) == "Cc" for char in value):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        if host not in allowed_hosts or host in ("localhost",) or host.endswith((".local", ".localhost")):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        return value if host and not parsed.fragment else None
    except ValueError:
        return None


def safe_backdrop_url(record: dict, allowed_hosts: tuple[str, ...]) -> str | None:
    """Choose the first approved background URL distinct from the cover."""
    cover = safe_image_url(record.get("cover_image"), allowed_hosts)
    backdrops = record.get("backdrop_images")
    if not isinstance(backdrops, list):
        return None
    for item in backdrops:
        backdrop = safe_image_url(item, allowed_hosts)
        if backdrop and backdrop != cover:
            return backdrop
    return None


def image_map(record: dict, allowed_hosts: tuple[str, ...]) -> dict[str, str]:
    """固定输出文件名，不使用服务端路径、标题或演员名。"""
    cover = safe_image_url(record.get("cover_image"), allowed_hosts)
    backdrop = safe_backdrop_url(record, allowed_hosts)
    images = {}
    if cover:
        images["poster.jpg"] = cover
    if backdrop:
        images["fanart.jpg"] = backdrop
    return images


def clean_text(value: object) -> str:
    """XML 1.0 不允许的控制字符必须移除，转义交给 ElementTree。"""
    return "".join(c for c in str(value or "") if c in "\t\n\r" or "\x20" <= c <= "\ud7ff" or "\ue000" <= c <= "\ufffd" or "\U00010000" <= c <= "\U0010ffff")


def _named_entities(value: object) -> list[dict[str, str]]:
    """Normalize bounded canonical director/company summaries for MediaInfo."""
    if not isinstance(value, list):
        return []
    result, seen = [], set()
    for item in value[:32]:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or len(item["name"]) > 200):
            continue
        name = clean_text(item["name"]).strip()
        if name and len(name) <= 200 and name not in seen:
            result.append({"name": name})
            seen.add(name)
    return result


def project_record(record: dict, number: str, actors: list[dict], allowed_hosts: tuple[str, ...], source: str = SOURCE) -> dict:
    """构造 V3 规范媒体身份，不伪造 TMDB/IMDb 身份。"""
    titles = [v for v in record.get("title_display_name", []) if isinstance(v, dict) and isinstance(v.get("title"), str)]
    titles.sort(key=lambda v: {"zh": 0, "ja": 1, "en": 2}.get(v.get("language"), 3))
    title = titles[0]["title"] if titles else record.get("title") or number
    descriptions = [v for v in record.get("discription", []) if isinstance(v, dict) and isinstance(v.get("overview"), str)]
    descriptions.sort(key=lambda v: {"zh": 0, "ja": 1, "en": 2}.get(v.get("language"), 3))
    date = str(record.get("share_date") or "")
    date = date[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", date) else ""
    cover = safe_image_url(record.get("cover_image"), allowed_hosts)
    backdrop = safe_backdrop_url(record, allowed_hosts)
    runtime = record.get("video_duration")
    if type(runtime) is not int or not 1 <= runtime <= 999:
        runtime = record.get("runtime_minutes")
    if type(runtime) is not int or not 1 <= runtime <= 999:
        runtime = None
    directors = _named_entities(record.get("directors"))
    companies = _named_entities(record.get("production_companies"))
    return {"media_source": source, "media_id": str(record["_id"]), "scrape_source": source,
            "type": "电影", "title": clean_text(title), "original_title": clean_text(title),
            "year": date[:4] or None, "release_date": date or None,
            "overview": clean_text("\n".join(v["overview"] for v in descriptions)),
            "poster_path": cover, "backdrop_path": backdrop, "runtime": runtime,
            "directors": directors, "production_companies": companies, "category": "AV",
            "actors": [{"name": clean_text(a.get("name")), "type": "Actor", "profile_path": safe_image_url(a.get("profile_path"), allowed_hosts)} for a in actors if isinstance(a, dict) and a.get("name")],
            "names": [number], "genre_ids": []}


def render_nfo(info: dict, number: str) -> str:
    """只写 Manko 已核对字段，宿主后加的辅助 ID 不会进入 NFO。"""
    root = ET.Element("movie")
    for name, value in (("title", info.get("title")), ("originaltitle", info.get("original_title")),
                        ("plot", info.get("overview")), ("year", info.get("year")),
                        ("premiered", info.get("release_date")), ("num", number),
                        ("runtime", info.get("runtime"))):
        if value:
            ET.SubElement(root, name).text = clean_text(value)
    for name, key in (("director", "directors"), ("studio", "production_companies")):
        for item in _named_entities(info.get(key)):
            ET.SubElement(root, name).text = item["name"]
    ET.SubElement(root, "uniqueid", {"type": clean_text(info.get("media_source") or SOURCE), "default": "true"}).text = clean_text(info["media_id"])
    for actor in info.get("actors", []):
        element = ET.SubElement(root, "actor")
        ET.SubElement(element, "name").text = clean_text(actor.get("name"))
        if actor.get("profile_path"):
            ET.SubElement(element, "thumb").text = clean_text(actor["profile_path"])
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


class JsonCache:
    """限量、定期失效及原子落盘；绝不反序列化 V2 pickle。"""
    def __init__(self, path: Path, ttl: int = 86400, max_entries: int = 500, max_bytes: int = 8 * 1024 * 1024):
        self.path, self.ttl, self.max_entries = Path(path), ttl, max_entries
        self.max_bytes = max_bytes
        with _PATH_LOCKS_GUARD:
            self._lock = _PATH_LOCKS.setdefault(str(self.path.resolve()), threading.RLock())
        self._data = {}
        with self._lock:
            self._reload()

    def _reload(self):
        """同进程插件实例共享路径锁，每次操作先重读，避免旧快照覆盖新条目。"""
        self._data = {}
        try:
            if self.path.stat().st_size <= self.max_bytes:
                document = json.loads(self.path.read_text(encoding="utf-8"))
                if document.get("schema") == 1 and isinstance(document.get("entries"), dict):
                    self._data = document["entries"]
        except (OSError, ValueError, AttributeError):
            pass

    def get(self, key: str) -> dict | None:
        """读取返回深拷贝，防止宿主辅助字段污染缓存。"""
        with self._lock:
            self._reload()
            value = self._data.get(key)
            if not isinstance(value, dict) or not isinstance(value.get("expires"), (int, float)) or value["expires"] <= time.time():
                return None
            return copy.deepcopy(value.get("value")) if isinstance(value.get("value"), dict) else None

    def put(self, key: str, value: dict) -> None:
        """成功结果立即落盘；失败和模糊结果不缓存。"""
        with self._lock:
            self._reload()
            self._data.pop(key, None)
            self._data[key] = {"expires": time.time() + self.ttl, "value": copy.deepcopy(value)}
            self._data = {k: v for k, v in self._data.items() if isinstance(v, dict) and isinstance(v.get("expires"), (int, float)) and v["expires"] > time.time()}
            self._data = dict(list(self._data.items())[-self.max_entries:])
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(prefix=".scraping-", suffix=".json", dir=self.path.parent)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump({"schema": 1, "entries": self._data}, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)

    def put_if_absent(self, key: str, value: dict) -> bool:
        """同进程多实例以单一临界区发现新版本，防止检查后写入竞态。"""
        with self._lock:
            if self.get(key) is not None:
                return False
            self.put(key, value)
            return True
