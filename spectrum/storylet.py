"""加载剧情素材并按分层条件、依赖关系与触发记录筛选候选。
消费素材时先记录未确认送达的执行条目，实际发送状态由执行方更新。"""


from __future__ import annotations

from . import freeze as freeze_mod

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

try:
    from astrbot.api import logger
except Exception:
    logger = logging.getLogger("spectrum")

from . import threads as _threads


EFFECT_ALLOWLIST: frozenset[str] = frozenset({
    "tension_delta",
    "scene_id",
    "open_thread",
    "reinforce_thread",
    "close_thread",
    "beat_end",
    "intervention",
    "present",
})


_FORBIDDEN_SMELL = re.compile(
    r"(forbid|block|prevent|unreachable|must_not|deny_state|ban_)", re.I)


def _effect_list(v: Any) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [v]


def _validate_thread_effects(eff: dict) -> str:
    for key in ("open_thread", "reinforce_thread", "close_thread"):
        for raw in _effect_list(eff.get(key)):
            _spec, bad = _threads.parse_thread_spec(raw)
            if bad:
                return f"{key} 写法有误：{bad}"


    for raw in _effect_list(eff.get("open_thread")):
        if not isinstance(raw, dict):
            continue
        trig = raw.get("trigger") or {}
        if not isinstance(trig, dict):
            continue
        for bucket in ("all", "any"):
            for cl in trig.get(bucket) or []:
                if isinstance(cl, dict) and int(cl.get("tier", 1)) != 1:
                    return "open_thread.trigger 只能用 tier 1 的条件"
    return ""


@dataclass(frozen=True)
class Storylet:
    sid: str
    kind: str = "action"
    narrative: tuple[str, ...] = ()
    condition: dict = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    k_turns: int = 0
    k_minutes: int = 0


    repeatable: bool = False
    target: str = ""
    min_idle_minutes: int = 0
    max_idle_minutes: int = 0
    priority: int = 0
    weight: float = 1.0
    scope: str = "global"
    scene_id: str = ""
    got_scene_id: str = ""
    ending_name: str = ""
    effects: dict = field(default_factory=dict)
    eval_annot: dict = field(default_factory=dict)
    enabled: bool = True

    problem: str = ""

    @property
    def usable(self) -> bool:
        return self.enabled and not self.problem and bool(self.narrative)


@dataclass
class TriggerState:
    fire_count: int = 0
    deps_satisfied: bool = True
    turns_left: int = 0
    consumed: bool = False

    @property
    def narr_index(self) -> int:
        return self.fire_count


@dataclass
class Candidate:
    storylet: Storylet
    state: TriggerState
    reason: str = ""


@dataclass
class ConsumedBeat:
    fire_id: int
    sid: str
    narrative: str
    index: int
    got_scene_id: str = ""
    ending_name: str = ""


