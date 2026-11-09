"""Filesystem receipts bind a completion record to bytes, not content correctness."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import paths
from .trash import snapshot


def capture(ws: Path, rel: str) -> dict:
    p = paths.resolve(ws, rel)
    if not p.exists():
        raise ValueError(f"产出不存在：{rel}")
    state = snapshot(p)
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"kind": state["type"], "sha256": digest}


def evidence(ws: Path, values: list[str]) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError("必须提供至少一个文件证据；推荐 file:相对路径")
    found = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("证据必须是字符串")
        rel = value[5:] if value.startswith("file:") else value
        p = Path(rel)
        if p.is_absolute():
            if not p.is_file():
                raise ValueError("证据文件不存在")
            found.append(str(p))
        else:
            p = paths.resolve(ws, rel)
            if not p.is_file():
                raise ValueError(f"证据文件不存在：{rel}")
            found.append(rel)
    return found


def check(rows: dict, ws: Path) -> list[str]:
    latest, errors = {}, []
    for m in rows["modification"]:
        for rel in (m.get("files_created") or []) + (m.get("files_modified") or []):
            latest[rel] = m
        for rel in m.get("files_deleted") or []:
            latest[rel] = None
    for rel, m in latest.items():
        if m is None:
            continue
        try:
            current = capture(ws, rel)
            saved = (m.get("artifacts") or {}).get(rel)
            if saved is not None and current != saved:
                errors.append(f"{rel}: 产出哈希与最近记录不一致")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    for task in rows["task"]:
        if task.get("status") not in ("done", "verified"):
            continue
        try:
            evidence(ws, task.get("evidence"))
        except (OSError, ValueError) as exc:
            errors.append(f"{task.get('id')}: {exc}")
    return errors
