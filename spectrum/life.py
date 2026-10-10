"""保存共同生活片段、同行归属、现状与行程。
同时解析外部事实指令，按代码规则审核内容并确定受众。"""


from __future__ import annotations

import json
import re
import time
from datetime import datetime

from astrbot.api import logger


GLOBAL = "global"


DEFAULT_DURATION_H = 3.0


MAX_PLANS = 20


def _parse_iso(s: str) -> float | None:
    if not s or not isinstance(s, str):
        return None
    t = s.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(t).timestamp()
    except Exception:
        pass

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(t[:19], fmt).timestamp()
        except Exception:
            continue
    return None


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


class LifeLine:
    def __init__(self, store):
        self.store = store


    def get_state(self) -> dict:
        raw = self.store.get_state(GLOBAL, "life_state", "") or ""
        if not raw:
            return {}
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {"state": raw}
        except Exception:

            return {"state": raw}

    def get_state_text(self) -> str:
        return str(self.get_state().get("state") or "")

    def get_owner(self) -> dict:
        raw = self.store.get_state(GLOBAL, "life_owner", "") or ""
        if not raw:
            return {}
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}


    def current_life_id(self) -> str:
        return (self.store.get_state(GLOBAL, "life_current_id", "") or "").strip()

    def new_life_id(self, now: float) -> str:
        base = "L%d" % int(now * 1000)
        if self.store.get_life(base) is None:
            return base


        for i in range(1, 100):
            cand = "%s.%d" % (base, i)
            if self.store.get_life(cand) is None:
                return cand
        return "%s.%d" % (base, int(now * 1000) % 97)

    def open_life(self, kind: str, *, owner_session: str = "", who: str = "",
                  now: float | None = None) -> str:
        now = now or time.time()
        self.close_life(now=now)
        lid = self.new_life_id(now)
        self.store.add_life(lid, kind, now, owner_session=owner_session, with_who=who)
        self.store.set_state(GLOBAL, "life_current_id", lid)
        logger.info("[spectrum·生活线] 开段 %s（%s%s）", lid, kind,
                    "，跟 " + who if who else "")
        return lid

    def close_life(self, *, now: float | None = None,
                   title: str = "", summary: str = "") -> str:
        lid = self.current_life_id()
        if not lid:
            return ""
        n = self.store.end_life(lid, now or time.time(), title=title, summary=summary)
        if n:
            logger.info("[spectrum·生活线] 关段 %s%s", lid,
                        ("：" + title) if title else "")
        return lid

    def delete_life(self, life_id: str) -> dict:
        life_id = (life_id or "").strip()
        if not life_id:
            return {"entries": 0, "lives": 0}
        if self.store.get_life(life_id) is None:
            return {"entries": 0, "lives": 0}
        was_current = (self.current_life_id() == life_id)
        n = self.store.delete_life(life_id)
        if was_current:
            self.store.set_state(GLOBAL, "life_current_id", "")
            if self.get_owner():
                self.store.set_state(GLOBAL, "life_owner", "")
                logger.info("[spectrum·生活线] 删掉当前段 %s，外出锁一并归还", life_id)
        logger.info("[spectrum·生活线] 删除段 %s（连带 %d 条记录%s）",
                    life_id, int(n.get("entries") or 0),
                    "，它是当前段" if was_current else "")
        return n

    def release_current(self, life_id: str) -> bool:
        life_id = (life_id or "").strip()
        if not life_id or self.current_life_id() != life_id:
            return False
        self.store.set_state(GLOBAL, "life_current_id", "")
        if self.get_owner():
            self.store.set_state(GLOBAL, "life_owner", "")
            logger.info("[spectrum·生活线] 结束当前段 %s，外出锁一并归还", life_id)
        return True

    def ensure_life(self, now: float | None = None) -> str:
        lid = self.current_life_id()
        if lid:
            return lid
        return self.open_life("home", now=now)

    def clear_owner_only(self) -> None:
        self.store.set_state(GLOBAL, "life_owner", "")


    def set_owner(self, *, session: str = "", who: str = "",
                  now: float | None = None) -> None:
        now = now or time.time()
        session = (session or "").strip()
        who = (who or "").strip()
        old = self.get_owner()

        if not session:
            if old:
                self.store.set_state(GLOBAL, "last_outing_return", json.dumps(
                    {"started_at": old["since"], "returned_at": now}))
            self.store.set_state(GLOBAL, "life_owner", "")

            if old:
                self.open_life("home", now=now)
            return

        self.store.set_state(GLOBAL, "life_owner", json.dumps(
            {"session": session, "with": who,
             "since": old["since"] if old.get("session") == session else now}, ensure_ascii=False))


        cur = self.current_life_id()
        row = self.store.get_life(cur) if cur else None
        if row is not None:
            try:
                same = (row["kind"] == "outing"
                        and row["owner_session"] == session
                        and row["ended_at"] is None)
            except Exception:
                same = False
            if same:
                return
        self.open_life("outing", owner_session=session, who=who, now=now)

    def is_owner(self, umo: str) -> bool:
        owner = self.get_owner()
        if not owner:
            return False
        return owner.get("session") == umo

    def set_state(self, text: str, *, source: str = "",
                  now: float | None = None, audience=None,
                  items: list | None = None, expected_version: str | None = None) -> bool:
        """写入共同现状及相关受众信息。"""
        text = (text or "").strip()

        if not text and not items:
            return False


        from . import audience as _aud
        d = {"state": text, "at": now or time.time(), "src": source,
             "audience": _aud.render_obj(audience)}
        if items:


            d["items"] = [
                {"text": str(it.get("text") or ""),
                 "audience": _aud.render_obj(it.get("audience"))}
                for it in items if isinstance(it, dict) and str(it.get("text") or "")
            ]
        with self.store.world_read():
            if expected_version is not None and self.store.world_version() != expected_version:
                logger.info("[spectrum·生活线] 世界已变化，旧摘要不覆盖当前现状: %s", source)
                return False
            self.store.set_state(GLOBAL, "life_state", json.dumps(d, ensure_ascii=False))
        return True

    def get_audience(self):
        from . import audience as _aud
        return _aud.parse((self.get_state() or {}).get("audience"))

    def items(self) -> list:
        its = (self.get_state() or {}).get("items")
        return its if isinstance(its, list) else []

    def get_state_for(self, umo: str) -> str:
        from . import audience as _aud
        its = self.items()
        if its:
            kept = []
            for it in its:
                if not isinstance(it, dict):
                    continue
                if _aud.admit(_aud.parse(it.get("audience")), umo):
                    kept.append(str(it.get("text") or ""))
            return " ".join(x for x in kept if x)
        if not _aud.admit(self.get_audience(), umo):
            return ""
        return self.get_state_text()

    def admit_state(self, umo: str) -> bool:
        from . import audience as _aud
        its = self.items()
        if its:
            for it in its:
                if (isinstance(it, dict)
                        and _aud.admit(_aud.parse(it.get("audience")), umo)):
                    return True
            return False
        return _aud.admit(self.get_audience(), umo)


    def get_plans(self) -> list[dict]:
        raw = self.store.get_state(GLOBAL, "life_plans", "[]") or "[]"
        try:
            d = json.loads(raw)
            return d if isinstance(d, list) else []
        except Exception:
            return []

    def _save(self, plans: list[dict]) -> None:
        plans = sorted(plans, key=lambda p: p.get("start") or 0, reverse=True)[:MAX_PLANS]
        self.store.set_state(GLOBAL, "life_plans",
                             json.dumps(plans, ensure_ascii=False))

    def active_plans(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        out = []
        for p in self.get_plans():
            if p.get("status") != "active":
                continue
            end = p.get("end") or p.get("start") or 0

            if end + DEFAULT_DURATION_H * 3600 < now:
                continue
            out.append(p)
        return out


    @staticmethod
    def _span(p: dict, now: float) -> tuple[float, float]:
        s = p.get("start") or now
        e = p.get("end") or (s + DEFAULT_DURATION_H * 3600)
        return s, e

    def find_conflicts(self, new_plan: dict, now: float | None = None) -> list[dict]:
        now = now or time.time()
        new_with = (new_plan.get("with") or "").strip()
        if not new_with:
            return []
        ns, ne = self._span(new_plan, now)
        out = []
        for p in self.active_plans(now):
            if not (p.get("with") or "").strip():
                continue
            if (p.get("with") or "").strip() == new_with:
                continue
            ps, pe = self._span(p, now)
            if ns < pe and ps < ne:
                out.append(p)
        return out

    @staticmethod
    def _new_plan_id(now: float, existing: list[dict]) -> str:
        used = {p.get("id") for p in (existing or [])}
        base = "P%d" % int(now * 1000)
        if base not in used:
            return base
        for i in range(1, 100):
            cand = "%s.%d" % (base, i)
            if cand not in used:
                return cand
        return "%s.%d" % (base, int(now * 1000) % 97)

    def _resolve_who(self, summary: str, aliases: list[tuple[str, str]],
                     exclude_umo: str = "") -> str:
        if not summary or not aliases:
            return ""


        scored = find_mentioned_people_scored(summary, aliases, exclude=exclude_umo)
        high = [pid for pid, conf in scored if conf == 2]
        low = [pid for pid, conf in scored if conf == 1]
        pids = high if high else low
        if not pids:
            return ""


        pid = pids[0]
        try:
            name = self.store.get_person_name(pid)
            if name:
                return name
        except Exception:
            pass
        for alias, apid in aliases:
            if apid == pid:
                return alias
        return ""

    def submit_plan(self, step: dict, *, source: str = "",
                    aliases: list[tuple[str, str]] | None = None,
                    now: float | None = None) -> dict:
        """写入行程并处理时间重叠产生的安排冲突。"""
        now = now or time.time()
        start = _parse_iso(step.get("dueAt") or "") or now
        end = _parse_iso(step.get("until") or "")
        if end is None:
            end = start + DEFAULT_DURATION_H * 3600

        summary = str(step.get("summary") or "").strip()
        who = str(step.get("with") or "").strip()
        if not who:
            who = self._resolve_who(summary, aliases or [], exclude_umo=source)

        plan = {


            "id": self._new_plan_id(now, self.get_plans()),
            "summary": summary,
            "with": who,
            "movement": "go" if step.get("movement") == "go" else "",
            "start": start,
            "end": end,
            "status": "active",
            "src": source,
            "at": now,
        }
        conflicts = self.find_conflicts(plan, now)

        plans = self.get_plans()
        superseded = []
        for c in conflicts:
            for p in plans:
                if p.get("id") == c.get("id"):
                    p["status"] = "superseded"
                    p["superseded_by"] = plan["id"]
                    p["superseded_at"] = now
                    superseded.append(p)
        plans.append(plan)
        self._save(plans)

        return {"ok": True, "conflicts": conflicts, "plan": plan,
                "superseded": superseded}


    def render_plans(self, now: float | None = None) -> str:
        now = now or time.time()
        rows = self.active_plans(now)
        if not rows:
            return ""
        lines = []
        for p in sorted(rows, key=lambda x: x.get("start") or 0):
            s, e = self._span(p, now)
            who = f"（跟{p['with']}）" if p.get("with") else ""
            lines.append(f"- {_fmt(s)}–{_fmt(e)}: {p.get('summary','')}{who}")
        return "\n".join(lines)

    def render_conflict(self, result: dict) -> str:
        sup = result.get("superseded") or []
        if not sup:
            return ""
        lines = []
        for p in sup:
            s, _ = self._span(p, 0)
            who = p.get("with") or ""
            lines.append(f"- 原本 {_fmt(s)} 跟{who}有约：{p.get('summary','')}"
                         f"（已改）")
        return "\n".join(lines)


def find_mentioned_people(text: str, aliases: list[tuple[str, str]],
                          exclude: str = "") -> list[str]:
    if not text:
        return []

    ordered = sorted(aliases, key=lambda x: -len(x[0] or ""))
    remaining = text
    hits, seen_pid = [], set()
    for alias, pid in ordered:
        alias = (alias or "").strip()
        if not alias or pid == exclude:
            continue
        matched = _match_alias(alias, remaining)
        if not matched:
            continue

        remaining = remaining.replace(matched, "\x00")
        if pid in seen_pid:
            continue
        hits.append(pid)
        seen_pid.add(pid)
    return hits


_ALIAS_SEPS = " 　·・.。,_-—|/\\()（）[]【】<>《》\"'“”‘’:：;；!！?？~～+*#"


_MIN_LATIN = 3


def _prefix_forms(alias: str) -> list[str]:
    core = "".join(c for c in alias if c.isalnum() and ord(c) < 128)
    if len(core) < _MIN_LATIN + 1:
        return []
    return [core[:n] for n in range(len(core) - 1, _MIN_LATIN - 1, -1)]


def _alias_parts(alias: str) -> list[str]:
    parts = [alias]
    buf = ""
    for ch in alias:
        if ch in _ALIAS_SEPS or not ch.isalnum():
            if buf:
                parts.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        parts.append(buf)

    out, seen = [], set()
    for p in sorted(parts, key=len, reverse=True):
        if len(p) < 2 or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _match_alias(alias: str, text: str) -> str:
    if alias in text:
        return alias

    for part in _alias_parts(alias):
        if len(part) >= 2 and part in text:
            return part


    for part in _prefix_forms(alias):
        i = text.find(part)
        while i != -1:
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + len(part)] if i + len(part) < len(text) else ""


            if not (prev.isascii() and prev.isalpha()) and \
               not (nxt.isascii() and nxt.isalpha()):
                return part
            i = text.find(part, i + 1)
    return ""


