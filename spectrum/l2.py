"""执行生活决策、承诺跟进、认知整理和后台生成。
生活变化与对外沟通分别处理；发送确认后再登记回复及相应沟通效果。"""


from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime as _dt
from typing import Any, Callable

try:
    from astrbot.api import logger
except Exception:
    import logging

    logger = logging.getLogger("spectrum.l2")

from . import clock
from . import context
from . import memory as _memory
from . import vectors as _vectors
from . import worldbook as _worldbook
from .lifecycle import intent_ready, runtime_facts
from . import freeze as freeze_mod
from .contract import (CONTINUITY_COMPRESSION_PROMPT, build_address_identity, build_contract, build_director_block,
                        build_l3_context_block, build_observed_block,
                        build_person_block, build_phase_block, build_tools_block,
                        build_worldbook_block)
from .parse import parse as parse_decision


from .prism_bridge import peek_emotion as _peek_prism_emotion
from .prism_bridge import peek_signals as _peek_prism_signals


def _item_text(x) -> str:
    if isinstance(x, dict):
        for k in ("fact", "text", "content", "summary"):
            v = x.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""
    return str(x).strip()


class L2:
    def __init__(self, store, context, cfg_get, persons=None) -> None:
        self.store = store
        self.context = context
        self.get_cfg = cfg_get

        self.persons = persons

        from .life import LifeLine
        self.life = LifeLine(store)


        from .director import Director
        self.director = Director(self)

        self._sys_core: dict[str, str] = {}
        self._last_advance: dict[str, float] = {}


        self._tool_names: dict[str, list[dict]] = {}

        self._handled: dict[str, None] = {}


        self._conflict_notes: dict[str, str] = {}


        self._title_fails: dict[str, int] = {}

    def queue_life_title(self, lid: str) -> None:
        if lid:
            self.store.mark_title_pending(lid, True)

    def close_life_and_queue_title(self, *, now: float | None = None) -> str:
        lid = self.life.close_life(now=now)
        self.queue_life_title(lid)
        return lid

    def take_conflict_note(self, umo: str) -> str:
        return self._conflict_notes.pop(umo, "")

    def open_segment(self, kind: str, *, who: str = "",
                     now: float | None = None) -> str:
        now = now or time.time()
        prev = self.life.current_life_id()
        lid = self.life.open_life(kind, who=who, now=now)
        if prev and prev != lid:
            self.queue_life_title(prev)
        return lid

    def _mutation_policy(self, decision, *, speaks: bool) -> None:
        if speaks or not getattr(decision, "ok", False):
            return
        if bool(self.get_cfg("director_silent_state_mutation", False)):
            return
        if getattr(decision, "outing", None):
            logger.info("[spectrum·L2] 沉默轮：忽略 outingUpdate（%s）",
                        (decision.outing or {}).get("action"))
            decision.outing = None
        if getattr(decision, "sleep", None):
            logger.info("[spectrum·L2] 沉默轮：忽略 sleepUpdate（%s）",
                        (decision.sleep or {}).get("action"))
            decision.sleep = None

    def active_sessions(self) -> list[str]:
        try:
            return [u for u in self.store.list_umos()
                    if self.store.get_state(u, "sys_core")]
        except Exception:


            logger.exception("[spectrum·L2] 枚举活跃会话失败，退回内存快照")
            return list(self._sys_core.keys())

    def _outing_target_ok(self, tgt: str, umo: str, now: float) -> bool:
        if not tgt:
            logger.warning("[spectrum·L3] 外出目标被拒：空地址")
            return False
        try:
            known = {s["session"] for s in self.store.known_sessions(exclude=None)}
        except Exception:
            logger.exception("[spectrum·L3] 取会话表失败，本次外出信号按拒绝处理")
            return False
        if tgt not in known:
            logger.warning("[spectrum·L3] 外出目标被拒：%s 不是已知会话（疑似现编），主线不动",
                           tgt.replace("default:", ""))
            return False
        if "GroupMessage" in tgt:
            logger.warning("[spectrum·L3] 外出目标被拒：%s 是群聊（同行者是人，不是群），主线不动",
                           tgt.replace("default:", ""))
            return False
        if tgt == umo:
            return True
        try:
            limit = float(self.get_cfg("outing_recent_minutes", 20)) * 60.0
            last = self.store.last_activity_ts(tgt)
        except Exception:
            logger.exception("[spectrum·L3] 取最近活动时间失败，本次外出信号按拒绝处理")
            return False
        if last is None or freeze_mod.elapsed_since(last, now) > limit:
            logger.warning(
                "[spectrum·L3] 外出目标被拒：%s 已静默 %s 分钟（阈值 %.0f），主线不动",
                tgt.replace("default:", ""),
                ("%.0f" % (freeze_mod.elapsed_since(last, now) / 60.0)) if last else "从未",
                limit / 60.0,
            )
            return False
        return True
    @staticmethod
    def _person_of_umo(umo: str) -> str:
        try:
            parts = str(umo or "").split(":")
            if len(parts) >= 3 and "FriendMessage" in parts[1]:
                return f"{parts[0]}:{parts[-1]}"
        except Exception:
            pass
        return ""

    def _remember_person(self, person_id: str, text: str, umo: str) -> None:
        if self.persons is None or not person_id:
            return
        self.persons.remember(person_id, text, origin_umo=umo)

    def _absorb_exchange_to_persons(self, umo: str, now: float) -> int:
        if self.persons is None:
            return 0
        try:
            rows = self.store.recent_exchange(umo, 8)
            aliases = self.store.all_person_aliases()
        except Exception:


            logger.warning("[spectrum·群像] 往来积累读取失败，本轮跳过", exc_info=True)
            return 0
        if not rows:
            return 0


        n = 0
        for r in rows:


            if r["kind"] != "received":
                continue
            text = (r["text"] or "").strip()

            meta = self.store.parse_meta(r["meta"])
            sender = str(meta.get("sender") or "").strip()
            if not self._worth_remembering(text):
                continue


            from .person import make_person_id
            platform = umo.split(":", 1)[0]
            sender_id = str(meta.get("sender_id") or "").strip()
            if sender_id:
                pid = make_person_id(platform, sender_id)
                if self.store.get_person(pid) is None:
                    continue
            else:

                pid = self._pid_by_name(sender, [(alias, pid) for alias, pid in aliases
                                                if pid.startswith(platform + ":")])
            if not pid:
                continue
            if self.persons.remember(pid, text, origin_umo=umo) > 0:
                n += 1

        if n:
            logger.info("[spectrum·群像] 往来自动积累 %d 条进人物档案", n)
        return n

    @staticmethod
    def _worth_remembering(text: str) -> bool:
        if not text or len(text) < 8:
            return False

        BORING = ("哈哈", "呵呵", "好的", "好吧", "嗯嗯", "哈哈哈", "在吗", "早",
                  "晚安", "谢谢", "没事", "收到", "行吧")
        t = text.strip()
        if any(t == b or (len(t) <= 6 and b in t) for b in BORING):
            return False

        if not any(c.isalnum() for c in t):
            return False
        return True

    def _pid_by_name(self, name: str, aliases: list[tuple[str, str]]) -> str:
        name = (name or "").strip()
        if not name:
            return ""
        matches = {pid for alias, pid in aliases if alias == name}
        return next(iter(matches)) if len(matches) == 1 else ""

    def _absorb_facts_to_persons(self, umo: str, facts: list[str],
                                 evidence_text: str = "") -> int:
        if self.persons is None or not facts:
            return 0
        try:
            from .person import unambiguous_aliases
            aliases = unambiguous_aliases(self.store.all_person_aliases(), umo.split(":", 1)[0])
        except Exception:
            return 0
        if not aliases:
            return 0

        from .life import find_mentioned_people_scored
        n = 0
        for text in facts:
            text = (text or "").strip()
            if not text:
                continue


            if evidence_text:
                ok, cov = _memory.evidence_grounded(text, evidence_text)
                if not ok:
                    logger.info(
                        "[spectrum·群像] 档案证据不足，丢弃（字面覆盖率 %.2f）：%.60s",
                        cov, text)
                    continue
            try:
                scored = find_mentioned_people_scored(text, aliases)
            except Exception:
                continue
            pids = [p for p, conf in scored if conf == 2]
            if not pids:
                continue

            if self.persons.remember(pids[0], text, origin_umo=umo) > 0:
                n += 1
        if n:
            logger.info("[spectrum·群像] facts 汇入人物档案 %d 条", n)
        return n

    def _remember_requested_commitment(self, umo: str, rc: dict) -> None:
        due = rc.get("not_before")
        due_txt = ""
        if due:
            try:
                due_txt = _dt.fromtimestamp(due).strftime("%Y-%m-%d")
            except Exception:
                due_txt = ""
        note = f"{_memory.PROMISE_PREFIX}我让{rc.get('who', '对方')}答应了我：{rc['summary']}"
        if due_txt:
            note += f"（{due_txt}前）"
        _ev_items = self._exchange_evidence(umo)[1]
        _src = _memory.evidence_entry_ids(note, _ev_items) if _ev_items else []
        _rid = rc.get("related_id") or 0
        if _memory.refresh_promise_memory(self.store, umo, _rid, note, _src):
            logger.info("[spectrum·L3+] 记下反向承诺请求(%s): %s（同一件约定，原地刷新 #%s）",
                        rc.get('kind'), rc['summary'], _rid)
            return
        _act = self.store.upsert_factual_memory(umo, note, source_entry_ids=_src)
        logger.info("[spectrum·L3+] 记下反向承诺请求(%s): %s%s",
                    rc.get('kind'), rc['summary'],
                    "（已有，原地刷新）" if _act == "refresh" else "")

    def _absorb_memory_updates(self, umo: str, items: list,
                               evidence_text: str = "",
                               evidence_lines: "list[tuple[int, str]] | None" = None,
                               accepted: "list[str] | None" = None,
                               tag: str = "压缩") -> int:
        n = 0
        lines = list(evidence_lines or [])
        for raw in items or []:
            t = str(raw or "").strip()
            if not t:
                continue
            if evidence_text:
                ok, cover = _memory.evidence_grounded(t, evidence_text)
                if not ok:
                    logger.info(
                        "[spectrum·L3+] [%s] 核心认知证据不足，丢弃（字面覆盖率 %.2f）：%.60s",
                        tag, cover, t)
                    continue
            try:


                src = _memory.evidence_entry_ids(t, lines) if lines else []
                act = self.store.upsert_factual_memory(umo, t, source_entry_ids=src)
                if not act:
                    continue
                n += 1
                if accepted is not None:
                    accepted.append(t)
                if act == "refresh":
                    logger.info(
                        "[spectrum·L3+] [%s] 已记过的核心认知，原地刷新（来源 %s）：%.60s",
                        tag, ",".join(str(i) for i in src) or "无", t)
                else:
                    logger.info("[spectrum·L3+] [%s] 记下核心认知（来源 %s）: %s",
                                tag, ",".join(str(i) for i in src) or "无", t)
            except Exception:

                logger.exception("[spectrum·L3+] 核心认知入库失败: %.60s", t)
        return n

    def _exchange_evidence(self, umo: str, turns: int = 12
                           ) -> "tuple[str, list[tuple[int, str]]]":
        try:
            rows = self.store.recent_exchange(umo, turns)
        except Exception:
            logger.warning("[spectrum·L3+] 往来证据读取失败，本轮不做证据校验", exc_info=True)
            return "", []
        lines, items = [], []
        for r in rows:
            sender = str(self.store.parse_meta(r["meta"]).get("sender") or "").strip()
            line = (f"{sender or '对方'}：{r['text']}" if r["kind"] == "received"
                    else f"我：{r['text']}")
            lines.append(line)
            try:
                items.append((int(r["id"]), line))
            except (IndexError, KeyError, TypeError, ValueError):
                pass


        try:
            from . import audience as _aud
            for r in reversed(self.store.observed_rows(limit=turns)):
                if not _aud.admit(_aud.parse(r["audience"]), umo):
                    continue
                line = f"我亲眼看见：{r['text']}"
                lines.append(line)
                try:
                    items.append((int(r["id"]), line))
                except (IndexError, KeyError, TypeError, ValueError):
                    pass
        except Exception:
            logger.warning("[spectrum·L3+] 看见的事读取失败，本轮证据里不含它", exc_info=True)
        return "\n".join(reversed(lines)), items

    def _person_id_of(self, umo: str, is_group: bool, peer_name: str | None) -> str:
        if self.persons is None or not self.persons.enabled():
            return ""
        if is_group:
            return ""
        pid = self._person_of_umo(umo)
        return pid if pid and self.store.get_person(pid) else ""

    def _person_block_for(self, umo: str, is_group: bool,
                          peer_name: str | None,
                          state_text: str | None = None) -> str:
        if self.persons is None:
            return ""
        pid = self._person_id_of(umo, is_group, peer_name)
        if not pid:
            return ""


        _q = self.life.get_state_text() if state_text is None else state_text
        _sess = self.store.get_continuity_struct(umo)
        _q = " ".join([_q] + list(_sess.get("facts") or [])[:3])
        dossier = self.persons.dossier(pid, umo, query=_q or "")
        return build_person_block(
            dossier,
            peer_name=peer_name,
            is_group=is_group,
            group_name=self.store.get_state(umo, "group_name"),
        )


    def set_tool_specs(self, umo: str, specs: list[dict]) -> None:
        out = []
        for s in specs or []:
            if not isinstance(s, dict):
                continue
            n = str(s.get("name") or "")
            if not n:
                continue
            out.append({
                "name": n,
                "description": s.get("description") or "",
                "parameters": s.get("parameters") or {},
            })
        self._tool_names[umo] = out


    def set_sys_core(self, umo: str, core: str) -> None:
        self._sys_core[umo] = core


        self.store.set_state(umo, "sys_core", core)

    async def current_sys_core(self, umo: str) -> str:
        core = self._sys_core.get(umo) or self.store.get_state(umo, "sys_core") or ""
        if not core:
            return ""
        cm = self.context.conversation_manager
        cid = await cm.get_curr_conversation_id(umo)
        conversation = await cm.get_conversation(umo, cid) if cid else None
        pm = self.context.persona_manager
        pid, persona, _, _ = await pm.resolve_selected_persona(
            umo=umo,
            conversation_persona_id=conversation.persona_id if conversation else None,
            platform_name=umo.split(":", 1)[0],
        )
        if persona and pid != "default":
            persona_text = (await pm.get_persona(pid)).system_prompt
        else:
            persona_text = persona["prompt"] if persona else ""
        marker = "═══ 以下是这个人的设定与说话方式 ═══"
        contract, separator, _ = core.partition(marker)
        if not separator:
            raise ValueError("sys_core 缺少合约与人设分隔标记")
        contract = build_contract(
            bool(self.get_cfg("possession_enabled", True)), prefill=False,
            life_style=str(self.get_cfg("life_style", "") or ""))
        refreshed = f"{contract.rstrip()}\n\n{marker}\n\n{persona_text}"
        if refreshed != core:
            self.set_sys_core(umo, refreshed)
        return refreshed


    def record_shared_events(self, umo: str, decision, *, basis: str,
                             source_entry_id: int, now: float,
                             delivery_receipts: dict[str, int] | None = None) -> None:
        for item in decision.shared_events:
            if item["basis"] != basis:
                continue
            event_umo, event_entry = umo, source_entry_id
            if basis == "received":
                event_entry = item["source_entry_id"]
                source = self.store._db.execute(
                    "SELECT id FROM entries WHERE id=? AND umo=? AND kind='received'",
                    (event_entry, umo),
                ).fetchone()
                if source is None:
                    logger.warning("[spectrum·认知] 引用的收到消息不属于来源会话：%s", event_entry)
                    continue
            related_id = item.get("related_id") or item.get("relatedId")
            if basis == "reply" and related_id:
                prior = self.store.shared_life_event(related_id)
                if prior is not None and prior["source_umo"] != umo:
                    receipt = (delivery_receipts or {}).get(prior["source_umo"])
                    if receipt is not None:
                        event_umo, event_entry = prior["source_umo"], receipt
            try:
                self.store.record_shared_life_event(
                    item, source_umo=event_umo, source_entry_id=event_entry,
                    life_id=self.life.current_life_id(), ts=now)
            except ValueError as exc:
                logger.warning("[spectrum·生活事件] 未执行模型提出的事件更新：%s", exc)

    _SENT_CLAIM = re.compile(
        r"按了发送|发完(?:了)?|发出去了|(?:刚|已经|早就|确实).{0,8}"
        r"(?:发|通知|告诉|说)(?:给|过|了)?|(?:发给|通知|告诉).{1,18}(?:了|过)"
        r"|给.{1,18}发了|发了.{0,18}(?:消息|改期)"
    )
    _CHANGE_NOTICE = re.compile(r"改期|不去了|去不了|去不成|无法赴约|不能赴约|另约|改天")

    def _unverified_go_notice(self, text: str, hint: str = "",
                              related_ids=()):
        if not self._SENT_CLAIM.search(text):
            return None
        rows = self.store.shared_life_events(self.life.current_life_id(), limit=64)
        for prior in rows:
            if prior["kind"] != "commitment" or prior["movement"] != "go":
                continue
            peer = (self.store.get_person_name(self._person_of_umo(prior["source_umo"]))
                    or "对方")
            if (peer not in text + hint and prior["id"] not in related_ids
                    and "改期" not in text):
                continue
            if any(self._CHANGE_NOTICE.search(row["text"])
                   for row in self.store.cross_send_receipts(
                       prior["source_umo"], prior["ts"])):
                continue
            return prior
        return None

    def _strip_unverified_state(self, text: str) -> str:
        return " ".join(
            clause for clause in re.split(r"(?<=[。；;])\s*", text or "")
            if clause and self._unverified_go_notice(clause) is None
        ).strip()

    def reconcile_cross_send_claim(self, umo: str, decision, user_text: str) -> str:
        output = (decision.script or "") + "\n" + (decision.content or "")
        related_ids = {
            item.get("related_id") or item.get("relatedId")
            for item in decision.shared_events
        }
        prior = self._unverified_go_notice(output, user_text, related_ids)
        if prior is not None and prior["source_umo"] != umo:
            target = prior["source_umo"]
            peer = (self.store.get_person_name(self._person_of_umo(target))
                    or "对方")
            decision.script = (decision.script or "") + (
                f"\n\n我重新核对了与{peer}的会话：改期消息还没有送达。"
                "我刚才把想做的事记成了已经做过，这是错的。")
            if self._SENT_CLAIM.search(decision.content or ""):
                decision.content = (
                    f"还没有。我刚才说已经通知{peer}，是我记错了；"
                    "改期消息还没送到他那里。")
            decision.shared_events = [
                item for item in decision.shared_events
                if not (item["kind"] == "physical"
                        and self._SENT_CLAIM.search(item["text"]))
            ]
            if self._SENT_CLAIM.search(decision.continuity_update or ""):
                decision.continuity_update = ""
            logger.warning("[spectrum·投递核对] %s 的改期未送达，已修正本轮叙述", peer)
            return target
        return ""

    def shared_event_context(self, umo: str = "", query: str = "") -> str:
        from .shared_life import render_events
        from .experience import render_self_state, render_cross_recall

        rows = self.store.shared_life_events(self.life.current_life_id(), query=query)

        def name(umo: str) -> str:
            person_id = self._person_of_umo(umo)
            if person_id:
                return self.store.get_person_name(person_id) or ""
            return self.store.get_state(umo, "group_name", "") or ""

        tz = str(self.get_cfg('timezone', 'Asia/Shanghai'))
        rendered = render_events(rows, name, chain=self.store.shared_event_chain, timezone=tz)
        if umo:
            own = render_self_state(self.store, self.life, umo)
            if own:
                rendered += '\n\n' + own
            if self.get_cfg('cross_ref_enabled', True):
                recalled = self.store.recall_cross_session(
                    umo, query, limit=int(self.get_cfg('memory_origin_segments', 2)),
                    budget=int(self.get_cfg('memory_origin_segment_chars', 1500)))
                if recalled:
                    rendered += '\n\n' + render_cross_recall(self.store, recalled, tz)
            received = [r for r in self.store.recent_exchange(umo, 4) if r['kind'] == 'received']
            if received:
                rendered += '\n\n【我最近收到的消息来源编号】\n' + '\n'.join(
                    f"[原话#{r['id']}] {r['text']}" for r in reversed(received[:2]))
        stays = [r for r in rows if r["kind"] == "commitment"
                 and r["movement"] == "stay"]
        departures = [
            {"summary": r["text"], "start": r["ts"]}
            for r in rows if r["kind"] == "commitment" and r["movement"] == "go"
        ]
        departures.extend(p for p in self.life.active_plans()
                          if p.get("movement") == "go")
        conflicts = [
            (r, p) for r in stays for p in departures
            if r["valid_until"] is None
            or float(p["start"]) <= float(r["valid_until"])
        ]
        if conflicts:
            r, p = conflicts[0]
            rendered += (
                "\n【我尚未处理的冲突】"
                f"我答应过「{r['text']}」，又约了「{p['summary']}」。"
                "前一个承诺还在生效，两件事不能同时兑现。"
                "在旧约明确解除或外出安排改期前，我不能确认明天能出门；"
                "我需要把这个冲突说清楚。")
            go_promises = [e for e in rows if e["kind"] == "commitment"
                           and e["movement"] == "go"]
            if go_promises:
                peer = name(go_promises[0]["source_umo"]) or "对方"
                rendered += (
                    f"\n我对{peer}的赴约承诺还在原会话中生效。"
                    "我在别的会话里说要改期，不等于已经把改期消息发给了他。")
        return rendered

    def stay_blocks_departure(self, when: float | None = None) -> bool:
        when = time.time() if when is None else when
        return any(
            row["kind"] == "commitment" and row["movement"] == "stay"
            and (row["valid_until"] is None or when <= row["valid_until"])
            for row in self.store.shared_life_events(
                self.life.current_life_id(), limit=128)
        )

    @staticmethod
    def communication_payload(decision) -> dict:
        """保存延迟发送后仍需确认的承诺、变更和来源标识。"""
        return {"follow_up": decision.follow_up, "intent_updates": decision.intent_updates,
                "requested_commitment": decision.requested_commitment,
                "turn_id": decision.turn_id, "source_actor": decision.source_actor}

    def apply_intent_updates(self, umo: str, decision, *, delivered: bool,
                             delivery_receipts: dict | None = None) -> None:
        for update in decision.intent_updates:
            row = self.store._db.execute("SELECT * FROM intents WHERE id=?", (update["id"],)).fetchone()
            if row is None or row["status"] in {"cancelled", "fulfilled", "expired"}:
                continue
            target_sent = (delivered and row["umo"] == umo) or row["umo"] in (delivery_receipts or {})
            outcome = update["outcome"]
            if row["umo"] != umo and not target_sent:
                continue
            if outcome != "cancelled" and not target_sent:
                continue
            if outcome == "rescheduled" and update.get("not_before"):
                self.store.reschedule_intent(update["id"], update["not_before"])
            else:
                self.store.set_intent_status(update["id"], "fulfilled" if outcome == "fulfilled" else "cancelled")

    def confirm_communication(self, umo: str, decision, *, delivery_receipts: dict | None = None) -> None:
        """按已确认沟通登记后续承诺和变更，并避免重复提交。"""
        if decision.communication_committed:
            return
        if decision.follow_up:
            if self.get_cfg("l2_commitments_enabled", False):
                iid = self.store.add_intent(
                    umo, "follow-up", decision.follow_up["summary"],
                    decision.follow_up["not_before"],
                    {"kind": decision.follow_up["kind"],
                     "trigger": decision.follow_up.get("trigger", "time"),
                     "movement": decision.follow_up.get("movement", ""),
                     "related_id": decision.follow_up.get("related_id"),
                     "expires_at": decision.follow_up.get("expires_at")},
                )
                logger.info("[spectrum·L2] 记下承诺 #%d（%s）：%s 到期",
                            iid, decision.follow_up["kind"], decision.follow_up["summary"][:40])
            else:
                logger.info("[spectrum·L2] 检测到承诺，但 l2_commitments_enabled=false，未落库")
        self.apply_intent_updates(umo, decision, delivered=True, delivery_receipts=delivery_receipts)

        if decision.requested_commitment:
            self._remember_requested_commitment(umo, decision.requested_commitment)
        decision.communication_committed = True

    def confirm_reply(self, umo: str, decision, content: str, *, complete: bool = True,
                      delivery_receipts: dict | None = None) -> int:
        """写入实际送达记录；完整回复才确认沟通与回复来源的共同事件。"""
        now = time.time()
        entry_id = self.store.add_entry(
            umo, "delivered", content, ts=now, life_id=self.life.current_life_id(),
            meta={"turn_id": decision.turn_id, "actor": decision.source_actor,
                  "complete_reply": complete})
        if complete:
            self.confirm_communication(umo, decision, delivery_receipts=delivery_receipts)
            self.record_shared_events(umo, decision, basis="reply", source_entry_id=entry_id,
                                      now=now, delivery_receipts=delivery_receipts)
        self.store.set_last_write_ts(umo, now)
        return entry_id

    def handle_decision(self, umo: str, decision, person_id: str = "", *,
                        communication_confirmed: bool = True) -> None:
        """执行有效且未处理的生活决策，区分沟通确认与生活状态更新。"""
        if freeze_mod.is_frozen() or not decision.ok:
            return
        if decision.decision_id in self._handled:
            logger.warning("[spectrum·L2] 同一条 decision 被重复处理，已跳过（幂等保护）")
            return


        now = time.time()
        self._handled[decision.decision_id] = None

        if len(self._handled) > 512:
            self._handled.pop(next(iter(self._handled)))
        if communication_confirmed:
            self.confirm_communication(umo, decision)
        else:
            self.apply_intent_updates(umo, decision, delivered=False)


        if decision.outing:
            act = decision.outing.get("action")
            if act == "out":
                tgt = (decision.outing.get("session") or "").strip()
                if self.stay_blocks_departure(now):
                    logger.warning("[spectrum·生活事件] 留守承诺未解除，外出状态未执行")
                elif not tgt:

                    logger.info("[spectrum·L3] 收到外出信号但没给 session，忽略")
                elif not self._outing_target_ok(tgt, umo, now):


                    pass
                else:


                    _who = self.store.get_person_name(
                        self._person_of_umo(tgt)) or tgt


                    _old = self.life.current_life_id()
                    self.life.set_owner(session=tgt, who=_who, now=now)
                    if _old and _old != self.life.current_life_id():
                        self.queue_life_title(_old)
                    logger.info("[spectrum·L3] 主线移交：跟 %s 外出，owner=%s",
                                _who, tgt.replace("default:", ""))
            elif act == "back":
                _ow = self.life.get_owner()
                _old = self.life.current_life_id()
                self.life.set_owner(session="", now=now)
                if _old and _old != self.life.current_life_id():
                    self.queue_life_title(_old)
                logger.info("[spectrum·L3] 主线归还给她自己（原 owner=%s）",
                            str(_ow.get("session", "")).replace("default:", ""))


        if decision.continuity_update:

            self.store.add_volatile_change(umo, decision.continuity_update)
            logger.info("[spectrum·L3] 现状变化已记录(Buffering): %s", decision.continuity_update)

        if decision.timeline_plan:


            steps = decision.timeline_plan.get("steps") or []


            try:
                _al = self.store.all_person_aliases()
            except Exception:
                _al = []
            for st in steps:
                if not isinstance(st, dict):
                    continue
                from .life import _parse_iso
                due = _parse_iso(str(st.get("dueAt") or "")) or now
                if st.get("movement") == "go" and self.stay_blocks_departure(due):
                    logger.warning("[spectrum·生活事件] 留守承诺未解除，外出计划未提交")
                    continue
                res = self.life.submit_plan(st, source=umo, aliases=_al, now=now)
                sup = res.get("superseded") or []
                if sup:
                    note = self.life.render_conflict(res)
                    if note:


                        self.store.add_volatile_change(umo, note)
                        self._conflict_notes[umo] = note
                    logger.info(
                        "[spectrum·L3] 行程冲突：新「%s」顶掉 %d 条旧安排",
                        st.get("summary", ""), len(sup))
            logger.info("[spectrum·L3] 计划更新: %d 个步骤（全局主线）", len(steps))

        if decision.willingness_delta is not None:
            old_w = self.store.get_willingness(umo)
            new_w = max(0.0, min(1.0, old_w + decision.willingness_delta))
            self.store.set_willingness(umo, new_w)
            logger.info("[spectrum·L3] 意愿分变更: %.2f -> %.2f (delta=%.2f)", old_w, new_w, decision.willingness_delta)

        if decision.memory_updates:


            evidence_text, evidence_items = self._exchange_evidence(umo)
            accepted: list[str] = []
            self._absorb_memory_updates(
                umo, decision.memory_updates,
                evidence_text=evidence_text, evidence_lines=evidence_items,
                accepted=accepted, tag="主合约")


            for m in accepted:
                self._remember_person(person_id, m, umo)


        if decision.sleep and self.get_cfg("l2_sleep_enabled", False):
            if decision.sleep["action"] == "sleep":
                until = decision.sleep.get("until")
                if until is None:

                    lo = max(1, int(self.get_cfg("sleep_min_minutes", 60)))
                    hi = max(lo, int(self.get_cfg("sleep_max_minutes", 600)))
                    import random
                    until = time.time() + random.randint(lo, hi) * 60
                self.store.set_sleep_asleep(until)


                _ow = self.life.get_owner()
                if _ow:
                    self.life.clear_owner_only()
                    logger.info("[spectrum·睡眠] 入睡，主线归还（原为跟「%s」外出）",
                                _ow.get("with") or "?")


                _lid = self.life.current_life_id()
                self.life.close_life(now=time.time())
                if _lid:
                    self.queue_life_title(_lid)
            elif decision.sleep["action"] == "wake":
                self.store.set_sleep_awake()


                self.open_segment("home", now=time.time())
                logger.info("[spectrum·睡眠] 在 %s 醒来（全局）", umo)


    _LIFE_TITLE_PROMPT = (
        "我就是这段生活的主角。我会根据我真实经历过的事情，"
        "为这段生活留下一个以后能认出的名字。\n\n"
        "【输出】只输出一段 JSON，不要其他内容：\n"
        "{\n"
        '"title": "8~16字的日记式标题",\n'
        '"summary": "40字以内的一句话概述"\n'
        "}\n\n"
        "【规则】\n"
        '1. "title" 要具体、有辨识度，优先包含实际出现的地点、人物或事件。'
        "禁止“平常的一天”这类泛化标题。\n"
        "2. 只根据提供的材料写，不得编造、推测或补充任何未发生的情节。\n"
        "3. 我始终使用第一人称记录，“我”就是我自己。\n"
        '4. "summary" 只概括实际发生的核心经历，不写空泛评价。\n'
        "5. 严格遵守字数限制和 JSON 格式，不添加任何额外文字。"
    )


    OPEN_TITLE_AFTER_MIN = 25

    async def _maybe_title_open_life(self, now: float) -> None:
        lid = self.life.current_life_id()
        if not lid:
            return
        row = self.store.get_life(lid)
        if row is None or row["ended_at"] is not None:
            return

        if (row["title"] or "").strip() or row["title_pending"]:
            return
        if freeze_mod.elapsed_since(float(row["started_at"] or 0), now) < self.OPEN_TITLE_AFTER_MIN * 60:
            return

        try:
            if len(self.store.entries_of_life(lid, limit=5)) < 3:
                return
        except Exception:
            return
        self.queue_life_title(lid)
        logger.info("[spectrum·生活线] 段 %s 开了 %d 分钟仍在进行，先起个名",
                    lid, int(freeze_mod.elapsed_since(float(row["started_at"] or 0), now) / 60))

    async def _fill_life_title(self, lid: str, *, now: float) -> bool:
        if not lid:
            return ""

        epoch = freeze_mod.checkpoint()
        if not freeze_mod.may_commit(epoch):
            return False
        title, summary = "", ""
        try:
            rows = self.store.entries_of_life(lid, limit=60)
            if len(rows) >= 3:
                lines = []
                for r in rows:
                    sender = str(self.store.parse_meta(r["meta"]).get("sender") or "").strip()
                    if r["kind"] == "received":
                        lines.append(f"{sender or '对方'}：{r['text']}")
                    elif r["kind"] == "delivered":
                        lines.append(f"我：{r['text']}")
                    elif r["kind"] == "observed":
                        lines.append(f"（我看见）{r['text']}")
                    else:
                        lines.append(f"（我心里）{r['text']}")
                payload = "\n".join(lines)[-4000:]
                provider = await self.context.get_using_provider_async()
                model = self.get_cfg("advance_model_override", None) or None
                resp = await freeze_mod.call_model(provider,
                    system_prompt=self._LIFE_TITLE_PROMPT,
                    prompt=payload,
                    model=model if isinstance(model, str) else None,
                )
                if not freeze_mod.may_commit(epoch):
                    return False
                raw = (getattr(resp, "completion_text", "") or "").strip()
                i, j = raw.find("{"), raw.rfind("}")
                if i != -1 and j > i:
                    obj = json.loads(raw[i:j + 1])
                    title = str(obj.get("title") or "").strip()[:40]
                    summary = str(obj.get("summary") or "").strip()[:120]
        except Exception as error:
            freeze_mod.record_failure("life-title", error)


            logger.warning("[spectrum·生活线] 生成段标题失败：%s", lid, exc_info=True)
            return False

        if not title:
            return False


        self.store.set_life_title(lid, title=title, summary=summary)
        logger.info("[spectrum·生活线] 段 %s：「%s」", lid, title)
        return True


    async def tick(self) -> None:
        """冻结时不推进，否则按导演开关选择对应后台调度路径。"""
        if freeze_mod.is_frozen():
            return
        if not self.get_cfg("director_enabled", False):
            return await self._tick_legacy()
        await self.director.tick()


    async def _tick_legacy(self) -> None:
        epoch = freeze_mod.checkpoint()
        now = time.time()


        spoke: set[str] = set()
        await self._flush_delayed(now, spoke)
        if not freeze_mod.may_commit(epoch):
            return
        await self._flush_due_intents(now, spoke)
        if not freeze_mod.may_commit(epoch):
            return
        await self._maybe_advance(now, spoke)
        if not freeze_mod.may_commit(epoch):
            return
        await self._maybe_wake(now, spoke)
        if not freeze_mod.may_commit(epoch):
            return


        await self._maybe_title_open_life(now)
        if not freeze_mod.may_commit(epoch):
            return


        pending = self.store.pending_title_lives(limit=5)
        if pending:
            row = pending[0]
            _lid = row["life_id"]
            _ok = await self._fill_life_title(_lid, now=now)
            if not freeze_mod.may_commit(epoch):
                return
            if not _ok:


                self._title_fails[_lid] = self._title_fails.get(_lid, 0) + 1
                if self._title_fails[_lid] >= 3:
                    logger.warning("[spectrum·生活线] 段 %s 起名连败 3 次，放弃",
                                   _lid)
                    self.store.expire_life_title(_lid)
                    self._title_fails.pop(_lid, None)


        await self._maybe_compress_continuity(now)
        if not freeze_mod.may_commit(epoch):
            return


        await self._decay_willingness(now)

    def absorb_relation(self, umo: str, person_id: str, decision=None) -> None:
        if not person_id:
            return
        try:
            sig = _peek_prism_signals(umo) or {}
            result = self.store.apply_relationship(person_id, umo, sig)
            if result is None:
                return
            self.store.record_relation_snapshot(time.time(), [{
                "umo": umo, "person_id": person_id,
                "affinity": result["affinity"], "trust": result["trust"],
                "valence": float(sig.get("valence") or 0.0),
            }])
            if result["liking_signal"] or result["trust_signal"]:
                logger.info(
                    "[spectrum·关系v3] %s 好感信号%+.3f 信任信号%+.3f → 好感%.4f 信任%.4f 融合与依据%s",
                    person_id[-10:], result["liking_signal"], result["trust_signal"],
                    result["affinity"], result["trust"],
                    json.dumps({"fusion": result["fusion"], "evidence": sig.get("evidence", {})}, ensure_ascii=False))
        except Exception:
            self._relation_fail = getattr(self, "_relation_fail", 0) + 1
            if self._relation_fail in (1, 10, 100, 1000):
                logger.exception("[spectrum·关系] 更新失败（第 %d 次）", self._relation_fail)

    def apply_emotion_to_willingness(self, umo: str) -> None:
        val = _peek_prism_emotion(umo)
        if val is None:
            return


        delta = float(val) * 0.03
        if abs(delta) < 0.002:
            return
        old = self.store.get_willingness(umo)
        new = max(0.0, min(1.0, old + delta))
        self.store.set_willingness(umo, new)
        logger.info("[spectrum·意愿] %s 情绪净值 %+.3f → %.3f",
                    umo[-14:], val, new)

    async def _decay_willingness(self, now: float) -> None:
        last_decay = float(self.store.get_state("global", "last_willingness_decay", "0"))
        if freeze_mod.elapsed_since(last_decay, now) < 7200:
            return
        self.store.set_state("global", "last_willingness_decay", repr(now))

        for umo in self.store.list_umos():
            w = self.store.get_willingness(umo)

            diff = w - 0.5
            if abs(diff) < 0.01:
                continue

            step = max(0.02, abs(diff) * 0.1)
            if diff > 0:
                self.store.set_willingness(umo, max(0.5, w - step))
            else:
                self.store.set_willingness(umo, min(0.5, w + step))

    async def _maybe_compress_continuity(self, now: float) -> None:
        time_interval = 3600
        min_gap = 300
        length_threshold = 400

        umo = self._pick_compress_target(now, min_gap=min_gap,
                                         time_interval=time_interval,
                                         length_threshold=length_threshold)
        if not umo:
            return
        last = self.store.last_summary_ts(umo)
        volatile = self.store.get_volatile_changes(umo)
        scripts = self.store.get_scripts_since(umo, last)
        await self._compress_continuity(umo, scripts, volatile, now)

    def _pick_compress_target(self, now: float, *,
                              min_gap: float = 300,
                              time_interval: float = 3600,
                              length_threshold: int = 400) -> str:
        for umo in self.active_sessions():
            last = self.store.last_summary_ts(umo)

            if last and freeze_mod.elapsed_since(last, now) < min_gap:
                continue
            volatile = self.store.get_volatile_changes(umo)
            scripts = self.store.get_scripts_since(umo, last)

            fresh = sum(len(v) for v in volatile) + sum(len(s) for s in scripts)
            should = (freeze_mod.elapsed_since(last, now) >= time_interval and fresh > 0) \
                     or (fresh > length_threshold)
            if not should:
                continue
            logger.info("[spectrum·L3] 该压缩了 (理由: %s, 新内容 %d 字, 缓冲区: %d条): %s",
                        "超时" if freeze_mod.elapsed_since(last, now) >= time_interval else "超长",
                        fresh, len(volatile), umo)
            return umo
        return ""

    async def _maybe_compress_continuity_for(self, umo: str, *,
                                            prompt: str | None = None,
                                            with_items: bool = False) -> None:
        last = self.store.last_summary_ts(umo)
        volatile = self.store.get_volatile_changes(umo)
        scripts = self.store.get_scripts_since(umo, last)
        await self._compress_continuity(umo, scripts, volatile, time.time(),
                                        prompt=prompt, with_items=with_items)

    def idle_minutes(self, umo: str, now: float) -> float | None:
        try:
            ts = self.store.last_activity_ts(umo)
        except Exception:
            logger.exception("[spectrum·L2] 读最后往来时间失败：%s", umo)
            return None
        return None if ts is None else max(0.0, freeze_mod.elapsed_since(float(ts), now) / 60.0)

    async def _compress_continuity(self, umo: str, scripts: list[str], volatile: list[str], now: float,
                                  prompt: str | None = None,
                                  with_items: bool = False) -> None:
        if freeze_mod.is_frozen():
            return
        freeze_epoch = freeze_mod.checkpoint()
        world_version = self.store.world_version()
        prev = self.store.get_continuity_struct(umo)
        prev_for_prompt = dict(prev)
        prev_for_prompt["state"] = self.life.get_state_for(umo) or prev.get("state") or ""
        scripts_text = "\n\n".join(scripts)
        volatile_text = "\n".join(f"- {v}" for v in volatile)


        exchange_text, exchange_items = self._exchange_evidence(umo, 12)

        _prev_parts = []


        _prev_items = (self.life.get_state().get("items")
                       if prompt is not None else None)
        _prev_lines = []
        from . import audience as _aud
        for _it in (_prev_items if isinstance(_prev_items, list) else []):
            if not isinstance(_it, dict):
                continue
            if not _aud.admit(_aud.parse(_it.get("audience")), umo):
                continue
            _t = str(_it.get("text") or "").strip()
            if _t:
                _prev_lines.append(f"[现状 {_state_item_id(_t)}] {_t}")
        if _prev_lines:
            _prev_parts.append("\n".join(_prev_lines))
        elif prev_for_prompt.get("state"):
            _prev_parts.append(str(prev_for_prompt["state"]))


        _ow = self.life.get_owner()
        if _ow and _ow.get("session") and _ow.get("session") != umo:
            _with = _ow.get("with") or "某人"
            if "GroupMessage" in umo:


                _prev_parts.append(
                    "【当前位置状态】\n\n"
                    f"我当前实际位于外部场所，与 \"{_with}\" 同行。\n\n"
                    "以下往来均发生于我处于外部场所期间，通过手机参与当前群聊。"
                    "群里说话的人的身份不得用于推断或覆盖我的实际位置。\n\n"
                    "生成「我此刻的处境」时，我必须保持：\n\n"
                    "- 当前地点：外部场所\n"
                    f"- 当前同行者：\"{_with}\"\n"
                    "- 当前在群里说话的人：不是固定的某一位，随消息变化\n"
                    "- 不得描述为在家、房间、窗边等与当前状态冲突的地点。\n\n"
                    "除非后续剧情明确发生地点变更，否则不得修改上述状态。\n\n"
                    "【人物指代】\n\n"
                    f"\"{_with}\" 与群里说话的人不必然互斥："
                    f"\"{_with}\" 可以是群成员，也可以不是。\n\n"
                    f"- \"{_with}\" 始终指现实中与我同行的人，"
                    f"例如：「我和 \"{_with}\" 在车上」。\n"
                    "- 群里说话的人始终指正在群里发言的那位，例如：「群里那位在问我」。\n"
                    "- 涉及人物行为时，必须明确写出名字或明确的指代，"
                    "不得使用无法确定指向的「他」「她」「那个人」等代词。\n"
                    f"- 不得让群里说话的人承担 \"{_with}\" 的动作，也不得反过来。\n"
                    f"- 「我此刻的处境」中涉及人物时，优先直接使用 \"{_with}\" "
                    "或群里说话者的名字，避免产生指代歧义。"
                )
            else:
                _peer = (self.store.get_person_name(self._person_of_umo(umo))
                         or "对方")
                _prev_parts.append(
                    "【当前位置状态】\n\n"
                    f"我当前实际位于外部场所，与 \"{_with}\" 同行。\n\n"
                    f"以下往来均发生于我处于外部场所期间，通过手机与 \"{_peer}\" 进行。"
                    f"\"{_peer}\" 的身份不得用于推断或覆盖我的实际位置。\n\n"
                    "生成「我此刻的处境」时，我必须保持：\n\n"
                    "- 当前地点：外部场所\n"
                    f"- 当前同行者：\"{_with}\"\n"
                    f"- 当前聊天对象：\"{_peer}\"\n"
                    "- 不得描述为在家、房间、窗边等与当前状态冲突的地点。\n\n"
                    "除非后续剧情明确发生地点变更，否则不得修改上述状态。\n\n"
                    "【人物指代】\n\n"
                    f"\"{_with}\" 与 \"{_peer}\" 是两个独立人物，不得混淆、合并或互换身份。\n\n"
                    f"- \"{_with}\" 始终指现实中与我同行的人，例如：「我和 \"{_with}\" 在车上」。\n"
                    f"- \"{_peer}\" 始终指正在与我进行手机通信的人，例如：「\"{_peer}\" 在手机上问我」。\n"
                    "- 涉及两人的行为时，必须明确写出对应名字，不得使用无法确定指向的「他」「她」「那个人」等代词。\n"
                    f"- 不得让 \"{_peer}\" 承担 \"{_with}\" 的动作，也不得反过来。\n"
                    f"- 「我此刻的处境」中涉及人物时，优先直接使用 \"{_with}\" 或 \"{_peer}\"，避免产生指代歧义。"
                )
        if prev_for_prompt.get("facts"):


            _facts_label = ("【这些是上一轮记下的，不是本轮的往来】"
                            if prompt is not None else "【这些是真发生过的】")
            _prev_parts.append(_facts_label + "\n"
                               + "\n".join(f"· {x}" for x in prev_for_prompt["facts"]))
        if prev_for_prompt.get("views"):
            _prev_parts.append("【这些只是我当时以为的，不一定是真的】\n"
                               + "\n".join(f"· {x}" for x in prev_for_prompt["views"]))
        _prev_text = "\n\n".join(_prev_parts) or "（无）"
        payload = (
            runtime_facts(self.store, str(self.get_cfg("timezone", "Asia/Shanghai"))) + "\n\n"
            f"【原先的现状】\n{_prev_text}\n\n"
            f"【我已记下的生活事件】\n{self.shared_event_context(umo, ' '.join(prev.get('facts') or []) + ' ' + scripts_text) or '（无）'}\n\n"
            f"【最新状态变化记录】\n{volatile_text or '（无）'}\n\n"
            f"【最近真实收发过的往来】\n{exchange_text or '（无）'}\n\n"
            f"【她自己的日记】\n{scripts_text}"
        )


        if prompt is not None:
            _known = self.store.list_core_memories(umo)
            if _known:
                payload += ("\n\n【我已记下的长期认知】\n"
                            + "\n".join(f"[id {i}] {t}" for i, t in _known))

        try:
            provider = await self.context.get_using_provider_async(umo)
            model = self.get_cfg("advance_model_override", None) or None
            resp = await freeze_mod.call_model(provider,


                system_prompt=prompt or CONTINUITY_COMPRESSION_PROMPT,
                prompt=payload,
                model=model if isinstance(model, str) else None,
            )
            if not freeze_mod.may_commit(freeze_epoch):
                return
            raw_out = (getattr(resp, "completion_text", "") or "").strip()
            if not raw_out:
                return
            new = self._parse_continuity(raw_out, prev, scripts, now)
            if new:


                from . import audience as _aud


                _prev_state_items = (self.life.get_state().get("items")
                                     if with_items else None)
                _items, _text = _build_state_items(
                    new, self.life.get_owner(), self.store,
                    prev_items=_prev_state_items) if with_items else ([], "")
                if _items:
                    _items = [it for it in _items
                              if self._unverified_go_notice(
                                  str(it.get("text") or "")) is None]
                    _text = " ".join(str(it["text"]) for it in _items)
                if not _text:
                    _text = self._strip_unverified_state(new.get("state") or "")
                _wrote = self.life.set_state(
                    _text, source=umo, now=now,
                    audience=_aud.for_life_write(self.life.get_owner(), umo),
                    items=_items, expected_version=world_version)
                if _wrote:

                    self._clear_compress_miss()
                elif self.store.world_version() != world_version:

                    logger.info("[spectrum·L3] 压缩期间世界变化，仅保存本会话认知: %s", umo)
                else:

                    logger.info("[spectrum·L3] 压缩未产出有效现状，跳过写入: %s",
                                umo.replace("default:", ""))
                    self._on_compress_miss(umo, now)
                self.store.set_continuity_struct(umo, {
                    "state": prev.get("state") or "",
                    "facts": new.get("facts") or [],
                    "views": new.get("views") or [],
                    "refs": new.get("refs") or [],
                    "at": now,
                })
                self.store.clear_volatile_changes(umo, consumed=volatile)
                self.store.set_last_summary_ts(umo, now)


                self._absorb_facts_to_persons(umo, new.get("facts") or [],
                                              evidence_text=exchange_text)


                _n_mem = self._absorb_memory_updates(
                    umo, new.get("memory_updates") or [],
                    evidence_text=exchange_text, evidence_lines=exchange_items)
                if _n_mem:


                    _vtabs = ("memories",) + (
                        ("entries",) if (
                            self.get_cfg("memory_origin_enabled", False)
                            and self.get_cfg("memory_origin_vector_enabled", False)
                        ) else ())
                    await _vectors.backfill(self.context, self.store,
                                            tables=_vtabs, max_rounds=1)

                self._absorb_exchange_to_persons(umo, now)
                logger.info(
                    "[spectrum·L3] 现状压缩完成（facts=%d views=%d）→ state 进全局主线: %.60s...",
                    len(new.get("facts") or []), len(new.get("views") or []),
                    new.get("state") or "",
                )
        except Exception:
            logger.exception("[spectrum·L3] 现状压缩失败")


    _COMPRESS_MISS_LIMIT = 3

    def _clear_compress_miss(self) -> None:
        from .life import GLOBAL
        self.store.set_state(GLOBAL, "compress_miss_streak", "0")

    def _on_compress_miss(self, umo: str, now: float) -> None:
        from .life import GLOBAL
        n = self.store.bump_counter(GLOBAL, "compress_miss_streak")
        if n < self._COMPRESS_MISS_LIMIT:
            return
        logger.error(
            "[spectrum·L3] 压缩连续 %d 次未产出有效现状 ⇒ **现状停更中** "
            "（她的处境在所有会话里都会停在旧的那一份）。已启用保底现状。"
            "最后一次失败来自 %s", n, umo.replace("default:", ""))
        text = self._strip_unverified_state(self._fallback_state_text(umo))
        if not text:
            return
        from . import audience as _aud
        self.life.set_state(
            text, source=umo, now=now,
            audience=_aud.for_life_write(self.life.get_owner(), umo))
        logger.warning("[spectrum·L3] 已写入保底现状（%d 字，只用真实往来）：%.80s",
                       len(text), text.replace("\n", " "))

    def _fallback_state_text(self, umo: str, turns: int = 6) -> str:
        try:
            rows = self.store.recent_exchange(umo, turns)
        except Exception:
            logger.exception("[spectrum·L3] 保底现状：读往来失败")
            return ""
        if not rows:
            return ""
        placeholder = getattr(self.store, "_PLACEHOLDER_SENDERS", ())

        def _who(row):
            s = str(self.store.parse_meta(row["meta"]).get("sender") or "").strip()
            return "" if s in placeholder else s

        peer = ""
        for r in rows:
            if r["kind"] == "received" and _who(r):
                peer = _who(r)
                break
        lines = [f"· 我最近在和「{peer}」往来。"] if peer else []
        for r in reversed(rows[-4:]):
            who = _who(r) or peer or "对方"
            if r["kind"] == "received":
                lines.append(f"· {who} 对我说：「{r['text']}」")
            else:
                lines.append(f"· 我对 {who} 说：「{r['text']}」")
        return "\n".join(lines)


    _PARSE_KEYS = ("state", "state_items", "facts", "views", "memoryUpdates")

    @staticmethod
    def _parse_continuity(raw_out: str, prev: dict, scripts: list[str],
                          now: float) -> dict:
        obj = None
        i, j = raw_out.find("{"), raw_out.rfind("}")
        if i != -1 and j > i:
            try:
                obj = json.loads(raw_out[i:j + 1])
            except Exception:
                obj = None

        if not isinstance(obj, dict):
            txt = raw_out.strip()


            if txt.startswith("{") or txt.startswith("["):
                logger.warning(
                    "[spectrum·L3] 压缩输出像 JSON 但解析失败（多半是引号没转义），"
                    "**不覆盖现状**，保留旧的。头 120 字：%.120r", txt)
                txt = str(prev.get("state") or "")
            return {
                "state": txt[:600],
                "facts": list(prev.get("facts") or []),
                "views": list(prev.get("views") or []),
                "refs": list(prev.get("refs") or []),

                "memory_updates": [],
                "at": now,
            }

        def _lst(v):
            if isinstance(v, list):
                return [t for t in (_item_text(x) for x in v) if t][:8]
            if isinstance(v, str) and v.strip():
                return [v.strip()]
            return []


        facts = (_lst(obj["facts"]) if isinstance(obj.get("facts"), (list, str))
                 else list(prev.get("facts") or []))
        views = (_lst(obj["views"]) if isinstance(obj.get("views"), (list, str))
                 else list(prev.get("views") or []))
        return {
            "state": str(obj.get("state") or "").strip()[:600],


            "state_items": obj.get("state_items"),
            "facts": facts,
            "views": views,


            "memory_updates": _lst(obj.get("memoryUpdates")),

            "refs": [s[:120] for s in scripts][-5:],
            "at": now,
        }

    def _sleep_muted(self, now: float) -> bool:
        if not self.store.is_asleep():
            return False
        until = self.store.get_sleep().get("until", 0)
        if until and self.store.sleep_due(now):

            return self.get_cfg("l2_sleep_enabled", False) and False
        return True


    async def _maybe_wake(self, now: float, spoke: set[str] | None = None) -> None:
        if not self.get_cfg("l2_sleep_enabled", False):
            return
        if not self.store.is_asleep():
            return
        until = self.store.get_sleep().get("until", 0)
        if not until or not self.store.sleep_due(now):
            return


        umo = self._wake_window()
        if not umo:


            return
        if spoke is not None and umo in spoke:
            return
        self.store.set_sleep_awake()


        self.open_segment("home", now=now)
        logger.info("[spectrum·睡眠] 到点叫醒（在 %s 发声），睡中有 %d 条未读",
                    umo, len(self.store.get_sleep_inbox()))
        await self._fire(umo, "wake", audience_viewer=umo)
        if spoke is not None:
            spoke.add(umo)

    def _wake_window(self) -> str | None:
        _ow = (self.life.get_owner() or {}).get("session")
        if _ow and self.store.get_state(_ow, "sys_core"):
            return _ow


        cands = self.active_sessions()
        if not cands:
            return None
        return max(cands, key=lambda u: self.store.last_activity_ts(u) or 0)


    async def _flush_delayed(self, now: float, spoke: set[str] | None = None) -> None:
        for row in self.store.due_pending(now):
            if freeze_mod.is_frozen():
                return
            umo = row["umo"]

            if self._sleep_muted(now):
                continue
            claimed = self.store.claim_pending(row["id"], now)
            if claimed is None:
                continue
            row = claimed
            try:
                await self._send(umo, row["content"])
            except Exception as error:
                self.store.mark_pending(row["id"], "waiting")
                freeze_mod.record_failure("delivery", error)
                logger.exception("[spectrum·L2] 延迟投递失败")
                continue

            sent_ok = True
            try:
                self.store.mark_pending(row["id"], "sent")
            except Exception:
                sent_ok = False
                logger.exception(
                    "[spectrum·L2·重复风险] 消息已发出但**状态更新失败** —— "
                    "pending #%d 保留发送中状态，需要核对送达结果", row["id"])

            try:
                from .parse import Decision
                decision = Decision(ok=True, content=row["content"],
                                    **row["communication"])
                decision.shared_events = row["events"]
                self.confirm_reply(umo, decision, row["content"])
                self.store.clear_pending_life_events(row["id"])
                self.store.clear_pending_communication(row["id"])
            except Exception:
                logger.exception(
                    "[spectrum·L2·不一致] 消息已发出但**记账失败** —— "
                    "仓里有记录缺口，content=%.60r", row["content"])

            if spoke is not None:
                spoke.add(umo)
            logger.info("[spectrum·L2] 延迟投递 #%d（状态更新%s）",
                        row["id"], "成功" if sent_ok else "失败，待核对送达结果")


    async def _flush_due_intents(self, now: float, spoke: set[str] | None = None) -> None:
        if not self.get_cfg("l2_commitments_enabled", False):
            return
        for row in self.ready_due_intents(now):
            umo = row["umo"]
            if self._sleep_muted(now):
                continue

            if spoke is not None and umo in spoke:
                continue
            last = self.store.last_write_ts(umo)
            if last and freeze_mod.elapsed_since(last, now) < 60:
                continue


            try:
                if not self.store.fire_intent(row["id"]):
                    continue
            except Exception:
                logger.exception("[spectrum·L2] 承诺 #%d 开火标记失败，**本轮不发**"
                                 "（宁可漏一次，不可重复发）", row["id"])
                continue
            logger.info("[spectrum·L2] 承诺 #%d 到期，后台兑现", row["id"])
            await self._fire(umo, "intent-due",
                             due_intent_texts=[(row["id"], row["summary"])],
                             store_intent_id=row["id"],
                             audience_viewer=umo)
            if spoke is not None:
                spoke.add(umo)

    def ready_due_intents(self, now):
        self.store.recover_interrupted_intents(
            now, int(self.get_cfg("director_intent_max_attempts", 3)))
        rows = self.store._db.execute("SELECT * FROM intents WHERE status='waiting' ORDER BY not_before").fetchall()
        return [row for row in rows if intent_ready(self.store, self.life, row, now)]


    async def _maybe_advance(self, now: float, spoke: set[str] | None = None) -> None:
        if not self.get_cfg("l2_advance_enabled", False):
            return
        idle_min = int(self.get_cfg("advance_idle_minutes", 180))
        cool_min = int(self.get_cfg("advance_cool_minutes", 30))


        willingness_threshold = float(self.get_cfg("l3_willingness_threshold", 0.4))


        for umo in self.active_sessions():

            if spoke is not None and umo in spoke:
                continue

            if self._sleep_muted(now):
                continue

            willingness = self.store.get_willingness(umo)
            if willingness < willingness_threshold:

                continue


            idle = self.idle_minutes(umo, now)
            if idle is None:

                continue
            last_a = self._last_advance.get(umo, now)
            if freeze_mod.elapsed_since(last_a, now) / 60 < cool_min:
                continue
            if idle >= idle_min:
                self._last_advance[umo] = now
                logger.info("[spectrum·L2/L3] 独立推进 %s（意愿 %.2f，已安静 %.0f 分钟）",
                            umo, willingness, idle)


                await self._fire(umo, "advance", audience_viewer=umo)
                if spoke is not None:
                    spoke.add(umo)


    async def _fire(self, umo: str, phase: str, due_intent_texts: None = None,
                    store_intent_id: int | None = None,
                    narrative: str = "",
                    phase_block: str | None = None,
                    mutation_policy: "Callable[[Any], None] | None" = None,
                    speak_policy: "Callable[[Any], None] | None" = None,
                    observer: "Callable[[Any], None] | None" = None,
                    history_block: str = "",
                    audience_viewer: str | None = None,
                    _world_refresh: bool = True) -> bool:
        """后台读取世界快照并生成决策；版本变化时至多重新生成一次，发送后另行确认。"""
        if freeze_mod.is_frozen():
            return False
        freeze_epoch = freeze_mod.checkpoint()
        core = await self.current_sys_core(umo)
        if not core:
            return False
        tz_name = str(self.get_cfg("timezone", "Asia/Shanghai"))
        now_dt = clock.now(tz_name)

        last_ts = self.store.last_write_ts(umo)
        from_dt = clock.default_from(
            now_dt,
            __import__("datetime").datetime.fromtimestamp(last_ts, tz=now_dt.tzinfo)
            if last_ts else None,
        )
        interval_text = clock.build_interval(from_dt, now_dt, self.get_cfg("latitude", 30.6))

        due_text = ""
        if due_intent_texts:
            due_text = "\n".join(f"· #{iid}：{txt}" for iid, txt in due_intent_texts)


        _v = audience_viewer
        recall_query = ' '.join(self.store.get_continuity_struct(umo).get('facts') or []) + ' ' + self.life.get_state_text()
        _world = context.build_world_snapshot(
            self.store, self.life, umo, _v, lambda: self.shared_event_context(umo, recall_query))
        _ctx = _world.context
        continuity = _ctx.life_state
        recent_scripts = list(_ctx.scripts)
        if _v is not None:
            logger.info("[spectrum·受众] [AudienceGuard] %s", _ctx.log_fields())
            if not continuity:
                logger.info("[spectrum·受众] 现状未准入，本轮不注入: viewer=%s aud=%s",
                            _v.replace("default:", ""),
                            self.life.get_audience())
            if not recent_scripts:
                logger.info("[spectrum·受众] 内心戏未准入，本轮不注入: viewer=%s",
                            _v.replace("default:", ""))
        volatile = self.store.get_volatile_changes(umo)
        plan = {}
        _bp = _world.plans
        if _bp:
            plan = {"_rendered": _bp}


        _mq = (f"{continuity} {' '.join(volatile)}"
               if self.get_cfg("enable_fts_memory", True) else "")

        _qv = (await _vectors.embed_query(self.context, _mq)
               if self.get_cfg("memory_vector_enabled", True) else None)
        memories = self.store.memories_for_prompt(
            umo, _mq,
            recent_n=int(self.get_cfg("memory_recent_limit", 4)),
            related_n=int(self.get_cfg("memory_related_limit", 8)),
            qvec=_qv,
        )


        group_name = self.store.get_state(umo, "group_name")
        is_group = "GroupMessage" in umo
        peer_name = None if is_group else self.store.last_sender_of(umo)

        peer_hint = peer_name
        _turn = context.new_turn(umo, self._person_id_of(umo, is_group, peer_hint), _world)


        _companion_bg = _world.companion


        origin_segments = self.store.recall_original(umo, _mq, qvec=_qv)


        worldbook_l3: list = []
        worldbook_core_block = ""
        worldbook_person_block = ""
        worldbook_turn_block = ""
        try:
            if self.get_cfg("worldbook_enabled", False):
                _wb_groups = _worldbook.group_by_position(
                    self.store.worldbook_hits("", umo=umo, qvec=_qv))
                worldbook_l3 = _wb_groups["l3"]
                worldbook_core_block = build_worldbook_block(_wb_groups["core"])
                worldbook_person_block = build_worldbook_block(_wb_groups["person"])
                worldbook_turn_block = build_worldbook_block(_wb_groups["turn"])
        except Exception:
            logger.debug("[spectrum·世界书] 后台轮召回失败，本轮不注入", exc_info=True)

        l3_context = build_l3_context_block(
            continuity, recent_scripts, plan, memories, volatile,
            group_name=group_name,
            peer_name=peer_name,
            participants=self.store.recent_senders(umo) if is_group else [],
            companion=_companion_bg,
            origin_segments=origin_segments,
            promise_memories=self.store.list_promise_memories(umo),
            shared_events=_world.shared_events,

            worldbook_rows=worldbook_l3,
        )


        person_block = self._person_block_for(umo, is_group, peer_name,
                                              state_text=_ctx.life_state)


        _specs = self._tool_names.get(umo) or []
        tools_context = build_tools_block(


            _specs, self.store.known_sessions(exclude=None), umo,


            usage_notes=self.store.tool_notes_all(),

            max_tools=int(self.get_cfg("tools_list_max", 12)),
        )

        system = "\n\n".join(
            p
            for p in (
                core,


                worldbook_core_block or None,
                l3_context,
                runtime_facts(self.store, tz_name),
                build_address_identity(umo, peer_name),
                person_block,
                worldbook_person_block or None,
                tools_context,

                (phase_block if phase_block is not None
                 else build_phase_block(phase, due_text)) or None,
                worldbook_turn_block or None,
                interval_text,


                history_block or None,


                build_observed_block(getattr(_ctx, "observed", ())) or None,


                build_director_block(narrative) or None,
            )
            if p
        )
        inbox = []
        if phase == "user-message":


            payload = "后台这一轮：群里刚刚有人说话，我被注意到、或想开口了。"
        elif phase == "wake":

            inbox = self.store.get_sleep_inbox()
            if inbox:
                lines = []
                for it in inbox:
                    who = it.get("from") or "对方"

                    where = _sleep_source(self.store, it.get("umo") or "")
                    lines.append(f"· {who}{where}：{it.get('text','')}")
                inbox_text = "\n".join(lines)
                payload = (
                    f"【我刚睡醒。】我睡着这段时间，手机里攒了这些没看过的消息：\n"
                    f"{inbox_text}\n\n"
                    f"看完想回就挑一两条回；也可以只淡淡应一句'刚醒'，不欠谁回信。"
                )
            else:
                payload = "【我刚睡醒，手机里没有新消息。】像平常一样开始新的一天。"
        else:
            payload = (
                f"【此刻没有新消息，这是后台的一轮生活。】\n"
                f"请完成上面要求的「{phase}」阶段。"
            )

        try:
            provider = await self.context.get_using_provider_async(umo)
            model = self.get_cfg("advance_model_override", None) or None
            resp = await freeze_mod.call_model(provider,
                system_prompt=system, prompt=payload,
                model=model if isinstance(model, str) else None,
            )
        except Exception as error:
            freeze_mod.record_failure("model", error)
            logger.exception("[spectrum·L2] 后台主模型调用失败")
            return False

        if not freeze_mod.may_commit(freeze_epoch):
            return False
        text = (getattr(resp, "completion_text", "") or "").strip()
        if not text:
            logger.warning("[spectrum·L2] 后台输出为空，跳过")
            return False
        decision = parse_decision(text, now_dt)
        decision.turn_id = _turn.turn_id
        decision.world_version = _turn.world.version
        decision.source_actor = _turn.actor

        if not decision.ok:
            logger.error("[spectrum·L2] 后台解析失败(%s)：%.200s", decision.error, text)
            return False

        if self.store.world_version() != _turn.world.version:
            if not _world_refresh:
                return False
            return await self._fire(
                umo, phase, due_intent_texts=due_intent_texts, store_intent_id=store_intent_id,
                narrative=narrative, phase_block=phase_block, mutation_policy=mutation_policy,
                speak_policy=speak_policy, observer=observer, history_block=history_block,
                audience_viewer=audience_viewer, _world_refresh=False)

        with self.store.world_read():
            if self.store.world_version() != _turn.world.version:
                return False


            if observer is not None:
                try:
                    observer(decision)
                except Exception:
                    logger.exception("[spectrum·L2] 后台观测回调异常（不影响本轮）")


            try:
                self.store.set_last_write_ts(umo, now_dt.timestamp())
            except Exception:
                logger.exception("[spectrum·L2] 更新时间锚失败")

            self.record_shared_events(umo, decision, basis="received", source_entry_id=0,
                                      now=now_dt.timestamp())
            if decision.script:
                try:


                    from . import audience as _aud
                    script_eid = self.store.add_entry(
                        umo, "script", decision.script, ts=now_dt.timestamp(),
                        life_id=self.life.ensure_life(),
                        audience=_aud.for_session_write(umo))
                    self.record_shared_events(
                        umo, decision, basis="script", source_entry_id=script_eid,
                        now=now_dt.timestamp())
                except Exception:
                    logger.exception("[spectrum·L2] 内心戏落库失败")


            if speak_policy is not None:
                speak_policy(decision)
            if mutation_policy is not None:
                mutation_policy(decision)
            self._mutation_policy(decision, speaks=bool(decision.delivers))


            try:
                self.handle_decision(
                    umo, decision,
                    person_id=self._person_id_of(umo, "GroupMessage" in umo, peer_hint),
                    communication_confirmed=False,
                )
                if inbox:
                    self.store.acknowledge_sleep_inbox(inbox)
                if store_intent_id is not None:
                    self.store.complete_intent_attempt(store_intent_id)
            except Exception:
                logger.exception("[spectrum·L2] 决策落库失败（发消息这一步不受影响）")

        sent = False
        if decision.mode == "delayed" and decision.send_at is not None:
            self.store.add_pending(
                umo, decision.send_at.timestamp(), decision.content, origin="background-delayed",
                communication=self.communication_payload(decision), events=decision.shared_events)
        elif decision.delivers:
            try:
                await self._send(umo, decision.content)
                logger.info("[spectrum·L2] 后台 %s -> %s", phase, decision.content[:40])
            except Exception as error:
                logger.exception("[spectrum·L2] 后台发送失败")
                freeze_mod.record_failure("delivery", error)
            else:
                sent = True
                try:
                    self.confirm_reply(umo, decision, decision.content)
                except Exception:
                    logger.exception(
                        "[spectrum·L2·不一致] 消息已发出但记账失败，content=%.60r",
                        decision.content)
        else:
            logger.info("[spectrum·L2] 后台 %s：沉默", phase)
        if store_intent_id is not None:

            if not decision.intent_updates:
                logger.info("[spectrum·L2] 承诺 #%d 本轮未确认兑现，保留已尝试状态", store_intent_id)
        return sent

    async def _send(self, umo: str, content: str) -> None:
        from astrbot.core.message.message_event_result import MessageChain
        from astrbot.api.message_components import Plain

        if freeze_mod.is_frozen():
            raise freeze_mod.FrozenSkipped("系统已冻结，未发送消息")
        found = await self.context.send_message(umo, MessageChain(chain=[Plain(content)]))
        if found is False:
            raise RuntimeError(f"找不到发送平台，消息未送达: {umo}")

