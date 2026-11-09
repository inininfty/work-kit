"""Portable path validation. Whitespace inside a filename is legitimate."""
from __future__ import annotations

import ntpath
import stat
from pathlib import Path, PurePosixPath


def relative(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("路径为空或首尾有空白")
    text = value.replace("\\", "/")
    if text.startswith("/") or ntpath.splitdrive(text)[0] or ":" in text:
        raise ValueError("必须使用不带盘符的相对路径")
    if any(ord(c) < 32 for c in text) or ".." in text.split("/"):
        raise ValueError("路径含控制字符或 ..")
    for part in text.split("/"):
        if part not in ("", ".") and (part.endswith((".", " ")) or any(c in part for c in '<>"|?*')):
            raise ValueError("路径含跨平台歧义字符或末尾点/空格")
    rel = PurePosixPath(text).as_posix()
    if rel in ("", "."):
        raise ValueError("不能把根目录当作文件路径")
    return rel


def under(child: Path, parent: Path) -> bool:
    return child.resolve().is_relative_to(parent.resolve())


def resolve(root: Path, value: str) -> Path:
    target = root / relative(value)
    cursor = target
    while cursor != root and cursor.parent != cursor:
        if linked(cursor):
            raise ValueError("路径经过符号链接或联接点")
        cursor = cursor.parent
    if not under(target, root):
        raise ValueError("路径解析后在工作区外")
    return target


def reference(root: Path, value: str | Path) -> Path:
    p = Path(value)
    return (p if p.is_absolute() else root / p).resolve()


def linked(p: Path) -> bool:
    try:
        info = p.lstat()
    except FileNotFoundError:
        return False
    return p.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def mutation(root: Path, target: Path, kit: Path, readonly: list[Path]) -> None:
    ws, rp, kp = root.resolve(), target.resolve(), kit.resolve()
    if rp == ws or not under(rp, ws):
        raise ValueError("拒绝工作区根目录或工作区外路径")
    if under(rp, kp) or under(kp, rp):
        raise ValueError("拒绝壳目录及其祖先")
    if any(part.casefold() == ".git" for part in rp.relative_to(ws).parts):
        raise ValueError("拒绝 .git")
    for ro in readonly:
        ro = reference(ws, ro)
        if under(rp, ro) or under(ro, rp):
            raise ValueError("目标与只读根相交")
    p = target.absolute()
    while p != ws and p.parent != p:
        if linked(p):
            raise ValueError("拒绝通过符号链接或联接点修改")
        p = p.parent


def shell(root: Path, value: str) -> Path:
    rel = relative(value)
    if rel.split("/")[0].casefold() in {"data", "config", "packs", ".git", ".runtime"}:
        raise ValueError("升级清单包含受保护目录")
    target = resolve(root, rel)
    for p in (target, *target.parents):
        if p == root.resolve():
            break
        if linked(p):
            raise ValueError("升级目标包含链接")
    return target
