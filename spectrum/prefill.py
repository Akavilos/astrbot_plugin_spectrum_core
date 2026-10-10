"""适配角色自述思考的assistant续写请求。
分别检查总开关与提供商模式，处理工具字段及上游拒绝收尾角色时的回退。"""


from __future__ import annotations

import re

from . import contract

try:
    from astrbot.api import logger
except Exception:
    import logging

    logger = logging.getLogger("spectrum")


OPEN = "<thinking>"


_MARK = re.compile(r"^\s*" + re.escape(OPEN))


_BETA_HINT = "beta"


_GEMINI_HINT = "gemini"


_CFG_GET = None


def set_cfg_getter(fn) -> None:
    global _CFG_GET
    _CFG_GET = fn


def cfg_on(key: str, default: bool = True) -> bool:
    if _CFG_GET is None:
        return default
    try:
        v = _CFG_GET(key, default)
        return default if v is None else bool(v)
    except Exception:
        return default


def provider_is_gemini(provider) -> bool:
    try:
        cfg = provider.provider_config or {}
    except Exception:
        return False
    return _GEMINI_HINT in str(cfg.get("model") or "").lower()


def provider_supports_prefill(provider) -> bool:
    try:
        base = str((provider.provider_config or {}).get("api_base") or "")
    except Exception:
        return False
    return _BETA_HINT in base


DEFAULT_PREFILL_RULES = (
    "我按 `<think_format>` 承接这一轮有关的想法：\n\n"
    + contract.THINK_FORMAT
)


PREFILL_CLOSED_HEAD = """我从自己的念头继续这一轮，按 <think_format> 想清楚眼前有关的事，再按 <content_output> 写下经历和回复。
<thinking></thinking>
Recorder:
[START THINKING]
"""


_CLOSED_HEAD_MARK = PREFILL_CLOSED_HEAD[:12]
_LEGACY_CLOSED_HEAD_MARK = "轮原生思维通道已预闭合"


def _is_gemini_head(content: str) -> bool:
    return content.startswith((_CLOSED_HEAD_MARK, _LEGACY_CLOSED_HEAD_MARK))


def _is_our_prefill(msg) -> bool:
    if not isinstance(msg, dict) or msg.get("role") != "assistant":
        return False
    c = str(msg.get("content") or "")
    return bool(_MARK.match(c)) or _is_gemini_head(c)


def prefill_enabled_for(provider) -> bool:
    """先检查总开关，再按Gemini或兼容前缀提供商检查对应模式。"""
    if not cfg_on("possession_enabled", True):
        return False
    if provider_is_gemini(provider):
        return cfg_on("prefill_gemini_closed", True)
    return cfg_on("prefill_possession", True) and provider_supports_prefill(provider)


def prefill_adapter_active(provider) -> bool:
    prepare = getattr(provider, "_prepare_chat_payload", None)
    return bool(getattr(prepare, "_spectrum_prefill_adapter", False))


def build_prefill(recall_text: str='') -> str:
    parts = [OPEN, '（这都是我的想法）', '']
    recall = (recall_text or '').strip()
    if recall:
        parts.append(recall)
        parts.append('')
    parts.append(DEFAULT_PREFILL_RULES.strip())
    parts.append('')
    parts.append('我看到了这条消息。')
    return '\n'.join(parts)


_patched = False
_warned_no_beta = False


def _promote_prefill(payloads: dict, provider) -> None:
    if not cfg_on("possession_enabled", True):
        return
    messages = payloads.get("messages")
    if not isinstance(messages, list):
        return

    idxs = [i for i, m in enumerate(messages)
            if isinstance(m, dict) and m.get("role") == "assistant"
            and _MARK.match(str(m.get("content") or ""))]
    if not idxs:
        return


    keep = idxs[-1]
    for i in reversed(idxs[:-1]):
        messages.pop(i)
        keep -= 1


    for m in messages[keep + 1:]:
        if isinstance(m, dict) and m.get("role") in ("assistant", "tool"):
            messages.pop(keep)
            return


    if provider_is_gemini(provider):
        if not cfg_on("prefill_gemini_closed", True):
            logger.info("[spectrum·夺舍] Gemini 预闭合形态已关（配置），本条 prefill 丢弃")
            messages.pop(keep)
            return
        msg = messages.pop(keep)
        msg.pop("_no_save", None)
        orig = str(msg.get("content") or "").lstrip()


        if orig.startswith("<thinking>"):
            orig = orig[len("<thinking>"):].lstrip("\n")


        msg["content"] = PREFILL_CLOSED_HEAD + "\n" + orig
        messages.append(msg)
        payloads["_spectrum_prefill"] = True
        logger.info("[spectrum·夺舍] Gemini 预闭合形态已注入（停在 assistant 上、不配 prefix）")
        return


    if not cfg_on("prefill_possession", True):
        messages.pop(keep)
        return
    msg = messages.pop(keep)
    msg.pop("_no_save", None)

    global _warned_no_beta

    if not provider_supports_prefill(provider):


        if not _warned_no_beta:
            _warned_no_beta = True
            base = ""
            try:
                base = str((provider.provider_config or {}).get("api_base") or "")
            except Exception:
                pass
            logger.warning(
                "[spectrum·夺舍] provider 端点 %r 不是 DeepSeek 的 beta 端点，"
                "prefill 已丢弃。前缀续写只在 https://api.deepseek.com/beta 上有效。",
                base,
            )
        return

    msg["prefix"] = True
    messages.append(msg)


    payloads["_spectrum_prefill"] = True


    logger.info(
        "[spectrum·夺舍] prefill 已挂到请求（消息数=%d，末条 role=%s，prefix=True）",
        len(messages), messages[-1].get("role"),
    )


