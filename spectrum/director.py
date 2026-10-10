"""安排并执行后台回合。
协调剧情素材、承诺、醒来、认知整理、故事线和介入预算。"""


from __future__ import annotations

from . import freeze as freeze_mod

import json
import logging
import math
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .drama_eval import (
    annotated, budget_filter, can_close, combine, intervention_cost,
    story_density,
)
from .director_prompts import director_history_block, director_phase_block
from .storylet import (
    Candidate, ConsumedBeat, EvalContext, Storylet, StoryletEngine,
    freeze_order,
)
from .thread_lib import LIB_BY_ID, REF_PREFIX, expand_thread_ref
from .threads import parse_thread_spec, urgency

try:
    from astrbot.api import logger
except Exception:
    logger = logging.getLogger("spectrum")


TURN_ORDER = {
    "wake": 0,
    "intent": 1,
    "beat": 2,
    "fallback": 3,
    "compress": 4,
}


_CONFIG_KEYS: dict[str, Any] = {
    "director_advance_cool_minutes": 20,
    "director_advance_idle_minutes": 40,
    "director_advance_max_idle_minutes": 0,
    "director_advance_willingness_threshold": 0.3,
    "director_arc_beats": 12,
    "director_arc_max_beats": 40,
    "director_beat_span_minutes": 45,
    "director_closure_enabled": False,
    "director_compress_via_director": True,
    "director_defer_minutes": 10,
    "director_eval_enabled": False,
    "director_fires_max_rows": 0,
    "director_history_turns": 6,
    "director_intensity_decay_per_minute": 0.0,
    "director_intent_expire_minutes": 60,
    "director_intent_max_attempts": 3,
    "director_intervention_budget": 2.4,
    "director_intervention_recharge": 1.0,
    "director_library_ttl_s": 60,
    "director_max_turns_per_tick": 2,
    "director_min_paid_threads": 1,
    "director_pacing_enabled": False,
    "director_peak_threshold": 0.9,
    "director_relax_minutes": 0,
    "director_startup_grace_s": 60,
    "director_sustain_minutes": 0,
    "director_thread_abandon_factor": 2.0,
    "director_thread_max_open": 5,
    "director_thread_max_per_kind": 2,
    "director_thread_max_per_scope": 2,
    "director_tier2_fresh_minutes": 10,
    "director_tier3_enabled": False,
    "director_tier3_max_per_hour": 20,
    "director_w_message": 0.0,
    "director_w_mood": 0.0,
}


_CONFIG_MISSING = object()


_TIER2_PROMPT = """下面每一条描述我的一个状态量，格式为「变量名: 当前取值 // 取值规则」。

【状态量】
%s

【处境快照】
%s

我只输出一个 JSON 对象。
键是取值相对当前发生变化的变量名，值是变化后的取值。
取值未发生变化的变量不出现在结果中。
我的取值必须符合该变量注释中声明的规则。
我无法确定的变量不猜，不出现在结果中。
我不输出任何解释。"""


_TIER3_PROMPT = """我根据眼前的处境快照，判断下面这些条件是否已经成立。

【待判条件】
%s

【处境快照】
%s

我只输出一个 JSON 数组，写入已经成立的条件下标。
没有成立的条件时，我输出空数组。
我只依据快照里的内容判断，不推测未给出的事，也不输出解释。"""


@dataclass
class Turn:
    kind: str
    umo: str
    sid: str = ""
    narrative: str = ""
    payload: dict = field(default_factory=dict)


    targets: tuple[str, ...] = ()

    @property
    def priority(self) -> int:
        return TURN_ORDER.get(self.kind, 99)


@dataclass
class DirectorPlan:
    turns: list[Turn] = field(default_factory=list)
    considered: int = 0
    dropped: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    phase: str = ""
    note: str = ""

    def render_for_log(self) -> str:
        tail = (f"（候选 {self.considered}，筛掉 {len(self.dropped)}，"
                f"延后 {len(self.deferred)}）")
        if not self.turns:
            why = f"：{self.note}" if self.note else ""
            return f"无回合{why}{tail}"
        seq = " → ".join(f"{t.kind}:{t.sid or t.umo[:18]}" for t in self.turns)
        return f"{seq}{tail}"


