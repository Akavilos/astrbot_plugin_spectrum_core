"""按平台身份登记人物、别名和出现过的会话，构造当前人物档案。
群聊档案按配置隐藏私聊认知；昵称不作为唯一身份。"""


from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from astrbot.api import logger


_PLACEHOLDERS = ("某人", "对方", "群友", "", "N/A", "n/a")


def make_person_id(platform: str, user_id: str) -> str:
    user_id = (user_id or "").strip()
    if not user_id:
        return ""
    return f"{(platform or 'unknown').strip()}:{user_id}"


def is_private_umo(umo: str) -> bool:
    return "FriendMessage" in (umo or "")


def scope_of(umo: str) -> str:
    return "private" if is_private_umo(umo) else "public"


class PersonRegistry:
    def __init__(self, store, cfg_get) -> None:
        self.store = store
        self.get_cfg = cfg_get


    def enabled(self) -> bool:
        return bool(self.get_cfg("person_profile_enabled", True))


    def observe(self, umo: str, sender_id: str, sender_name: str, platform: str = "") -> str:
        """以平台和发送者身份建立人物，记录昵称别名及来源会话。"""
        if not self.enabled():
            return ""
        person_id = make_person_id(platform, sender_id)
        if not person_id:
            return ""

        name = (sender_name or "").strip()
        if name in _PLACEHOLDERS:
            name = ""

        first_sight = self.store.get_person(person_id) is None
        self.store.upsert_person(person_id, platform=platform, name=name)
        if name:
            self.store.add_person_alias(person_id, name, umo)

        self.store.touch_person_session(person_id, umo)

        if first_sight:
            logger.info("[spectrum·群像] 新人物入库 %s（%s）于 %s", person_id, name or "?", umo)
        return person_id

    def note_mention(self, umo: str, sender_id: str, sender_name: str,
                     platform: str = "") -> str:
        if not self.enabled():
            return ""
        person_id = make_person_id(platform, sender_id)
        name = (sender_name or "").strip()
        if not person_id or not name or name in _PLACEHOLDERS:
            return ""

        first_sight = self.store.get_person(person_id) is None
        self.store.upsert_person(person_id, platform=platform, name=name)
        self.store.add_person_alias(person_id, name, umo)
        if first_sight:
            logger.info("[spectrum·群像] @提及入库 %s（%s）于 %s", person_id, name, umo)
        return person_id


    def remember(self, person_id: str, text: str, origin_umo: str, importance: float = 0.5) -> int:
        if not self.enabled() or not person_id or not (text or "").strip():
            return 0
        mid = self.store.add_person_memory(
            person_id, text, origin_umo=origin_umo,
            scope=scope_of(origin_umo), importance=importance,
        )
        if mid > 0:
            logger.info("[spectrum·群像] 记下关于 %s 的事: %s", person_id, text[:50])
        return mid


    def dossier(self, person_id: str, umo: str, query: str = "", memory_limit: int = 6) -> dict:
        """汇总当前人物档案与可见认知，并标明被隐藏的私聊记录数量。"""
        person = self.store.get_person(person_id)
        if not person:
            return {}

        names = []
        for r in self.store.list_person_aliases(person_id):
            if r["alias"] not in names:
                names.append(r["alias"])

        is_group = not is_private_umo(umo)

        hide_private = is_group and bool(self.get_cfg("person_private_in_group", True))
        picked = self.select_memories(person_id, umo, query, memory_limit)
        with self.store._lock:
            withheld = self.store._db.execute(
                "SELECT COUNT(*) FROM person_memories WHERE person_id=? AND scope='private'",
                (person_id,)).fetchone()[0] if hide_private else 0
        visible = [r["text"] for r in picked]

        return {
            "person_id": person_id,
            "name": person["name"],
            "names": names,
            "impression": person["impression"],
            "affinity": float(person["affinity"]),
            "trust": float(person["trust"]),
            "memories": visible,
            "memory_records": [dict(r) for r in picked],
            "withheld": withheld,
            "sessions": [r["umo"] for r in self.store.list_person_sessions(person_id)],
            "is_group": is_group,
            "first_seen": float(person["first_seen"] or 0),
            "last_seen": float(person["last_seen"] or 0),
        }

    def select_memories(self, person_id: str, umo: str, query: str = "",
                        limit: int = 6) -> list:
        if not self.enabled() or limit <= 0:
            return []
        scope = ("public" if not is_private_umo(umo)
                 and self.get_cfg("person_private_in_group", True) else None)
        related = self.store.search_person_memories(person_id, query, limit=min(4, limit), scope=scope)
        selected = list(related)
        seen = {row["id"] for row in selected}
        for row in self.store.get_person_memories(person_id, limit=limit, scope=scope):
            if len(selected) >= limit:
                break
            if row["id"] not in seen:
                selected.append(row)
                seen.add(row["id"])
        return selected


def render_memory_record(record: dict) -> str:
    channel = "私聊" if record["scope"] == "private" else "群聊/公开"
    when = (datetime.fromtimestamp(float(record["ts"]), ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
            if record.get("ts") else "")
    date = f"；{when}" if when else ""
    return f"〔记忆#{record['id']}；{channel}来源{date}〕{record['text']}"


def unambiguous_aliases(aliases: list[tuple[str, str]], platform: str) -> list[tuple[str, str]]:
    scoped = [(alias, pid) for alias, pid in aliases if pid.startswith(platform + ":")]
    owners: dict[str, set[str]] = {}
    for alias, pid in scoped:
        owners.setdefault(alias, set()).add(pid)
    unique = [(alias, pid) for alias, pid in scoped if len(owners[alias]) == 1]
    unique.extend((pid, pid) for pid in dict.fromkeys(pid for _, pid in scoped))
    return unique
