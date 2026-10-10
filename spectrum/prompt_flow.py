"""通过 AstrBot 的公开消息接口添加本轮背景；框架维护原生聊天历史。"""

from collections import Counter
import re

from astrbot.core.agent.message import TextPart


def temporary_context(*blocks: str) -> list[TextPart]:
    """合并已筛选的背景，标记为本轮有效，不写入框架聊天记录。"""
    text = "\n\n".join(block for block in blocks if block)
    return [TextPart(text=text).mark_as_temp()] if text else []


def inject_context(req, contract: str, *blocks: str) -> None:
    """保留原系统前缀、历史、用户输入、媒体和工具，只追加稳定合约和临时背景。"""
    req.system_prompt = "\n\n".join(p for p in (req.system_prompt, contract) if p)
    req.extra_user_content_parts.extend(temporary_context(*blocks))


def _plain(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text").strip()
    return ""


def missing_exchange(rows, contexts, current_text: str, parse_meta, *, is_group: bool) -> str:
    """补充框架历史未收录的真实往来；按次数去重，保留重复说话的顺序。"""
    known = Counter()
    for message in contexts or []:
        role = message.get("role")
        text = _plain(message.get("content"))
        if role == "assistant":
            reply = re.search(r"<reply>(.*?)</reply>", text, re.S)
            if reply:
                text = reply.group(1).strip()
        if role in ("user", "assistant") and text:
            known[(role, text)] += 1

    # 当前来信已经在 req.prompt 中；只排除最新的一条同文记录。
    current_id = next((r["id"] for r in rows
                       if r["kind"] == "received" and r["text"].strip() == current_text.strip()), None)
    lines = []
    for row in reversed(rows):
        if row["id"] == current_id:
            continue
        role = "user" if row["kind"] == "received" else "assistant"
        text = row["text"].strip()
        key = (role, text)
        if known[key]:
            known[key] -= 1
            continue
        sender = str(parse_meta(row["meta"]).get("sender") or "").strip()
        label = (sender if sender not in ("", "某人", "对方", "群友")
                 else "群友" if is_group else "对方") if role == "user" else "我"
        lines.append(f"{label}：{text}")
    return "\n".join(lines)
