"""规范化共同生活事件并生成带时间、来源和状态的可读记录。"""


from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


EVENT_KINDS = {"physical", "commitment", "knowledge"}
EVENT_STATES = {"active", "resolved"}


def normalize_event(raw: dict) -> dict:
    """校验事件类型、来源与出行方向，并统一期限和关联ID字段。"""
    kind = str(raw.get("kind") or "").strip()
    subject = str(raw.get("subject") or "").strip()
    text = str(raw.get("text") or "").strip()
    state = str(raw.get("state") or "active").strip()
    basis = str(raw.get("basis") or "").strip()
    movement = str(raw.get("movement") or "").strip()
    valid_until = raw.get("validUntil", raw.get("valid_until"))
    related_id = raw.get("relatedId", raw.get("related_id"))
    if kind not in EVENT_KINDS or state not in EVENT_STATES:
        raise ValueError("未知的生活事件类型或状态")
    if basis not in {"script", "reply", "received"}:
        raise ValueError("生活事件没有明确的来源类型")
    expected = {"physical": "script", "commitment": "reply", "knowledge": "received"}
    if basis != expected[kind]:
        raise ValueError("事件类型与来源类型不匹配")
    source_entry_id = raw.get("sourceEntryId", raw.get("source_entry_id"))
    if kind == "knowledge" and (source_entry_id is None or int(source_entry_id) <= 0):
        raise ValueError("认知更新需要引用收到的原始消息")
    if movement not in {"", "stay", "go"}:
        raise ValueError("未知的出行方向")
    if not subject or not text:
        raise ValueError("生活事件缺少主体或正文")
    if related_id is not None:
        related_id = int(related_id)
    if valid_until:
        valid_until = (float(valid_until) if isinstance(valid_until, (int, float))
                       else datetime.fromisoformat(str(valid_until).replace("Z", "+00:00")).timestamp())
    return {
        "kind": kind, "subject": subject[:100], "text": text[:400],
        "state": state, "basis": basis, "related_id": related_id,
        "movement": movement, "valid_until": valid_until,
        "source_entry_id": int(source_entry_id) if source_entry_id is not None else None,
    }


def render_events(rows, person_name, *, chain=None, timezone="Asia/Shanghai", now=None) -> str:
    """按原始经历时间展示事件链，更新记录不冒充一次新经历。"""
    zone = ZoneInfo(timezone)
    now = datetime.now(zone).timestamp() if now is None else now
    lines = []
    for row in rows:
        source = row["source_umo"]
        who = person_name(source) or "另一个窗口"
        history = chain(row["id"]) if chain else [row]
        for event in history:
            when = datetime.fromtimestamp(float(event["ts"]), zone).strftime("%Y-%m-%d %H:%M")
            actor = person_name(event["source_umo"]) or "另一个窗口"
            verb = {"physical": "实际经历", "commitment": "说出口的承诺", "knowledge": "从收到的消息中得知"}[event["kind"]]
            lines.append(f"- [{event['id']}] {when}，我与{actor}往来时的{verb}：{event['text']}")
        ended = row["state"] == "resolved" or (row["valid_until"] is not None and float(row["valid_until"]) <= now)
        status = "已结束或到期" if ended else "尚未记下结束；不表示此刻刚发生"
        lines.append(f"  我在与{who}的往来中记下的最新进展[{row['id']}]：{status}。记录在哪个会话，不等于东西是谁送的或事情是谁做的。")
    if lines:
        lines.insert(0, "我在不同会话中经历的是同一段人生。日期是记录发生的实际时间；我不会因切换会话或睡醒而重演过去的事件。后续确认更新我对同一件事的认知，旧疑问只说明我当时问过，不能推翻后来已知的来源。")
    return "\n".join(lines)
