#!/usr/bin/env python3
"""work-kit 日常命令 —— 手不用碰 JSON。

  python bin/kit.py status
  python bin/kit.py task new "标题" [--next "第一步"] [--role main]
  python bin/kit.py task list [--open]
  python bin/kit.py task doing T-001
  python bin/kit.py task done T-001 --evidence "怎么验的" [--change C-001]
  python bin/kit.py change new T-001 --files a.py,b.py [--modified x.md] [--budget 100]
  python bin/kit.py change approve C-001
  python bin/kit.py log C-001 --created a.py --modified b.py [--lines 120] [--complete]
  python bin/kit.py attach        # 重新写工作区根的规则指针
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))

from core import attach as attach_mod  # noqa: E402
from core import config, ledger, trash as trash_mod, views, paths, artifacts, experience  # noqa: E402


def _rows(name: str) -> list[dict]:
    rows, bad = ledger.read(name)
    if bad:
        raise SystemExit("账本里有坏行，先修：" + "; ".join(bad))
    return rows


def _find(rows: list[dict], rid: str) -> dict:
    hit = [r for r in rows if r.get("id") == rid]
    if len(hit) != 1:
        raise SystemExit(f"{rid} 命中 {len(hit)} 行，必须有且只有一行")
    return hit[0]


def _split(value) -> list[str]:
    if not value:
        return []
    values = value if isinstance(value, list) else [value]
    return [paths.relative(part.strip()) for item in values for part in item.split(",") if part.strip()]


def _human(n: int | None) -> str:
    if n is None:
        return "?"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _count_lines(ws: Path, files: list[str]) -> int:
    total = 0
    for rel in files:
        p = ws / rel
        try:
            if p.is_file():
                total += len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            pass
    return total


def cmd_status(_: argparse.Namespace) -> int:
    print(views.summary())
    return 0


def cmd_attach(_: argparse.Namespace) -> int:
    action = attach_mod.attach(config.workspace(), KIT)
    print(f"工作区根 AGENTS.md：{action}")
    return 0


def cmd_task_new(a: argparse.Namespace) -> int:
    row = ledger.create("task", "T", {"rev": 1, "title": a.title, "status": "todo",
        "requirement_ref": a.ref or "用户口头要求", "atomic": a.atomic or "做完可验收",
        "role": a.role, "next": a.next or ""})
    print(f"{row['id']} 已建（todo）：{a.title}")
    return 0


def cmd_task_list(a: argparse.Namespace) -> int:
    rows = _rows("task")
    if a.open:
        rows = [r for r in rows if r.get("status") in ("todo", "doing", "blocked")]
    if not rows:
        print("没有任务。用 task new 建一条。")
        return 0
    for r in rows:
        print(f"{r.get('id')}  [{r.get('status')}]  {r.get('title')}")
        nxt = str(r.get("next") or "").strip()
        if nxt:
            print(f"        下一步：{nxt[:70]}")
    return 0


def cmd_task_set(a: argparse.Namespace) -> int:
    def edit(rows):
        row = _find(rows["task"], a.id)
        if a.cmd == "done":
            artifacts.evidence(config.workspace(), a.evidence)
            if not a.change:
                raise ValueError("完成任务必须引用已经收口且属于该任务的清单 --change")
            row["status"] = "done"
            row["evidence"] = a.evidence
            if a.change:
                change = _find(rows["change"], a.change)
                if change["for_task"] != a.id or change["status"] != "done":
                    raise ValueError("完成清单与任务不一致")
                row["completed_by"] = sorted(set(row.get("completed_by", [])) | {a.change})
        else:
            if not any(c["for_task"] == a.id and c["status"] == "doing" and c.get("approved_at") for c in rows["change"]):
                raise ValueError("开始任务前必须有已批准清单")
            row["status"] = "doing"
        row["rev"] = int(row.get("rev", 1)) + 1
    ledger.transaction(edit)
    print(f"{a.id} → {a.cmd}")
    return 0


def cmd_change_new(a: argparse.Namespace) -> int:
    task = _find(_rows("task"), a.task)
    if task["status"] in ("done", "verified", "abandoned"):
        raise ValueError("终态任务不能直接追加工作；新建任务或明确重新开启")
    created, modified = _split(a.files), _split(a.modified)
    for rel in created + modified:
        paths.mutation(config.workspace(), paths.resolve(config.workspace(), rel), KIT, config.readonly_roots())
    row = ledger.create("change", "C", {"for_task": task["id"],
        "requirement_ref": a.ref or task.get("requirement_ref", "用户口头要求"),
        "approved_by": "pending", "approved_at": None, "new_files": created,
        "modified_files": modified, "line_budget": a.budget, "lines_written": None,
        "do_not_touch": _split(a.do_not_touch), "outside_change_list": [], "status": "proposed"})
    print(f"{row['id']} 已建（proposed，未批准）")
    return 0


def cmd_change_approve(a: argparse.Namespace) -> int:
    def edit(rows):
        change = _find(rows["change"], a.id)
        if change["status"] != "proposed":
            raise ValueError("只能批准 proposed 清单")
        change.update(approved_by="用户", approved_at=ledger.stamp(), status="doing")
        task = _find(rows["task"], change["for_task"])
        if task["status"] == "todo":
            task.update(status="doing", rev=task.get("rev", 1) + 1)
    ledger.transaction(edit)
    print(f"{a.id} 已记录批准（不代表对用户身份做过认证）")
    return 0


def cmd_log(a: argparse.Namespace) -> int:
    created, modified = _split(a.created), _split(a.modified)
    ws = config.workspace()
    receipts = {}
    for rel in created + modified:
        paths.mutation(ws, paths.resolve(ws, rel), KIT, config.readonly_roots())
        receipts[rel] = artifacts.capture(ws, rel)
    evidence = a.evidence or ["file:" + rel for rel in created + modified if paths.resolve(ws, rel).is_file()]
    if a.complete:
        artifacts.evidence(ws, evidence)
    def edit(rows):
        change = _find(rows["change"], a.change)
        if change["status"] != "doing" or not change.get("approved_at"):
            raise ValueError("只能收口已批准且进行中的清单")
        if set(created) != set(change["new_files"]) or set(modified) != set(change["modified_files"]):
            raise ValueError("产出与承诺的新增/修改文件不一致")
        for rel in receipts:
            if artifacts.capture(ws, rel) != receipts[rel]:
                raise ValueError("等待写锁时产出变化；请重新检查后收口")
            target = paths.resolve(ws, rel)
            if any(paths.under(target, paths.reference(ws, guard)) for guard in change.get("do_not_touch", [])):
                raise ValueError("产出命中 do_not_touch")
        mid = ledger.allocate(rows["modification"], "M")
        row = {"id": mid, "ts": ledger.stamp(), "for_task": change["for_task"],
            "via_change_list": change["id"], "files_created": created, "files_modified": modified,
            "files_deleted": [], "artifacts": receipts, "outside_change_list": [],
            "gate_result": {"before": "NOT RUN", "after": "NOT RUN"}, "note": a.note or ""}
        rows["modification"].append(row)
        change.update(status="done", lines_written=a.lines if a.lines is not None else _count_lines(ws, created + modified))
        if a.complete:
            task = _find(rows["task"], change["for_task"])
            task.update(status="done", evidence=evidence, completed_by=[change["id"]], rev=task.get("rev", 1) + 1)
        return mid
    mid = ledger.transaction(edit)
    print(f"{mid} 已记；{a.change} → done")
    return 0


def _describe(p: Path) -> tuple[str, int]:
    """(人读描述, 字节数合计)。声明要给的是「路径 + 影响」，不是裸路径。"""
    if p.is_file():
        size = p.stat().st_size
        return f"[文件] {p}  {_human(size)}", size
    files = [x for x in p.rglob("*") if x.is_file()]
    total = sum(x.stat().st_size for x in files)
    return f"[目录] {p}  {len(files)} 个文件，合计 {_human(total)}", total


def cmd_remove(a: argparse.Namespace) -> int:
    ws = config.workspace()
    ok, errs = trash_mod.plan(ws, KIT, a.paths)
    if errs:
        print("整单拒收，磁盘零变化：")
        for e in errs:
            print(f"  - {e}")
        return 1
    lines: list[str] = []
    total = 0
    for p in ok:
        text, size = _describe(p)
        lines.append(text)
        total += size
    if not a.yes:
        print(f"删除计划（{len(ok)} 项，合计 {_human(total)}）：")
        for line in lines:
            print(f"  {line}")
        print()
        print("影响：这些路径会从工作区移走。")
        print("可逆：进 data/trash/<stamp>/，带 manifest 与 SHA256，可 restore 放回并校验。")
        print("复核：确认没有不该删的路径后，再加 --yes 执行；不带 --yes 时磁盘零变化。")
        return 0
    manifest = trash_mod.trash(ws, KIT, ok)
    print(f"已移入回收站 {manifest['stamp']}（{len(manifest['items'])} 项，哈希已核）")
    print(f"如需撤回：python bin/kit.py restore {manifest['stamp']}")
    return 0


def cmd_restore(a: argparse.Namespace) -> int:
    manifest = trash_mod.restore(config.workspace(), KIT, a.stamp)
    print(f"已放回 {manifest['stamp']}（{len(manifest['items'])} 项，哈希已核）")
    return 0


def cmd_trash(a: argparse.Namespace) -> int:
    batches = trash_mod.listing(KIT)
    if not batches:
        print("回收站是空的。")
        return 0
    for m in batches:
        names = ", ".join(i["rel"] for i in m["items"][:5])
        print(f"{m['stamp']}  {m['ts']}  {len(m['items'])} 项  {names}")
    return 0


def cmd_update(a: argparse.Namespace) -> int:
    cfg = config.load()
    state = config.state()
    src = str(Path(a.source).expanduser().resolve()) if a.source else state.get("update_source") or cfg.get("update_source")
    if not src:
        print("第一次要指上游：python bin/kit.py update --source <新的 work-kit 目录>")
        return 2
    steps = [
        [sys.executable, str(KIT / "bin" / "upgrade.py"), "--source", src, "--apply"],
        [sys.executable, str(KIT / "bin" / "kit.py"), "attach"],
        [sys.executable, str(KIT / "bin" / "check.py"), "all"],
    ]
    for cmd in steps:
        print(f"\n$ {' '.join(cmd[1:])}")
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"[FAIL] 这一步退出码 {rc}，后续不再执行")
            return rc
    state = config.state()
    state["update_source"] = src
    config.save_state(state)
    print("\n升级完成。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kit", description="work-kit 日常命令")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    recall = sub.add_parser("recall")
    recall.add_argument("query")
    recall.add_argument("--limit", type=int, default=5)
    recall.set_defaults(fn=lambda a: (print(json.dumps(experience.recall(a.query, a.limit), ensure_ascii=False, indent=2)) or 0))
    sub.add_parser("recover").set_defaults(fn=lambda _: (print("已恢复" if ledger.recover() else "无中断事务") or 0))
    sub.add_parser("attach").set_defaults(fn=cmd_attach)

    t = sub.add_parser("task").add_subparsers(dest="cmd", required=True)
    p = t.add_parser("new")
    p.add_argument("title")
    p.add_argument("--next", default="")
    p.add_argument("--ref", default="")
    p.add_argument("--atomic", default="")
    p.add_argument("--role", default="main")
    p.set_defaults(fn=cmd_task_new)
    p = t.add_parser("list")
    p.add_argument("--open", action="store_true")
    p.set_defaults(fn=cmd_task_list)
    for name in ("doing", "done"):
        p = t.add_parser(name)
        p.add_argument("id")
        p.add_argument("--evidence", action="append")
        p.add_argument("--change")
        p.set_defaults(fn=cmd_task_set)

    c = sub.add_parser("change").add_subparsers(dest="cmd", required=True)
    p = c.add_parser("new")
    p.add_argument("task")
    p.add_argument("--files", action="append", default=[])
    p.add_argument("--modified", action="append", default=[])
    p.add_argument("--budget", type=int, default=0)
    p.add_argument("--ref", default="")
    p.add_argument("--do-not-touch", action="append", default=[])
    p.set_defaults(fn=cmd_change_new)
    p = c.add_parser("approve")
    p.add_argument("id")
    p.set_defaults(fn=cmd_change_approve)

    p = sub.add_parser("log")
    p.add_argument("change")
    p.add_argument("--created", action="append", default=[])
    p.add_argument("--modified", action="append", default=[])
    p.add_argument("--lines", type=int, default=None)
    p.add_argument("--note", default="")
    p.add_argument("--evidence", action="append", default=[])
    p.add_argument("--complete", action="store_true")
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("remove")
    p.add_argument("paths", nargs="+")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_remove)

    p = sub.add_parser("restore")
    p.add_argument("stamp")
    p.set_defaults(fn=cmd_restore)

    sub.add_parser("trash").set_defaults(fn=cmd_trash)

    p = sub.add_parser("update")
    p.add_argument("--source", default="")
    p.set_defaults(fn=cmd_update)
    return ap


def main(argv: list[str]) -> int:
    try:  # 让子进程输出与自己的输出按真实顺序出现，不要先攒后吐
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    if not config.exists() and argv[1:2] not in (["attach"], []):
        print("[FAIL] 未初始化：先跑 python bin/init.py")
        return 2
    args = build_parser().parse_args(argv[1:])
    try:
        return int(args.fn(args))
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
