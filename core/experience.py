"""Bounded literal retrieval of user-owned cases/patterns; never auto-promote rules."""
from __future__ import annotations

import json
import re

from . import config


def recall(query: str, limit: int = 5) -> list[dict]:
    words = [w for w in re.split(r"\s+", query.casefold().strip()) if w]
    if not words:
        raise ValueError("检索词不能为空")
    if limit < 1 or limit > 20:
        raise ValueError("limit 必须在 1–20 之间")
    hits = []
    for filename, key in (("negative-examples.json", "cases"), ("patterns.json", "patterns")):
        p = config.DATA / filename
        if not p.is_file():
            raise ValueError(f"经验库缺失：{filename}")
        if p.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("经验库超过直接检索预算；请先建立索引")
        document = json.loads(p.read_text(encoding="utf-8-sig"))
        rows = document.get(key)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError(f"经验库结构不合法：{filename}")
        for row in rows:
            text = json.dumps(row, ensure_ascii=False).casefold()
            score = sum(text.count(word) for word in words)
            if score:
                hits.append({"source": "data/" + filename, "id": row.get("id"),
                             "score": score, "record": row})
    return sorted(hits, key=lambda r: (-r["score"], str(r["id"])))[:limit]
