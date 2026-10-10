"""实现世界书关键词、次关键词、概率、互斥组与预算规则。
持续与冷却按回合计算；四个注入位置是光谱上下文位置，不是聊天深度。"""


from __future__ import annotations

import random
import re


SELECTIVE_AND_ANY = "and_any"
SELECTIVE_NOT_ALL = "not_all"
SELECTIVE_NOT_ANY = "not_any"
SELECTIVE_AND_ALL = "and_all"


SELECTIVE_LOGICS = (
    SELECTIVE_AND_ANY, SELECTIVE_NOT_ALL, SELECTIVE_NOT_ANY, SELECTIVE_AND_ALL,
)


SELECTIVE_LABELS = {
    SELECTIVE_AND_ANY: "任一命中",
    SELECTIVE_NOT_ALL: "并非全部命中",
    SELECTIVE_NOT_ANY: "一个都不命中",
    SELECTIVE_AND_ALL: "全部命中",
}


_ASCII_WORD = r"[0-9A-Za-z_]"


def split_terms(raw) -> list[str]:
    if raw is None:
        return []
    out: list[str] = []
    for part in re.split(r"[,，\n]", str(raw)):
        t = part.strip()
        if t and t not in out:
            out.append(t)
    return out


def _get(entry, name, default=None):
    try:
        v = entry[name]
    except (KeyError, IndexError, TypeError):
        return default
    return default if v is None else v


def matcher_kwargs(entry) -> dict:
    return {
        "case_sensitive": bool(int(_get(entry, "case_sensitive", 0) or 0)),
        "whole_words": bool(int(_get(entry, "match_whole_words", 0) or 0)),
        "use_regex": bool(int(_get(entry, "use_regex", 0) or 0)),
    }


def term_hits(term: str, haystack: str, *, case_sensitive: bool = False,
              whole_words: bool = False, use_regex: bool = False) -> bool:
    if not term:
        return False
    hay = str(haystack or "")
    if not hay:
        return False
    t = str(term)
    if use_regex:
        pat = t
    elif whole_words:
        pat = rf"(?<!{_ASCII_WORD}){re.escape(t)}(?!{_ASCII_WORD})"
    else:
        pat = re.escape(t)
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.search(pat, hay, flags) is not None
    except re.error:
        return False


def any_term_hits(terms: list[str], haystack: str, **kwargs) -> bool:
    return any(term_hits(t, haystack, **kwargs) for t in terms)


def all_terms_hit(terms: list[str], haystack: str, **kwargs) -> bool:
    return all(term_hits(t, haystack, **kwargs) for t in terms)


def normalize_logic(value) -> str:
    v = str(value or "").strip().lower()
    if v in SELECTIVE_LOGICS:
        return v
    legacy = {"0": SELECTIVE_AND_ANY, "1": SELECTIVE_NOT_ALL,
              "2": SELECTIVE_NOT_ANY, "3": SELECTIVE_AND_ALL}
    return legacy.get(v, SELECTIVE_AND_ANY)


def selective_pass(secondary: list[str], logic: str, haystack: str, **kwargs) -> bool:
    if not secondary:
        return True
    hit = any_term_hits(secondary, haystack, **kwargs)
    all_hit = all_terms_hit(secondary, haystack, **kwargs)
    logic = normalize_logic(logic)
    if logic == SELECTIVE_AND_ANY:
        return hit
    if logic == SELECTIVE_AND_ALL:
        return all_hit
    if logic == SELECTIVE_NOT_ANY:
        return not hit

    return not all_hit


def is_constant(entry) -> bool:
    return bool(int(_get(entry, "constant", 0) or 0))


def keyword_activate(entry, haystack: str) -> bool:
    keys = split_terms(_get(entry, "keys", ""))
    if not keys:
        return False
    kw = matcher_kwargs(entry)
    if not any_term_hits(keys, haystack, **kw):
        return False
    if not int(_get(entry, "selective", 0) or 0):
        return True
    secondary = split_terms(_get(entry, "secondary", ""))
    return selective_pass(secondary, _get(entry, "selective_logic", ""), haystack, **kw)


DEFAULT_GROUP_WEIGHT = 100.0


DEFAULT_RNG = random.random


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x3400 <= o <= 0x9FFF
            or 0xF900 <= o <= 0xFAFF
            or 0x3040 <= o <= 0x30FF
            or 0xAC00 <= o <= 0xD7AF)


def estimate_tokens(text) -> int:
    s = str(text or "")
    if not s:
        return 0
    cjk = sum(1 for c in s if _is_cjk(c))
    return cjk + (len(s) - cjk + 3) // 4


def probability_pass(entry, rng) -> bool:
    p = int(_get(entry, "probability", 100) or 0)
    if p >= 100:
        return True
    if p <= 0:
        return False
    return rng() * 100 < p


