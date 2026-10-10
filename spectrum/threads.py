"""定义故事线字段、状态转换和按剧情回合计算的到期程度。"""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ELEMENTS: tuple[str, ...] = ("object", "event", "speech_act", "rule", "symbol")


VISIBILITIES: tuple[str, ...] = ("pointer", "plant")


SCOPES: tuple[str, ...] = ("act", "segment", "local")


KINDS: tuple[str, ...] = ("prop", "person", "info", "feeling")


PAYOFF_FORMS: tuple[str, ...] = ("direct", "variant", "implicit")


STATE_PLANTED = "planted"
STATE_HINTED = "hinted"
STATE_REINFORCED = "reinforced"
STATE_PAID = "paid"
STATE_ABANDONED = "abandoned"


OPEN_STATES: tuple[str, ...] = (STATE_PLANTED, STATE_HINTED, STATE_REINFORCED)

CLOSED_STATES: tuple[str, ...] = (STATE_PAID, STATE_ABANDONED)

ALL_STATES: tuple[str, ...] = OPEN_STATES + CLOSED_STATES


_LEGACY_STATE: dict[str, str] = {
    "open": STATE_PLANTED,
    "closed": STATE_PAID,
    "": STATE_PLANTED,
}


def normalize_state(state: str) -> str:
    s = str(state or "").strip()
    if s in ALL_STATES:
        return s
    return _LEGACY_STATE.get(s, STATE_PLANTED)


def next_state_on_touch(state: str) -> str:
    s = normalize_state(state)
    if s == STATE_PLANTED:
        return STATE_HINTED
    if s == STATE_HINTED:
        return STATE_REINFORCED
    return s


def is_open(state: str) -> bool:
    return normalize_state(state) in OPEN_STATES


URGENCY_WARNING_AT = 0.7
URGENCY_OVERDUE_AT = 1.0

URGENCY_NORMAL = "normal"
URGENCY_WARNING = "warning"
URGENCY_OVERDUE = "overdue"


def urgency(open_beat: int, due_beats: int, beats_now: int) -> float | None:
    """以已经过剧情回合除以计划回合计算到期程度；无期限返回空。"""
    if due_beats <= 0:
        return None
    spent = max(0, int(beats_now) - int(open_beat))
    return spent / float(due_beats)


def urgency_level(u: float | None) -> str:
    if u is None:
        return URGENCY_NORMAL
    if u >= URGENCY_OVERDUE_AT:
        return URGENCY_OVERDUE
    if u >= URGENCY_WARNING_AT:
        return URGENCY_WARNING
    return URGENCY_NORMAL


SPEC_KEYS: frozenset[str] = frozenset({
    "id", "title",
    "element", "visibility", "scope", "kind", "payoff_form",
    "planned_payoff",
    "due_beats", "trigger",
})


@dataclass(frozen=True)
class ThreadSpec:
    tid: str
    title: str = ""
    element: str = ""
    visibility: str = ""
    scope: str = ""
    kind: str = ""
    payoff_form: str = ""
    planned_payoff: str = ""
    due_beats: int = 0
    trigger: dict = field(default_factory=dict)

    @property
    def has_trigger(self) -> bool:
        return bool(self.trigger)


def parse_thread_spec(raw: Any) -> tuple[ThreadSpec | None, str]:
    if isinstance(raw, str):
        tid = raw.strip()
        if not tid:
            return None, "open_thread 的值是空字符串"
        return ThreadSpec(tid=tid), ""

    if not isinstance(raw, dict):
        return None, f"open_thread 的值既不是字符串也不是对象：{type(raw).__name__}"

    unknown = set(raw) - SPEC_KEYS
    if unknown:
        return None, f"open_thread 有不认得的键：{sorted(unknown)}"

    tid = str(raw.get("id") or "").strip()
    if not tid:
        return None, "open_thread 少了 id"

    def _enum(name: str, allowed: tuple[str, ...]) -> tuple[str, str]:
        v = str(raw.get(name) or "").strip()
        if v and v not in allowed:
            return "", f"{name}={v!r} 不在 {list(allowed)} 里"
        return v, ""

    vals = {}
    for name, allowed in (("element", ELEMENTS), ("visibility", VISIBILITIES),
                          ("scope", SCOPES), ("kind", KINDS),
                          ("payoff_form", PAYOFF_FORMS)):
        v, bad = _enum(name, allowed)
        if bad:
            return None, bad
        vals[name] = v

    try:
        due = int(raw.get("due_beats") or 0)
    except (TypeError, ValueError):
        return None, f"due_beats={raw.get('due_beats')!r} 不是整数"
    if due < 0:
        return None, f"due_beats={due} 是负数"

    trig = raw.get("trigger") or {}
    if not isinstance(trig, dict):
        return None, "trigger 必须是一个条件对象（跟 storylet 的 condition 同一种写法）"

    return ThreadSpec(
        tid=tid,
        title=str(raw.get("title") or "").strip(),
        planned_payoff=str(raw.get("planned_payoff") or "").strip(),
        due_beats=due,
        trigger=trig,
        **vals,
    ), ""


POINTER_KINDS: tuple[str, ...] = (
    "situational_pressure",
    "character_action",
    "stated_wish_or_threat",
    "prediction_or_omen",
    "discovery_about_past",
    "unanswered_question",
    "charged_object",
)


PLANT_KINDS: tuple[str, ...] = (
    "future_condition",
    "character_attitude",
    "loaded_line",
    "rule_to_be_broken",
    "future_object",
    "not_what_it_seems",
    "scaled_recurrence",
)


__all__ = [
    "ELEMENTS", "VISIBILITIES", "SCOPES", "KINDS", "PAYOFF_FORMS",
    "STATE_PLANTED", "STATE_HINTED", "STATE_REINFORCED",
    "STATE_PAID", "STATE_ABANDONED",
    "OPEN_STATES", "CLOSED_STATES", "ALL_STATES",
    "normalize_state", "next_state_on_touch", "is_open",
    "urgency", "urgency_level",
    "URGENCY_NORMAL", "URGENCY_WARNING", "URGENCY_OVERDUE",
    "URGENCY_WARNING_AT", "URGENCY_OVERDUE_AT",
    "ThreadSpec", "SPEC_KEYS", "parse_thread_spec",
    "POINTER_KINDS", "PLANT_KINDS",
]
