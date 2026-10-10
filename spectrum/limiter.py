"""以补充式请求额度限制模型调用频率。
接口故障后的暂停与排队超时分别处理，不等同于生活冻结。"""


from __future__ import annotations

import asyncio
import time

from astrbot.api import logger


_CFG_GET = None


def set_cfg_getter(fn) -> None:
    global _CFG_GET
    _CFG_GET = fn


def _cfg(key: str, default):
    try:
        if _CFG_GET is None:
            return default
        v = _CFG_GET(key, default)
        return default if v is None else v
    except Exception:
        return default


def enabled() -> bool:
    return bool(_cfg("low_freq_mode", False))


def rpm() -> float:
    try:
        return max(1.0, float(_cfg("low_freq_rpm", 8)))
    except Exception:
        return 8.0


def pause_seconds() -> float:
    try:
        return max(1.0, float(_cfg("low_freq_pause_seconds", 60)))
    except Exception:
        return 60.0


def max_wait_seconds() -> float:
    try:
        return max(0.0, float(_cfg("low_freq_max_wait_seconds", 30)))
    except Exception:
        return 30.0


_clock = time.monotonic


_paused_until: float = 0.0


_tokens: float = -1.0
_bucket_at: float = 0.0


_stats = {"sent": 0, "skipped_pause": 0, "skipped_wait": 0, "paused_n": 0}


class LowFreqSkipped(RuntimeError):
    pass


def status() -> dict:
    return {
        "enabled": enabled(),
        "rpm": rpm(),
        "paused_for": max(0.0, _paused_until - _now()),
        "tokens": None if _tokens < 0 else round(_tokens, 2),
        **_stats,
    }


def reset_state() -> None:
    global _paused_until, _tokens, _bucket_at
    _paused_until = 0.0
    _tokens = -1.0
    _bucket_at = 0.0
    for k in _stats:
        _stats[k] = 0


def _now() -> float:
    try:
        return float(_clock())
    except Exception:
        return 0.0


def note_failure() -> None:
    """配置启用时设置接口故障后的暂停截止时间。"""
    global _paused_until
    if not enabled():
        return
    secs = pause_seconds()
    _paused_until = _now() + secs
    _stats["paused_n"] += 1
    logger.warning("[spectrum·低频] 有请求失败 ⇒ 静默 %.0f 秒（期间所有请求不发）", secs)


def _refill() -> None:
    global _tokens, _bucket_at
    now = _now()
    cap = rpm()
    if _tokens < 0:
        _tokens, _bucket_at = cap, now
        return
    dt = now - _bucket_at
    if dt <= 0:
        return
    _tokens = min(cap, _tokens + dt * (cap / 60.0))
    _bucket_at = now


def _take() -> bool:
    global _tokens
    _refill()
    if _tokens >= 1.0:
        _tokens -= 1.0
        return True
    return False


def _wait_for_token_sync_hint() -> float:
    _refill()
    if _tokens >= 1.0:
        return 0.0
    rate = rpm() / 60.0
    return (1.0 - _tokens) / rate if rate > 0 else 0.0


async def acquire() -> None:
    """等待补充式额度；故障暂停或等待超时通过专用异常跳过请求。"""
    if not enabled():
        return
    now = _now()
    if now < _paused_until:
        _stats["skipped_pause"] += 1
        raise LowFreqSkipped("熔断中，还剩 %.0f 秒" % (_paused_until - now))


    deadline = time.monotonic() + max_wait_seconds()
    while True:
        if not enabled():
            return
        now = _now()
        if now < _paused_until:
            _stats["skipped_pause"] += 1
            raise LowFreqSkipped("熔断中，还剩 %.0f 秒" % (_paused_until - now))
        if _take():
            _stats["sent"] += 1
            return
        if time.monotonic() >= deadline:
            _stats["skipped_wait"] += 1
            raise LowFreqSkipped("限流排队超时（上限 %.0f 秒）" % max_wait_seconds())
        await asyncio.sleep(max(0.02, min(_wait_for_token_sync_hint(), 0.2)))


def note_of_exception(exc: BaseException) -> None:
    if not enabled():
        return
    name = type(exc).__name__
    if name in ("TimeoutError", "APITimeoutError", "APIConnectionError",
                "ConnectError", "ReadTimeout", "WriteTimeout", "PoolTimeout"):
        note_failure()
        return
    code = None
    for attr in ("status_code", "status", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            code = v
            break
    if code is None:
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None) if resp is not None else None
    if isinstance(code, int) and (code == 429 or code == 408 or 500 <= code <= 599):
        note_failure()

