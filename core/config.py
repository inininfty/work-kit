"""配置与路径。缺配置即 fail closed —— 见 README「为什么缺配置时拒绝写入」。"""

from __future__ import annotations

import json
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
CONFIG_DIR = KIT / "config"
CONFIG = CONFIG_DIR / "kit.config.json"
STATE = KIT / ".runtime" / "kit.state.json"
DATA = KIT / "data"


class NotInitialized(RuntimeError):
    """配置不存在。调用方必须停下来问人，不要猜路径。"""


def exists() -> bool:
    return CONFIG.is_file()


def load() -> dict:
    if not CONFIG.is_file():
        raise NotInitialized(
            "还没有初始化：缺少 config/kit.config.json。\n"
            f"先跑：python \"{KIT / 'bin' / 'init.py'}\"\n"
            "在配置生成之前，本壳不判断任何写入是否被允许。"
        )
    # 容错：记事本 / PowerShell 5.1 写出的 UTF-8 常带 BOM，utf-8-sig 能吃掉它。
    # 0.4.0 实测：配置带 BOM 时旧代码整壳拒动（「配置读不了」），对新手是很硬的墙。
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    if not isinstance(cfg, dict) or not isinstance(cfg.get("workspace"), str) or not cfg["workspace"].strip():
        raise ValueError("配置必须包含非空 workspace")
    if not Path(cfg["workspace"]).is_absolute():
        raise ValueError("workspace 必须是绝对路径")
    for key in ("readonly_roots", "privacy_tokens"):
        values = cfg.get(key, [])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError(f"{key} 必须是字符串数组")
    return cfg


def workspace() -> Path:
    return Path(load()["workspace"]).resolve()


def readonly_roots() -> list[Path]:
    return [Path(p) for p in load().get("readonly_roots", []) if str(p).strip()]


def privacy_tokens() -> list[str]:
    return [str(t) for t in load().get("privacy_tokens", []) if str(t).strip()]


def state() -> dict:
    # Read old releases without modifying config; new metadata lives in .runtime.
    legacy = CONFIG_DIR / "kit.state.json"
    p = STATE if STATE.is_file() else legacy
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8-sig"))
    return {"version": None, "installed": {}}


def save_state(obj: dict) -> None:
    from .ledger import atomic
    atomic(STATE, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
