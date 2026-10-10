"""检查提示词声明的输出字段是否与解析入口相符。
检查目标由 seams 提供，返回不一致说明，不修改运行状态。"""

from __future__ import annotations

import re


_KEY_TOP = re.compile(r'^\s{2,4}"([A-Za-z_][A-Za-z0-9_]*)"\s*:', re.M)


_KEY_ANY = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')


_KEY_NAMED = re.compile(r"可选字段[\s:：]*([A-Za-z_][A-Za-z0-9_]*)")


def declared_keys(prompt: str, *, inline: bool = False) -> set[str]:
    text = prompt or ""
    pat = _KEY_ANY if inline else _KEY_TOP
    return set(pat.findall(text)) | set(_KEY_NAMED.findall(text))


def seams() -> list:
    from . import contract as C
    from . import director_prompts as DP
    from . import parse as P
    from .l2 import L2


    _probe_spec = [{"name": "_probe", "description": "", "parameters": {}}]
    return [
        ("压缩·导演版", DP.DIRECTOR_COMPRESSION_PROMPT, False, L2._PARSE_KEYS),
        ("压缩·老路径", C.CONTINUITY_COMPRESSION_PROMPT, False, L2._PARSE_KEYS),


        ("落点声明", DP.DIRECTOR_AUDIENCE_BLOCK, True,
         ("seen", "audience", "reply", "mode", "content")),


        ("合约·reply 与可选字段", C.build_contract(), True, P._PARSE_KEYS),


        ("工具调用（文本化）",
         C.build_tools_block(_probe_spec, [], ""), True, ("name", "arguments")),
    ]


def _check(rows) -> list[str]:
    out = []
    for name, prompt, inline, known in rows:
        missing = declared_keys(prompt, inline=inline) - set(known)
        if missing:
            out.append(f"{name}：提示词声明了 {sorted(missing)}，但解析器不读它们")
    return out


def audit() -> list[str]:
    return _check(seams())


def report(logger) -> list[str]:
    try:
        rows = seams()
        problems = _check(rows)
    except Exception:
        logger.exception("[spectrum·契约自检] 自检**自身**失败（不影响运行）")
        return []
    if problems:
        for p in problems:
            logger.error(
                "[spectrum·契约自检] **输出契约已漂移**：%s "
                "⇒ 这条链路上的内容会写不进去，而且不会有别的报错。", p)
    else:
        logger.info("[spectrum·契约自检] 通过（%d 条接缝）", len(rows))
    return problems
