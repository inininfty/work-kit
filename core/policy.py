"""Minimum state contract; approval metadata records intent, not user identity."""
from __future__ import annotations

TRANSITIONS = {
    "todo": {"doing", "abandoned"},
    "doing": {"done", "blocked", "todo", "abandoned"},
    "blocked": {"doing", "abandoned"},
    "done": {"doing", "verified"},
    "verified": set(),
    "abandoned": set(),
}


def task_transition(old: dict, new: dict) -> None:
    before, after = old.get("status"), new.get("status")
    if before != after and after not in TRANSITIONS.get(before, set()):
        raise ValueError(f"非法状态转移：{before} → {after}")
    if old != new and new.get("rev") != old.get("rev", 1) + 1:
        raise ValueError("任务更新必须使用当前 rev + 1，拒绝旧快照")
    if after in ("done", "verified") and not new.get("evidence"):
        raise ValueError("完成任务必须有可核验证据")
    if after == "blocked" and not new.get("blocker"):
        raise ValueError("blocked 必须有 blocker")
    if after == "verified" and not new.get("verified_by"):
        raise ValueError("verified 必须有验收人")


def change_transition(old: dict, new: dict) -> None:
    transitions = {"proposed": {"doing", "abandoned"}, "doing": {"done", "abandoned"},
                   "done": set(), "abandoned": set()}
    before, after = old.get("status"), new.get("status")
    if before != after and after not in transitions.get(before, set()):
        raise ValueError(f"非法清单转移：{before} → {after}")
    if after in ("doing", "done") and (not new.get("approved_at") or new.get("approved_by") in (None, "pending")):
        raise ValueError("清单未获批准")
