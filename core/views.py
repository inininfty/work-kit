"""按需生成状态摘要。不常驻上下文——这是「减费用」的关键。"""

from __future__ import annotations

from . import ledger

IN_FLIGHT = ("todo", "doing", "blocked")


def _counts(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key) or "?")
        out[k] = out.get(k, 0) + 1
    return out


def summary(limit: int = 5) -> str:
    rows, bad = ledger.read_all()
    tasks, changes, mods = (rows[k] for k in ("task", "change", "modification"))

    lines: list[str] = []
    lines.append(
        "账本：任务 %d · 清单 %d · 修改 %d"
        % (len(tasks), len(changes), len(mods))
    )
    if bad:
        lines.append("坏行 %d 处，先修：" % len(bad))
        lines.extend("  " + b for b in bad[:5])

    status = _counts(tasks, "status")
    if status:
        lines.append(
            "任务状态：" + " · ".join(f"{k}={v}" for k, v in sorted(status.items()))
        )

    open_tasks = [t for t in tasks if t.get("status") in IN_FLIGHT]
    if open_tasks:
        lines.append("")
        lines.append("在飞（%d）：" % len(open_tasks))
        for t in open_tasks[:limit]:
            nxt = str(t.get("next") or "").replace("\n", " ")
            if len(nxt) > 60:
                nxt = nxt[:60] + "…"
            lines.append(f"  {t.get('id')} [{t.get('status')}] {t.get('title')}")
            if nxt:
                lines.append(f"      下一步：{nxt}")
    else:
        lines.append("在飞：无")

    if mods:
        lines.append("")
        lines.append("最近改动（%d）：" % min(len(mods), limit))
        for m in mods[-limit:]:
            lines.append(
                f"  {m.get('id')} → {m.get('for_task')}："
                + ", ".join(m.get("files_created", []))[:70]
            )

    return "\n".join(lines)
