#!/usr/bin/env python3
"""work-kit 闸门。退出码即结论：0 全过 / 1 有失败 / 2 未初始化。

用法：
  python bin/check.py all       # 全部检查（默认）
  python bin/check.py privacy   # 只扫绝对路径与敏感词
  python bin/check.py package   # 打包前检查，并打印发布白名单
  python bin/check.py status    # 打印状态摘要（按需，不常驻）
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))

from core import config, ledger, views, paths, artifacts  # noqa: E402

TASK_STATUS = {"todo", "doing", "blocked", "done", "verified", "abandoned"}
CHANGE_STATUS = {"proposed", "doing", "done", "abandoned"}

# 盘符绝对路径、Unix 家目录。写成模式而不是字面量——源码里不许出现真路径。
ABS_PATH = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|/(?:Users|home)/)")


def manifest() -> dict:
    return json.loads((KIT / "kit.json").read_text(encoding="utf-8"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------- 五项核心检查 ----------


def load_all() -> tuple[list[tuple[bool, str, str]], dict]:
    out: list[tuple[bool, str, str]] = []
    rows, bad = ledger.read_all()
    if bad:
        return [(False, "三账一致快照", "; ".join(bad))], rows
    for name in ("task", "change", "modification"):
        out.append((True, f"{name} 账本可解析", f"{len(rows[name])} 行"))
    return out, rows


def check_ids(rows: dict) -> list[tuple[bool, str, str]]:
    out = []
    for name, items in rows.items():
        seen, dup = set(), set()
        for r in items:
            i = r.get("id")
            if i in seen:
                dup.add(str(i))
            seen.add(i)
        out.append(
            (
                not dup,
                f"{name} id 唯一",
                ("重复：" + ",".join(sorted(dup))) if dup else f"{len(seen)} 个",
            )
        )
    return out


def check_task_status(rows: list[dict]) -> tuple[bool, str, str]:
    bad: list[str] = []
    for r in rows:
        tid = r.get("id", "?")
        st = r.get("status")
        if st not in TASK_STATUS:
            bad.append(f"{tid} 状态 {st!r} 不在枚举内")
            continue
        if st in ("done", "verified") and not r.get("evidence"):
            bad.append(f"{tid} 是 {st} 却没有 evidence")
        if st == "verified" and not r.get("verified_by"):
            bad.append(f"{tid} 是 verified 却没有 verified_by")
        if st == "blocked" and not r.get("blocker"):
            bad.append(f"{tid} 是 blocked 却没有 blocker")
        if st in ("todo", "doing") and not r.get("role"):
            bad.append(f"{tid} 是 {st} 却没有 role（下一步归谁）")
    return (not bad, "任务状态与必填字段", "; ".join(bad[:5]) if bad else f"{len(rows)} 行合规")


def check_change_status(rows: list[dict]) -> tuple[bool, str, str]:
    bad: list[str] = []
    for r in rows:
        cid = r.get("id", "?")
        st = r.get("status")
        if st not in CHANGE_STATUS:
            bad.append(f"{cid} 状态 {st!r} 不在枚举内")
            continue
        if st == "proposed" and r.get("approved_at"):
            bad.append(f"{cid} 还没批准却填了 approved_at")
        if st in ("doing", "done") and (not r.get("approved_at") or r.get("approved_by") in (None, "pending")):
            bad.append(f"{cid} 缺少批准记录")
        if st == "done" and not r.get("approved_at"):
            bad.append(f"{cid} 是 done 却没有 approved_at（没批准就完成）")
        if st == "done" and not isinstance(r.get("lines_written"), int):
            bad.append(f"{cid} 是 done 却没有 lines_written")
    return (not bad, "清单状态与必填字段", "; ".join(bad[:5]) if bad else f"{len(rows)} 行合规")


# ---------- 0.4.0 新增：路径、边界、证据、承诺对账 ----------

_BAD_REL = re.compile(r"^(?:[A-Za-z]:|/|\\\\)")


def _clean_rel(f: str) -> str | None:
    try:
        paths.relative(f)
    except ValueError as exc:
        return str(exc)
    return None


def check_paths(rows: dict) -> tuple[bool, str, str]:
    bad: list[str] = []
    for c in rows["change"]:
        for f in (c.get("new_files") or []) + (c.get("modified_files") or []):
            why = _clean_rel(str(f))
            if why:
                bad.append(f"{c.get('id')}: {f!r} {why}")
    for m in rows["modification"]:
        for f in (
            list(m.get("files_created") or [])
            + list(m.get("files_modified") or [])
            + list(m.get("files_deleted") or [])
        ):
            why = _clean_rel(str(f))
            if why:
                bad.append(f"{m.get('id')}: {f!r} {why}")
    return (not bad, "申报路径是纯相对路径", "; ".join(bad[:5]) if bad else "全部干净")


def _norm(p: object) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


def _under(child: str, parent: str) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p + "/")


def check_boundary(rows: dict, ws: Path, readonly: list[str]) -> tuple[bool, str, str]:
    bad = []
    changes = {c.get("id"): c for c in rows["change"]}
    for m in rows["modification"]:
        change = changes.get(m.get("via_change_list"), {})
        for rel in (m.get("files_created") or []) + (m.get("files_modified") or []) + (m.get("files_deleted") or []):
            try:
                target = paths.resolve(ws, rel)
                paths.mutation(ws, target, KIT, [paths.reference(ws, r) for r in readonly])
                if any(paths.under(target, paths.reference(ws, g)) for g in change.get("do_not_touch", [])):
                    raise ValueError("命中 do_not_touch")
            except (OSError, ValueError) as exc:
                bad.append(f"{m.get('id')}: {rel}: {exc}")
    return not bad, "修改记录没有越界", "; ".join(bad[:5])


def check_promises(rows: dict) -> tuple[bool, str, str]:
    """清单 ↔ 修改账本双向对账：承诺的文件要真被改，改了的文件要真被承诺。"""
    bad: list[str] = []
    for c in rows["change"]:
        if c.get("status") != "done":
            continue
        promised = set(map(str, (c.get("new_files") or []) + (c.get("modified_files") or [])))
        done: set[str] = set()
        for m in rows["modification"]:
            if m.get("via_change_list") == c.get("id"):
                done |= set(map(str, (m.get("files_created") or []) + (m.get("files_modified") or []) + (m.get("files_deleted") or [])))
        missing = sorted(promised - done)
        extra = sorted(done - promised - set(map(str, c.get("outside_change_list") or [])))
        if missing:
            bad.append(f"{c.get('id')}: 承诺了但没做 {missing[:3]}")
        if extra:
            bad.append(f"{c.get('id')}: 做了但没承诺 {extra[:3]}")
    return (not bad, "清单与修改账本双向对账", "; ".join(bad[:5]) if bad else f"{len(rows['change'])} 行对账通过")


def _looks_like_path(s: str) -> bool:
    t = s.strip()
    if not t or any(ch.isspace() for ch in t):
        return False
    return ("/" in t) or ("\\" in t)


def check_evidence(rows: list[dict], ws: Path) -> tuple[bool, str, str]:
    bad = []
    for task in rows:
        if task.get("status") in ("done", "verified"):
            try:
                artifacts.evidence(ws, task.get("evidence"))
            except (OSError, ValueError) as exc:
                bad.append(f"{task.get('id')}: {exc}")
    return not bad, "完成证据文件存在", "; ".join(bad[:5])


def check_links(rows: dict) -> tuple[bool, str, str]:
    tids = {r.get("id") for r in rows["task"]}
    cids = {r.get("id") for r in rows["change"]}
    bad: list[str] = []
    for m in rows["modification"]:
        if m.get("for_task") not in tids:
            bad.append(f"{m.get('id')} 指向不存在的任务 {m.get('for_task')}")
        via = m.get("via_change_list")
        if via and via not in cids:
            bad.append(f"{m.get('id')} 引用了不存在的清单 {via}")
        change = next((c for c in rows["change"] if c.get("id") == via), None)
        if change is None or change.get("for_task") != m.get("for_task"):
            bad.append(f"{m.get('id')} 的任务和清单归属不一致")
    for t in rows["task"]:
        for c in t.get("completed_by") or []:
            if c not in cids:
                bad.append(f"{t.get('id')} 的 completed_by 含不存在的 {c}")
            elif not any(row.get("id") == c and row.get("for_task") == t.get("id") and row.get("status") == "done" for row in rows["change"]):
                bad.append(f"{t.get('id')} 的完成清单未完成或属于其他任务")
    for c in rows["change"]:
        if c.get("for_task") not in tids:
            bad.append(f"{c.get('id')} 指向不存在的任务 {c.get('for_task')}")
    return (not bad, "账本之间的引用", "; ".join(bad[:5]) if bad else "全部可解析")


def check_coverage(rows: dict) -> tuple[bool, str, str]:
    used = {m.get("via_change_list") for m in rows["modification"]}
    bad = [
        f"{c.get('id')} 是 done 但修改账本没有引用它"
        for c in rows["change"]
        if c.get("status") == "done" and c.get("id") not in used
    ]
    return (
        not bad,
        "清单 done 必须有落地记录",
        "; ".join(bad[:5]) if bad else f"{len(rows['change'])} 行对账通过",
    )


# ---------- 壳与包 ----------


def shell_files(man: dict) -> list[str]:
    return [r for r in man["shell"] if not r.startswith(("data/", "config/"))]


def unknown_files(man: dict) -> list[str]:
    allowed = set(man["shell"])
    out: list[str] = []
    for p in sorted(KIT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(KIT).as_posix()
        if rel in allowed:
            continue
        if rel.startswith(("data/", "config/", ".runtime/")) or rel in (config.state().get("retired", []) if config.exists() else []):
            continue
        if "__pycache__" in p.parts or p.suffix == ".pyc" or rel.endswith(".new"):
            continue
        out.append(rel)
    return out


def privacy_hits(man: dict, tokens: list[str]) -> list[str]:
    hits: list[str] = []
    for rel in shell_files(man):
        p = KIT / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            hits.append(f"{rel}: 不是 UTF-8 文本，拒绝打包前无法审计")
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if ABS_PATH.search(line):
                hits.append(f"{rel}:{n}: 出现绝对路径")
            for t in tokens:
                if t and t in line:
                    hits.append(f"{rel}:{n}: 含敏感词 {t!r}")
    return hits


# ---------- 输出 ----------


def emit(results: list[tuple[bool, str, str]]) -> int:
    failed = 0
    for good, name, detail in results:
        if good:
            print(f"[OK  ] {name}" + (f" —— {detail}" if detail else ""))
        else:
            failed += 1
            print(f"[FAIL] {name} —— {detail}")
    print()
    print(f"{len(results) - failed} 项通过，{failed} 项失败")
    return 0 if failed == 0 else 1


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "all"

    if cmd == "status":
        print(views.summary())
        return 0

    if cmd not in ("all", "privacy", "package"):
        print("[FAIL] 未知检查命令")
        return 2
    man = manifest()

    results: list[tuple[bool, str, str]] = []
    if cmd == "all":
        pending = []
        for receipt in (KIT / ".runtime/upgrades").glob("*/upgrade.json"):
            try:
                if json.loads(receipt.read_text(encoding="utf-8")).get("state") not in ("complete", "rolled_back"):
                    pending.append(receipt.parent.name)
            except (OSError, ValueError):
                pending.append(receipt.parent.name)
        results.append((not pending, "无未完成升级", ", ".join(pending)))

    if cmd == "all":
        if not config.exists():
            print("[FAIL] 未初始化：缺少 config/kit.config.json")
            print(f"       先跑：python \"{KIT / 'bin' / 'init.py'}\"")
            print("       在配置生成之前，本壳不判断任何写入是否被允许。")
            return 2
        try:
            cfg = config.load()
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] 配置读不了：{e}")
            return 2
        results.append((True, "配置可读", str(cfg.get("workspace", ""))))
        results.append(
            (
                Path(cfg.get("workspace", "")).is_dir(),
                "工作区存在",
                str(cfg.get("workspace", "")),
            )
        )

    if cmd in ("all", "privacy", "package"):
        rows_out, rows = load_all() if cmd == "all" else ([], {name: [] for name in ledger.LEDGERS})
        results.extend(rows_out)
        if cmd in ("all", "package"):
            results.extend(check_ids(rows))
            results.append(check_task_status(rows["task"]))
            results.append(check_change_status(rows["change"]))
            results.append(check_links(rows))
            results.append(check_coverage(rows))
            results.append(check_paths(rows))
            results.append(check_promises(rows))
            if cmd == "all":
                ws = Path(cfg.get("workspace", ""))
                readonly = [str(p) for p in (cfg.get("readonly_roots") or [])]
                results.append(check_boundary(rows, ws, readonly))
                results.append(check_evidence(rows["task"], ws))
                errors = artifacts.check(rows, ws)
                results.append((not errors, "实际产出与最近回执一致", "; ".join(errors[:5])))
            unknown = unknown_files(man)
            results.append(
                (
                    not unknown,
                    "没有未申报文件",
                    ("目录里多出：" + ", ".join(unknown[:5])) if unknown else "只有壳与数据区",
                )
            )

        tokens = config.privacy_tokens() if config.exists() else []
        hits = privacy_hits(man, tokens)
        results.append(
            (
                not hits,
                "壳文件里没有绝对路径",
                "; ".join(hits[:5]) if hits else f"扫了 {len(shell_files(man))} 个文件",
            )
        )

    if cmd == "package":
        missing = [r for r in man["shell"] if not (KIT / r).is_file()]
        for rel in man["shell"]:
            try:
                paths.shell(KIT, rel)
            except (OSError, ValueError) as exc:
                missing.append(f"{rel}: {exc}")
        results.append(
            (not missing, "白名单里的文件都在", ", ".join(missing) if missing else "齐全")
        )
        print()
        print("发布白名单（只打这些）：")
        for rel in man["shell"]:
            print(f"  {rel}")

    return emit(results)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
