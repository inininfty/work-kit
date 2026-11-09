#!/usr/bin/env python3
"""Validate a complete release before writing; preserve backups and roll back failures."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
from core import config, ledger, paths


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def shell_of(root: Path) -> dict:
    man = json.loads((root / "kit.json").read_text(encoding="utf-8-sig"))
    entries = man.get("shell")
    if not isinstance(entries, list) or not entries:
        raise ValueError("发布清单为空或不合法")
    files = [paths.relative(rel) for rel in entries]
    if len({rel.casefold() for rel in files}) != len(files):
        raise ValueError("发布清单包含重复路径")
    required = {"kit.json", "VERSION", "bin/check.py", "bin/kit.py", "core/ledger.py"}
    if not required.issubset(files):
        raise ValueError("发布清单缺少必要文件")
    for rel in files:
        if not paths.shell(root, rel).is_file():
            raise ValueError(f"发布文件不存在：{rel}")
    if (root / "VERSION").read_text(encoding="utf-8").strip() != man.get("version"):
        raise ValueError("发布版本声明不一致")
    return {"version": man["version"], "files": files}


def upgrade(source: Path, apply: bool = False) -> int:
    new = shell_of(source)
    state = config.state()
    installed = state.get("installed", {})
    plan = {"add": [], "update": [], "conflict": []}
    for rel in new["files"]:
        dst = paths.shell(KIT, rel)
        if not dst.exists():
            plan["add"].append(rel)
        elif sha(dst) == installed.get(rel):
            plan["update"].append(rel)
        else:
            plan["conflict"].append(rel)
    print(json.dumps({"version": new["version"], "plan": plan}, ensure_ascii=False))
    if not apply:
        return 0
    if plan["conflict"]:
        # The active release remains entirely unchanged until conflicts are resolved.
        for rel in new["files"]:
            target = paths.shell(KIT, rel + ".new")
            if target.exists():
                raise FileExistsError("已有 .new 候选；先处理它，拒绝覆盖")
        for rel in new["files"]:
            target = paths.shell(KIT, rel + ".new")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / rel, target)
        print("发现本地修改：只生成整版 .new，活动版本未升级。")
        return 3
    batch = KIT / ".runtime" / "upgrades" / uuid.uuid4().hex
    batch.mkdir(parents=True, exist_ok=False)
    prior_state = config.STATE.read_bytes() if config.STATE.exists() else None
    hashes = {}
    for rel in new["files"]:
        staged = batch / "stage" / rel
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, staged)
        hashes[rel] = sha(staged)
        dst = paths.shell(KIT, rel)
        if dst.exists():
            back = batch / "backup" / rel
            back.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, back)
    journal = {"state": "prepared", "version": new["version"], "files": new["files"],
               "added": plan["add"], "prior_state": state}
    ledger.atomic(batch / "upgrade.json", json.dumps(journal, ensure_ascii=False))
    touched = []
    try:
        for rel in new["files"]:
            dst = paths.shell(KIT, rel)
            touched.append(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            temp = dst.with_name(dst.name + ".upgrade-" + uuid.uuid4().hex)
            shutil.copy2(batch / "stage" / rel, temp)
            os.replace(temp, dst)
            if sha(dst) != hashes[rel]:
                raise RuntimeError("升级回读哈希不一致")
        retired = sorted(set(state.get("retired", [])) | (set(installed) - set(new["files"])))
        config.save_state({**state, "version": new["version"], "installed": hashes, "retired": retired,
                           "installed_at": ledger.stamp()})
    except BaseException:
        for rel in reversed(touched):
            dst, back = KIT / rel, batch / "backup" / rel
            if back.exists():
                shutil.copy2(back, dst)
            elif dst.exists():
                dst.unlink()
        if prior_state is not None:
            config.STATE.write_bytes(prior_state)
        elif config.STATE.exists():
            config.STATE.unlink()
        journal["state"] = "rolled_back"
        ledger.atomic(batch / "upgrade.json", json.dumps(journal, ensure_ascii=False))
        raise
    journal["state"] = "complete"
    ledger.atomic(batch / "upgrade.json", json.dumps(journal, ensure_ascii=False))
    print("升级成功；个人配置和经验未覆盖，运行元数据已更新，备份在 .runtime/upgrades。")
    return 0


def main(argv: list[str]) -> int:
    try:
        if not config.exists():
            raise ValueError("未初始化，先运行 init")
        if "--source" not in argv:
            state = config.state()
            missing = [r for r in state.get("installed", {}) if not paths.shell(KIT, r).is_file()]
            changed = [r for r, h in state.get("installed", {}).items()
                       if (KIT / r).is_file() and sha(KIT / r) != h]
            print(json.dumps({"version": state.get("version"), "missing": missing, "changed": changed}, ensure_ascii=False))
            return 1 if missing or changed else 0
        i = argv.index("--source")
        if i + 1 == len(argv):
            raise ValueError("--source 后需要目录")
        with ledger.Lock():
            return upgrade(Path(argv[i + 1]).expanduser().resolve(), "--apply" in argv)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
