"""从角色输出解析生活文本、回复决策与工具调用。
处理标签与JSON格式差异，返回决策对象供后续状态和发送流程使用。"""


from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4


_PARSE_KEYS = frozenset({

    "seen", "reply", "mode", "content",

    "followUpCommitment", "requestCommitment", "continuityUpdate",


    "relatedId",
    "timelinePlan", "steps", "dueAt", "summary", "notBefore", "expiresAt", "trigger",
    "sharedEvents", "subject", "text", "basis", "sourceEntryId", "movement", "validUntil", "with",
    "memoryUpdates", "outingUpdate", "sleepUpdate", "until",

    "state", "session", "who", "kind", "id", "sendAt", "outcome",

    "name", "arguments",
})

_BLOCK = re.compile(r"<life>\s*(.*?)\s*</life>", re.S)


_INTER = re.compile(r"<reply>\s*(.*?)\s*(?:</reply>|$)", re.S)


_THINK = re.compile(r"<think>\s*(.*?)\s*(?:</think>|(?=<life>)|(?=<reply>)|$)", re.S)


_PREFILL_HEAD = re.compile(r"^.*?</thinking>", re.S)

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")


_CN2EN = (
    ("<生活>", "<life>"), ("</生活>", "</life>"),
    ("<互动>", "<reply>"), ("</互动>", "</reply>"),
    ("<思考>", "<think>"), ("</思考>", "</think>"),
)


def normalize_tags(text: str) -> str:
    if not text:
        return text
    for a, b in _CN2EN:
        if a in text:
            text = text.replace(a, b)
    return text


_TOOLCALL = re.compile(
    r"<｜tool▁calls▁begin｜>\s*(\{.*?\})\s*<｜tool▁calls▁end｜>", re.S
)


def extract_tool_calls(text: str) -> tuple[str, list[dict]]:
    if not text:
        return text, []
    calls: list[dict] = []
    for m in _TOOLCALL.finditer(text):
        try:
            obj = json.loads(m.group(1))
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("name"):
            args = obj.get("arguments")
            calls.append({
                "name": str(obj["name"]),
                "arguments": args if isinstance(args, dict) else {},
            })
    cleaned = _TOOLCALL.sub("", text)
    return cleaned, calls


def strip_all_tags(text: str) -> str:
    if not text:
        return text
    t = text

    for pat in (r"<reply>.*?(?:</reply>|$)", r"<life>.*?(?:</life>|$)"):
        t = re.sub(pat, "", t, flags=re.S)


    t = re.sub(r"</?(?:[A-Za-z_][\w.:-]*|[一-鿿]{1,10})>", "", t)

    t = re.sub(r"[ \t]*[-—–]{2,}[ \t]*$", "", t, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def prefill_think(text: str) -> str:
    if not text:
        return ""
    i = text.find("</thinking>")
    if i != -1:
        return _clean_think_head(text[:i])


    j = text.find("<life>")
    if j > 0:
        return _clean_think_head(text[:j])
    return ""


_STRAY_TAG = re.compile(r"</?\s*(?:思考|think|thinking)\s*>", re.I)


def _clean_think_head(seg: str) -> str:
    seg = _STRAY_TAG.sub("", seg)
    seg = re.sub(r"[ \t]*[-—–]{2,}[ \t]*$", "", seg, flags=re.M)
    return seg.strip()


_CLOSED_THINK = re.compile(r"<\s*/\s*(?:思考|think|thinking)\s*>", re.I)


def drop_prefill_head(text: str) -> str:
    if not text:
        return text
    m = _PREFILL_HEAD.search(text)
    if m:
        return text[m.end():].lstrip()
    i = text.find("<life>")
    if i > 0 and not _CLOSED_THINK.search(text[:i]):
        return text[i:]
    return text

_VALID_MODES = ("none", "immediate", "delayed")


@dataclass
class Decision:
    script: str = ""


    think: str = ""
    seen: bool = True
    mode: str = "none"
    content: str = ""
    send_at: datetime | None = None


    tool_calls: list[dict] = field(default_factory=list)

    ok: bool = False
    error: str = ""
    raw: str = ""
    repairs: list[str] = field(default_factory=list)


    follow_up: dict | None = None
    intent_updates: list[dict] = field(default_factory=list)


    requested_commitment: dict | None = None


    continuity_update: str | None = None
    timeline_plan: dict | None = None
    willingness_delta: float | None = None
    memory_updates: list[str] = field(default_factory=list)
    shared_events: list[dict] = field(default_factory=list)

    sleep: dict | None = None


    outing: dict | None = None


    decision_id: str = field(default_factory=lambda: uuid4().hex, kw_only=True)
    turn_id: str = field(default="", kw_only=True)
    world_version: str = field(default="", kw_only=True)
    source_actor: str = field(default="", kw_only=True)
    communication_committed: bool = field(default=False, kw_only=True)

    @property
    def delivers(self) -> bool:
        return self.mode in ("immediate", "delayed") and bool(self.content.strip())


def _strip_fence(text: str) -> str:
    t = text.strip()
    t = _FENCE.sub("", t).strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t).strip()
    if t.endswith("```"):
        t = t[:-3].strip()
    return t