class Director:
    def __init__(self, host, rng: random.Random | None = None) -> None:
        self.host = host
        self.store = host.store
        self.life = host.life

        self.rng = rng or random.Random()
        self.engine = StoryletEngine(self.store, self._cfg,
                                     ask_tier2=self._ask_tier2,
                                     ask_tier3=self._ask_tier3)

        self._story: dict = {}
        self._lib_loaded_at = 0.0

        self._owner_session = ""
        self._asleep = False


        self._ctx: EvalContext | None = None

        self._born_at = time.time()

        self._open_rows: list = []


        self._tier3_last_key: Any = None
        self._tier3_last_idx: list[int] = []

        self._last_plan_log: str = ""

        self._log_config_sources()


    def _log_config_sources(self) -> None:
        missing: list[str] = []
        for key in sorted(_CONFIG_KEYS):
            default = _CONFIG_KEYS[key]
            got = self._cfg(key, _CONFIG_MISSING)
            if got is _CONFIG_MISSING:
                missing.append(f"{key}={default!r}")
            else:
                logger.info("[spectrum·导演] 配置 %s = %r（来自配置）", key, got)
        if missing:
            logger.warning(
                "[spectrum·导演] 以下 %d 个配置键不在配置里，用代码默认值：%s",
                len(missing), "，".join(missing))

    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            v = self.host.get_cfg(key, default)
            return default if v is None else v
        except Exception:
            return default


    async def tick(self) -> None:
        epoch = freeze_mod.checkpoint()
        if not freeze_mod.may_commit(epoch):
            return
        now = time.time()


        spoke: set[str] = set()


        grace = float(self._cfg("director_startup_grace_s", 60))
        if freeze_mod.elapsed_since(self._born_at, now) < grace:
            logger.info("[spectrum·导演] 开机静默期（还有 %.0f 秒），本轮不出牌",
                        grace - (freeze_mod.elapsed_since(self._born_at, now)))
            try:
                self.store.set_story_state(self._story or self.store.get_story_state())
            except freeze_mod.FrozenSkipped:
                return
            except Exception as error:
                freeze_mod.record_failure("director", error)
                pass
            return


        try:
            await self._flush_mechanical(now, spoke)
            if not freeze_mod.may_commit(epoch):
                return
        except freeze_mod.FrozenSkipped:
            return
        except Exception as error:
            freeze_mod.record_failure("director", error)
            logger.exception("[spectrum·导演] 机械投递失败")


        try:
            plan = await self.advance(now)
            if not freeze_mod.may_commit(epoch):
                return
        except freeze_mod.FrozenSkipped:
            return
        except Exception as error:
            freeze_mod.record_failure("director", error)
            logger.exception("[spectrum·导演] advance 失败，退回只看承诺与唤醒")
            plan = DirectorPlan(note="advance 异常")
            try:
                plan.turns = await self._degraded_turns(now)
                if not freeze_mod.may_commit(epoch):
                    return
            except freeze_mod.FrozenSkipped:
                return
            except Exception as error:
                freeze_mod.record_failure("director", error)
                logger.exception("[spectrum·导演] 降级路径也失败了，本轮空转")


        _line = plan.render_for_log()
        if _line != self._last_plan_log:
            logger.info("[spectrum·导演] %s", _line)
            self._last_plan_log = _line


        for turn in plan.turns:


            _us = turn.targets or (turn.umo,)
            if turn.kind != "wake" and all(u in spoke for u in _us):
                continue
            try:
                await self.execute(turn, spoke)
                if not freeze_mod.may_commit(epoch):
                    return
            except freeze_mod.FrozenSkipped:
                return
            except Exception as error:
                freeze_mod.record_failure("director", error)
                logger.exception("[spectrum·导演] 回合 %s(%s) 执行失败，继续下一个",
                                 turn.kind, turn.sid or turn.umo)


        try:
            await self.title_pass(now)
            if not freeze_mod.may_commit(epoch):
                return
        except freeze_mod.FrozenSkipped:
            return
        except Exception as error:
            freeze_mod.record_failure("director", error)
            logger.exception("[spectrum·导演] 起名失败，不影响本轮")


        try:
            await self.host._decay_willingness(now)
            if not freeze_mod.may_commit(epoch):
                return
        except freeze_mod.FrozenSkipped:
            return
        except Exception as error:
            freeze_mod.record_failure("director", error)
            logger.exception("[spectrum·导演] 意愿衰减失败")


        try:
            self.store.set_story_state(self._story)
        except freeze_mod.FrozenSkipped:
            return
        except Exception as error:
            freeze_mod.record_failure("director", error)
            logger.exception("[spectrum·导演] story_state 写回失败")


        try:
            self.store.expire_stale_fired_intents(
                older_than_s=float(self._cfg("director_intent_expire_minutes", 60)) * 60,
                max_attempts=int(self._cfg("director_intent_max_attempts", 3)))
        except freeze_mod.FrozenSkipped:
            return
        except Exception as error:
            freeze_mod.record_failure("director", error)
            logger.exception("[spectrum·导演] 承诺作废扫描失败")


        try:
            cap = int(self._cfg("director_fires_max_rows", 0))
            if cap > 0:
                self.store.rollup_director_fires(cap)
        except freeze_mod.FrozenSkipped:
            return
        except Exception as error:
            freeze_mod.record_failure("director", error)
            logger.exception("[spectrum·导演] 转录折算失败，不影响本轮")


    async def advance(self, now: float) -> DirectorPlan:
        """生成本次任务表，综合剧情、承诺、睡眠、预算及每会话任务上限。"""
        epoch = freeze_mod.checkpoint()
        self._reload_library_if_stale(now)
        story = self.store.get_story_state()
        story["turn_seq"] = int(story.get("turn_seq", 0)) + 1
        self._story = story


        self._open_rows = self._sync_threads(story)

        plan = DirectorPlan(phase=str(story.get("phase")))


        sessions = self._sessions()


        self._pacing_heartbeat(story, now, sessions)


        ctx = self._build_ctx(story, now, sessions)


        ctx.trig = self.engine.reconstruct()
        cands = self.engine.candidates(ctx)
        plan.considered = len(cands)


        cands = self._close_thread_gate(cands, plan)


        cands = self._thread_axis_gate(cands, plan)


        asleep = self._sleep_muted(now)
        if asleep:
            plan.note = (plan.note + " 睡着").strip()
        will_select = (not asleep) and self._beat_terminated(story, now)


        fallback_cands = [c for c in cands if c.storylet.kind == "fallback"]
        cands = [c for c in cands if c.storylet.kind != "fallback"]
        turns: list[Turn] = []
        if fallback_cands and not asleep:
            self._emit_beat_turn(fallback_cands[0], ctx, now, plan, turns,
                                 note_miss=False)


        if will_select:
            cands = await self.engine.evaluate(cands, ctx)
            if not freeze_mod.may_commit(epoch):
                raise freeze_mod.FrozenSkipped("导演评估跨过系统暂停")


        _act = int(story.get("act_no") or 1)
        _arc = int(self._cfg("director_arc_beats", 12))
        ok, why = can_close(story, story.get("open_threads"),
                            min_beats=_arc,
                            min_paid=int(self._cfg("director_min_paid_threads", 1)),
                            paid_n=self.store.paid_threads_n(_act),
                            max_beats=int(self._cfg("director_arc_max_beats", 40)),
                            total_beats=int(story.get("total_beats") or 0))
        allow = bool(ok and self._cfg("director_closure_enabled", False))
        ends = [c for c in cands if c.storylet.ending_name]


        _closing = bool(ends) and allow
        if ends and not allow:
            cands = [c for c in cands if not c.storylet.ending_name]
            plan.note = (plan.note + f" 不能收束（{why}）").strip()
        elif ends:
            cands = ends
            plan.note = "可收束"


        if will_select and not _closing:
            self._roll_act(story, now, plan, _arc)


        if will_select:
            beat = self._select(cands, story, ctx)
            if beat is not None:


                self._emit_beat_turn(beat, ctx, now, plan, turns)


                plan.dropped += [c.storylet.sid for c in cands
                                 if c is not beat]
        else:
            plan.note = (plan.note + " 拍进行中").strip()


        turns = self._apply_budget(turns, story, now, plan)


        if not asleep:
            turns += self._lift_intents(story, now)
        turns += self._wake_edge(story, now)
        if not asleep:
            turns += self._compress_candidate(story, now, plan)


        turns.sort(key=lambda t: t.priority)
        cap = max(1, int(self._cfg("director_max_turns_per_tick", 2)))
        seen: dict[str, int] = {}
        kept: list[Turn] = []
        for t in turns:


            _all = list(t.targets or (t.umo,))
            _keep = [u for u in _all if seen.get(u, 0) < cap]
            if not _keep:
                plan.dropped.append(f"{t.kind}:{t.sid or t.umo}")
                continue
            for u in _keep:
                seen[u] = seen.get(u, 0) + 1
            if t.targets:
                if len(_keep) != len(_all):
                    plan.dropped.append(
                        f"{t.kind}:{t.sid or t.umo} 部分会话本 tick 已封顶")
                t.targets = tuple(_keep)
                t.umo = _keep[0]
            kept.append(t)
        turns = kept

        plan.turns = turns
        return plan

    async def _degraded_turns(self, now: float) -> list[Turn]:
        self._story = self.store.get_story_state()
        t = self._wake_edge(self._story, now) + self._lift_intents(self._story, now)
        t.sort(key=lambda x: x.priority)
        return t


    def _reload_library_if_stale(self, now: float) -> None:
        ttl = float(self._cfg("director_library_ttl_s", 60))
        if now - self._lib_loaded_at < ttl and self.engine.library():
            return
        self.engine.load()
        self._lib_loaded_at = now

    def _sessions(self) -> list[str]:
        try:
            return self.host.active_sessions()
        except Exception:
            logger.exception("[spectrum·导演] 会话枚举失败")
            return []

    def _peer_idle_minutes(self, umo: str, now: float) -> float | None:
        try:
            ts = self.store.last_received_ts(umo)
        except Exception:
            logger.exception("[spectrum·导演] 读「对方最后发言」失败：%s", umo)
            return None
        return None if ts is None else max(0.0, freeze_mod.elapsed_since(float(ts), now) / 60.0)

    def _history_block(self, umo: str) -> str:
        turns = int(self._cfg("director_history_turns", 6))
        if turns <= 0:
            return ""
        try:
            rows = self.store.recent_exchange(umo, turns) or []
        except Exception:
            logger.exception("[spectrum·导演] 取该会话的最近往来失败：%s", umo)
            return ""
        lines = []
        for r in reversed(list(rows)):
            try:
                who = "对方：" if r["kind"] == "received" else "我："
            except Exception:
                continue
            text = str(r["text"] or "").replace("\n", " ").strip()
            if text:
                lines.append(who + text)
        if not lines:
            return ""


        logger.info("[spectrum·导演] 后台轮带上 %d 行最近往来（%s）", len(lines), umo)
        return director_history_block("\n".join(lines))

    def _observer(self):
        if not self._cfg("possession_debug", False):
            return None
        return self._observe_background

    @staticmethod
    def _observe_background(decision) -> None:
        from .parse import prefill_think
        try:
            think = decision.think or prefill_think(decision.raw)
        except Exception:
            think = decision.think
        logger.info(
            "[spectrum·夺舍观测·后台]\n"
            "【她心里想的】%s\n"
            "【她说出口的】%s\n"
            "【原始输出头 200 字】%s",
            (think or "（模型没写思考）")[:400],
            (decision.content or "（没说）")[:120],
            (decision.raw or "")[:200],
        )

    def _build_ctx(self, story: dict, now: float, sessions: list[str]) -> EvalContext:
        idle: dict[str, float | None] = {}
        will: dict[str, float] = {}


        rel: dict[str, tuple[float, float]] = {}
        try:
            for p in self.store.list_all_persons(limit=1000):
                rel[str(p["person_id"])] = (float(p["affinity"]), float(p["trust"]))
        except Exception:
            logger.exception("[spectrum·导演] 读人物档案关系量失败，本轮关系条件按无数据走")
        for u in sessions:
            try:
                idle[u] = self._peer_idle_minutes(u, now)
                will[u] = float(self.store.get_willingness(u))
            except Exception:
                logger.exception("[spectrum·导演] 会话 %s 的指标读取失败", u)
        owner = ""
        companion = ""
        try:
            ow = self.life.get_owner() or {}
            owner = str(ow.get("session") or "")


            companion = str(ow.get("with") or "")
        except Exception:
            logger.exception("[spectrum·导演] 读主线归属失败")
        asleep = False
        try:
            asleep = bool(self.store.is_asleep())
        except Exception:
            pass
        self._owner_session = owner
        self._asleep = asleep

        _recent = ""
        try:
            _target = owner or (max(sessions, key=lambda u: self.store.last_activity_ts(u) or 0)
                                if sessions else "")
            if _target:


                turns = self.store.recent_exchange(_target, 8) or []
                _lines = []
                for t in reversed(list(turns)):
                    try:
                        who = "对方：" if t["kind"] == "received" else "我："
                        _lines.append(who + str(t["text"] or ""))
                    except Exception:
                        continue
                _recent = "\n".join(_lines)
        except Exception:
            logger.exception("[spectrum·导演] 取最近往来失败")
        self._ctx = EvalContext(
            now=now, story=story, trig={}, store=self.store, life=self.life,
            cfg=self._cfg, tz=str(self._cfg("timezone", "Asia/Shanghai")),
            idle_minutes=idle, owner_session=owner, companion=companion,
            willingness=will, relation=rel, sessions=sessions, asleep=asleep,
            recent_text=_recent,
        )
        return self._ctx


    def _pacing_heartbeat(self, story: dict, now: float,
                          sessions: list[str] | None = None) -> None:
        if not bool(self._cfg("director_pacing_enabled", False)):
            return
        self._feed_intensity(story, now, sessions or [])
        peak = float(self._cfg("director_peak_threshold", 0.9))
        sustain = float(self._cfg("director_sustain_minutes", 0)) * 60
        relax = float(self._cfg("director_relax_minutes", 0)) * 60


        decay = float(self._cfg("director_intensity_decay_per_minute", 0.0))
        per = dict(story.get("intensity_per") or {})
        suspend_until = float(story.get("decay_suspended_until", 0) or 0)
        suspend_start = float(story.get("phase_since", now))
        last_decay = float(story.get("pacing_decay_at", now))
        story["pacing_decay_at"] = now

        start_age = freeze_mod.elapsed_since(suspend_start, last_decay)
        end_age = freeze_mod.elapsed_since(suspend_start, now)
        paused_age = max(0.0, suspend_until - suspend_start)
        dt_min = max(0.0, end_age - max(start_age, paused_age)) / 60.0
        for u, v in list(per.items()):
            per[u] = max(0.0, float(v) - decay * dt_min)
        story["intensity_per"] = per

        story["intensity"] = max(per.values()) if per else 0.0

        phase = str(story.get("phase") or "BUILD_UP")
        since = float(story.get("phase_since") or now)
        age = freeze_mod.elapsed_since(since, now)
        inten = float(story["intensity"])

        if phase == "BUILD_UP":
            if inten > peak:
                self._set_phase(story, "SUSTAIN_PEAK", now)
        elif phase == "SUSTAIN_PEAK":
            if age >= sustain and inten <= peak:
                self._set_phase(story, "PEAK_FADE", now)
            elif age >= sustain * 2:
                self._set_phase(story, "PEAK_FADE", now)
        elif phase == "PEAK_FADE":

            if inten <= peak:
                self._set_phase(story, "RELAX", now)
        elif phase == "RELAX":
            if age >= relax:
                self._set_phase(story, "BUILD_UP", now)
        else:
            self._set_phase(story, "BUILD_UP", now)

    def _feed_intensity(self, story: dict, now: float,
                        sessions: list[str]) -> None:
        w_msg = float(self._cfg("director_w_message", 0.0))
        w_mood = float(self._cfg("director_w_mood", 0.0))
        per = dict(story.get("intensity_per") or {})
        seen = dict(story.get("pacing_seen") or {})

        for u in sessions:
            s = dict(seen.get(u) or {})
            add = 0.0
            if w_msg:
                ts = None
                try:
                    ts = self.store.last_activity_ts(u)
                except Exception:
                    logger.exception("[spectrum·导演] 读 %s 最后活跃失败", u)
                if ts is not None:
                    prev = s.get("act")
                    if prev is not None and ts > prev:


                        rate = self._recent_rate(u, ts)
                        base = float(s.get("rate_base") or 0.0)
                        if base > 0 and rate > 0:
                            burst = max(0.0, min(1.0, rate / base - 1.0))
                            add += w_msg * burst

                        s["rate_base"] = (rate if base <= 0
                                          else base * 0.9 + rate * 0.1)
                    s["act"] = ts
            if add:
                per[u] = min(1.0, float(per.get(u, 0.0)) + add)
            seen[u] = s

        if w_mood:

            try:
                from .prism_bridge import peek_emotion
                v = peek_emotion()
            except Exception:
                v = None
            if v is not None:
                per["_mood"] = min(1.0, abs(float(v)))

        story["intensity_per"] = per
        story["pacing_seen"] = seen


    def _recent_rate(self, umo: str, now: float) -> float:
        try:
            n = self.store.count_received_since(now - 600, umo=umo)
            return float(n) / 10.0
        except Exception:
            logger.exception("[spectrum·导演] 算最近速率失败：%s", umo)
            return 0.0

    def _set_phase(self, story: dict, phase: str, now: float) -> None:
        if story.get("phase") != phase:
            logger.info("[spectrum·导演] 节奏 %s → %s（强度 %.2f）",
                        story.get("phase"), phase, story.get("intensity", 0.0))
        story["phase"] = phase
        story["phase_since"] = now


    def _beat_terminated(self, story: dict, now: float) -> bool:
        cursor = str(story.get("beat_cursor") or "")
        if not cursor:
            return True
        span = float(self._cfg("director_beat_span_minutes", 45)) * 60
        return freeze_mod.elapsed_since(float(story.get("beat_started_at") or now), now) >= span

    def _roll_act(self, story: dict, now: float, plan, arc_beats: int) -> None:
        if arc_beats <= 0:
            return
        if int(story.get("beats_consumed") or 0) < arc_beats:
            return
        prev = int(story.get("act_no") or 1)
        story["act_no"] = prev + 1
        story["beats_consumed"] = 0
        story["last_beat_at_n"] = 0
        story["tension"] = 1
        story["act_started_at"] = now
        plan.note = (plan.note + f" 翻幕（第 {prev} 幕走满 {arc_beats} 拍）").strip()
        logger.info("[spectrum·导演] 翻幕：第 %d 幕走满 %d 拍，进入第 %d 幕",
                    prev, arc_beats, prev + 1)

    def _select(self, cands: list[Candidate], story: dict,
                ctx: EvalContext) -> Candidate | None:
        if not cands:
            return None


        top = max(c.storylet.priority for c in cands)
        tier = [c for c in cands if c.storylet.priority == top]
        if len(tier) == 1:
            return tier[0]


        n = int(story.get("beats_consumed") or 0)
        beatmax = max(1, int(self._cfg("director_arc_beats", 12)))
        if beatmax - n <= 0:


            logger.info("[spectrum·导演] 本幕已走满 %d 拍，弧打分停用，按权重抽签", n)
            return self._weighted_pick([(c, float(c.storylet.weight))
                                        for c in tier])
        target = float(story.get("arc_target") or 3)
        tension = float(story.get("tension") or 1)
        slope_next = (target - tension) / (beatmax - n)


        eval_on = bool(self._cfg("director_eval_enabled", False))
        scored: list[tuple[Candidate, float]] = []
        for c in tier:
            dx = float((c.storylet.effects or {}).get("tension_delta", 0.0))
            s = math.exp(-abs(slope_next - dx))
            if eval_on:
                dodm = self._dodm_score(c, story, n)
                if dodm is not None:


                    s *= (1.0 + dodm)
            scored.append((c, s * max(1e-6, float(c.storylet.weight))))


        return self._weighted_pick(scored) or tier[0]

    def _weighted_pick(self, pairs: list[tuple[Candidate, float]]) -> Candidate | None:
        pairs = [(c, float(w)) for c, w in pairs if w and float(w) > 0]
        total = sum(w for _c, w in pairs)
        if total <= 0:
            return None
        r = self.rng.random() * total
        acc = 0.0
        for c, w in pairs:
            acc += w
            if r <= acc:
                return c
        return pairs[-1][0]

    def _dodm_score(self, cand: Candidate, story: dict,
                    beats_consumed: int) -> float | None:
        feats: dict[str, float | None] = {}
        if beats_consumed:
            feats["story_density"] = story_density(
                float(story.get("last_beat_at_n") or 0.0), float(beats_consumed))
        else:
            feats["story_density"] = None
        feats.update(annotated(cand.storylet))
        return combine(feats)


    def _idle_last_map(self, st: Storylet) -> dict[str, float]:
        try:
            return self.store.director_last_fire_ts_by_session(st.sid)
        except Exception:
            logger.exception("[spectrum·导演] 读空闲冷却失败，按「无冷却」处理")
            return {}

    def _audience_ok(self, umo: str) -> bool:
        try:
            return bool(self.life.admit_state(umo))
        except Exception:
            logger.exception("[spectrum·受众] 归属判定异常，**按拒绝处理**")
            return False

    def _idle_eligible(self, umo: str, ctx: EvalContext, st: Storylet,
                       now: float, last: dict[str, float]) -> bool:
        lo = int(st.min_idle_minutes
                 or self._cfg("director_advance_idle_minutes", 40))
        hi = int(st.max_idle_minutes
                 or self._cfg("director_advance_max_idle_minutes", 0))

        idle = ctx.idle_minutes.get(umo)
        if idle is None:
            return False
        if idle < lo:
            return False
        if hi > 0 and idle > hi:
            return False

        thr = float(self._cfg("director_advance_willingness_threshold", 0.3))
        if float(ctx.willingness.get(umo, 0.0)) < thr:
            return False

        cool = float(self._cfg("director_advance_cool_minutes", 20))
        prev = float(last.get(umo, 0.0))
        if prev and freeze_mod.elapsed_since(prev, now) / 60.0 < cool:
            return False
        return True

    def _emit_beat_turn(self, cand, ctx, now: float, plan,
                        turns: list, *, note_miss: bool = True) -> bool:
        st = cand.storylet
        targets = [u for u in ctx.sessions if "GroupMessage" not in u]
        if targets:
            last = self._idle_last_map(st)
            kept = [u for u in targets
                    if self._idle_eligible(u, ctx, st, now, last)]
            if not kept and note_miss:
                plan.note = (plan.note
                             + " 意愿/空闲/冷却未过，本轮不推进").strip()
            targets = kept
        before = list(targets)
        targets = [u for u in targets if self._audience_ok(u)]
        for u in before:
            if u not in targets:
                logger.info("[spectrum·受众] 落点被归属挡下: target=%s %s",
                            u.replace("default:", "")[:24], "[AudienceGuard]")
        if not targets:
            if note_miss:
                plan.note = (plan.note + " 归属挡下全部落点").strip()
            return False


        pick = min(targets, key=lambda u: ctx.idle_minutes.get(u, float("inf")))
        ordered = (pick,) + tuple(u for u in targets if u != pick)
        logger.info("[spectrum·受众] [AudienceGuard] sid=%s candidate_umos=%s "
                    "selected_umo=%s gate=items",
                    st.sid,
                    [u.replace("default:", "")[:24] for u in before],
                    pick.replace("default:", "")[:24])
        turns.append(Turn(
            kind="fallback" if st.kind == "fallback" else "beat",
            umo=pick, sid=st.sid,
            targets=ordered,
            payload={"cand": cand}))
        return True


    def _apply_budget(self, turns: list[Turn], story: dict, now: float,
                      plan: DirectorPlan) -> list[Turn]:
        if not turns:
            return turns
        cap = float(self._cfg("director_intervention_budget", 2.4))
        recharge = float(self._cfg("director_intervention_recharge", 1.0))
        defer_s = float(self._cfg("director_defer_minutes", 10)) * 60


        last_at = float(story.get("budget_at") or now)
        elapsed_min = max(0.0, freeze_mod.elapsed_since(last_at, now) / 60.0)
        budget = min(cap, float(story.get("intervention_budget", cap))
                     + recharge * elapsed_min)
        story["budget_at"] = now

        deferred = dict(story.get("deferred") or {})
        deferred_since = dict(story.get("deferred_since") or {})

        for sid, nb in list(deferred.items()):
            start = float(deferred_since.get(sid, float(nb) - defer_s))
            if freeze_mod.elapsed_since(start, now) >= max(0.0, float(nb) - start):
                deferred.pop(sid, None)
                deferred_since.pop(sid, None)


        passthrough: list[Turn] = []
        billable: list[Turn] = []
        for t in turns:
            c: Candidate | None = t.payload.get("cand")
            if c is None or c.storylet.sid in deferred:
                if c is not None:
                    plan.deferred.append(c.storylet.sid)
                passthrough.append(t)
            else:
                billable.append(t)

        res = budget_filter(billable,
                            lambda t: intervention_cost(t.payload["cand"].storylet.effects),
                            budget)
        for t in res.deferred:
            sid = t.payload["cand"].storylet.sid
            deferred[sid] = now + defer_s
            deferred_since[sid] = now
            plan.deferred.append(sid)
            logger.info("[spectrum·导演] 干预 %s 超预算，延后 %s 分钟",
                        sid, self._cfg("director_defer_minutes", 10))

        story["deferred"] = deferred
        story["deferred_since"] = deferred_since


        story["intervention_budget"] = budget - res.spent
        return passthrough + res.allowed


    def _lift_intents(self, story: dict, now: float) -> list[Turn]:
        if not bool(self._cfg("l2_commitments_enabled", False)):
            return []
        out: list[Turn] = []
        try:
            rows = self.host.ready_due_intents(now)
        except Exception:
            logger.exception("[spectrum·导演] 承诺查询失败")
            return []
        for row in rows:
            out.append(Turn(kind="intent", umo=str(row["umo"]),
                            payload={"intent_id": int(row["id"]),
                                     "summary": str(row["summary"])}))
        return out

    def _wake_edge(self, story: dict, now: float) -> list[Turn]:
        if not bool(self._cfg("l2_sleep_enabled", False)):
            return []
        try:
            if not self.store.is_asleep():
                return []
            until = float(self.store.get_sleep().get("until", 0) or 0)
            if not until or not self.store.sleep_due(now):
                return []
            umo = self.host._wake_window()
            if not umo:
                return []
        except Exception:
            logger.exception("[spectrum·导演] 唤醒判定失败")
            return []
        self.store.set_sleep_awake()
        self.open_segment("home", now=now)
        logger.info("[spectrum·导演] 到点叫醒（在 %s 发声），睡中 %d 条未读",
                    umo, len(self.store.get_sleep_inbox()))
        return [Turn(kind="wake", umo=umo)]

    def _compress_candidate(self, story: dict, now: float,
                            plan: DirectorPlan) -> list[Turn]:
        if not bool(self._cfg("director_compress_via_director", True)):
            return []
        try:
            umo = self.host._pick_compress_target(now)
        except Exception:
            logger.exception("[spectrum·导演] 挑压缩目标失败")
            return []
        if not umo:
            return []
        return [Turn(kind="compress", umo=umo)]


    async def _flush_mechanical(self, now: float, spoke: set[str]) -> None:
        if not bool(self._cfg("l2_commitments_enabled", False)) and \
                not bool(self._cfg("l2_delayed_enabled", True)):
            return

        await self.host._flush_delayed(now, spoke)

    def _sleep_muted(self, now: float) -> bool:
        if not bool(self._cfg("l2_sleep_enabled", False)):
            return False
        try:
            if not self.store.is_asleep():
                return False
            until = float(self.store.get_sleep().get("until", 0) or 0)
            return not (until and self.store.sleep_due(now))
        except Exception:
            return False


    async def execute(self, turn: Turn, spoke: set[str]) -> None:
        """按回合类型调用生活生成、承诺执行或认知整理。"""
        if turn.kind == "intent":
            await self._exec_intent(turn, spoke)
        elif turn.kind == "wake":
            await self.host._fire(turn.umo, "wake",
                                  mutation_policy=require_diary_for_outing,
                                  audience_viewer=turn.umo)
            spoke.add(turn.umo)
        elif turn.kind in ("beat", "fallback"):
            await self._exec_beat(turn, spoke)
        elif turn.kind == "compress":
            await self._exec_compress(turn)
        else:
            logger.warning("[spectrum·导演] 未知回合类型 %r", turn.kind)

    async def _exec_intent(self, turn: Turn, spoke: set[str]) -> None:
        iid = int(turn.payload["intent_id"])
        from .lifecycle import intent_ready
        row = self.store._db.execute("SELECT * FROM intents WHERE id=?", (iid,)).fetchone()
        if not intent_ready(self.store, self.life, row, time.time()):
            return

        try:
            if not self.store.fire_intent(iid):
                return
        except Exception:
            logger.exception("[spectrum·导演] 承诺 #%s 开火标记失败，**本轮不发**"
                             "（宁可漏一次，不可重复发）", iid)
            return
        await self.host._fire(turn.umo, "intent-due",
                              due_intent_texts=[(iid, turn.payload["summary"])],
                              store_intent_id=iid,
                              mutation_policy=require_diary_for_outing,
                              audience_viewer=turn.umo)
        spoke.add(turn.umo)

    async def _exec_beat(self, turn: Turn, spoke: set[str]) -> None:
        epoch = freeze_mod.checkpoint()
        if not freeze_mod.may_commit(epoch):
            return
        cand: Candidate = turn.payload["cand"]
        story = self._story
        lid = self.life.ensure_life()


        beat: ConsumedBeat = self.engine.consume(
            cand, now=time.time(), umo=turn.umo, life_id=lid,
            turn_seq=int(story.get("turn_seq", 0)), ctx=self._ctx)
        self._apply_effects(cand.storylet, story)


        if turn.kind != "fallback":
            story["beat_cursor"] = beat.sid
            story["beat_started_at"] = time.time()

        story["last_beat_at_n"] = int(story.get("beats_consumed", 0))


        story["beats_consumed"] = int(story.get("beats_consumed", 0)) + 1
        story["total_beats"] = int(story.get("total_beats", 0)) + 1
        if beat.ending_name:
            story["ending_name"] = beat.ending_name
            logger.info("[spectrum·导演] 收束：%s", beat.ending_name)
        if beat.got_scene_id:
            story["scene_id"] = beat.got_scene_id
            story["scene_since"] = time.time()

        self.store.set_story_state(story)


        _targets = [u for u in (turn.targets or (turn.umo,)) if u not in spoke]
        _sent = False
        for _u in _targets:


            _ok = await self.host._fire(
                _u, "advance", narrative=beat.narrative,
                phase_block=director_phase_block("advance"),
                mutation_policy=require_diary_for_outing,
                speak_policy=require_audience_here,
                observer=self._observer(),
                history_block=self._history_block(_u),
                audience_viewer=_u)


            _sent = _sent or bool(_ok)
            spoke.add(_u)
            if not freeze_mod.may_commit(epoch):
                break

        try:
            self.store.set_director_fire_delivered(beat.fire_id, _sent)
        except Exception:
            logger.exception("[spectrum·导演] 补记发送结果失败")

    async def _exec_compress(self, turn: Turn) -> None:
        from .director_prompts import DIRECTOR_COMPRESSION_PROMPT
        await self.host._maybe_compress_continuity_for(
            turn.umo, prompt=DIRECTOR_COMPRESSION_PROMPT, with_items=True)

    def _apply_effects(self, st: Storylet, story: dict) -> None:
        """写入素材的紧张度、在场人物及故事线开闭效果。"""
        eff = st.effects or {}
        if "tension_delta" in eff:
            t = int(story.get("tension") or 1) + int(eff["tension_delta"])

            story["tension"] = max(1, min(4, t))


        now = time.time()


        beat_now = int(story.get("total_beats")
                       or story.get("beats_consumed") or 0)


        cap = int(self._cfg("director_thread_max_open", 5))
        open_n = len(story.get("open_threads") or [])
        for raw in _as_list(eff.get("open_thread")):
            spec, bad = _resolve_thread(raw)
            if spec is None:
                logger.warning("[spectrum·导演] 开线被跳过：%s（%s）", bad, raw)
                continue
            if cap > 0 and open_n >= cap:
                logger.warning("[spectrum·导演] 开线 %s 被跳过：已有 %d 条线在开"
                               "（上限 %d）", spec.tid, open_n, cap)
                continue
            open_n += 1
            self.store.open_thread(
                spec.tid, spec.title, now=now,
                element=spec.element, visibility=spec.visibility,
                scope=spec.scope, kind=spec.kind, payoff_form=spec.payoff_form,
                planned_payoff=spec.planned_payoff, trigger=spec.trigger,
                open_beat=beat_now, due_beats=spec.due_beats)
            logger.info("[spectrum·导演] 开线 %s（%s/%s/%s/%s/%s，%s）",
                        spec.tid, spec.element or "-", spec.visibility or "-",
                        spec.scope or "-", spec.kind or "-",
                        spec.payoff_form or "-",
                        f"计划 {spec.due_beats} 拍内收" if spec.due_beats else "无期限")
        for raw in _as_list(eff.get("reinforce_thread")):
            spec, bad = _resolve_thread(raw)
            if spec is None:
                logger.warning("[spectrum·导演] 加固被跳过：%s（%s）", bad, raw)
                continue
            self.store.touch_thread(spec.tid, now=now)
        for raw in _as_list(eff.get("close_thread")):
            spec, bad = _resolve_thread(raw)
            if spec is None:
                logger.warning("[spectrum·导演] 收线被跳过：%s（%s）", bad, raw)
                continue


            self.store.pay_thread(spec.tid, now=now,
                                  act_no=int(story.get("act_no") or 1))
            logger.info("[spectrum·导演] 收线 %s（第 %s 幕）", spec.tid,
                        int(story.get("act_no") or 1))

        if any(k in eff for k in ("open_thread", "reinforce_thread", "close_thread")):
            self._sync_threads(story)
        if "present" in eff:
            story["present"] = _as_list(eff["present"])


    def _thread_axis_gate(self, cands: list, plan) -> list:
        open_rows = list(getattr(self, "_open_rows", []) or [])
        open_ids = {str(r["tid"]) for r in open_rows}
        cap_kind = int(self._cfg("director_thread_max_per_kind", 2))
        cap_scope = int(self._cfg("director_thread_max_per_scope", 2))

        def axes_of(st) -> dict | None:
            raw = (st.effects or {}).get("open_thread")
            if raw is None:
                return None
            for one in _as_list(raw):
                expanded, bad = expand_thread_ref(one)
                if bad or expanded is None:
                    return None
                tid = (expanded if isinstance(expanded, str)
                       else str(expanded.get("id") or ""))
                t = LIB_BY_ID.get(tid.lstrip(REF_PREFIX))
                if t is not None:
                    return {"tid": t.tid, "kind": t.kind, "scope": t.scope}
            return None

        def count(axis: str, val: str) -> int:
            return sum(1 for r in open_rows if str(r[axis] or "") == val)

        out = []
        for c in cands:
            a = axes_of(c.storylet)
            if a is None:
                out.append(c)
                continue
            if a["tid"] in open_ids:
                plan.dropped.append(f"{c.storylet.sid}:线已开着")
                continue
            if cap_kind > 0 and count("kind", a["kind"]) >= cap_kind:
                plan.dropped.append(f"{c.storylet.sid}:kind={a['kind']}满了")
                continue
            if cap_scope > 0 and count("scope", a["scope"]) >= cap_scope:
                plan.dropped.append(f"{c.storylet.sid}:scope={a['scope']}满了")
                continue
            out.append(c)
        return out

    def _close_thread_gate(self, cands: list, plan) -> list:
        open_tids = {str(t) for t in (self._story.get("open_threads") or [])}
        out = []
        for c in cands:
            raw = (c.storylet.effects or {}).get("close_thread")
            if raw is None:
                out.append(c)
                continue
            tids = [str(x).lstrip(REF_PREFIX) for x in _as_list(raw)]
            if any(t in open_tids for t in tids):
                out.append(c)
                continue
            plan.dropped.append(f"{c.storylet.sid}:线没开着")
        return out

    def _sync_threads(self, story: dict) -> list:
        rows = self.store.threads_all("open")
        dead = self._age_threads(rows, story)
        alive = [r for r in rows if str(r["tid"]) not in dead]
        ids = [str(r["tid"]) for r in alive]
        if ids != (story.get("open_threads") or []):
            logger.info("[spectrum·导演] 线况：%s",
                        "、".join(ids) if ids else "（没有开着的线）")
        story["open_threads"] = ids
        return alive

    def _age_threads(self, rows: list, story: dict) -> set[str]:
        factor = float(self._cfg("director_thread_abandon_factor", 2.0))
        if factor <= 0:
            return set()


        beat_now = int(story.get("total_beats")
                       or story.get("beats_consumed") or 0)
        out: set[str] = set()
        for r in rows:
            u = urgency(int(r["open_beat"] or 0), int(r["due_beats"] or 0),
                        beat_now)
            if u is None or u < factor:
                continue
            tid = str(r["tid"])
            if self.store.abandon_thread(tid, reason=f"overdue x{u:.1f}"):
                logger.info("[spectrum·导演] 线 %s 超期作废（计划 %s 拍，已过 %s 拍）",
                            tid, int(r["due_beats"] or 0), beat_now - int(r["open_beat"] or 0))
            out.add(tid)
        return out


    def open_segment(self, kind: str, *, who: str = "",
                     now: float | None = None) -> str:
        now = now or time.time()
        prev = ""
        try:
            prev = self.life.current_life_id()
        except Exception:
            pass
        lid = self.host.open_segment(kind, who=who, now=now)
        try:
            self.store.add_director_fire(
                ts=now, sid="", kind="scene", life_id=lid,
                turn_seq=int((self._story or {}).get("turn_seq", 0)),
                meta={"from": prev, "to": lid, "kind": kind})
        except Exception:
            logger.exception("[spectrum·导演] 场景记录失败")
        return lid

    async def title_pass(self, now: float) -> None:
        epoch = freeze_mod.checkpoint()
        try:
            await self.host._maybe_title_open_life(now)
            if not freeze_mod.may_commit(epoch):
                return
        except Exception:
            logger.exception("[spectrum·导演] 检查待起名段失败")
        try:
            pending = self.store.pending_title_lives(limit=5)
        except Exception:
            logger.exception("[spectrum·导演] 读起名队列失败")
            return
        if not pending:
            return
        row = pending[0]
        lid = str(row["life_id"])
        ok = await self.host._fill_life_title(lid, now=now)
        if not freeze_mod.may_commit(epoch):
            return
        if not ok:
            fails = self.host._title_fails
            fails[lid] = fails.get(lid, 0) + 1
            if fails[lid] >= 3:
                logger.warning("[spectrum·生活线] 段 %s 起名连败 3 次，放弃", lid)
                self.store.expire_life_title(lid)
                fails.pop(lid, None)


    async def _ask_tier2(self, names: list[str], decls: dict) -> dict:
        if not names:
            return {}
        cache = self._story.get("tier2_cache") or {}
        lines = []
        for n in names:
            d = decls.get(n) or {}
            prev = (cache.get(n) or {}).get("value")
            shown = "(未填)" if prev is None else json.dumps(prev, ensure_ascii=False)
            lines.append("%s: %s    // %s" % (n, shown, _decl_text(d)))
        raw = await self._call_model(
            _TIER2_PROMPT % ("\n".join(lines), self._snapshot_text()))
        if not raw:
            return {}
        try:
            patch = _loads_obj(raw)
        except Exception:
            logger.warning("[spectrum·导演] Tier 2 返回的不是对象，整轮丢弃")
            return {}
        if not isinstance(patch, dict):
            logger.warning("[spectrum·导演] Tier 2 返回的不是对象，整轮丢弃")
            return {}


        for k, v in patch.items():
            if k not in names:
                logger.warning("[spectrum·导演] Tier 2 回了个没问过的量 %r，整轮丢弃", k)
                return {}
            if not self.engine.var_ok(k, v):
                logger.warning("[spectrum·导演] Tier 2 的 %s=%r 不合声明，整轮丢弃", k, v)
                return {}
        return patch


    def _tier3_take(self) -> bool:
        cap = int(self._cfg("director_tier3_max_per_hour", 20))
        if cap <= 0:
            return False
        now = time.time()
        story = self._story or {}
        b = dict(story.get("tier3_bucket") or {})
        at = float(b.get("at") or 0.0)
        tokens = float(b.get("tokens", float(cap)))
        if at:
            tokens = min(float(cap), tokens + cap * freeze_mod.elapsed_since(at, now) / 3600.0)
        if tokens < 1.0:
            return False
        story["tier3_bucket"] = {"tokens": tokens - 1.0, "at": now}
        self._story = story
        return True

    async def _ask_tier3(self, pairs: list[tuple[int, str]], snap: dict) -> list[int]:
        if not pairs:
            return []


        key = (tuple(pairs), self._snapshot_text())
        if key == self._tier3_last_key:
            return list(self._tier3_last_idx)


        if not self._tier3_take():
            logger.info("[spectrum·导演] Tier 3 配额用完，本轮按 NONE")
            return []
        conds = "\n".join("%d: %s" % (i, t) for i, t in pairs)
        raw = await self._call_model(
            _TIER3_PROMPT % (conds, self._snapshot_text()))
        if not raw:
            return []
        try:
            arr = _loads_obj(raw)
        except Exception:
            logger.warning("[spectrum·导演] Tier 3 返回的不是数组，按 NONE 处理")
            return []


        idx = arr if isinstance(arr, list) else []
        self._tier3_last_key = key
        self._tier3_last_idx = list(idx)
        return idx

    def _snapshot_text(self) -> str:
        s = self._story or {}
        recent = ""
        try:
            recent = str(getattr(self._ctx, "recent_text", "") or "")
        except Exception:
            recent = ""
        return (
            "节奏阶段：%s\n"
            "当前张力：%s\n"
            "已经走过的拍数：%s\n"
            "是否在外：%s\n"
            "是否睡着：%s\n"
            "开着的线索条数：%s\n"
            "最近的往来：\n%s"
            % (s.get("phase"), s.get("tension"), s.get("beats_consumed"),
               "是" if self._owner_session else "否",
               "是" if self._asleep else "否",
               len(s.get("open_threads") or []),
               recent or "（没有最近的往来）")
        )

    async def _call_model(self, prompt: str) -> str:
        epoch = freeze_mod.checkpoint()
        if not freeze_mod.may_commit(epoch):
            raise freeze_mod.FrozenSkipped("导演请求暂停")
        self.store.set_story_state(self._story)
        try:
            provider = await self.host.context.get_using_provider_async()
            if not freeze_mod.may_commit(epoch):
                raise freeze_mod.FrozenSkipped("导演请求暂停")
            resp = await freeze_mod.call_model(provider,
                system_prompt="", prompt=prompt,
                model=self._cfg("advance_model_override", None) or None)
            if not freeze_mod.may_commit(epoch):
                raise freeze_mod.FrozenSkipped("导演结果跨过系统暂停")
            return str(getattr(resp, "completion_text", "") or "")
        except freeze_mod.FrozenSkipped:
            raise
        except Exception as error:
            freeze_mod.record_failure("director-model", error)
            if freeze_mod.is_frozen():
                raise freeze_mod.FrozenSkipped("导演请求失败触发暂停") from error
            logger.exception("[spectrum·导演] 提示词调用失败，按未知处理")
            return ""


