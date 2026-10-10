"""维护正常、疑似断线与冻结状态，并持久化暂停信息。
累计业务失败可触发冻结；代次用于拒绝跨越暂停阶段的结果提交。"""


from __future__ import annotations

import asyncio
import json
import inspect
import time
import weakref
from contextvars import ContextVar

_failure_scope = ContextVar("spectrum_failure_scope", default=None)


def begin_failure_scope() -> dict:
    scope = {"model_failed": False}
    _failure_scope.set(scope)
    return scope

from astrbot.api import logger
from . import limiter


class FrozenSkipped(RuntimeError):
    pass


NORMAL = "normal"
SUSPECTED = "suspected"
FROZEN = "frozen"

_STATE_CN = {NORMAL: "正常", SUSPECTED: "疑似", FROZEN: "已冻结"}


_PROBE_TIMEOUT = 10.0


class Freeze:
    def __init__(self, cfg_get, context=None, *, probe=None, clock=time.monotonic) -> None:
        self._cfg_get = cfg_get

        self._ctx_ref = weakref.ref(context) if context is not None else None

        self._probe = probe
        self._clock = clock

        self._state = NORMAL
        self._fail_streak = 0
        self._offline_streak = 0
        self._online_streak = 0
        self._state_since = 0.0
        self._frozen_at = 0.0
        self._last_probe_at = float("-inf")
        self._last_probe = None
        self._last_umo = ""
        self._seq = 0
        self._forced_thaw_n = 0
        self._resume = asyncio.Event()
        self._store = None
        self._wall_clock = time.time
        self._failure_sources: set[str] = set()
        self._frozen_wall = 0.0
        self._paused_seconds = 0.0
        self._pause_periods: list[list[float]] = []

    def _persist(self) -> None:
        if self._store is not None:
            self._store.set_state("__freeze__", "runtime", json.dumps({
                "state": self._state, "fail_streak": self._fail_streak,
                "sources": sorted(self._failure_sources), "epoch": self._seq,
                "frozen_wall": self._frozen_wall, "paused_seconds": self._paused_seconds,
                "pause_periods": self._pause_periods,
            }))

    def _restore(self) -> None:
        if self._store is None:
            return
        raw = self._store.get_state("__freeze__", "runtime")
        if not raw:
            return
        state = json.loads(raw)
        self._state = state["state"]
        self._fail_streak = state["fail_streak"]
        self._failure_sources = set(state["sources"])
        self._seq = state["epoch"] + 1
        self._frozen_wall = state["frozen_wall"]
        self._paused_seconds = state["paused_seconds"]
        self._pause_periods = state.get("pause_periods", [])
        if self._state == FROZEN:
            elapsed = max(0.0, self._wall_clock() - self._frozen_wall)
            self._frozen_at = self._clock() - elapsed
            self._resume.clear()

    def record_failure(self, source: str, error: Exception | None = None) -> None:
        """累计非预期业务失败，同一异常只登记一次；达到阈值时转为冻结。"""
        if not self._enabled() or self._state == FROZEN:
            return
        if error is not None:
            if (isinstance(error, FrozenSkipped) or type(error).__name__ == "LowFreqSkipped"
                    or getattr(error, "_spectrum_expected_skip", False)):
                scope = _failure_scope.get()
                if scope is not None:
                    scope["skipped"] = True
                return
            if getattr(error, "_spectrum_freeze_recorded", False):
                return
            error._spectrum_freeze_recorded = True
        scope = _failure_scope.get()
        if scope is not None and source.startswith("model"):
            scope["model_failed"] = True
        self._fail_streak += 1
        self._failure_sources.add(source)
        if self._fail_streak >= self._fail_streak_need():
            self._to_frozen("累计业务失败达到阈值：" + ", ".join(sorted(self._failure_sources)))
        else:
            self._persist()

    def checkpoint(self) -> int:
        return self._seq

    def may_commit(self, checkpoint: int) -> bool:
        """仅当前代次且未冻结的结果可继续提交。"""
        return self._state != FROZEN and checkpoint == self._seq

    def business_now(self) -> float:
        elapsed = max(0.0, self._wall_clock() - self._frozen_wall) if self._state == FROZEN else 0.0
        return self._wall_clock() - self._paused_seconds - elapsed


    def _cfg(self, key, default):
        try:
            v = self._cfg_get(key, default)
        except Exception:
            return default
        return default if v is None else v

    def _cfg_int(self, key, default, lo=1):
        try:
            v = int(self._cfg(key, default))
        except Exception:
            v = default
        return max(lo, v)

    def _enabled(self) -> bool:
        try:
            return bool(self._cfg("freeze_enabled", False))
        except Exception:
            return False

    def _fail_streak_need(self) -> int:
        return self._cfg_int("freeze_fail_streak", 3)

    def _confirm_times(self) -> int:
        return self._cfg_int("freeze_confirm_times", 2)

    def _probe_interval(self) -> int:
        return self._cfg_int("freeze_probe_interval_seconds", 60, lo=10)

    def _thaw_confirm(self) -> int:
        return self._cfg_int("freeze_thaw_confirm_times", 2)

    def _max_minutes(self) -> int:
        try:
            return max(0, int(self._cfg("freeze_max_minutes", 120)))
        except Exception:
            return 120

    def _fail_open(self) -> bool:
        try:
            return bool(self._cfg("freeze_probe_fail_open", True))
        except Exception:
            return True


    def _to_normal(self, why: str) -> None:
        """记录暂停区间、清零故障计数并推进代次，唤醒等待任务。"""
        prev = self._state
        if prev == FROZEN:
            ended = self._wall_clock()
            self._paused_seconds += max(0.0, ended - self._frozen_wall)
            self._pause_periods.append([self._frozen_wall, ended])
        self._frozen_wall = 0.0
        self._failure_sources.clear()
        self._state = NORMAL
        self._fail_streak = 0
        self._offline_streak = 0
        self._online_streak = 0
        self._seq += 1
        if prev == FROZEN:
            mins = (self._clock() - self._frozen_at) / 60.0
            self._frozen_at = 0.0
            self._resume.set()
            logger.warning(
                "[spectrum·冻结] 解冻（%s），共静默 %.0f 分钟。"
                "暂停时长已记录，恢复调度。",
                why, mins)
        elif prev == SUSPECTED:
            logger.info("[spectrum·冻结] 退出疑似（%s）", why)
        self._persist()

    def _to_frozen(self, why: str) -> None:
        """持久化冻结起点，推进代次并阻止后续后台继续。"""
        self._state = FROZEN
        self._offline_streak = 0
        self._online_streak = 0
        self._frozen_at = self._clock()
        self._frozen_wall = self._wall_clock()
        self._state_since = self._frozen_at
        self._seq += 1
        self._resume.clear()
        self._persist()
        mm = self._max_minutes()
        logger.warning(
            "[spectrum·冻结] 已冻结（%s）—— L2 tick 停、不产出、不落库。"
            "平台故障按 %d 秒间隔探活；%s",
            why, self._probe_interval(),
            f"最长 {mm} 分钟" if mm > 0 else "不设上限")

    def _maybe_force_thaw(self, now: float) -> bool:
        if self._state != FROZEN:
            return False
        mm = self._max_minutes()
        if mm <= 0 or now - self._frozen_at < mm * 60.0:
            return False
        self._forced_thaw_n += 1
        logger.warning(
            "[spectrum·冻结] 已达 freeze_max_minutes=%d，强制解冻（第 %d 次）。"
            "若仍离线，这一轮发送会失败并可能重新进入疑似/冻结 —— "
            "这是预期的自愈循环：要更长的静默就调大该项，设 0 表示不设上限",
            mm, self._forced_thaw_n)
        self._to_normal("超过最长冻结时长")
        return True


    def note_send_failed(self) -> None:
        self.record_failure("delivery")

    def note_message_in(self, umo: str = "") -> None:
        if umo:
            self._last_umo = umo
        if not self._enabled():
            return

        if self._state != NORMAL and self._failure_sources == {"delivery"}:
            self._to_normal("有真实消息进来，平台投递恢复")

    async def wait_if_frozen(self) -> None:
        while self._state == FROZEN:
            try:
                await asyncio.wait_for(self._resume.wait(), timeout=self._probe_interval())
            except asyncio.TimeoutError:
                pass
            else:
                self._resume.clear()
                continue
            await self.tick_probe()

    async def tick_probe(self) -> None:
        now = self._clock()
        if self._maybe_force_thaw(now):
            return
        if not self._enabled() or self._state == NORMAL:
            return
        if self._state == FROZEN and self._failure_sources != {"delivery"}:
            return
        if now - self._last_probe_at < self._probe_interval():
            return
        self._last_probe_at = now

        seq = self._seq
        res = await self._run_probe()
        if self._seq != seq:

            return
        if res is None:
            res = bool(self._fail_open())
        self._last_probe = res

        if self._state == SUSPECTED:
            if res:
                self._to_normal("探针报在线")
            else:
                self._offline_streak += 1
                logger.info("[spectrum·冻结] 探针连续报离线 %d/%d",
                            self._offline_streak, self._confirm_times())
                if self._offline_streak >= self._confirm_times():
                    self._to_frozen("探针连续报离线")
        elif self._state == FROZEN:
            if res:
                self._online_streak += 1
                logger.info("[spectrum·冻结] 探针连续报在线 %d/%d",
                            self._online_streak, self._thaw_confirm())
                if self._online_streak >= self._thaw_confirm():
                    self._to_normal("探针连续报在线")
            else:
                self._online_streak = 0
                self._offline_streak += 1
                logger.info("[spectrum·冻结] 平台离线确认 %d/%d",
                            self._offline_streak, self._confirm_times())
            if res:
                self._offline_streak = 0

        self._maybe_force_thaw(self._clock())


    async def _run_probe(self):
        fn = self._probe or self._default_probe
        try:
            res = fn()
            if inspect.isawaitable(res):
                res = await asyncio.wait_for(res, timeout=_PROBE_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.info("[spectrum·冻结] 探针失败，按在线处理（fail-open）", exc_info=True)
            return None
        return None if res is None else bool(res)

    async def _default_probe(self):
        try:
            inst = self._pick_instance()
            if inst is None:
                return None
            bot = getattr(inst, "bot", None) or inst
            call = getattr(bot, "call_action", None)
            if not callable(call):
                return None
            st = await asyncio.wait_for(call("get_status"), timeout=_PROBE_TIMEOUT)
            if isinstance(st, dict):
                if "online" in st:
                    return bool(st["online"])
                if "good" in st:
                    return bool(st["good"])
                if "status" in st:
                    return str(st["status"]).lower() in ("online", "good", "ok", "true")
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    def _get_context(self):
        ref = self._ctx_ref
        if ref is not None:
            ctx = ref()
            if ctx is not None:
                return ctx
        return None

    def _pick_instance(self):
        ctx = self._get_context()
        if ctx is None:
            return None
        inst = None
        pid = self._last_umo.split(":", 1)[0] if self._last_umo else ""
        getter = getattr(ctx, "get_platform_inst", None)
        if pid and callable(getter):
            try:
                inst = getter(pid)
            except Exception:
                inst = None
        if inst is None:
            try:
                insts = ctx.platform_manager.get_insts()
            except Exception:
                insts = None
            if insts:
                inst = insts[0]
        return inst


    def manual(self, action: str) -> str:
        a = (action or "").strip().lower()
        if a in ("冻结", "freeze"):
            if self._state == FROZEN:
                return "已经是冻结状态。"
            self._failure_sources = {"manual"}
            self._to_frozen("手动")
            return ("已手动冻结：后台循环停在这里。"
                    "解冻用 /光谱核心 解冻。")
        if a in ("解冻", "thaw", "unfreeze"):
            if self._state == NORMAL:
                return "当前是正常状态，没有可解的冻。"
            self._to_normal("手动")
            return "已解冻，后台立刻继续。"
        if a in ("冻结重置", "reset"):
            self._forced_thaw_n = 0
            self._last_probe = None
            self._last_probe_at = float("-inf")
            self._to_normal("手动重置")
            return "已重置：解冻 + 计数清零。"
        if a in ("冻结状态", "status"):
            return self.status()
        return "用法：/光谱核心 冻结 | 解冻 | 冻结状态 | 冻结重置"

    def status(self) -> str:
        now = self._clock()
        lines = [f"投递冻结：{'开' if self._enabled() else '关（不会自动触发，手动命令仍可用）'}"]
        extra = ""
        if self._state == FROZEN:
            dur = (now - self._frozen_at) / 60.0
            mm = self._max_minutes()
            extra = f"（已静默 {dur:.0f} 分钟，{'不设上限' if mm <= 0 else f'还剩 {max(0.0, mm - dur):.0f} 分钟'})"
        lines.append(f"状态：{_STATE_CN.get(self._state, self._state)}{extra}")
        lines.append(f"累计业务失败：{self._fail_streak}/{self._fail_streak_need()} 次触发冻结")
        if self._failure_sources:
            lines.append("失败来源：" + ", ".join(sorted(self._failure_sources)))
        lines.append(f"探针连续离线：{self._offline_streak}/{self._confirm_times()}"
                     f"　连续在线：{self._online_streak}/{self._thaw_confirm()}")
        if self._offline_streak >= self._confirm_times():
            lines.append("已确认平台离线；等待平台恢复或手动解冻")
        if self._last_probe is None:
            lines.append("最近一次探针：还没探过")
        else:
            lines.append(f"最近一次探针：{'在线' if self._last_probe else '离线'}")
        lines.append(f"探针可用性：{self._probe_desc()}")
        if self._forced_thaw_n:
            lines.append(f"已强制解冻：{self._forced_thaw_n} 次")
        return "\n".join(lines)

    def _probe_desc(self) -> str:
        if self._probe is not None:
            return "已注入（测试用）"
        ctx = self._get_context()
        if ctx is None:
            return "拿不到 context ⇒ 探针不可用，永不判离线"
        if not callable(getattr(ctx, "get_platform_inst", None)):
            return "context 上没有 get_platform_inst ⇒ 探针不可用"
        inst = self._pick_instance()
        if inst is None:
            return "拿不到平台实例 ⇒ 探针不可用"
        bot = getattr(inst, "bot", None) or inst
        if not callable(getattr(bot, "call_action", None)):
            return "平台实例上没有 call_action ⇒ 探针不可用"
        return "可用"


_INSTANCE: Freeze | None = None


def install(cfg_get, context=None, *, probe=None, clock=time.monotonic, store=None, wall_clock=time.time) -> Freeze:
    global _INSTANCE
    _INSTANCE = Freeze(cfg_get, context, probe=probe, clock=clock)
    _INSTANCE._store = store
    _INSTANCE._wall_clock = wall_clock
    _INSTANCE._restore()
    return _INSTANCE


def reset_for_test() -> None:
    global _INSTANCE
    _INSTANCE = None


def get_instance() -> Freeze | None:
    return _INSTANCE


def note_send_failed() -> None:
    if _INSTANCE is not None:
        _INSTANCE.note_send_failed()


def note_message_in(umo: str = "") -> None:
    if _INSTANCE is not None:
        _INSTANCE.note_message_in(umo)


async def tick_probe() -> None:
    if _INSTANCE is not None:
        await _INSTANCE.tick_probe()


async def wait_if_frozen() -> None:
    if _INSTANCE is not None:
        await _INSTANCE.wait_if_frozen()


def manual(action: str) -> str:
    if _INSTANCE is None:
        return "投递冻结未安装（光谱核心没起来？）"
    return _INSTANCE.manual(action)


def status() -> str:
    if _INSTANCE is None:
        return "投递冻结未安装（光谱核心没起来？）"
    return _INSTANCE.status()


def record_failure(source: str, error: Exception | None = None) -> None:
    if _INSTANCE is not None:
        _INSTANCE.record_failure(source, error)


def checkpoint() -> int:
    return _INSTANCE.checkpoint() if _INSTANCE else 0


def may_commit(epoch: int) -> bool:
    return _INSTANCE.may_commit(epoch) if _INSTANCE else True


def is_frozen() -> bool:
    return _INSTANCE is not None and _INSTANCE._state == FROZEN


async def call_model(provider, **kwargs):
    epoch = checkpoint()
    if not may_commit(epoch):
        raise FrozenSkipped("系统暂停，未请求模型")
    await limiter.acquire()
    if not may_commit(epoch):
        raise FrozenSkipped("排队期间系统暂停，未请求模型")
    try:
        response = await provider.text_chat(**kwargs)
        if getattr(response, "role", "") == "err":
            raise RuntimeError("供应商返回模型错误结果")
    except Exception as error:
        limiter.note_of_exception(error)
        record_failure("model", error)
        raise
    if not may_commit(epoch):
        raise FrozenSkipped("模型结果跨过系统暂停")
    return response


def business_now() -> float:
    return _INSTANCE.business_now() if _INSTANCE else time.time()


def elapsed_since(start: float, now: float | None = None) -> float:
    """计算区间经过时间，扣除与该区间重叠的暂停时长。"""
    if now is None:
        now = _INSTANCE._wall_clock() if _INSTANCE else time.time()
    if _INSTANCE is None:
        return max(0.0, now - start)
    periods = list(_INSTANCE._pause_periods)
    if is_frozen():
        periods.append([_INSTANCE._frozen_wall, now])
    paused = sum(max(0.0, min(now, end) - max(start, begin)) for begin, end in periods)
    return max(0.0, now - start - paused)


def effective_deadline(start: float, due: float, now: float | None = None) -> float:
    """把暂停时长计入展示期限，数据库原定时间不在此改写。"""
    if now is None:
        now = _INSTANCE._wall_clock() if _INSTANCE else time.time()
    paused = max(0.0, now - start) - elapsed_since(start, now)
    return due + paused
