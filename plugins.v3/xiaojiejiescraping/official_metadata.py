"""Official ATTACKERS page lookup for supplemental metadata only."""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import re

from .core import clean_text
from .direct_secondary import _HtmlProvider


class OfficialMetadataProvider(_HtmlProvider):
    """Read a fixed, exact catalog URL and extract only its labeled fields."""
    source = "attackers"
    base = "https://attackers.net"

    def _detail_path(self, path: str) -> bool:
        return bool(re.fullmatch(r"/works/detail/ADN\d{1,7}/?", path))

    @staticmethod
    def _page_is_blocked(html) -> bool:
        text = " ".join(" ".join(html.itertext()).split()).casefold()
        return any(marker in text for marker in (
            "captcha", "access denied", "403 forbidden", "ページが見つかりません",
            "ただいまアクセスが集中", "アクセスを制限",
        ))

    def _parse(self, html, number: str) -> dict | None:
        match = re.fullmatch(r"ADN-(\d{1,7})", number or "", re.I)
        if html is None or not match or self._page_is_blocked(html):
            return None
        expected = "ADN" + match.group(1)
        tables = html.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' p-workPage__table ')]")
        if len(tables) != 1:
            return None
        rows = tables[0].xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' item ')]")
        values = {"品番": [], "監督": [], "収録時間": []}
        actresses = []
        actress_rows = 0
        actress_rows_valid = True
        for row in rows:
            labels = [" ".join(" ".join(node.itertext()).split()) for node in row.xpath("./*[contains(concat(' ', normalize-space(@class), ' '), ' th ')]")]
            cells = row.xpath("./*[contains(concat(' ', normalize-space(@class), ' '), ' td ')]")
            if "女優" in labels:
                actress_rows += 1
                if len(labels) != 1 or labels[0] != "女優" or len(cells) != 1:
                    actress_rows_valid = False
                    continue
                links = cells[0].xpath(".//a")
                if not links:
                    actress_rows_valid = False
                for link in links:
                    name = " ".join(" ".join(link.itertext()).split())
                    href = link.get("href", "")
                    if (name and len(name) <= 200 and len(href) <= 2048
                            and re.fullmatch(r"https://attackers\.net/actress/detail/\d+/?", href)):
                        actresses.append(name)
                    else:
                        actress_rows_valid = False
                continue
            if len(labels) != 1:
                continue
            if len(cells) == 1:
                if labels[0] in values:
                    values[labels[0]].append(" ".join(" ".join(cells[0].itertext()).split()))
        catalog = values["品番"]
        catalog_tokens = []
        if len(catalog) == 1:
            catalog_tokens = re.findall(r"[A-Za-z0-9-]+", catalog[0])
        product_tokens = [token.upper() for token in catalog_tokens if token.upper() not in {"DVD", "BD", "BLURAY", "BLU-RAY"}]
        if len(catalog) != 1 or product_tokens != [expected]:
            return None

        result = {}
        if actress_rows == 1 and actress_rows_valid and actresses:
            result["_actress_evidence"] = {
                "source": "attackers-official", "number": expected,
                "names": list(dict.fromkeys(actresses)),
            }
        directors = {value for value in values["監督"] if value and value not in {"-", "－", "なし"}}
        if len(directors) == 1:
            director = next(iter(directors))
            if len(director) <= 200:
                result["directors"] = [{"name": clean_text(director)}]

        runtimes = {value for value in values["収録時間"] if value}
        if len(runtimes) == 1:
            runtime = re.fullmatch(r"(?:DVD\s*)?(\d{1,3})\s*分", next(iter(runtimes)), re.I)
            if runtime and 1 <= int(runtime.group(1)) <= 999:
                result["runtime_minutes"] = int(runtime.group(1))

        official_metadata = " ".join(" ".join(html.xpath("//title/text() | //meta/@content")).split())
        maker_marker = all(marker in official_metadata for marker in ("AVメーカー", "ATTACKERS", "アタッカーズ", "公式"))
        if maker_marker:
            result["production_companies"] = [{"name": "アタッカーズ"}]
        return result or None

    def supplement(self, number: str) -> dict | None:
        match = re.fullmatch(r"ADN-(\d{1,7})", number or "", re.I)
        if not match:
            return None
        catalog = "ADN" + match.group(1)
        html, _ = self._load("/works/detail/" + catalog)
        return self._parse(html, number)

    async def async_supplement(self, number: str) -> dict | None:
        match = re.fullmatch(r"ADN-(\d{1,7})", number or "", re.I)
        if not match:
            return None
        catalog = "ADN" + match.group(1)
        html, _ = await self._aload("/works/detail/" + catalog)
        return self._parse(html, number)
