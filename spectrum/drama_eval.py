"""计算剧情介入成本、素材评分和故事收尾条件。"""


from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable


SENNA_COST: dict[str, float] = {
    "more_info": 1.2,
    "npc_influence": 2.4,
    "consequences": 2.4,
    "illusion_of_choice": 4.0,
    "hard_deny": 5.0,
    "humor": 5.0,
}


DEFAULT_INTERVENTION = "more_info"


def intervention_cost(effects: dict) -> float:
    key = str((effects or {}).get("intervention") or DEFAULT_INTERVENTION)
    if key not in SENNA_COST:

        return max(SENNA_COST.values())
    return SENNA_COST[key]


@dataclass
class BudgetResult:
    allowed: list = field(default_factory=list)
    deferred: list = field(default_factory=list)
    spent: float = 0.0


def budget_filter(items: list, cost_of: Callable[[Any], float],
                  budget: float) -> BudgetResult:
    r = BudgetResult()
    left = float(budget)
    for it in items:
        c = float(cost_of(it))
        if c <= left:
            r.allowed.append(it)
            left -= c
            r.spent += c
        else:
            r.deferred.append(it)
    return r


_DENSITY_LO, _DENSITY_HI = 1.0, 4.0


def story_density(prev_pos: float | None, cur_pos: float) -> float | None:
    if prev_pos is None:
        return None
    d = 1.5 * abs(float(cur_pos) - float(prev_pos))
    if _DENSITY_LO <= d <= _DENSITY_HI:
        return 1.0
    if d == 0.0:
        return -1.0
    if d < _DENSITY_LO:

        return -1.0 + 2.0 * (d / _DENSITY_LO)

    over = d - _DENSITY_HI
    return max(-1.0, 1.0 - 2.0 * (over / 8.0))


ANNOTATED_FEATURES = ("motivation", "thought_flow", "manipulation")


def annotated(storylet) -> dict:
    raw = getattr(storylet, "eval_annot", None) or {}
    return {k: raw.get(k) for k in ANNOTATED_FEATURES if raw.get(k) is not None}


def combine(features: dict[str, float | None],
            weights: dict[str, float] | None = None) -> float | None:
    weights = weights or {}
    num = den = 0.0
    for k, v in features.items():
        if v is None:
            continue
        w = float(weights.get(k, 1.0))
        num += w * float(v)
        den += w
    return (num / den) if den > 0 else None


def can_close(story_state: dict, open_threads: list | None = None,
              min_beats: int = 0, min_paid: int = 1, paid_n: int = 0,
              max_beats: int = 0, total_beats: int = 0) -> tuple[bool, str]:
    threads = open_threads
    if threads is None:
        threads = story_state.get("open_threads") or []
    if story_state.get("ending_name"):
        return False, "已经收束过了"
    if threads:
        return False, f"还有 {len(threads)} 条线没收回：{threads}"
    n = int(story_state.get("beats_consumed") or 0)

    all_n = int(total_beats or n)
    if min_beats > 0 and n < min_beats:
        return False, f"本幕才走了 {n} 拍，不足 {min_beats} 拍"
    if min_paid > 0 and paid_n < min_paid:


        if max_beats <= 0 or all_n < max_beats:
            tail = (f"；累计走满 {max_beats} 拍后才会放宽" if max_beats > 0
                    else "；没有设兜底上限")
            return False, (f"本幕回收的线不够：已回收 {paid_n} 条，至少要 {min_paid} 条。"
                           f"本幕已走 {n} 拍，累计 {all_n} 拍{tail}")
        return True, (f"本幕只回收了 {paid_n} 条线，不足 {min_paid} 条，"
                      f"但累计已走满 {all_n} 拍上限 {max_beats}，按兜底放行")
    return True, f"线索已收完，本幕回收过 {paid_n} 条线，本幕走了 {n} 拍"


__all__ = [
    "SENNA_COST", "DEFAULT_INTERVENTION", "intervention_cost",
    "BudgetResult", "budget_filter",
    "story_density", "annotated", "combine", "ANNOTATED_FEATURES",
    "can_close",
]