def require_diary_for_outing(decision) -> None:
    if not getattr(decision, "ok", False):
        return
    if not getattr(decision, "outing", None):
        return
    if (getattr(decision, "script", "") or "").strip():
        return
    logger.info("[spectrum·导演] 位置变更被拒：同一轮没有日记（outingUpdate=%s）。"
                "日记未记录的地点，不视为她实际到过。",
                (decision.outing or {}).get("action"))
    decision.outing = None


_REPLY_BLOCK_RE = re.compile(r"<reply>(.*?)</reply>", re.S)
_AUDIENCE_RE = re.compile(r'"audience"\s*:\s*"([^"]{0,32})"')


def _audience_of(decision) -> str:
    raw = getattr(decision, "raw", "") or ""
    m = _REPLY_BLOCK_RE.search(raw)
    blob = m.group(1) if m else raw
    m2 = _AUDIENCE_RE.search(blob)
    return (m2.group(1) if m2 else "").strip().lower()


def require_audience_here(decision) -> None:
    if not getattr(decision, "ok", False):
        return
    if not getattr(decision, "delivers", False):
        return
    got = _audience_of(decision)
    if got == "here":
        return
    logger.info("[spectrum·导演] 未开口：audience=%s（只有 here 才发得出去）",
                got or "(未声明)")
    decision.mode = "none"
    decision.content = ""


