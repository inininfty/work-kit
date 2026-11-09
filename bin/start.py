#!/usr/bin/env python3
"""One entry: initialize, attach rules, check, and show status; no dependency downloads."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    if sys.version_info < (3, 11):
        print("[STOP] Python 3.11+ is required; no files changed.")
        return 2
    kit = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Initialize and verify this work-kit")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--confirm-readonly", action="store_true",
                        help="Use only after the user confirms all listed read-only candidates")
    args = parser.parse_args(argv)
    workspace = (args.workspace or kit.parent).resolve()
    if workspace != kit.parent or not workspace.is_dir():
        print("[STOP] Extract work-kit directly inside the intended project first; no files changed.")
        return 2
    sys.path.insert(0, str(kit))
    from core import config
    if config.exists():
        try:
            if Path(config.load()["workspace"]).resolve() != workspace:
                print("[STOP] Existing configuration belongs to another project; preserved unchanged.")
                return 2
        except (OSError, ValueError) as exc:
            print(f"[STOP] Existing configuration is invalid: {exc}")
            return 2
    spec = importlib.util.spec_from_file_location("workkit_init", kit / "bin/init.py")
    initializer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(initializer)
    init_args = ["init", "--auto", "--workspace", str(workspace)]
    if args.confirm_readonly:
        init_args.append("--yes")
    rc = initializer.main(init_args)
    if rc == 3:
        print("[ACTION] Ask the user to confirm the listed read-only candidates once.")
        print("[ACTION] After confirmation, repeat this command with --confirm-readonly.")
        return rc
    if rc:
        return rc
    rc = subprocess.run([sys.executable, str(kit / "bin/check.py"), "all"], cwd=workspace).returncode
    if rc:
        print("[STOP] Checks failed. Installation is not READY; preserve the output for DSH.")
        return rc
    rc = subprocess.run([sys.executable, str(kit / "bin/kit.py"), "status"], cwd=workspace).returncode
    if rc:
        return rc
    print("[READY] Configuration, workspace rules and checks are complete. Tell DSH your first task.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