def key_score(entry, haystack: str) -> int:
    kw = matcher_kwargs(entry)
    keys = split_terms(_get(entry, "keys", ""))
    if not keys:
        return 0
    primary = sum(1 for k in keys if term_hits(k, haystack, **kw))
    sec = split_terms(_get(entry, "secondary", ""))
    if not sec:
        return primary
    hits = sum(1 for s in sec if term_hits(s, haystack, **kw))
    logic = normalize_logic(_get(entry, "selective_logic", ""))
    if logic == SELECTIVE_AND_ANY:
        return primary + hits
    if logic == SELECTIVE_AND_ALL:
        return primary + hits if hits == len(sec) else primary
    return primary


def weighted_pick(entries: list, rng):
    weights = [max(0.0, float(_get(e, "group_weight", DEFAULT_GROUP_WEIGHT) or 0))
               for e in entries]
    total = sum(weights)
    if total <= 0:
        return entries[min(len(entries) - 1, int(rng() * len(entries)))]
    r = rng() * total
    acc = 0.0
    for e, w in zip(entries, weights):
        acc += w
        if r < acc:
            return e
    return entries[-1]


def pick_from_group(entries: list, haystack: str, rng):
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0]
    pool = list(entries)
    scored = {id(e): int(_get(e, "use_group_scoring", 0) or 0) for e in pool}
    if any(scored.values()):
        scores = {id(e): key_score(e, haystack) for e in pool}
        top = max(scores.values())
        pool = [e for e in pool if not scored[id(e)] or scores[id(e)] >= top]
    prios = [e for e in pool if int(_get(e, "prioritize", 0) or 0)]
    if prios:
        return max(prios, key=lambda e: (int(_get(e, "order_value", 0) or 0),
                                         -int(_get(e, "id", 0) or 0)))
    return weighted_pick(pool, rng)


def trim_to_budget(entries: list, max_entries: int = 0,
                   token_budget: int = 0) -> tuple[list, int]:
    """依据条数与估算篇幅筛选条目，返回保留内容和裁减数量。"""
    if not entries:
        return [], 0
    n, budget = int(max_entries or 0), int(token_budget or 0)
    kept: list = []
    used = counted = 0
    stopped = False
    for e in entries:
        if int(_get(e, "ignore_budget", 0) or 0):
            kept.append(e)
            continue
        if stopped:
            continue
        if n > 0 and counted >= n:
            stopped = True
            continue
        t = estimate_tokens(_get(e, "text", ""))
        if budget > 0 and used + t > budget:
            stopped = True
            continue
        kept.append(e)
        used += t
        counted += 1
    return kept, len(entries) - len(kept)


GATE_NORMAL = "normal"
GATE_STICKY = "sticky"
GATE_SUPPRESS = "suppress"


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def timed_gate(entry, seq: int, state) -> str:
    """按会话回合判断延迟、持续和冷却状态，不使用现实钟点。"""
    delay = max(0, _int(_get(entry, "delay", 0)))
    if delay > 0 and seq < delay:
        return GATE_SUPPRESS
    sticky_until = _int(_get(state, "sticky_until", 0))
    if sticky_until > seq:
        return GATE_STICKY
    if _int(_get(state, "cooldown_until", 0)) > seq:
        return GATE_SUPPRESS
    return GATE_NORMAL


def next_timed_state(entry, seq: int, currently_sticky: bool):
    if currently_sticky:
        return None
    sticky = max(0, _int(_get(entry, "sticky", 0)))
    cooldown = max(0, _int(_get(entry, "cooldown", 0)))
    if sticky <= 0 and cooldown <= 0:
        return None
    return {
        "sticky_until": seq + sticky if sticky else 0,
        "cooldown_until": seq + sticky + cooldown if cooldown else 0,
    }


def clamp_steps(value, default: int = 3, hard_max: int = 10) -> int:
    n = _int(value, default)
    if n <= 0:
        n = default
    return max(1, min(hard_max, n))


POSITION_L3 = "l3"
POSITION_CORE = "core"
POSITION_PERSON = "person"
POSITION_TURN = "turn"
POSITIONS = (POSITION_L3, POSITION_CORE, POSITION_PERSON, POSITION_TURN)


def group_by_position(rows) -> dict[str, list]:
    """把条目分到四个固定上下文位置，未知位置使用生活背景。"""
    grouped = {position: [] for position in POSITIONS}
    for row in rows or ():
        position = str(_get(row, "position", "") or "").strip().lower()
        grouped[position if position in grouped else POSITION_L3].append(row)
    return grouped


def split_by_position(rows) -> tuple[list, list]:
    l3: list = []
    core: list = []
    for r in (rows or []):
        pos = str(_get(r, "position", "") or "").strip().lower()
        (core if pos == POSITION_CORE else l3).append(r)
    return l3, core


def scan_text(head, lines, depth) -> str:
    d = max(0, _int(depth))
    picked = [str(x) for x in (lines or [])[:d]] if d else []
    return "\n".join([str(head or "")] + picked)


def resolve_budget(percent, context_tokens, cap) -> int:
    p = _int(percent, 25)
    if p < 0 or p > 100:
        p = 25
    ctx = max(0, _int(context_tokens))
    budget = int(round(p * ctx / 100.0)) or 1
    cap = max(0, _int(cap))
    if cap > 0 and budget > cap:
        return cap
    return budget