def _loads_lenient(blob: str) -> dict | None:
    blob = _strip_fence(blob).strip()
    if not blob:
        return None

    for candidate in _json_candidates(blob):
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _json_candidates(blob: str):
    yield blob

    i, j = blob.find("{"), blob.rfind("}")
    if i != -1 and j > i:
        yield blob[i : j + 1]

    fixed = blob.replace("'", '"').replace("“", '"').replace("”", '"')
    i, j = fixed.find("{"), fixed.rfind("}")
    if i != -1 and j > i:
        yield fixed[i : j + 1]


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _as_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _clean_content(value) -> str:
    if not isinstance(value, str):
        return ""
    t = value.strip()

    for a, b in (('"', '"'), ("“", "”"), ("'", "'"), ("「", "」")):
        if len(t) >= 2 and t.startswith(a) and t.endswith(b):
            t = t[1:-1].strip()
    if t.startswith("content:"):
        t = t[7:].strip()
    return t


_SANITIZE_INTER = re.compile(r"<reply>\s*(.*?)\s*(?:</reply>|$)", re.S)
_SANITIZE_LIFE = re.compile(r"<life>.*?(?:</life>|$)", re.S)


_SANITIZE_THINK = re.compile(r"<think>.*?(?:</think>|$)", re.S)


def _extract_reply_content(text: str) -> str:
    if not text:
        return ""
    blob = None
    m = _SANITIZE_INTER.search(text)
    if m:
        blob = m.group(1)
    else:
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j > i:
            blob = text[i : j + 1]
    if not blob:
        return ""
    obj = _loads_lenient(blob)
    if not isinstance(obj, dict):
        return ""
    reply = obj.get("reply")
    if isinstance(reply, dict):
        return _clean_content(reply.get("content"))
    if "content" in obj:
        return _clean_content(obj.get("content"))
    return ""