_DROP = ("tools", "tool_choice", "functions", "function_call")


def _strip_tool_fields(payloads: dict) -> list[str]:
    done = []
    for key in _DROP:
        if payloads.pop(key, None) is not None:
            done.append(key)
    return done


_MODEL_TURN_HINT = "ending with a model turn"


def is_model_turn_reject(exc: BaseException) -> bool:
    try:
        return _MODEL_TURN_HINT in str(exc)
    except Exception:
        return False


def drop_prefill_messages(payloads: dict) -> int:
    messages = payloads.get("messages")
    if not isinstance(messages, list):
        return 0
    keep = [m for m in messages if not _is_our_prefill(m)]
    dropped = len(messages) - len(keep)
    if dropped:
        messages[:] = keep
    return dropped


def gemini_to_user_turn(payloads: dict) -> bool:
    messages = payloads.get("messages", [])
    if not messages or messages[-1].get("role") != "assistant":
        return False
    content = str(messages[-1].get("content") or "")
    if not _is_gemini_head(content):
        return False
    body = content.split("[START THINKING]", 1)[-1].strip()
    body = body.replace("</thinking>", "</think>")
    messages.pop()
    content = (
        "【我这一轮的思考起点】\n" + body +
        "\n我承接上面的实际来信，把这一轮的想法写在 <think> 与 </think> 之间，"
        "随后按既定 <content_output> 输出 <life> 和 <reply>。"
    )
    messages.append({"role": "user", "content": content})
    return True


async def send_with_model_turn_fallback(call, payloads, tools, args, kwargs, *,
                                        used_prefill: bool):
    """仅对收尾角色拒绝重试；继续被拒时转换Gemini收尾或移除前缀。"""
    try:
        return await call(payloads, tools, *args, **kwargs)
    except Exception as exc:
        if not (used_prefill and is_model_turn_reject(exc)):
            raise
        logger.warning("[spectrum·夺舍] 上游拒了「以 model 轮收尾」，原样重试一次")
        try:
            return await call(payloads, tools, *args, **kwargs)
        except Exception as exc2:
            if not is_model_turn_reject(exc2):
                raise
        if gemini_to_user_turn(payloads):
            logger.warning("[spectrum·夺舍] Gemini 链路拒绝 assistant 收尾，本轮保留思考材料改用 user 收尾")
            return await call(payloads, tools, *args, **kwargs)
        dropped = drop_prefill_messages(payloads)
        logger.warning(
            "[spectrum·夺舍] 重试仍被拒，本轮摘掉 prefill 再发（摘 %d 条，本轮无思考段）",
            dropped)
        return await call(payloads, tools, *args, **kwargs)


def default_prefill_on() -> bool:
    return bool(cfg_on("possession_enabled", True)
                and (cfg_on("prefill_possession", True)
                     or cfg_on("prefill_gemini_closed", True)))


def install() -> bool:
    """包装提供商请求组装与发送；回退请求重新计入限流并核对冻结代次。"""
    global _patched
    if _patched:
        return True
    try:
        from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
    except Exception:
        logger.warning("[spectrum·夺舍] 找不到 ProviderOpenAIOfficial，prefill 模式不可用")
        return False

    original_prepare = ProviderOpenAIOfficial._prepare_chat_payload
    original_query = ProviderOpenAIOfficial._query

    async def _prepare_with_prefill(self, *args, **kwargs):
        payloads, context_query = await original_prepare(self, *args, **kwargs)
        try:
            _promote_prefill(payloads, self)
        except Exception:

            logger.exception("[spectrum·夺舍] prefill 后处理失败，按原样发出")
        return payloads, context_query

    async def _query_with_tool_strip(self, payloads, tools=None, *args, **kwargs):
        if not cfg_on("possession_enabled", True):
            return await original_query(self, payloads, tools, *args, **kwargs)


        used_prefill = False
        try:
            if payloads.pop("_spectrum_prefill", None):
                used_prefill = True
                dropped = _strip_tool_fields(payloads)
                tools = None
                logger.info(
                    "[spectrum·夺舍] 发包前拿掉工具定义（%s）——"
                    "prefix 与 function call 互斥",
                    ("、".join(dropped) if dropped else "无残留字段"),
                )
        except Exception:
            logger.exception("[spectrum·夺舍] 剥工具字段失败，按原样发出")


        from . import freeze, limiter
        epoch = freeze.checkpoint()
        attempts = 0

        async def _call(p, t, *a, **k):
            nonlocal attempts
            if attempts:
                await limiter.acquire()
            if not freeze.may_commit(epoch):
                raise freeze.FrozenSkipped("格式回退请求跨过系统暂停")
            attempts += 1
            return await original_query(self, p, t, *a, **k)

        return await send_with_model_turn_fallback(
            _call, payloads, tools, args, kwargs, used_prefill=used_prefill)

    _prepare_with_prefill._spectrum_prefill_adapter = True
    ProviderOpenAIOfficial._prepare_chat_payload = _prepare_with_prefill
    ProviderOpenAIOfficial._query = _query_with_tool_strip
    _patched = True
    logger.info("[spectrum·夺舍] prefill 补丁已装上（思维链夺舍 · 酒馆式）")
    return True
