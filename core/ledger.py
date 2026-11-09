"""Serialized ID allocation and recoverable multi-ledger commits; standard library only."""
from __future__ import annotations

import copy
import errno
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, policy

LEDGERS = {"task": "task-ledger.jsonl", "change": "change-list.jsonl",
           "modification": "modification-ledger.jsonl"}


def stamp() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def path(name: str) -> Path:
    return config.DATA / LEDGERS[name]


def atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        if p.read_bytes() != text.encode("utf-8"):
            raise RuntimeError("写入回读不一致")
    finally:
        tmp.unlink(missing_ok=True)


class Lock:
    """OS-managed byte lock. A live lock never expires; process exit releases it."""
    def __init__(self, name: str = "all", timeout: float = 30.0, stale: float = 120.0):
        self.path = config.DATA / ".ledger.lock"
        self.timeout = timeout
        self.handle = None

    def __enter__(self):
        config.DATA.mkdir(parents=True, exist_ok=True)
        from .paths import linked
        if linked(config.DATA) or linked(self.path):
            raise ValueError("账本目录和锁文件不得是链接")
        self.handle = self.path.open("a+b")
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    self.handle.close()
                    raise
                if time.monotonic() >= deadline:
                    self.handle.close()
                    raise TimeoutError("等待账本写锁超时") from exc
                time.sleep(0.02)

    def __exit__(self, *exc):
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


def journal() -> Path:
    return config.DATA / ".ledger-pending.json"


def _read(name: str) -> tuple[list[dict], list[str]]:
    p = path(name)
    if not p.is_file():
        return [], [f"缺少账本 {p.name}，先初始化"]
    rows, bad = [], []
    for i, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ValueError("每行必须是有 id 的对象")
            rows.append(row)
        except (ValueError, TypeError) as exc:
            bad.append(f"{p.name}:{i}: {exc}")
    if len({r["id"] for r in rows}) != len(rows):
        bad.append("账本 id 重复")
    return rows, bad


def _recover() -> bool:
    if not journal().exists():
        return False
    tx = json.loads(journal().read_text(encoding="utf-8"))
    if tx.get("schema") != 1 or set(tx.get("files", {})) != set(LEDGERS):
        raise ValueError("事务日志不合法，拒绝自动恢复")
    for text in tx["files"].values():
        ids = []
        for line in text.splitlines():
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ValueError("事务内容损坏")
            ids.append(row["id"])
        if len(set(ids)) != len(ids):
            raise ValueError("事务内容包含重复 id")
    for name, text in tx["files"].items():
        atomic(path(name), text)
    journal().unlink()
    return True


def recover() -> bool:
    with Lock():
        return _recover()


def read(name: str) -> tuple[list[dict], list[str]]:
    with Lock():
        if journal().exists():
            return [], ["存在中断事务；先运行 kit recover，不读取半写状态"]
        return _read(name)


def read_all() -> tuple[dict, list[str]]:
    with Lock():
        if journal().exists():
            return {name: [] for name in LEDGERS}, ["存在中断事务；先运行 kit recover"]
        rows, errors = {}, []
        for name in LEDGERS:
            rows[name], bad = _read(name)
            errors.extend(bad)
        return rows, errors


def allocate(rows: list[dict], prefix: str) -> str:
    nums = [int(r["id"].split("-")[1]) for r in rows
            if r["id"].startswith(prefix + "-") and r["id"].split("-")[1].isdigit()]
    return f"{prefix}-{max(nums, default=0) + 1:03d}"


def transaction(edit):
    with Lock():
        _recover()
        rows = {}
        for name in LEDGERS:
            data, bad = _read(name)
            if bad:
                raise ValueError("; ".join(bad))
            rows[name] = data
        before = copy.deepcopy(rows)
        result = edit(rows)
        for name in LEDGERS:
            old = {r["id"]: r for r in before[name]}
            if not set(old).issubset({r["id"] for r in rows[name]}):
                raise ValueError("拒绝删除历史账本行")
            if len({r["id"] for r in rows[name]}) != len(rows[name]):
                raise ValueError("拒绝重复 id")
            for row in rows[name]:
                if row["id"] not in old:
                    if name == "task" and row.get("status") != "todo":
                        raise ValueError("新任务必须从 todo 开始")
                    if name == "change" and row.get("status") != "proposed":
                        raise ValueError("新清单必须从 proposed 开始")
                if row["id"] in old and row != old[row["id"]]:
                    if name == "task":
                        policy.task_transition(old[row["id"]], row)
                    elif name == "change":
                        policy.change_transition(old[row["id"]], row)
                    elif name == "modification":
                        raise ValueError("修改账本只追加，不允许改历史")
                    row["ts"] = stamp()
        payload = {name: "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data)
                   for name, data in rows.items()}
        atomic(journal(), json.dumps({"schema": 1, "files": payload}, ensure_ascii=False))
        _recover()
        return copy.deepcopy(result)


def append(name: str, rec: dict, *, dry_run: bool = False) -> dict:
    def edit(rows):
        if any(r["id"] == rec.get("id") for r in rows[name]):
            raise ValueError("id 已存在")
        row = dict(rec, ts=stamp())
        rows[name].append(row)
        return row
    if dry_run:
        with Lock():
            data, bad = _read(name)
            if bad or any(r["id"] == rec.get("id") for r in data):
                raise ValueError("账本损坏或 id 已存在")
            return dict(rec, ts=stamp())
    return transaction(edit)


def create(name: str, prefix: str, rec: dict) -> dict:
    def edit(rows):
        row = dict(rec, id=allocate(rows[name], prefix), ts=stamp())
        rows[name].append(row)
        return row
    return transaction(edit)


def replace(name: str, rec: dict) -> dict:
    def edit(rows):
        hits = [i for i, row in enumerate(rows[name]) if row["id"] == rec.get("id")]
        if len(hits) != 1:
            raise ValueError("替换必须恰好命中一行")
        rows[name][hits[0]] = dict(rec)
        return rows[name][hits[0]]
    return transaction(edit)


def next_id(name: str, prefix: str) -> str:
    """Preview only. Writers allocate via create() while holding the lock."""
    with Lock():
        _recover()
        rows, bad = _read(name)
        if bad:
            raise ValueError("; ".join(bad))
        return allocate(rows, prefix)
