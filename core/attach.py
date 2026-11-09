"""在工作区根写一条指向壳的规则指针。

为什么要有这个（2026-10-25 用户原话：「你没处理这个agentmd问题」）：
  DSH 按目录读 AGENTS.md。壳的规则在 work-kit/AGENTS.md 里，只对 work-kit/
  下的操作生效——等于装了个只管自己房间的规矩。这里在**工作区根**放一个标记块，
  内容只有一句指针，规则本体仍然只有一份（防两份规则分叉）。

幂等：靠 BEGIN/END 标记定位；重复调用只替换块内内容，不动用户自己的正文。
"""

from __future__ import annotations

from pathlib import Path
import hashlib

from . import ledger, paths

BEGIN = "<!-- work-kit:begin（由 work-kit 自动维护，别手改这一段） -->"
END = "<!-- work-kit:end -->"


def block(kit: Path) -> str:
    name = kit.name
    return "\n".join(
        [
            BEGIN,
            "## 本工作区的规矩",
            "",
            f"规则本体在 `{name}/AGENTS.md`。开工前先读它，不要凭记忆。",
            "",
            f"- 闸门：`python {name}/bin/check.py all`（退出码 0 才算完）",
            f"- 账本：`{name}/data/`",
            f"- 状态：`python {name}/bin/kit.py status`",
            "",
            "不要在这里抄规则——两份规则一定会分叉。",
            END,
        ]
    )


def attach(ws: Path, kit: Path) -> str:
    """返回 created / appended / replaced / unchanged。"""
    p = ws / "AGENTS.md"
    if paths.linked(p):
        raise ValueError("根 AGENTS.md 是链接，拒绝覆盖")
    before = p.read_bytes() if p.is_file() else None
    text = before.decode("utf-8-sig") if before is not None else ""
    newline = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")
    new = block(kit)

    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1:
        raise ValueError("AGENTS 标记不配对或重复；拒绝自动合并")
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        out = head + new + tail
        action = "replaced"
    elif text.strip():
        body = text if text.endswith("\n") else text + "\n"
        out = body + "\n" + new + "\n"
        action = "appended"
    else:
        out = new + "\n"
        action = "created"

    if p.is_file() and out == text:
        return "unchanged"
    if p.exists() and p.read_bytes() != before:
        raise ValueError("AGENTS 在准备期间变化；拒绝覆盖")
    if before is not None:
        backup = kit / ".runtime" / "attach" / (hashlib.sha256(before).hexdigest() + ".md")
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            backup.write_bytes(before)
    p.parent.mkdir(parents=True, exist_ok=True)
    ledger.atomic(p, out.replace("\n", newline))
    return action