def _decl_text(d: dict) -> str:
    d = d or {}
    bits = []
    if d.get("enum"):
        bits.append("取值只能是 " + " / ".join(str(x) for x in d["enum"]))
    else:
        t = str(d.get("type", "number"))
        rng = d.get("range")
        if t == "bool":
            bits.append("取值只能是 true 或 false")
        elif isinstance(rng, list) and len(rng) == 2:
            bits.append("取值是 %s 到 %s 之间的数" % (rng[0], rng[1]))
        else:
            bits.append("取值是数")
    if d.get("desc"):
        bits.append(str(d["desc"]))
    return "；".join(bits)


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _resolve_thread(raw: Any) -> tuple[Any, str]:
    raw, bad = expand_thread_ref(raw)
    if bad:
        return None, bad
    return parse_thread_spec(raw)


def _loads_obj(raw: str) -> Any:
    import json as _json
    t = (raw or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    t = t.strip()
    try:
        return _json.loads(t)
    except Exception:
        pass
    for lo, hi in (("{", "}"), ("[", "]")):
        a, b = t.find(lo), t.rfind(hi)
        if a >= 0 and b > a:
            try:
                return _json.loads(t[a:b + 1])
            except Exception:
                continue
    raise ValueError("抠不出 JSON")


__all__ = ["Director", "DirectorPlan", "Turn", "TURN_ORDER"]