@dataclass
class EvalContext:
    now: float
    story: dict
    trig: dict[str, TriggerState] = field(default_factory=dict)
    store: Any = None
    life: Any = None
    cfg: Callable[[str, Any], Any] = lambda k, d=None: d


    tz: str = "Asia/Shanghai"


    idle_minutes: dict[str, float | None] = field(default_factory=dict)

    owner_session: str = ""

    companion: str = ""

    willingness: dict[str, float] = field(default_factory=dict)

    relation: dict[str, tuple[float, float]] = field(default_factory=dict)

    sessions: list[str] = field(default_factory=list)

    asleep: bool = False


    recent_text: str = ""


    def resolve(self, name: str) -> Any:
        s = self.story
        if name == "tension":
            return s.get("tension")
        if name == "arc_target":
            return s.get("arc_target")
        if name == "intensity":
            return s.get("intensity")
        if name == "phase":
            return s.get("phase")
        if name == "scene_id":
            return s.get("scene_id") or "default"
        if name == "beats_consumed":
            return s.get("beats_consumed")
        if name == "turn_seq":
            return s.get("turn_seq")
        if name == "open_threads_n":
            return len(s.get("open_threads") or [])
        if name == "has_ending":
            return bool(s.get("ending_name"))
        if name == "asleep":
            return bool(self.asleep)
        if name == "has_owner":
            return bool(self.owner_session)
        if name in ("owner_idle_minutes", "minutes"):

            if not self.owner_session:
                return None
            v = self.idle_minutes.get(self.owner_session)
            if v is None:
                return None

            return int(v) if name == "minutes" else v
        if name in ("who", "companion"):


            return self.companion or None
        if name == "idle_hours":
            v = self.resolve("minutes")
            if v is None:
                return None
            return int(v) // 60
        if name == "idle_hours_text":
            h = self.resolve("idle_hours")
            if h is None:
                return None
            if h <= 0:
                m = self.resolve("minutes")
                return f"{m} 分钟"
            m = int(self.resolve("minutes")) % 60
            return f"{h} 小时 {m} 分" if m else f"{h} 小时"
        if name == "idle_minutes":

            vals = [v for v in self.idle_minutes.values() if v is not None]
            return max(vals) if vals else None
        if name == "willingness":
            vals = list(self.willingness.values())
            return max(vals) if vals else None
        if name == "affinity":
            vals = [a for a, _t in self.relation.values()]
            return max(vals) if vals else None
        if name == "trust":
            vals = [t for _a, t in self.relation.values()]
            return max(vals) if vals else None
        if name in ("local_time", "hour"):


            try:
                local = (self.now + 8 * 3600) % 86400
            except Exception:
                return None
            if name == "hour":
                return int(local // 3600)
            return "%02d:%02d" % (int(local // 3600), int((local % 3600) // 60))
        return None


_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _as_num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _p_threshold(cl: dict, ctx: EvalContext) -> bool:
    left = _as_num(ctx.resolve(str(cl.get("var", ""))))
    right = _as_num(cl.get("value"))
    op = _OPS.get(str(cl.get("op", "")))
    if left is None or right is None or op is None:
        return _fail("threshold", cl)
    return bool(op(left, right))


def _p_set(cl: dict, ctx: EvalContext) -> bool:
    val = ctx.resolve(str(cl.get("var", "")))
    pool = cl.get("in")
    if val is None or not isinstance(pool, list):
        return _fail("set", cl)
    return val in pool


def _p_window(cl: dict, ctx: EvalContext) -> bool:
    if str(cl.get("var", "local_time")) != "local_time":
        return _fail("window:var", cl)
    cur = ctx.resolve("local_time")
    lo, hi = str(cl.get("from", "")), str(cl.get("to", ""))
    if not isinstance(cur, str) or not lo or not hi:
        return _fail("window", cl)
    if lo <= hi:
        return lo <= cur <= hi
    return cur >= lo or cur <= hi


def _p_graph(cl: dict, ctx: EvalContext) -> bool:
    if str(cl.get("var", "depends_on")) != "depends_on":
        return _fail("graph:var", cl)
    want = bool(cl.get("satisfied", True))
    for dep in cl.get("of") or []:
        st = ctx.trig.get(str(dep))
        if st is None or st.deps_satisfied != want:
            return False
    return True


def _p_flag(cl: dict, ctx: EvalContext) -> bool:
    val = ctx.resolve(str(cl.get("var", "")))
    if val is None and not bool(cl.get("allow_unknown")):
        return _fail("flag", cl)
    want = cl.get("equals", True)
    return bool(val) == bool(want)


def _p_count(cl: dict, ctx: EvalContext) -> bool:
    what = str(cl.get("of", "fires"))
    left = None
    if what == "fires":
        sid = str(cl.get("sid", ""))
        st = ctx.trig.get(sid)
        left = st.fire_count if st else None
    elif what == "received_since":
        if ctx.store is None:
            return _fail("count:store", cl)
        left = ctx.store.count_received_since(float(cl.get("since", 0)))
    if left is None:
        return _fail("count", cl)
    right = _as_num(cl.get("value"))
    op = _OPS.get(str(cl.get("op", "")))
    if right is None or op is None:
        return _fail("count:op", cl)
    return bool(op(float(left), right))


PREDICATES: dict[str, Callable[[dict, EvalContext], bool]] = {
    "threshold": _p_threshold,
    "set": _p_set,
    "window": _p_window,
    "graph": _p_graph,
    "flag": _p_flag,
    "count": _p_count,
}


def _fail(where: str, cl: dict) -> bool:
    logger.warning("[spectrum·导演] 条件无法判定，按不成立处理：%s ← %s", where, cl)
    return False


Tier2Asker = Callable[[list[str], dict], Awaitable[dict]]


Tier3Asker = Callable[[list[tuple[int, str]], dict], Awaitable[list[int]]]


class StoryletEngine:
    def __init__(self, store, cfg: Callable[[str, Any], Any],
                 ask_tier2: Tier2Asker | None = None,
                 ask_tier3: Tier3Asker | None = None) -> None:
        self.store = store
        self.cfg = cfg
        self.ask_tier2 = ask_tier2
        self.ask_tier3 = ask_tier3
        self._lib: dict[str, Storylet] = {}

        self._vars: dict[str, dict] = {}


    def load(self) -> dict[str, str]:
        problems: dict[str, str] = {}
        lib: dict[str, Storylet] = {}
        try:
            rows = self.store.storylets_all()
        except Exception:
            logger.exception("[spectrum·导演] storylet 库读取失败，本轮按空库处理")
            self._lib = {}
            return {"": "库读取失败"}

        for row in rows:
            st = self._from_row(row)
            if st.problem:
                problems[st.sid] = st.problem
            lib[st.sid] = st


        for sid in self._find_cycles(lib):
            st = lib[sid]
            lib[sid] = _replace(st, problem="depends_on 成环")
            problems[sid] = "depends_on 成环"

        self._lib = lib
        self._vars = self._load_vars()
        if problems:
            logger.warning("[spectrum·导演] %d 条 storylet 被禁用：%s",
                           len(problems), problems)
        else:
            logger.info("[spectrum·导演] storylet 库就绪：%d 条可用",
                        len([s for s in lib.values() if s.usable]))
        return problems

    def _from_row(self, row) -> Storylet:
        def _j(raw, default):
            try:
                v = json.loads(raw) if raw else default
                return v if isinstance(v, type(default)) else default
            except Exception:
                return default

        sid = str(row["sid"] or "")
        problem = ""


        try:
            narr_raw = json.loads(row["narrative"]) if row["narrative"] else []
        except Exception:
            narr_raw = []
        if isinstance(narr_raw, str):
            narr = (narr_raw,) if narr_raw.strip() else ()
        elif isinstance(narr_raw, list):
            narr = tuple(str(x) for x in narr_raw if str(x).strip())
        else:
            narr = ()
        if not narr:
            problem = "narrative 为空"

        cond = _j(row["condition"], {})
        if not isinstance(cond, dict):
            cond = {}
            problem = problem or "condition 不是对象"

        eff = _j(row["effects"], {})
        if not isinstance(eff, dict):
            eff = {}
        bad = set(eff) - EFFECT_ALLOWLIST
        if bad:
            problem = f"effects 越界：{sorted(bad)}"
        for k in eff:
            if _FORBIDDEN_SMELL.search(str(k)):
                problem = f"effects 含禁止性键：{k}"


        if not problem:
            problem = _validate_thread_effects(eff)


        meta = _j(row["meta"], {})
        return Storylet(
            sid=sid,
            kind=str(row["kind"] or "action"),
            narrative=narr,
            condition=cond,
            depends_on=tuple(str(x) for x in _j(row["depends_on"], []) if str(x)),
            k_turns=int(row["k_turns"] or 0),
            k_minutes=int(meta.get("k_minutes") or 0),
            repeatable=bool(meta.get("repeatable")),
            target=str(meta.get("target") or ""),
            min_idle_minutes=int(meta.get("min_idle_minutes") or 0),
            max_idle_minutes=int(meta.get("max_idle_minutes") or 0),
            priority=int(row["priority"] or 0),
            weight=float(row["weight"] if row["weight"] is not None else 1.0),
            scope=str(row["scope"] or "global"),
            scene_id=str(row["scene_id"] or ""),
            got_scene_id=str(row["got_scene_id"] or ""),
            ending_name=str(row["ending_name"] or ""),
            effects=eff,
            eval_annot=_j(row["eval_annot"], {}),
            enabled=bool(row["enabled"]),
            problem=problem,
        )

    def _load_vars(self) -> dict[str, dict]:
        try:
            raw = self.store.get_state("global", "director_vars", "")
            d = json.loads(raw) if raw else {}
            return d if isinstance(d, dict) else {}
        except Exception:
            logger.warning("[spectrum·导演] director_vars 不是合法 JSON，按空处理")
            return {}

    @staticmethod
    def _find_cycles(lib: dict[str, Storylet]) -> set[str]:
        WHITE, GREY, BLACK = 0, 1, 2
        color: dict[str, int] = {sid: WHITE for sid in lib}
        bad: set[str] = set()
        stack: list[str] = []

        def visit(sid: str) -> None:
            color[sid] = GREY
            stack.append(sid)
            for dep in lib[sid].depends_on:
                if dep not in lib or dep not in color:
                    continue
                if color[dep] == GREY:
                    bad.update(stack[stack.index(dep):])
                elif color[dep] == WHITE:
                    visit(dep)
            stack.pop()
            color[sid] = BLACK

        for sid in list(lib):
            if color[sid] == WHITE:
                visit(sid)
        return bad


    def reconstruct(self) -> dict[str, TriggerState]:
        try:
            counts = self.store.director_fire_counts()
            last_ts = self.store.director_last_fire_ts()
        except Exception:
            logger.exception("[spectrum·导演] 转录读取失败，按「零触发」重建")
            counts, last_ts = {}, {}


        baseline = self._global_last_activity(last_ts)

        out: dict[str, TriggerState] = {}
        for sid, st in self._lib.items():
            n = len(st.narrative)
            fc = int(counts.get(sid, 0))


            out[sid] = TriggerState(
                fire_count=fc,
                deps_satisfied=self._deps_ok(st, counts),


                consumed=(st.kind != "fallback" and not st.repeatable
                          and ((fc >= n) if n else True)),
                turns_left=self._turns_left(st, last_ts.get(sid), baseline),
            )
        return out

    def _global_last_activity(self, last_ts: dict[str, float]) -> float:
        cands = [float(v) for v in (last_ts or {}).values() if v]
        try:
            ts = self.store.last_activity_ts_any()
            if ts:
                cands.append(float(ts))
        except Exception:
            logger.exception("[spectrum·导演] 读全局最近活动失败")
        return max(cands) if cands else 0.0

    def _deps_ok(self, st: Storylet, counts: dict[str, int]) -> bool:
        for dep in st.depends_on:
            d = self._lib.get(dep)
            if d is None:
                return False
            if int(counts.get(dep, 0)) < len(d.narrative):
                return False
        return True

    def _turns_left(self, st: Storylet, last_fire_ts: float | None,
                    baseline: float = 0.0) -> int:
        if st.kind != "fallback":
            return 0
        since_ts = float(last_fire_ts or 0.0)


        if st.k_minutes:
            import time as _t


            if not since_ts:
                since_ts = float(baseline or 0.0)
            return max(0, int(st.k_minutes) - int((_t.time() - since_ts) / 60.0))

        try:
            n = self.store.count_received_since(since_ts)
        except Exception:
            logger.exception("[spectrum·导演] fallback 计数失败，按「还没到」处理")
            return max(1, st.k_turns)
        return max(0, int(st.k_turns) - int(n))


    def candidates(self, ctx: EvalContext) -> list[Candidate]:
        out: list[Candidate] = []
        scene = ctx.story.get("scene_id") or "default"
        for sid, st in self._lib.items():
            if not st.usable:
                continue
            if st.scope != "global" and st.scope not in ctx.sessions:
                continue
            if st.scene_id and st.scene_id != scene:
                continue
            ts = ctx.trig.get(sid) or TriggerState()

            if st.kind == "fallback":


                if ts.turns_left <= 0 and not ts.consumed:
                    out.append(Candidate(st, ts, reason=f"fallback k={st.k_turns}"))
                continue

            if ts.consumed:
                continue
            if not ts.deps_satisfied:
                continue
            out.append(Candidate(st, ts, reason="条件待判"))
        return out


    def eval_tier1(self, st: Storylet, ctx: EvalContext) -> bool:
        return _bucket_pass(st, ctx, 1,
                            lambda cl: self._one_tier1(cl, ctx))

    def eval_condition(self, cond: dict, ctx: EvalContext) -> bool:
        return bucket_pass(cond, ctx, 1,
                           lambda cl: self._one_tier1(cl, ctx))

    def _one_tier1(self, cl: dict, ctx: EvalContext) -> bool:
        fn = PREDICATES.get(str(cl.get("pred", "")))
        if fn is None:
            return _fail(f"未知谓词 {cl.get('pred')!r}", cl)
        try:
            return bool(fn(cl, ctx))
        except Exception:
            logger.exception("[spectrum·导演] 谓词求值异常，按不成立：%s", cl)
            return False

    async def evaluate(self, cands: list[Candidate], ctx: EvalContext) -> list[Candidate]:
        alive = [c for c in cands if self.eval_tier1(c.storylet, ctx)]


        alive = await self._eval_tier2(alive, ctx)


        alive = await self._eval_tier3(alive, ctx)

        for c in alive:
            c.reason = "条件通过"
        return alive

    async def _eval_tier2(self, cands: list[Candidate],
                          ctx: EvalContext) -> list[Candidate]:
        needed: dict[str, list[tuple[Candidate, dict]]] = {}
        for c in cands:
            for cl in (c.storylet.condition.get("all") or []) + \
                      (c.storylet.condition.get("any") or []):
                if isinstance(cl, dict) and int(cl.get("tier", 1)) == 2:
                    needed.setdefault(str(cl.get("var", "")), []).append((c, cl))

        if needed and self.ask_tier2 is not None:
            cache = ctx.story.setdefault("tier2_cache", {})
            fresh_for = float(self.cfg("director_tier2_fresh_minutes", 10)) * 60
            stale = [n for n in needed
                     if not _is_fresh(cache.get(n), ctx.now, fresh_for)]
            if stale:
                try:
                    got = await self.ask_tier2(stale, self._vars)
                except Exception:
                    logger.exception("[spectrum·导演] Tier 2 变量取值失败，本轮按未知")
                    got = {}
                for n in stale:
                    v = (got or {}).get(n)
                    if self._var_ok(n, v):
                        cache[n] = {"value": v, "at": ctx.now}


        return [c for c in cands
                if _bucket_pass(c.storylet, ctx, 2,
                                lambda cl: self._cmp_var(cl, ctx))]

    def _cmp_var(self, cl: dict, ctx: EvalContext) -> bool:
        name = str(cl.get("var", ""))
        entry = (ctx.story.get("tier2_cache") or {}).get(name)
        val = entry.get("value") if isinstance(entry, dict) else None
        if val is None:
            return False
        op = _OPS.get(str(cl.get("op", "")))
        target = cl.get("value")

        if isinstance(val, bool) or isinstance(target, bool):
            if str(cl.get("op")) in ("==", "!="):
                return (val == target) if cl["op"] == "==" else (val != target)
            return False
        l, r = _as_num(val), _as_num(target)
        if l is None or r is None or op is None:
            return _fail(f"Tier2 比较 {name}", cl)
        return bool(op(l, r))

    def var_ok(self, name: str, v: Any) -> bool:
        return self._var_ok(name, v)

    def _var_ok(self, name: str, v: Any) -> bool:
        decl = self._vars.get(name)
        if decl is None or v is None:
            return v is not None
        t = str(decl.get("type", "number"))
        if t == "bool":
            return isinstance(v, bool)
        if t == "number":
            n = _as_num(v)
            if n is None:
                return False
            rng = decl.get("range")
            if isinstance(rng, list) and len(rng) == 2:
                return float(rng[0]) <= n <= float(rng[1])
            return True
        if t == "enum":
            pool = decl.get("enum") or []
            return v in pool
        return True

    async def _eval_tier3(self, cands: list[Candidate],
                          ctx: EvalContext) -> list[Candidate]:
        """配置允许时批量调用复杂条件判断；未确认的候选不保留。"""
        want = [c for c in cands if _has_tier3(c.storylet)]
        if not want or self.ask_tier3 is None:
            return [c for c in cands if not _has_tier3(c.storylet)]

        if not bool(self.cfg("director_tier3_enabled", False)):


            return [c for c in cands if not _has_tier3(c.storylet)]

        ordered = freeze_order(want)
        pairs = [(i, _tier3_text(c.storylet)) for i, c in enumerate(ordered)]
        try:
            idx = await self.ask_tier3(pairs, _tier3_snapshot(ctx))
        except Exception:
            logger.exception("[spectrum·导演] Tier 3 调用失败，本轮全部按不成立")
            idx = []

        keep: set[str] = set()
        for i in idx or []:
            if isinstance(i, bool) or not isinstance(i, int):
                logger.warning("[spectrum·导演] Tier 3 返回非整数下标 %r，丢弃", i)
                continue
            if not (0 <= i < len(ordered)):


                logger.warning("[spectrum·导演] Tier 3 下标越界 %r，丢弃", i)
                continue
            keep.add(ordered[i].storylet.sid)

        return [c for c in cands
                if (not _has_tier3(c.storylet)) or c.storylet.sid in keep]


    def consume(self, cand: Candidate, *, now: float, umo: str = "",
                life_id: str = "", turn_seq: int = 0,
                ctx: EvalContext | None = None) -> ConsumedBeat:
        """选取本次叙事段并记录未确认投递的执行条目，返回消费结果。"""
        st = cand.storylet
        idx = cand.state.narr_index
        if st.repeatable and st.narrative:


            idx %= len(st.narrative)
        seg = st.narrative[idx] if idx < len(st.narrative) else st.narrative[-1]
        if ctx is not None:
            seg = render_narrative(seg, ctx)
        fid = self.store.add_director_fire(
            ts=now, sid=st.sid, kind=st.kind, umo=umo, life_id=life_id,
            turn_seq=turn_seq, narr_index=idx, delivered=0, narrative=seg,
            meta={"effects": st.effects, "priority": st.priority,
                  "weight": st.weight, "reason": cand.reason},
        )
        return ConsumedBeat(fire_id=fid, sid=st.sid, narrative=seg, index=idx,
                            got_scene_id=st.got_scene_id,
                            ending_name=st.ending_name)


    def library(self) -> dict[str, Storylet]:
        return dict(self._lib)

    def var_decls(self) -> dict[str, dict]:
        return dict(self._vars)


_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def render_narrative(text: str, ctx: EvalContext) -> str:
    def sub(m):
        name = m.group(1)
        try:
            v = ctx.resolve(name)
        except Exception:
            logger.exception("[spectrum·导演] narrative 变量 %s 求值异常", name)
            v = None
        if v is None:
            logger.warning("[spectrum·导演] narrative 里的 {%s} 没有定义，原样保留",
                           name)
            return m.group(0)
        return str(v)

    return _PLACEHOLDER.sub(sub, text or "")


def _replace(st: Storylet, **kw) -> Storylet:
    d = st.__dict__.copy()
    d.update(kw)
    return Storylet(**d)


def _is_fresh(entry: Any, now: float, fresh_for: float) -> bool:
    return (isinstance(entry, dict) and entry.get("value") is not None
            and freeze_mod.elapsed_since(float(entry.get("at", 0)), now) < fresh_for)


def _clauses(st: Storylet, bucket: str, tier: int) -> list[dict]:
    return clauses_of(st.condition, bucket, tier)


def clauses_of(cond: dict, bucket: str, tier: int) -> list[dict]:
    return [cl for cl in (cond or {}).get(bucket) or []
            if isinstance(cl, dict) and int(cl.get("tier", 1)) == tier]


def bucket_pass(cond: dict, ctx: EvalContext, tier: int, check) -> bool:
    for cl in clauses_of(cond, "all", tier):
        if not check(cl):
            return False
    anys = clauses_of(cond, "any", tier)
    if anys and not any(check(cl) for cl in anys):
        return False
    return True


def _bucket_pass(st: Storylet, ctx: EvalContext, tier: int,
                 check) -> bool:
    return bucket_pass(st.condition, ctx, tier, check)


def _has_tier3(st: Storylet) -> bool:
    return bool(_clauses(st, "all", 3) or _clauses(st, "any", 3))


def _tier3_text(st: Storylet) -> str:
    parts = [str(cl.get("nl", "")) for cl in _clauses(st, "all", 3)]
    parts += [str(cl.get("nl", "")) for cl in _clauses(st, "any", 3)]
    return "；且，".join(p for p in parts if p)


def freeze_order(cands: list[Candidate]) -> list[Candidate]:
    return sorted(cands, key=lambda c: c.storylet.sid)


def _tier3_snapshot(ctx: EvalContext) -> dict:
    return {
        "now": ctx.now,
        "phase": ctx.story.get("phase"),
        "scene_id": ctx.story.get("scene_id") or "default",
        "tension": ctx.story.get("tension"),
        "beats_consumed": ctx.story.get("beats_consumed"),
        "has_owner": bool(ctx.owner_session),
        "asleep": ctx.asleep,
        "open_threads_n": len(ctx.story.get("open_threads") or []),


        "recent_exchange": ctx.recent_text or "（没有最近的往来）",
    }


__all__ = [
    "Storylet", "TriggerState", "Candidate", "ConsumedBeat", "EvalContext",
    "StoryletEngine", "PREDICATES", "EFFECT_ALLOWLIST", "freeze_order",
    "render_narrative", "bucket_pass", "clauses_of",
]