def sanitize_text(raw: str, max_len: int = 500) -> str:
    if not raw:
        return ""
    raw = normalize_tags(raw)

    raw = drop_prefill_head(raw)
    content = _extract_reply_content(raw)
    if content:
        return content[:max_len]

    t = raw
    for _ in range(4):
        nxt = _SANITIZE_INTER.sub("", t)
        nxt = _SANITIZE_LIFE.sub("", nxt)
        nxt = _SANITIZE_THINK.sub("", nxt)
        if nxt == t:
            break
        t = nxt
    t = _strip_fence(t)
    i = t.find("{")
    if i != -1:
        t = t[:i]
    t = re.sub(r"[<>]{1,}", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()[:max_len]


def parse(raw: str, now: datetime) -> Decision:
    d = Decision(raw=raw or "")
    raw = normalize_tags(raw or "")
    d.raw = raw
    text = (raw or "").lstrip("﻿").strip()
    if not text:
        d.error = "空输出"
        return d


    _raw = raw or ""
    _i = _raw.find("</thinking>")
    _scan = _raw[_i + len("</thinking>"):] if _i != -1 else _raw
    _, d.tool_calls = extract_tool_calls(_scan)


    text = drop_prefill_head(text)

    text, _ = extract_tool_calls(text)
    if d.tool_calls:
        d.repairs.append("文本工具调用 %d 个" % len(d.tool_calls))

    m_script = _BLOCK.search(text)
    m_inter = _INTER.search(text)
    m_think = _THINK.search(text)

    if m_think:
        d.think = m_think.group(1).strip()
    if m_script:
        d.script = m_script.group(1).strip()
    if m_inter:
        payload = _loads_lenient(m_inter.group(1))
    else:

        payload = None
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j > i:
            payload = _loads_lenient(text[i : j + 1])
        if payload is None:
            d.error = "缺少 <reply> 段且找不到 JSON"
            return d
        d.repairs.append("缺少 <reply> 标签，已从全文抢救 JSON")
        if not d.script:
            head = text[:i].strip()
            if head:
                d.script = head

    if payload is None:


        _cands = list(_INTER.finditer(text))
        if len(_cands) > 1:
            _p = _loads_lenient(_cands[-1].group(1))
            if _p is not None:
                payload = _p
                d.repairs.append("首个 <reply> 非法，改用末段 <reply>")
                _blocks = list(_BLOCK.finditer(text))
                if _blocks:
                    _s = _blocks[-1].group(1).strip()
                    if _s:
                        d.script = _s
    if payload is None:
        d.error = "<reply> 段不是合法 JSON"
        return d


    seen = payload.get("seen", True)
    if isinstance(seen, str):
        seen = seen.strip().lower() not in ("false", "0", "no", "否")
    d.seen = bool(seen)


    reply = payload.get("reply")
    if not isinstance(reply, dict):

        if "mode" in payload or "content" in payload:
            reply = payload
            d.repairs.append("reply 被拍平到顶层，已兼容")
        else:
            d.mode, d.content = "none", ""
            d.ok = True
            return d

    mode = str(reply.get("mode", "none")).strip().lower()
    if mode not in _VALID_MODES:
        d.repairs.append(f"未知 mode={mode!r}，按 none 处理")
        mode = "none"

    content = _clean_content(reply.get("content"))

    if mode in ("immediate", "delayed") and not content:
        d.repairs.append(f"mode={mode} 但 content 为空，降级为 none")
        mode = "none"

    if mode == "delayed":
        send_at = _parse_iso(reply.get("sendAt"))
        if send_at is None:
            d.repairs.append("delayed 但 sendAt 缺失/不可解析，改为立即发送")
            mode = "immediate"
        else:
            if send_at.tzinfo is None and now.tzinfo is not None:
                send_at = send_at.replace(tzinfo=now.tzinfo)
            if send_at <= now:
                d.repairs.append("sendAt 不晚于现在，改为立即发送")
                mode = "immediate"
            elif send_at - now > timedelta(days=3):
                d.repairs.append("sendAt 超过 3 天，截断为 1 小时后")
                send_at = now + timedelta(hours=1)
            else:
                d.send_at = send_at

    d.mode = mode
    d.content = content


    _FU_KINDS = ("thinking", "checking", "decision", "emotional-settle")
    fu = payload.get("followUpCommitment")
    if isinstance(fu, dict):
        kind = str(fu.get("kind", "thinking"))
        if kind not in _FU_KINDS:
            kind = "thinking"
            d.repairs.append("followUpCommitment.kind 非法，按 thinking")
        nb = _parse_iso(fu.get("notBefore"))
        if nb is not None and nb.tzinfo is None and now.tzinfo is not None:
            nb = nb.replace(tzinfo=now.tzinfo)
        if nb is None or nb <= now:
            d.repairs.append("followUpCommitment.notBefore 缺失/不晚于现在，忽略该承诺")
        else:
            d.follow_up = {
                "kind": kind,
                "summary": str(fu.get("summary") or "").strip()[:300],
                "not_before": nb.timestamp(),
                "trigger": "after_return" if fu.get("trigger") == "after_return" else "time",
                "movement": "go" if fu.get("movement") == "go" else "",
                "related_id": int(fu["relatedId"]) if isinstance(fu.get("relatedId"), int) else None,
                "expires_at": (
                    _parse_iso(fu.get("expiresAt")).timestamp()
                    if _parse_iso(fu.get("expiresAt")) else None
                ),
            }


    rc = payload.get("requestCommitment")
    if isinstance(rc, dict):
        rc_summary = str(rc.get("summary") or "").strip()[:300]
        if rc_summary:
            rc_nb = _parse_iso(rc.get("notBefore"))
            d.requested_commitment = {
                "kind": str(rc.get("kind") or "promise")[:40],
                "summary": rc_summary,
                "who": str(rc.get("who") or "对方")[:40],
                "not_before": rc_nb.timestamp() if rc_nb else None,


                "related_id": _as_int(rc.get("relatedId")),
            }


    upd = payload.get("intentUpdates")
    if isinstance(upd, list):
        for u in upd:
            if isinstance(u, dict) and u.get("id") is not None:
                item = {"id": u.get("id"),
                        "outcome": str(u.get("outcome") or "cancelled")}
                nb = _parse_iso(u.get("notBefore"))
                if nb is not None and nb.tzinfo is None and now.tzinfo is not None:
                    nb = nb.replace(tzinfo=now.tzinfo)
                item["not_before"] = nb.timestamp() if nb else None
                d.intent_updates.append(item)


    cu = payload.get("continuityUpdate")
    if isinstance(cu, str) and cu.strip():
        d.continuity_update = cu.strip()


    tp = payload.get("timelinePlan")
    if isinstance(tp, dict):
        d.timeline_plan = tp


    mu = payload.get("memoryUpdates")
    if isinstance(mu, list):
        for item in mu:
            if isinstance(item, str) and item.strip():
                d.memory_updates.append(item.strip())

    events = payload.get("sharedEvents")
    if isinstance(events, list):
        from .shared_life import normalize_event
        for item in events:
            if isinstance(item, dict):
                try:
                    d.shared_events.append(normalize_event(item))
                except (ValueError, TypeError):
                    d.repairs.append("sharedEvents 条目无效，已忽略")


    ou = payload.get("outingUpdate")
    if isinstance(ou, dict):
        st = str(ou.get("state") or "").strip().lower()
        if st in ("out", "outing", "away"):
            d.outing = {
                "action": "out",
                "session": str(ou.get("session") or "").strip(),
            }
        elif st in ("back", "home", "return", "in"):
            d.outing = {"action": "back"}


    su = payload.get("sleepUpdate")
    if isinstance(su, dict):
        st = str(su.get("state") or "").strip().lower()
        if st in ("sleep", "asleep"):
            until = _parse_iso(su.get("until"))
            d.sleep = {
                "action": "sleep",
                "until": until.timestamp() if until else None,
            }
        elif st in ("wake", "awake", "up"):
            d.sleep = {"action": "wake"}

    d.ok = True
    return d
