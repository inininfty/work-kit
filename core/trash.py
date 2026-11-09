"""Recoverable deletion: validate all, save manifest, copy/verify all, then remove."""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config, ledger, paths


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12]


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(p: Path) -> dict:
    if paths.linked(p):
        raise ValueError("拒绝链接")
    if p.is_file():
        return {"type": "file", "hashes": {"": sha(p)}, "dirs": []}
    if not p.is_dir():
        raise ValueError("仅支持普通文件和目录")
    hashes, dirs = {}, []
    for child in sorted(p.rglob("*")):
        if paths.linked(child):
            raise ValueError("目录包含链接或联接点")
        rel = child.relative_to(p).as_posix()
        if child.name.casefold() == ".git":
            raise ValueError("目录包含 .git，拒绝整目录删除")
        if child.is_file():
            hashes[rel] = sha(child)
        elif child.is_dir():
            dirs.append(rel)
        else:
            raise ValueError("目录包含特殊文件")
    return {"type": "dir", "hashes": hashes, "dirs": dirs}


def root(kit: Path) -> Path:
    return kit / "data" / "trash"


def plan(ws: Path, kit: Path, rels: list[str], readonly: list[Path] | None = None):
    if readonly is None:
        readonly = config.readonly_roots() if config.exists() else []
    selected, errors = [], []
    for rel in rels:
        try:
            p = paths.reference(ws, rel)
            paths.mutation(ws, Path(rel) if Path(rel).is_absolute() else ws / rel, kit, readonly)
            if not p.exists():
                raise ValueError("路径不存在")
            if any(paths.under(p, other) or paths.under(other, p) for other in selected):
                raise ValueError("目标重复或父子目录重叠")
            snapshot(p)
            selected.append(p)
        except (OSError, ValueError) as exc:
            errors.append(f"{rel}: {exc}")
    return ([] if errors else selected), errors


def _save(dest: Path, manifest: dict) -> None:
    ledger.atomic(dest / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)


def _check_partial(p: Path, expected: dict) -> None:
    """An interrupted directory removal may leave only a subset of verified bytes."""
    if not p.exists():
        return
    if expected["type"] == "file":
        if not p.is_file() or snapshot(p) != expected:
            raise FileExistsError("恢复位置含不同文件，拒绝覆盖")
        return
    if not p.is_dir() or paths.linked(p):
        raise FileExistsError("恢复位置不是原目录")
    allowed_files, allowed_dirs = expected["hashes"], set(expected["dirs"])
    for child in p.rglob("*"):
        if paths.linked(child):
            raise FileExistsError("部分恢复位置包含链接")
        rel = child.relative_to(p).as_posix()
        if child.is_dir():
            if rel not in allowed_dirs:
                raise FileExistsError(f"恢复位置包含未登记目录：{rel}")
        elif child.is_file():
            if rel not in allowed_files or sha(child) != allowed_files[rel]:
                raise FileExistsError(f"恢复位置包含不同文件：{rel}")
        else:
            raise FileExistsError("恢复位置包含特殊文件")


def _restore_missing(src: Path, dst: Path, expected: dict) -> None:
    if expected["type"] == "file":
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return
    dst.mkdir(parents=True, exist_ok=True)
    for rel in expected["dirs"]:
        (dst / rel).mkdir(parents=True, exist_ok=True)
    for rel in expected["hashes"]:
        target = dst / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, target)


def trash(ws: Path, kit: Path, selected: list[Path]) -> dict:
    # Revalidate at execution time; a printed plan does not freeze filesystem state.
    valid, errors = plan(ws, kit, [str(p) for p in selected])
    if errors or not valid:
        raise ValueError("; ".join(errors) or "没有删除目标")
    manifest = {"schema": 2, "stamp": stamp(), "ts": datetime.now(timezone.utc).isoformat(),
                "state": "planned", "items": []}
    for p in valid:
        manifest["items"].append({"rel": p.relative_to(ws.resolve()).as_posix(),
                                  "snapshot": snapshot(p), "removed": False})
    dest = root(kit) / manifest["stamp"]
    dest.mkdir(parents=True, exist_ok=False)
    _save(dest, manifest)
    for item, p in zip(manifest["items"], valid):
        _copy(p, dest / "payload" / item["rel"])
    for item, p in zip(manifest["items"], valid):
        if snapshot(p) != item["snapshot"] or snapshot(dest / "payload" / item["rel"]) != item["snapshot"]:
            raise RuntimeError("复制验证失败；未开始删除，备份已保留")
    manifest["state"] = "verified"
    _save(dest, manifest)
    for item, p in zip(manifest["items"], valid):
        if snapshot(p) != item["snapshot"]:
            raise RuntimeError("源文件在验证后变化；已停止，见 manifest 中的 removed 状态")
        shutil.rmtree(p) if p.is_dir() else p.unlink()
        item["removed"] = True
        _save(dest, manifest)
    manifest["state"] = "trashed"
    _save(dest, manifest)
    return manifest


def listing(kit: Path) -> list[dict]:
    base = root(kit)
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.glob("*/manifest.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            out.append({"stamp": p.parent.name, "ts": "unknown", "items": [], "error": str(exc)})
    return out


def restore(ws: Path, kit: Path, ts: str) -> dict:
    if paths.relative(ts) != ts or "/" in ts or "\\" in ts:
        raise ValueError("批次名必须是单个目录名")
    dest = paths.resolve(root(kit), ts)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 2:
        raise ValueError("旧批次缺少完整目录哈希；请人工核对旧备份，不自动覆盖")
    operations = []
    seen = []
    # Validate every entry and every backup before writing any restored file.
    for item in manifest["items"]:
        rel = paths.relative(item["rel"])
        src, dst = paths.resolve(dest / "payload", rel), paths.resolve(ws, rel)
        paths.mutation(ws, dst, kit, config.readonly_roots())
        if any(paths.under(dst, p) or paths.under(p, dst) for p in seen):
            raise ValueError("恢复清单包含重复或重叠目标")
        seen.append(dst)
        if not src.exists() or snapshot(src) != item["snapshot"]:
            raise ValueError("备份缺失或哈希不一致；拒绝恢复")
        _check_partial(dst, item["snapshot"])
        operations.append((src, dst, item["snapshot"]))
    for src, dst, expected in operations:
        _restore_missing(src, dst, expected)
        if snapshot(dst) != expected:
            raise RuntimeError("恢复回读失败；备份仍保留")
    manifest["state"] = "restored"
    _save(dest, manifest)
    return manifest
