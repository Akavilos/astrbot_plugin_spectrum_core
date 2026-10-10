"""构造角色自述思考材料，缓存原生推理返回长度的诊断结果。
诊断探测本身会调用模型，返回长度不决定提供商是否关闭原生推理。"""


from __future__ import annotations

import time

try:
    from astrbot.api import logger
except Exception:
    import logging

    logger = logging.getLogger("spectrum")


_PROBE_TTL = 24 * 3600


_PROBE_PROMPT = "1"


class PossessionProbe:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[bool, float]] = {}

    def cached(self, key: str) -> bool | None:
        got = self._cache.get(key)
        if not got:
            return None
        value, at = got
        if (time.time() - at) > _PROBE_TTL:
            return None
        return value

    def remember(self, key: str, supported: bool) -> None:
        self._cache[key] = (supported, time.time())

    async def probe(self, provider) -> int:
        """实际请求模型并返回原生推理字段长度；调用失败返回负值。"""
        try:
            resp = await provider.text_chat(
                prompt=_PROBE_PROMPT,
                max_tokens=64,
                temperature=0,
            )
        except Exception:
            logger.debug("[spectrum·夺舍] 探测请求失败，跳过诊断", exc_info=True)
            return -1

        got = getattr(resp, "reasoning_content", None)
        return len(str(got).strip()) if got else 0


probe = PossessionProbe()


def possession_instruction() -> str:
    return ""