MAX_REFS = 2


REFS_PER_PERSON = 3


def render_observed_rows(rows) -> str:
    from . import audience as _aud
    out = []
    for r in (rows or []):
        try:
            when = datetime.fromtimestamp(float(r["ts"])).strftime("%m-%d %H:%M")
            a = _aud.parse(r["audience"])
            if a == _aud.GLOBAL:
                mark = "全局"
            elif a is None:
                mark = "未知"
            else:
                mark = "锁给 " + "、".join(
                    str(u).replace("default:", "")[:24] for u in sorted(a))
            out.append(f"· [{mark}] {when}  {r['text']}")
        except Exception:
            continue
    return "\n".join(out)


def observed_scene_from(message_str: str, fallback: str = "") -> str:
    raw = (message_str or "").strip() or (fallback or "").strip()
    text = raw.replace("叙述事实", " ", 1).strip()
    if len(text) >= 2 and text[0] in "{｛" and text[-1] in "}｝":
        text = text[1:-1].strip()
    return text


_OBSERVED_QUOTE = re.compile(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"]*"')
_OBSERVED_SELF_CLAUSE = re.compile(
    r"(?:^|[，。！？；\n])\s*(?:我|你|AI|ai|Ai|机器人)(?!家)")
_OBSERVED_META_COMMAND = re.compile(
    r"(?:忽略|无视)(?:之前|以上|前面|所有)?(?:的)?(?:规则|指令|提示词)|"
    r"(?:回复|回答|输出)时(?:必须|应该|要)|"
    r"从现在起(?:必须|应该|只|不许|不要)")


def observed_reject_reason(text: str) -> str:
    """按词句规则筛查外部事实输入，返回拒绝原因；不调用模型审核。"""
    scene = _OBSERVED_QUOTE.sub("", str(text or ""))
    if _OBSERVED_SELF_CLAUSE.search(scene):
        return "这段直接写了我的状态或行为。请只写我眼前发生的事。"
    if _OBSERVED_META_COMMAND.search(scene):
        return "这段包含面向我的指令。请只写我眼前发生的事。"
    return ""


_OBSERVED_DELETE_ACTIONS = {
    "all": "all",
    "最近": "recent",
    "历史": "history",
}


def observed_delete_mode(raw: str) -> str | None:
    return _OBSERVED_DELETE_ACTIONS.get((raw or "").strip().lower())


def observed_audience(store, life, text: str):
    from . import audience as _aud
    aliases = store.all_person_aliases()
    names: list = []
    for pid, _conf in find_mentioned_people_scored(text, aliases):
        nm = store.get_person_name(pid) or ""
        if nm and nm not in names:
            names.append(nm)


    return _aud.for_item(names, life.get_owner(),
                         store.sessions_of_person_name)


def find_mentioned_people_scored(
    text: str, aliases: list[tuple[str, str]], exclude: str = "",
) -> list[tuple[str, int]]:
    if not text:
        return []
    ordered = sorted(aliases, key=lambda x: -len(x[0] or ""))
    remaining = text
    out: list[tuple[str, int]] = []
    seen_pid = set()


    for alias, pid in ordered:
        if pid == exclude or pid in seen_pid or not pid:
            continue
        qq = str(pid).rsplit(":", 1)[-1]
        if not qq.isdigit() or len(qq) < 5:
            continue
        i = remaining.find(qq)
        while i != -1:
            prev = remaining[i - 1] if i > 0 else ""
            nxt = remaining[i + len(qq)] if i + len(qq) < len(remaining) else ""
            if not prev.isdigit() and not nxt.isdigit():
                remaining = remaining[:i] + "\x00" * len(qq) + remaining[i + len(qq):]
                out.append((pid, 2))
                seen_pid.add(pid)
                break
            i = remaining.find(qq, i + 1)

    for alias, pid in ordered:
        alias = (alias or "").strip()
        if not alias or pid == exclude or pid in seen_pid:
            continue
        if alias in remaining:
            remaining = remaining.replace(alias, "\x00")
            out.append((pid, 2))
            seen_pid.add(pid)
            continue
        matched = _match_alias(alias, remaining)
        if matched:
            remaining = remaining.replace(matched, "\x00")
            out.append((pid, 1))
            seen_pid.add(pid)
    return out


def render_references(refs: list[tuple[str, list[str]]]) -> str:
    if not refs:
        return ""
    out = []
    for name, mems in refs:
        if not mems:
            continue
        out.append(f"· {name}：" + "；".join(mems))
    return "\n".join(out)