def _sleep_source(store, umo: str) -> str:
    if not umo or "GroupMessage" not in umo:
        return ""
    try:
        name = str(store.get_state(umo, "group_name") or "").strip()
    except Exception:
        logger.exception("[spectrum·L2] 读群名失败：%s", umo)
        return ""
    return f"（群聊「{name or umo.split(':')[-1]}」）"


def _state_item_id(text: str) -> str:
    return hashlib.sha1(str(text or "").strip().encode("utf-8")).hexdigest()[:8]


def _build_state_items(new: dict, owner: dict, store,
                       prev_items=None) -> "tuple[list, str]":
    from . import audience as _aud
    prev_by_id: dict = {}
    for _p in (prev_items if isinstance(prev_items, list) else []):
        if not isinstance(_p, dict):
            continue
        _pt = str(_p.get("text") or "").strip()
        if _pt:
            prev_by_id[_state_item_id(_pt)] = _p.get("audience")
    raw = new.get("state_items")
    items: list = []
    text: str = ""
    if isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            txt = str(it.get("text") or "").strip()
            if not txt:
                continue
            rid = str(it.get("id") or "").strip()
            if rid and rid in prev_by_id:

                audience = _aud.parse(prev_by_id[rid])
            else:
                names = it.get("with")
                if isinstance(names, str):
                    names = [names]
                if not isinstance(names, (list, tuple)):
                    names = []
                audience = _aud.for_item(names, owner,
                                         store.sessions_of_person_name,
                                         text=txt)
            items.append({"text": txt, "audience": audience})
    elif isinstance(raw, str) and raw.strip():


        text = raw.strip()[:600]
    else:


        logger.warning(
            "[spectrum·L3] 压缩输出的 state_items 形状不认识（%s），"
            "本轮现状不会更新", type(raw).__name__)
    if items:
        text = " ".join(i["text"] for i in items)
    return items, text
