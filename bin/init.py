#!/usr/bin/env python3
"""初始化。两种模式：

  python bin/init.py                 # 交互：问三个问题
  python bin/init.py --auto          # 自动：自己认工作区、扫只读候选
  python bin/init.py --auto --yes    # 自动且跳过确认（有候选时才需要）

退出码：0 装好 / 1 失败 / 3 有只读候选需要确认（此时**不写配置**）

为什么自动模式还要人点头：猜错只读根是双向的代价——把别人的东西当自己的
（可能写坏），或把自己的东西锁死（干不了活）。所以机器只负责提议，人只回答
「对/不对」。
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))

from core import attach as attach_mod  # noqa: E402
from core import config  # noqa: E402

TZ = timezone(timedelta(hours=8))

# 名字里带这些词的目录，默认怀疑是第三方资产
READONLY_HINTS = (
    "vendor",
    "third_party",
    "third-party",
    "external",
    "archive",
    "reference",
    "refs",
    "assets",
    "资源",
    "资料",
    "参考",
    "素材",
    "备份",
    "旧版",
)


def stamp() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def ask(prompt: str, default: str = "") -> str:
    try:
        raw = input(prompt).strip()
    except EOFError:
        raw = ""
    return raw or default


def arg_value(argv: list[str], name: str) -> str | None:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def candidates(ws: Path) -> list[Path]:
    """只列工作区下一层目录。不外扫、不读内容——隐私靠这个边界守住。"""
    out: list[Path] = []
    try:
        for p in sorted(ws.iterdir()):
            if p.is_dir() and p != KIT and not p.name.startswith("."):
                out.append(p)
    except OSError:
        pass
    return out


def classify(p: Path) -> list[str]:
    """给一个目录列出「为什么怀疑它是只读的」。空列表 = 没有证据。"""
    reasons: list[str] = []
    try:
        if (p / ".git").exists():
            reasons.append("含独立 .git（另一个仓库）")
        if (p / "node_modules").is_dir():
            reasons.append("含 node_modules（依赖目录）")
        low = p.name.lower()
        for hint in READONLY_HINTS:
            if hint in low:
                reasons.append(f"名字含「{hint}」")
                break
        if p.is_symlink():
            reasons.append("符号链接")
    except OSError:
        pass
    return reasons


def write_all(ws: Path, purpose: str, readonly: list[str]) -> dict:
    cfg = {
        "kit_version": (KIT / "VERSION").read_text(encoding="utf-8").strip(),
        "workspace": str(ws).replace("\\", "/"),
        "purpose": purpose,
        "readonly_roots": [p.replace("\\", "/") for p in readonly],
        "privacy_tokens": [],
        "created_at": stamp(),
    }
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.CONFIG.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    config.DATA.mkdir(parents=True, exist_ok=True)
    seeds = {
        "task-ledger.jsonl": "",
        "change-list.jsonl": "",
        "modification-ledger.jsonl": "",
        "negative-examples.json": json.dumps(
            {"schema_version": 1, "kind": "negative-examples", "cases": []},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        "patterns.json": json.dumps(
            {"schema_version": 1, "kind": "patterns", "patterns": []},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }
    created: list[str] = []
    for name, text in seeds.items():
        p = config.DATA / name
        if not p.exists():
            p.write_text(text, encoding="utf-8")
            created.append(name)

    man = json.loads((KIT / "kit.json").read_text(encoding="utf-8"))
    installed = {rel: sha(KIT / rel) for rel in man["shell"] if (KIT / rel).is_file()}
    config.save_state(
        {"version": man["version"], "installed": installed, "installed_at": stamp()}
    )

    print()
    print(f"[OK] 配置：{config.CONFIG.relative_to(KIT).as_posix()}")
    print(f"[OK] 工作区：{cfg['workspace']}")
    print(f"[OK] 只读根：{len(cfg['readonly_roots'])} 个")
    if created:
        print(f"[OK] 空账本：{', '.join(created)}")
    try:
        action = attach_mod.attach(ws, KIT)
        print(f"[OK] 规则指针：工作区根 AGENTS.md（{action}）")
    except (OSError, ValueError) as e:
        print(f"[FAIL] 规则指针没写成：{e}")
        raise RuntimeError("安装未完成：规则指针失败，不能报告装好") from e
    print()
    print("下一步：")
    print(f"  python \"{KIT / 'bin' / 'check.py'}\" all")
    print("   全 pass 之后，在 data/ 里写第一条任务（AGENTS.md 有字段表）。")
    return cfg


def interactive(ws: Path) -> int:
    purpose = ask("Q2 一句话：这个工作区要做什么？ ", "")

    picked: list[str] = []
    cands = candidates(ws)
    if cands:
        print("Q3 哪些目录是只读的（别人的 / 第三方的资产）？")
        for i, p in enumerate(cands, 1):
            print(f"  {i}. {p.name}")
        print("  输入编号，逗号分隔；直接回车 = 没有")
        raw = ask("  > ", "")
        for chunk in raw.replace("，", ",").split(","):
            chunk = chunk.strip()
            if chunk.isdigit() and 1 <= int(chunk) <= len(cands):
                picked.append(str(cands[int(chunk) - 1]))
    else:
        print("Q3 只读目录：工作区下没有子目录，跳过。")

    write_all(ws, purpose, picked)
    return 0


def auto(ws: Path, yes: bool) -> int:
    print(f"[auto] 工作区：{ws}")
    print(f"[auto] 壳目录：{KIT}")

    found: list[tuple[Path, list[str]]] = []
    for p in candidates(ws):
        reasons = classify(p)
        if reasons:
            found.append((p, reasons))

    if found:
        print(f"[auto] 只读候选：{len(found)} 个")
        for p, reasons in found:
            print(f"   - {p.name}  ← {'；'.join(reasons)}")
        if not yes:
            print()
            print("[STOP] 这些候选还没有人确认，所以**没有写配置**。")
            print("       把上面的表给用户看，只问这一件事：哪些确实只读？")
            print("       确认后重跑：python bin/init.py --auto --yes")
            print("       要改别的目录：写进 config/kit.config.json 的 readonly_roots。")
            return 3
    else:
        print("[auto] 只读候选：0 个（没有发现第三方资产的迹象）")

    write_all(ws, "", [str(p) for p, _ in found])
    return 0


def main(argv: list[str]) -> int:
    force = "--force" in argv
    if config.exists() and not force:
        print(f"已经初始化过：{config.CONFIG}")
        print("要重来请加 --force（覆盖 config，但不动 data/）")
        return 0

    ws_arg = arg_value(argv, "--workspace")
    ws_default = Path(ws_arg).expanduser() if ws_arg else KIT.parent
    ws = ws_default.resolve()

    if "--auto" in argv:
        if not ws.is_dir():
            print(f"[FAIL] 工作区不是目录：{ws}")
            return 1
        return auto(ws, "--yes" in argv)

    print("work-kit 初始化：三个问题，都可以直接回车跳过。")
    print()
    raw = ask(f"Q1 这个工作区在哪？[{ws_default}] ", str(ws_default))
    ws = Path(raw).expanduser().resolve()
    if not ws.is_dir():
        print(f"[FAIL] 不是目录：{ws}")
        return 1
    return interactive(ws)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
