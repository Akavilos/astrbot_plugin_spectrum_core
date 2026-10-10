"""光谱框架入口：组织本轮上下文、解析角色决策并跟踪实际发送。
后台任务、共同生活与存储分别委托给对应模块；事件附带轮次和冻结代次。"""


from __future__ import annotations

import asyncio
import json
import re
import time
import traceback
from datetime import datetime, timedelta

import astrbot.api.star as star
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain

from .spectrum import audience as _aud
from .spectrum import clock
from .spectrum import settings as settings_config
from .spectrum import context as session_context
from .spectrum.delivery import track_reply, install_render_receipts
from .spectrum import contract_audit
from .spectrum.contract import (_TOOL_SEND_NAME, build_address_identity, build_contract,
                                build_l3_context_block, build_observed_block,
                                build_person_block, build_phase_block,
                                build_runtime_block, build_tools_block,
                                build_worldbook_block)
from .spectrum.filter import PassiveInterjectFilter, set_active_plugin
from .spectrum.l2 import L2
from .spectrum.lifecycle import runtime_facts
from .spectrum import limiter as low_freq
from .spectrum import freeze as freeze_mod
from .spectrum.life import (find_mentioned_people_scored, observed_audience,
                            observed_delete_mode, observed_reject_reason,
                            observed_scene_from, render_observed_rows,
                            render_references)
from .spectrum.page_api import PageApi
from .spectrum.parse import (parse as parse_decision, prefill_think,
                            sanitize_text, _extract_reply_content,
                            normalize_tags, extract_tool_calls)
from .spectrum.person import PersonRegistry, is_private_umo, make_person_id
from .spectrum.prompt_flow import inject_context, missing_exchange, temporary_context
from .spectrum import store as store_mod
from .spectrum import vectors as vectors_mod
from .spectrum import worldbook as _worldbook
from .spectrum.store import Store

PLUGIN_NAME = "astrbot_plugin_spectrum_core"


_SCRIPT_TAIL = 70


_CONTENT_RESCUE = re.compile(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', re.S)


_NARRATIVE_REQUEST = re.compile(
    r"(给我讲|给我讲讲|讲讲|说说|来一个|来段|来一段|讲一下|说一下|编一个|编个|"
    r"讲|说|来|弄个|整个|写个).{0,10}?(笑话|故事|段子|童话|睡前故事|经历|经过|"
    r"全过程|完整过程|来龙去脉|前因后果|详情|细节|展开)|"
    r"(讲故事|讲个故事|说个故事|讲笑话|讲个笑话|说个笑话|来讲讲|说来听听|"
    r"展开讲讲|展开说说|讲个段子|说个段子|来个段子)",
    re.I,
)


def _sender(event: "AstrMessageEvent", default: str = "对方") -> str:
    try:
        name = (event.get_sender_name() or "").strip()
        return name or default
    except Exception:
        return default


def _sender_id(event: "AstrMessageEvent") -> str:
    try:
        return (event.get_sender_id() or "").strip()
    except Exception:
        return ""


_FRAMEWORK_SCAFFOLD_MARKS = ("\n## Skills", "\n### Skill rules")


def _strip_framework_scaffold(persona: str) -> str:
    if not persona:
        return persona
    cut = len(persona)
    for mark in _FRAMEWORK_SCAFFOLD_MARKS:
        i = persona.find(mark)
        if i != -1:
            cut = min(cut, i)
    return persona[:cut].rstrip()


def _platform(umo: str) -> str:
    return (umo or "").split(":")[0]


_CMD_NAMES = ("光谱核心", "叙述事实", "叙述查看", "叙述删除")


def _wake_prefixes(context) -> tuple[str, ...]:
    try:
        cfg = context.get_config()
        ps = cfg.get("wake_prefix") or ["/"]
        if isinstance(ps, str):
            ps = [ps]
        return tuple(str(p) for p in ps if p)
    except Exception:
        return ("/",)


def _is_plugin_command(event, context) -> bool:
    cands = set(_CMD_NAMES)
    for p in _wake_prefixes(context):
        cands.update(p + c for c in _CMD_NAMES)

    texts = []
    try:
        for seg in event.get_messages():
            t = getattr(seg, "text", None)
            if isinstance(t, str) and t:
                texts.append(t)
    except Exception:
        texts = []
    if not texts:
        texts = [getattr(event, "message_str", "") or ""]
    head = "".join(texts).strip()
    if not head:
        return False
    return any(head == c or head.startswith(c + " ")
               or head.startswith(c + "\u3000") for c in cands)


from .spectrum.prism_bridge import peek_emotion as _peek_prism_emotion
from .spectrum.prism_bridge import peek_signals as _peek_prism_signals


def _read_prism(umo: str, *, is_group: bool, peer_name: str = "",
                group_name: str = "", cfg=None) -> str:
    try:
        from astrbot.core.star.star import star_map
    except Exception:
        return ""


    _dbg = bool(cfg("possession_enabled", True) and cfg("possession_debug", False)) if callable(cfg) else False
    if _dbg:
        _seen = []
        for meta in list(star_map.values()):
            cls = getattr(meta, "star_cls", None)
            _seen.append("%s:%s" % (getattr(meta, "name", "?"),
                                    ("有cls" if cls is not None else "cls=None")
                                    + ("+能力" if cls is not None and hasattr(cls, "read_context") else "")))
        logger.info("[spectrum·棱镜] star_map 扫描：%s", " | ".join(_seen[:12]))

    for meta in list(star_map.values()):
        if getattr(meta, "name", "") != "astrbot_plugin_prism":
            continue
        cls = getattr(meta, "star_cls", None)
        if cls is None:
            continue

        if not (hasattr(cls, "read_context") or hasattr(cls, "write_into_thought")):
            continue


        if _dbg:
            logger.info("[spectrum·棱镜] 命中读记忆的对象：%s（模块 %s）",
                        type(cls).__name__, type(cls).__module__)
        try:


            fn = getattr(cls, "write_into_thought", None) or cls.read_context
            text = fn(
                umo=umo, is_group=is_group,
                peer_name=peer_name, group_name=group_name,
            )
            return (text or "").strip()
        except Exception:


            logger.warning("[spectrum] 读棱镜上下文失败：%s",
                           traceback.format_exc().splitlines()[-1], exc_info=True)
            return ""
    return ""


def _get_reasoning(event) -> str:
    return ""


_MENTION_RE = re.compile(r"@([^\s@()\n]{1,24})\((\d{5,12})\)")


def _note_mentions(persons, umo: str, text: str, platform: str) -> None:
    if not text:
        return
    for name, qq in _MENTION_RE.findall(text):
        try:
            persons.note_mention(umo, qq, name, platform)
        except Exception:
            logger.debug("[spectrum·群像] @提及登记失败 %s", name, exc_info=True)


def _entry_meta(sender: str, sender_id: str = "") -> dict:
    meta = {"sender": sender}
    if sender_id:
        meta["sender_id"] = sender_id
    return meta


def _group_name(event: "AstrMessageEvent") -> str | None:
    try:
        grp = getattr(event.message_obj, "group", None)
        name = (getattr(grp, "group_name", "") or "").strip() if grp else ""
        return name or None
    except Exception:
        return None


def _render_life_state(st: str) -> str:
    s = (st or "").strip()
    if not s.startswith("{"):
        return s or "（空）"
    lines: list[str] = []
    try:
        d = json.loads(s)
    except Exception:
        d = None
    if isinstance(d, dict) and isinstance(d.get("state_items"), list):
        for it in d["state_items"]:
            if not isinstance(it, dict):
                continue
            text = str(it.get("text") or "").strip()
            if not text:
                continue
            who = [str(x).strip() for x in (it.get("with") or [])
                   if str(x).strip()]
            lines.append(f"· {text}"
                         + (f"（涉及：{'、'.join(who)}）" if who else ""))
    if not lines:


        for m in re.finditer(
                r'"text"\s*:\s*"(.*?)"\s*(?=,\s*\n|\s*\n\s*[\]}])', s, re.S):
            text = m.group(1).strip()
            if text:
                lines.append(f"· {text}")
    return "\n".join(lines) if lines else s


@star.register("astrbot_plugin_spectrum_core", "Akavilos", "光谱核心 (Spectrum Core)", "0.3.5")
class SpectrumCorePlugin(star.Star):
    def __init__(self, context: star.Context, config: dict | None = None) -> None:
        """建立配置、存储、人物与生活协调对象，安装运行控制入口。"""
        super().__init__(context)
        self.config = config or {}
        settings_config.migrate(self.config)
        self.data_dir = star.StarTools.get_data_dir(PLUGIN_NAME)
        self.store = Store(self.data_dir / "spectrum.db")


        store_mod.set_cfg_getter(self._cfg)
        self.persons = PersonRegistry(self.store, self._cfg)
        self.l2 = L2(self.store, self.context, self._cfg, persons=self.persons)
        self.page_api = PageApi(self)
        set_active_plugin(self)


        try:
            freeze_mod.install(self._cfg, self.context, store=self.store)
        except Exception:
            logger.exception("[spectrum·冻结] 安装失败（不影响其它功能）")
        self.runtime_control = freeze_mod
        self._delivery_task: asyncio.Task | None = None


        self._armed: dict[str, float] = {}


        self._req_at: dict[str, float] = {}


        self._last_prefill: dict[str, bool] = {}


        self._last_tools: dict[str, object] = {}


        self._last_sys: dict[str, str] = {}


        self._last_ctx: dict[str, list] = {}


        self._last_finish: dict[str, str] = {}


        self._current_person: dict[str, tuple[str, float]] = {}


        self._send_tool_class = None
        self._send_tool_wrapper = None
        self._send_tool_original = None


    def _install_send_receipts(self) -> None:
        try:
            from astrbot.core.tools.message_tools import SendMessageToUserTool
            previous = SendMessageToUserTool.call
            original = getattr(previous, "_spectrum_original", previous)

            async def tracked(tool, wrapper, **kwargs):
                event = wrapper.context.event
                self._check_turn_runtime(event)
                try:
                    outcome = await original(tool, wrapper, **kwargs)
                except Exception as error:
                    freeze_mod.record_failure("delivery", error)
                    raise
                current = event.unified_msg_origin
                marker = "Message sent to session "
                if current not in self._armed or not str(outcome).startswith(marker):
                    return outcome
                target = str(outcome)[len(marker):].strip()
                if target == current:
                    return outcome
                plain = "".join(
                    str(part.get("text") or "")
                    for part in kwargs.get("messages", [])
                    if isinstance(part, dict) and part.get("type") == "plain"
                ).strip()
                if plain:
                    try:
                        entry_id = self.store.add_entry(
                            target, "delivered", plain, ts=time.time(),
                            life_id=self.l2.life.ensure_life(),
                            meta={"cross_send_from": current})
                        receipts = dict(event.get_extra("_spectrum_delivery_receipts", {}) or {})
                        receipts[target] = entry_id
                        event.set_extra("_spectrum_delivery_receipts", receipts)
                    except Exception:
                        logger.exception("[spectrum·投递回执] 消息已送达但回执落库失败")
                return outcome

            tracked._spectrum_original = original
            SendMessageToUserTool.call = tracked
            self._send_tool_class = SendMessageToUserTool
            self._send_tool_wrapper = tracked
            self._send_tool_original = original
        except Exception:
            logger.exception("[spectrum·投递回执] 安装失败")

    async def initialize(self) -> None:
        logger.info(
            "[spectrum] 已加载。enabled=%s scope=%s（enabled=false 时完全不介入）",
            self._cfg("enabled", False),
            self._cfg("scope", "private"),
        )


        contract_audit.report(logger)
        self._install_send_receipts()
        from astrbot.core import html_renderer, file_token_service
        install_render_receipts(html_renderer, file_token_service)
        if hasattr(self.context, "register_web_api"):
            try:
                self.page_api.register_routes()
                logger.info("[spectrum] Page API 注册成功")
            except Exception:
                logger.exception("[spectrum] 注册 Page API 失败")
        else:
            logger.warning("[spectrum] 框架不支持 register_web_api，可视化面板将不可用")


        low_freq.set_cfg_getter(self._cfg)

        self._delivery_task = asyncio.create_task(self._delivery_loop())


        try:
            asyncio.create_task(self._vector_backfill())
        except Exception:
            logger.exception("[spectrum·向量] 回填任务起不来（不影响其他功能）")

    @filter.custom_filter(PassiveInterjectFilter, plugin=None)
    async def eavesdrop_hook(self, event: AstrMessageEvent):
        pass

    def on_message_eavesdrop_sync(self, event: AstrMessageEvent) -> None:
        freeze_mod.note_message_in(event.unified_msg_origin)
        asyncio.create_task(self._eavesdrop_guarded(event))

    async def _eavesdrop_guarded(self, event: AstrMessageEvent) -> None:
        try:
            await self.on_message_eavesdrop(event)
        except Exception as error:
            freeze_mod.record_failure("observation", error)
            logger.exception("[spectrum] 群聊观察出错，本条消息跳过（不影响主回复）")

    def _record_frozen_message(self, event: AstrMessageEvent) -> None:
        if event.get_extra("_spectrum_frozen_received", False):
            return
        if _is_plugin_command(event, self.context):
            return
        text = (getattr(event, "message_str", "") or "").strip()
        if not text:
            return
        self.store.add_entry(
            event.unified_msg_origin, "received", text, ts=time.time(),
            meta=_entry_meta(_sender(event, "某人"), _sender_id(event)),
            life_id=self.l2.life.current_life_id())
        event.set_extra("_spectrum_frozen_received", True)

    async def on_message_eavesdrop(self, event: AstrMessageEvent) -> None:
        if not self._cfg("enabled", False):
            return

        umo = event.unified_msg_origin


        if _is_plugin_command(event, self.context):
            return

        user_text = (getattr(event, "message_str", "") or "").strip()
        if not user_text:
            return

        if event.get_extra("_spectrum_frozen_received", False):
            return
        if freeze_mod.is_frozen():
            self._record_frozen_message(event)
            return


        g_name = _group_name(event)
        if g_name and g_name != self.store.get_state(umo, "group_name"):
            self.store.set_state(umo, "group_name", g_name)

        sender_name = _sender(event, "某人")
        sender_id = _sender_id(event)
        now_ts = time.time()


        self.persons.observe(umo, sender_id, sender_name, _platform(umo))

        _note_mentions(self.persons, umo, user_text, _platform(umo))

        meta = _entry_meta(sender_name, sender_id)


        if self.store.is_asleep():
            self.store.add_entry(umo, "received", user_text, ts=now_ts, meta=meta,
                                 life_id=self.l2.life.current_life_id())
            if self._cfg("sleep_inbox_enabled", True):
                self.store.push_sleep_inbox(user_text, sender_name, umo)
            return


        self.store.add_entry(umo, "received", user_text, ts=now_ts, meta=meta,
                             life_id=self.l2.life.current_life_id())


        if self._cfg("l3_interject_enabled", False):
            old_w = self.store.get_willingness(umo)
            import random
            delta = random.uniform(0.01, 0.03)
            if len(user_text) > 30: delta += 0.02
            new_w = min(1.0, old_w + delta)
            self.store.set_willingness(umo, new_w)


            threshold = float(self._cfg("l3_interject_threshold", 0.8))
            if new_w >= threshold:
                last_activity = self.store.last_activity_ts(umo)
                if last_activity and (now_ts - last_activity) > 30:
                    logger.info("[spectrum·L3-Flow] 意愿突破阈值 %.2f，尝试插话", new_w)
                    self.store.set_willingness(umo, 0.3)


                    await self.l2._fire(umo, "user-message", audience_viewer=umo)

    async def terminate(self) -> None:
        if (self._send_tool_class is not None
                and self._send_tool_class.call is self._send_tool_wrapper):
            self._send_tool_class.call = self._send_tool_original
        if self._delivery_task:
            self._delivery_task.cancel()
            try:
                await self._delivery_task
            except (asyncio.CancelledError, Exception):
                pass
        self.store.close()
        logger.info("[spectrum] 已卸载。")


    @staticmethod
    def _tool_names(req) -> list[str]:
        return [s["name"] for s in SpectrumCorePlugin._tool_specs(req)]

    @staticmethod
    def _tool_specs(req) -> list[dict]:
        try:
            tools = getattr(getattr(req, "func_tool", None), "tools", None) or []
            out = []
            for t in tools:
                name = getattr(t, "name", "")
                if not name or not bool(getattr(t, "active", True)):
                    continue
                out.append({
                    "name": name,
                    "description": getattr(t, "description", "") or "",
                    "parameters": getattr(t, "parameters", None) or {},
                })
            return out
        except Exception:
            return []


    def _cfg(self, key: str, default):
        return settings_config.value(self.config, key, default)


    def _embed_provider(self):
        try:
            getter = getattr(self.context, "get_all_embedding_providers", None)
            if not callable(getter):
                return None
            provs = getter() or []
            return provs[0] if provs else None
        except Exception:
            return None

    async def _embed_query(self, text: str):
        if not self._cfg("memory_vector_enabled", True):
            return None

        return await vectors_mod.embed_query(self.context, text)

    async def _vector_backfill(self) -> None:
        if not self._cfg("memory_vector_enabled", True):
            return


        for _ in range(24):
            if self._embed_provider() is not None:
                break
            await asyncio.sleep(5)
        if self._embed_provider() is None:
            logger.info("[spectrum·向量] 没有可用的 embedding provider，语义召回这一层不生效"
                        "（配好那条 embedding provider 之后，重启即自动生效）")
            return


        _origin_vec = (self._cfg("memory_origin_enabled", False)
                       and self._cfg("memory_origin_vector_enabled", False))


        _wb_vec = (self._cfg("worldbook_enabled", False)
                   and self._cfg("worldbook_vector_enabled", False))
        _tables = ("memories", "person_memories") + (
            ("entries",) if _origin_vec else ()) + (
            ("worldbook",) if _wb_vec else ())
        gone = 0
        for table in _tables:
            gone += self.store.prune_vectors(table)
        if gone:
            logger.info("[spectrum·向量] 清掉 %d 条指向已删行的死 id（旧代码留下的）", gone)

        total = await vectors_mod.backfill(self.context, self.store, tables=_tables)
        st = self.store.vector_status()
        logger.info("[spectrum·向量] 回填完成，本次补 %d 条；现状 %s", total, st)

    def _active(self, umo: str) -> bool:
        if not self._cfg("enabled", False):
            return False
        if self.store.get_state(umo, "fallback") == "1":
            return False
        scope = str(self._cfg("scope", "private"))

        if scope == "private" and "FriendMessage" not in umo:
            return False
        return True

    def _build_cross_refs(self, umo: str, text: str, exclude: str = "") -> str:
        from .spectrum.person import unambiguous_aliases
        aliases = unambiguous_aliases(self.store.all_person_aliases(), umo.split(":", 1)[0])
        if not aliases:
            return ""


        scored = find_mentioned_people_scored(text, aliases, exclude=exclude)
        high = [pid for pid, conf in scored if conf == 2]
        low = [pid for pid, conf in scored if conf == 1]
        pids = high if high else low
        if not pids:
            return ""

        from .spectrum.life import MAX_REFS, REFS_PER_PERSON
        refs = []
        for pid in pids[:MAX_REFS]:
            try:
                from .spectrum.person import render_memory_record
                rows = self.persons.select_memories(pid, umo, text, limit=REFS_PER_PERSON)
                mems = [render_memory_record(dict(r)) for r in rows
                        if r["text"] and str(r["text"]).strip()]
            except Exception:
                mems = []
            if not mems:
                continue
            name = self.store.get_person_name(pid) or pid
            refs.append((name, mems))
        return render_references(refs)


    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        if not _is_plugin_command(event, self.context):
            freeze_mod.note_message_in(event.unified_msg_origin)
        if freeze_mod.is_frozen():
            if self._active(event.unified_msg_origin):
                self._record_frozen_message(event)
            event.stop_event()
            return
        event.set_extra("_spectrum_freeze_epoch", freeze_mod.checkpoint())
        event.set_extra("_spectrum_failure_scope", freeze_mod.begin_failure_scope())
        try:
            await self._on_llm_request(event, req)
        except (freeze_mod.FrozenSkipped, low_freq.LowFreqSkipped):
            event.stop_event()
        except Exception as error:
            freeze_mod.record_failure("request", error)
            if freeze_mod.is_frozen():
                event.stop_event()
            logger.exception("[spectrum] on_llm_request 出错，本轮按普通模式放行")

    async def _on_llm_request(self, event: AstrMessageEvent, req) -> None:
        umo = event.unified_msg_origin


        if _is_plugin_command(event, self.context):
            event.stop_event()
            logger.info("[spectrum] 收到本插件指令，不接管本轮（原文=%r）",
                        (getattr(event, "message_str", "") or "")[:40])
            return


        freeze_mod.note_message_in(umo)

        if not self._active(umo):
            return
        if req is None or not hasattr(req, "system_prompt"):
            return

        tz_name = str(self._cfg("timezone", "Asia/Shanghai"))
        now_dt = clock.now(tz_name)
        now_ts = now_dt.timestamp()


        cancelled = self.store.cancel_pending_for(umo)
        if cancelled:
            logger.info("[spectrum] 用户新消息到达，作废 %d 条待发的延迟回复", cancelled)

        user_text = (getattr(event, "message_str", "") or req.prompt or "").strip()
        is_group = "GroupMessage" in umo
        sender_name = _sender(event)
        sender_id = _sender_id(event)
        platform = _platform(umo)
        group_name = _group_name(event) if is_group else None
        if group_name:
            self.store.set_state(umo, "group_name", group_name)


        person_id = self.persons.observe(umo, sender_id, sender_name, platform)

        if is_group:
            _note_mentions(self.persons, umo, user_text, platform)


        if person_id:
            self._current_person[umo] = (person_id, time.time())


        if self.store.is_asleep():
            until = self.store.get_sleep().get("until", 0)
            if until and not self.store.sleep_due(now_ts):

                if user_text:
                    self.store.add_entry(umo, "received", user_text,
                                         ts=now_ts, meta=_entry_meta(sender_name, sender_id),
                                         life_id=self.l2.life.current_life_id())
                    if self._cfg("sleep_inbox_enabled", True):
                        self.store.push_sleep_inbox(user_text, sender_name, umo)
                event.set_extra("_spectrum_sleep_muted", True)
                logger.info("[spectrum·睡眠] %s 睡中静音（全局未读 %d）",
                            umo, len(self.store.get_sleep_inbox()))
                return

            self.store.set_sleep_awake()

        if user_text:


            if not is_group:
                self.store.add_entry(umo, "received", user_text, ts=now_ts,
                                     meta=_entry_meta(sender_name, sender_id),
                                     life_id=self.l2.life.current_life_id())


        try:
            self.l2.apply_emotion_to_willingness(umo)
        except Exception:
            logger.exception("[spectrum·意愿] 调整失败（不影响本轮）")


        if is_group and event.is_at_or_wake_command:
            old_w = self.store.get_willingness(umo)
            if old_w < 0.6:
                self.store.set_willingness(umo, 0.6)
                logger.info("[spectrum·L3-Group] 被点名，意愿分抬升: %.2f -> 0.60", old_w)


        last_ts = self.store.last_write_ts(umo)
        from_dt = clock.default_from(
            now_dt,
            datetime.fromtimestamp(last_ts, tz=now_dt.tzinfo) if last_ts else None,
        )
        interval_text = clock.build_interval(
            from_dt, now_dt, self._cfg("latitude", 30.6)
        )

        exchange_text = self._format_exchange(umo, req=req, current_text=user_text)


        person_block = ""
        if person_id:
            dossier = self.persons.dossier(person_id, umo, query=user_text)
            person_block = build_person_block(
                dossier,
                peer_name=sender_name,
                is_group=is_group,
                group_name=group_name,
            )


        runtime_exchange = exchange_text
        possession_on = bool(self._cfg("possession_enabled", True))

        prism_block = ""
        if self._cfg("prism_enabled", True):
            prism_block = _read_prism(
                umo, is_group=is_group, peer_name=sender_name,
                group_name=group_name or "", cfg=self._cfg,
            ).strip()


        persona = req.system_prompt or ""

        contract = build_contract(
            possession_on,
            prefill=False,
            recall_text="",
            life_style=str(self._cfg("life_style", "") or ""),
        )
        core = f"{contract}\n\n═══ 以下是这个人的设定与说话方式 ═══\n\n{persona}"


        self.l2.set_sys_core(umo, core)


        _world = session_context.build_world_snapshot(
            self.store, self.l2.life, umo, umo,
            lambda: self.l2.shared_event_context(umo, user_text + ' ' + self.store.get_continuity(umo)))
        event.set_extra("_spectrum_turn", session_context.new_turn(umo, person_id, _world))
        continuity = _world.context.life_state
        recent_scripts = list(_world.context.scripts)
        volatile = self.store.get_volatile_changes(umo)
        plan = {"_rendered": _world.plans} if _world.plans else {"steps": []}


        conflict_note = self.l2.take_conflict_note(umo)


        _qvec = await self._embed_query(user_text)
        self._check_turn_runtime(event)
        memories = self.store.memories_for_prompt(
            umo,
            user_text if self._cfg("enable_fts_memory", True) else "",
            recent_n=int(self._cfg("memory_recent_limit", 4)),
            related_n=int(self._cfg("memory_related_limit", 8)),
            qvec=_qvec,
        )


        participants = self.store.recent_senders(umo) if is_group else []


        cross_refs = ""
        try:
            if user_text and self._cfg("cross_ref_enabled", True):
                cross_refs = self._build_cross_refs(
                    umo, user_text, exclude=person_id or "")
        except Exception:
            logger.debug("[spectrum·引用] 跨关系引用失败，跳过", exc_info=True)
            cross_refs = ""


        _companion = _world.companion


        origin_segments = self.store.recall_original(umo, user_text, qvec=_qvec)


        worldbook_l3: list = []
        worldbook_core_block = ""
        worldbook_person_block = ""
        worldbook_turn_block = ""
        try:
            if self._cfg("worldbook_enabled", False):
                _wb_groups = _worldbook.group_by_position(
                    self.store.worldbook_hits(user_text, umo=umo, qvec=_qvec))
                worldbook_l3 = _wb_groups["l3"]
                worldbook_core_block = build_worldbook_block(_wb_groups["core"])
                worldbook_person_block = build_worldbook_block(_wb_groups["person"])
                worldbook_turn_block = build_worldbook_block(_wb_groups["turn"])
        except Exception:
            logger.debug("[spectrum·世界书] 召回失败，本轮不注入", exc_info=True)

        l3_context = build_l3_context_block(
            continuity, recent_scripts, plan, memories, volatile,
            group_name=group_name,
            peer_name=None if is_group else sender_name,
            participants=participants,
            conflict_note=conflict_note,
            cross_refs=cross_refs,
            companion=_companion,
            origin_segments=origin_segments,

            promise_memories=self.store.list_promise_memories(umo),
            shared_events=_world.shared_events,

            worldbook_rows=worldbook_l3,
        )


        tool_specs = self._tool_specs(req)


        self.l2.set_tool_specs(umo, tool_specs)
        tools_context = build_tools_block(


            tool_specs, self.store.known_sessions(exclude=None), umo,
            text_mode=False,


            usage_notes=self.store.tool_notes_all(),


            max_tools=int(self._cfg("tools_list_max", 12)),
        )


        observed_block = ""
        try:
            _obs_rows = self.store.observed_rows(limit=2)
            observed_block = build_observed_block(
                [str(r["text"]) for r in _obs_rows
                 if _aud.admit(_aud.parse(r["audience"]), umo)])
        except Exception:
            logger.debug("[spectrum·看见] 读取失败，本轮不注入", exc_info=True)

        scene = f"群聊「{group_name}」" if is_group and group_name else "群聊" if is_group else "私聊"
        turn_identity = f"【当前通信】我正在{scene}回应「{sender_name}」。我分清眼前说话的人与其他会话里的真实人物。"
        if _NARRATIVE_REQUEST.search(user_text):
            turn_identity += "\n我收到了完整讲述的请求，这一轮把故事、笑话或解释讲完整，不把内容截在等待追问的地方。"
        inject_context(
            req, contract,
            worldbook_core_block, l3_context,
            runtime_facts(self.store, str(self._cfg("timezone", "Asia/Shanghai"))),
            build_address_identity(umo, None if is_group else sender_name),
            person_block, worldbook_person_block, tools_context,
            build_phase_block("user-message"), worldbook_turn_block,
            build_runtime_block(interval_text, runtime_exchange),
            observed_block, prism_block, turn_identity,
        )

        await low_freq.acquire()
        self._check_turn_runtime(event)
        self._armed[umo] = time.time()


        self._req_at[umo] = self._armed[umo]
        if len(self._req_at) > 256:
            for k in list(self._req_at)[:128]:
                self._req_at.pop(k, None)
        self._last_prefill[umo] = False

        self._last_tools[umo] = getattr(req, "func_tool", None)
        self._last_sys[umo] = getattr(req, "system_prompt", "") or ""
        self._last_ctx[umo] = list(req.contexts or [])
        event.set_extra("_spectrum_request", {
            "system": self._last_sys[umo], "contexts": self._last_ctx[umo],
            "tools": self._last_tools[umo], "prefill": self._last_prefill.get(umo, False),
            "extra_user_content_parts": list(req.extra_user_content_parts),
            "prompt": req.prompt, "at": time.time()})


        if len(self._last_tools) > 256:
            for k in list(self._last_tools)[:128]:
                self._last_tools.pop(k, None)
                self._last_prefill.pop(k, None)
                self._last_sys.pop(k, None)
                self._last_ctx.pop(k, None)
        if self._cfg("debug", False):
            logger.info("[spectrum] 接管请求 umo=%s\n%s", umo, req.system_prompt)

    def _format_exchange(self, umo: str, *, req=None, current_text: str = "") -> str:
        rows = self.store.recent_exchange(umo, int(self._cfg("exchange_limit", 12)))
        if not rows:
            return ""
        if req is not None:
            return missing_exchange(rows, req.contexts, current_text, self.store.parse_meta,
                                    is_group="GroupMessage" in umo)

        lines = []
        for r in reversed(rows):

            meta = self.store.parse_meta(r["meta"])
            sender = (meta.get("sender") or "").strip()
            kind = r["kind"]

            if kind == "received":


                label = sender if sender and sender not in ("某人", "对方", "群友") else (
                    "群友" if "GroupMessage" in umo else "对方"
                )
                lines.append(f"{label}：{r['text']}")
            else:


                lines.append(f"我：{r['text']}")

        return "\n".join(lines)


    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        try:
            await self._on_decorating_result(event)
        except freeze_mod.FrozenSkipped:
            event.stop_event()
        except Exception as error:
            freeze_mod.record_failure("result", error)
            if freeze_mod.is_frozen():
                event.stop_event()
                return


            logger.exception("[spectrum] on_decorating_result 出错，本轮只发能确认的那句")
            try:


                raw = self._plain_text(event.get_result())
                safe = _extract_reply_content(raw) if raw else ""
                result = event.get_result()
                if safe:
                    result.chain = [Plain(safe)]
                else:
                    result.chain = []
                    logger.warning("[spectrum] 出错兜底：提取不到可发内容，本轮静默")
            except Exception:

                logger.exception("[spectrum] 兜底处理也失败，本轮静默")
                try:
                    event.get_result().chain = []
                except Exception:
                    pass

    @filter.on_llm_response()
    async def _on_llm_response(self, event: AstrMessageEvent, response) -> None:
        scope = event.get_extra("_spectrum_failure_scope", {})
        if (getattr(response, "role", "") == "err"
                and not scope.get("model_failed", False) and not scope.get("skipped", False)):
            low_freq.note_failure()
            freeze_mod.record_failure("model-response", RuntimeError("宿主返回模型错误结果"))
        try:
            umo = event.unified_msg_origin
            fr = None
            raw = getattr(response, "raw_completion", None)
            if raw is not None and getattr(raw, "choices", None):
                fr = getattr(raw.choices[0], "finish_reason", None)
            fr = fr or getattr(response, "finish_reason", None)
            if fr:
                self._last_finish[umo] = str(fr)
                if len(self._last_finish) > 256:
                    for k in list(self._last_finish)[:128]:
                        self._last_finish.pop(k, None)
        except Exception:
            logger.debug("[spectrum] finish_reason 读取失败", exc_info=True)
    @staticmethod
    def _check_turn_runtime(event) -> None:
        """检查事件保存的冻结代次，跨阶段时终止本轮后续操作。"""
        epoch = event.get_extra("_spectrum_freeze_epoch", freeze_mod.checkpoint())
        if not freeze_mod.may_commit(epoch):
            raise freeze_mod.FrozenSkipped("本轮已跨过系统暂停，停止后续执行")

    async def _on_decorating_result(self, event: AstrMessageEvent) -> None:
        if not _is_plugin_command(event, self.context) and not freeze_mod.may_commit(
                event.get_extra("_spectrum_freeze_epoch", freeze_mod.checkpoint())):
            event.stop_event()
            return
        umo = event.unified_msg_origin


        if _is_plugin_command(event, self.context):
            if self._armed.get(umo):
                result = event.get_result()
                if result is not None:
                    result.chain = []
                event.stop_event()
            return

        result = event.get_result()
        if result is None:
            return


        if event.get_extra("_spectrum_sleep_muted", False):
            result.chain = []
            return

        raw = normalize_tags(self._plain_text(result))


        try:
            _err_msg = event.get_extra("persona_custom_error_message")
        except Exception:
            _err_msg = None
        if _err_msg and raw.strip() == str(_err_msg).strip():
            logger.error(
                "[spectrum·接口] **LLM 调用失败**：框架返回的是人设的 "
                "custom_error_message（%r），**本轮不是她的输出** —— "
                "不计入解析失败、不动接管状态。去查 provider 的报错"
                "（模型名下架 / 额度 / 网络）。",
                raw[:80],
            )

            self._armed.pop(umo, None)
            self._req_at.pop(umo, None)


            return


        logger.info(
            "[spectrum·诊断] 进门 umo=%s 链=%s raw=%d字 窗口=%s 标签=%s",
            umo,
            [type(c).__name__ for c in (result.chain or [])] or "空",
            len(raw),
            "是" if (self._armed.get(umo) or self._req_at.get(umo)) else "否",
            "有" if ("<life" in raw or "<reply" in raw or "<think" in raw
                     or '"seen"' in raw) else "无",
        )


        _armed_at = self._armed.get(umo)
        _req_at = event.get_extra("_spectrum_request", {}).get("at", self._req_at.get(umo))
        in_window = ((_armed_at is not None and (time.time() - _armed_at) <= 300)
                     or (_req_at is not None and (time.time() - _req_at) <= 300))

        if not raw:


            if in_window:
                _comps = [type(c).__name__ for c in (result.chain or [])]
                self._armed.pop(umo, None)
                result.chain = []
                logger.warning(
                    "[spectrum] 本轮由我们发起但取不到文本，静默（fail-closed）。"
                    "链条组件=%s", _comps or "空",
                )
            return

        tz_name = str(self._cfg("timezone", "Asia/Shanghai"))
        now_dt = clock.now(tz_name)


        is_body = ("<life" in raw) or ("<reply" in raw) or (
            '"seen"' in raw and '"reply"' in raw
        )
        is_marks = is_body or ("<think" in raw) or ("</think" in raw)


        _tool_calls = extract_tool_calls(raw)[1]
        tool_receipts: dict[str, int] = dict(
            event.get_extra("_spectrum_delivery_receipts", {}) or {})
        if not is_body and _tool_calls:
            _ran = False
            if event.get_extra("_spectrum_request", {}).get("prefill", self._last_prefill.get(umo)):
                try:
                    tool_receipts.update(
                        await self._run_tool_calls(event, umo, _tool_calls))
                    _ran = True
                except freeze_mod.FrozenSkipped:
                    raise
                except Exception:
                    logger.exception("[spectrum·工具] 工具轮执行失败")


            _fixed = await self._reask_for_decision(
                umo, raw, "这一轮只写了工具调用，缺少 <life>/<reply> 正文", event=event)
            self._check_turn_runtime(event)
            if _fixed is not None and _fixed.ok:


                raw = normalize_tags(extract_tool_calls(_fixed.raw or "")[0])
                is_body = ("<life" in raw) or ("<reply" in raw) or (
                    '"seen"' in raw and '"reply"' in raw)
                is_marks = is_body or ("<think" in raw) or ("</think" in raw)
                logger.info(
                    "[spectrum·工具] 工具轮（无正文）：%d 个调用已执行，"
                    "重问问出正文，继续走主流程", len(_tool_calls))

            else:
                event.stop_event()
                logger.warning(
                    "[spectrum·工具] 工具轮（无正文）：%d 个调用，已执行=%s，"
                    "重问也要不到正文，本轮不发（原样放行会把 %d 字原始输出发出去）",
                    len(_tool_calls), "是" if _ran else "否", len(raw))
                return


        if not in_window and not is_marks:
            return

        self._armed.pop(umo, None)
        if not in_window:


            safe = sanitize_text(raw)
            if safe:
                self._set_chain_text(result, safe)
                logger.warning(
                    "[spectrum] armed 缺失/过期但检测到光谱核心格式，已保底清洗（不记账） content=%.60r",
                    safe,
                )
            else:
                result.chain = []
                logger.warning("[spectrum] armed 缺失/过期且清洗无果，本轮静默")
            return


        decision = parse_decision(raw, now_dt)

        if not decision.ok:
            _err0 = decision.error
            rescued = self._rescue(raw)
            if rescued:
                logger.warning(
                    "[spectrum] 解析失败(%s)，已从原文抢救出 content", decision.error
                )
                decision.mode, decision.content, decision.ok = "immediate", rescued, True
            else:


                _fixed = await self._reask_for_decision(umo, raw, _err0, event=event)
                self._check_turn_runtime(event)
                if _fixed is not None and _fixed.ok:
                    logger.warning(
                        "[spectrum] V20 重问成功（首轮失败原因：%s，finish_reason=%s）",
                        _err0, self._last_finish.get(umo, "（没记到）"),
                    )
                    decision = _fixed
        if not decision.ok:
            streak = self.store.bump_counter(umo, "fail_streak")
            limit = int(self._cfg("max_fail_streak", 3))
            logger.error(
                "[spectrum] 解析失败(%s)，本轮静默（V20 已试过）。连续失败 %d 次。"
                "finish_reason=%s 原始输出：%.400r",
                decision.error,
                streak,
                self._last_finish.get(umo, "（没记到）"),
                raw,
            )
            if streak >= limit:
                self.store.set_state(umo, "fallback", "1")
                logger.error(
                    "[spectrum] 连续 %d 次解析失败，已对该会话**临时关闭光谱核心接管**"
                    "（发 /光谱核心 on 重新启用）。原始输出：%.300s",
                    streak,
                    raw,
                )
            result.chain = []
            return

        self.store.set_state(umo, "fail_streak", "0")
        if decision.repairs:
            logger.info("[spectrum] 解析修正：%s", "；".join(decision.repairs))


        if decision.tool_calls and event.get_extra("_spectrum_request", {}).get("prefill", self._last_prefill.get(umo)):


            try:
                tool_receipts.update(
                    await self._run_tool_calls(event, umo, decision.tool_calls))
            except freeze_mod.FrozenSkipped:
                raise
            except Exception:
                logger.exception("[spectrum·工具] 执行工具调用时出错，不影响本轮投递")
        elif decision.tool_calls:
            logger.warning(
                "[spectrum·工具] 默认合约模式下收到 %d 个文本工具调用，已忽略"
                "（该模式走框架原生调用）", len(decision.tool_calls),
            )

        self._check_turn_runtime(event)
        turn = event.get_extra("_spectrum_turn")
        if turn is not None and self.store.world_version() != turn.world.version:
            if event.get_extra("_spectrum_world_refreshed", False):
                event.stop_event()
                return
            world = session_context.build_world_snapshot(
                self.store, self.l2.life, umo, umo,
                lambda: self.l2.shared_event_context(umo, getattr(event, 'message_str', '') or self.store.get_continuity(umo)))
            event.set_extra("_spectrum_world_refreshed", True)
            fresh = await self._reask_for_decision(
                umo, raw, "我生成期间，共同环境已经变化。我根据当前环境重新决定并回复；"
                "我已经执行的工具不重复调用。\n" + session_context.render_current_world(world), event=event)
            self._check_turn_runtime(event)
            if fresh is None or not fresh.ok or self.store.world_version() != world.version:
                event.stop_event()
                return
            event.set_extra("_spectrum_turn", session_context.TurnContext(
                turn.turn_id, turn.session, turn.actor, world))
            self._set_chain_text(result, normalize_tags(extract_tool_calls(fresh.raw)[0]))
            return await self._on_decorating_result(event)
        with self.store.world_read():
            turn = event.get_extra("_spectrum_turn")
            if turn is not None and self.store.world_version() != turn.world.version:
                event.stop_event()
                return
            self.l2.reconcile_cross_send_claim(
                umo, decision, (getattr(event, "message_str", "") or ""))


            if decision.think:
                self.store.add_entry(umo, "think", decision.think, ts=now_dt.timestamp())


            if self._cfg("possession_enabled", True) and self._cfg("possession_debug", False):


                think = decision.think or prefill_think(decision.raw)


                logger.info(
                    "[spectrum·夺舍观测]\n"
                    "【她心里想的】%s\n"
                    "【她说出口的】%s\n"
                    "【原始输出（临时放大到 3000 字，原始长度 %d）】%s",
                    (think or "（模型没写思考）")[:400],
                    (decision.content or "（没说）")[:120],
                    len(decision.raw or ""),
                    (decision.raw or "")[:3000],
                )

            self.l2.record_shared_events(umo, decision, basis="received", source_entry_id=0,
                                         now=now_dt.timestamp())
            if decision.script:


                script_eid = self.store.add_entry(
                    umo, "script", decision.script, ts=now_dt.timestamp(),
                    life_id=self.l2.life.ensure_life(),
                    audience=_aud.for_session_write(umo))
                try:
                    self.l2.record_shared_events(
                        umo, decision, basis="script", source_entry_id=script_eid,
                        now=now_dt.timestamp())
                except (ValueError, TypeError):
                    logger.exception("[spectrum] 生活事件引用无效；内心戏已保存，本轮继续")

            logger.info(
                "[spectrum] seen=%s mode=%s content=%.60r",
                decision.seen,
                decision.mode,
                decision.content,
            )


            pid = self._take_current_person(umo)
            turn = event.get_extra("_spectrum_turn")
            if turn is not None:
                pid = turn.actor
                decision.turn_id = turn.turn_id
                decision.world_version = turn.world.version
                decision.source_actor = turn.actor


            try:
                self.l2.absorb_relation(umo, pid, decision)
            except Exception:
                logger.exception("[spectrum·好感] 推动失败（不影响本轮）")


            try:
                self.l2.handle_decision(umo, decision, person_id=pid, communication_confirmed=False)
            except Exception:
                logger.exception("[spectrum·L2] 决定落库失败（本轮其余流程继续）")
                try:
                    cur = int(self.store.get_state(umo, "fail_streak", "0") or 0)
                    self.store.set_state(umo, "fail_streak", str(cur + 1))
                except Exception:
                    pass

            if decision.mode == "immediate":
                self._deliver_now(umo, decision, result, now_dt, tool_receipts, event=event)
                return

            if decision.mode == "delayed" and decision.send_at is not None:
                pid = self.store.add_pending(
                    umo, decision.send_at.timestamp(), decision.content, origin="delayed",
                    communication=self.l2.communication_payload(decision), events=decision.shared_events,
                )
                self.store.set_last_write_ts(umo, now_dt.timestamp())
                result.chain = []
                logger.info(
                    "[spectrum] 延迟投递已排队 #%d，%s 发送",
                    pid,
                    decision.send_at.isoformat(timespec="seconds"),
                )
                return


            self.store.set_last_write_ts(umo, now_dt.timestamp())
            result.chain = []
            logger.info("[spectrum] 沉默（seen=%s），未发送任何消息", decision.seen)

    def _take_current_person(self, umo: str) -> str:
        entry = self._current_person.pop(umo, None)
        if not entry:
            return ""
        person_id, ts = entry
        if time.time() - ts > 300:
            logger.warning("[spectrum·群像] 发言人锚点已过期(%.0fs)，本轮不归属",
                           time.time() - ts)
            return ""
        return person_id

    @staticmethod
    def _set_chain_text(result, text: str) -> None:
        new_chain = [Plain(text)]
        for comp in result.chain:
            if not isinstance(comp, Plain):
                new_chain.append(comp)
        result.chain = new_chain

    def _deliver_now(self, umo: str, decision, result, now_dt: datetime,
                     tool_receipts: dict[str, int] | None = None, *, event=None) -> None:
        """设置待发文字并安装本轮回执跟踪；此处尚未确认送达。"""
        self._set_chain_text(result, decision.content)
        if event is not None:
            track_reply(event, decision, tool_receipts or {}, self._plain_text)

    @filter.after_message_sent()
    async def on_after_message_sent(self, event: AstrMessageEvent) -> None:
        """仅登记回执已经收集到的内容，完整发送时才确认相关沟通效果。"""
        delivery = event.get_extra("_spectrum_reply_delivery")
        if delivery is None or delivery.recorded or not delivery.text:
            return
        try:
            self.l2.confirm_reply(event.unified_msg_origin, delivery.decision, delivery.text,
                                  complete=delivery.complete, delivery_receipts=delivery.receipts)
        except Exception:
            logger.exception("[spectrum·投递回执] 消息已送达，但记账失败")
        else:
            delivery.recorded = True

    @staticmethod
    def _plain_text(result) -> str:
        parts: list[str] = []
        for comp in result.chain or []:
            text = getattr(comp, "text", None)
            if isinstance(comp, Plain) and text:
                parts.append(text)
        return "".join(parts).strip()

    @staticmethod
    def _rescue(raw: str) -> str:
        m = _CONTENT_RESCUE.search(raw)
        if not m:
            return ""
        try:
            return (
                m.group(1)
                .encode("utf-8")
                .decode("unicode_escape")
                .encode("latin-1", "ignore")
                .decode("utf-8", "ignore")
                .strip()
            )
        except Exception:
            return m.group(1).strip()


    _V20_REASK = (
        "【我上一轮写到一半就停了】\n\n"
        "{raw}\n\n"
        "【情况】\n\n"
        "上面这段没有被识别的收尾段，所以这一轮**一个字都没能发出去**。\n"
        "系统看到的是：{error}\n\n"
        "【现在】\n\n"
        "接着上面写下去，把这一轮写完，必须包含完整的 `<reply>` 段。\n"
        "不要重写前面已经写过的部分，不要解释，也不要道歉。\n"
    )

    async def _reask_for_decision(self, umo: str, raw: str, error: str, *, event=None):
        try:
            if not self._cfg("v20_reask_enabled", True):
                return None
            request_data = event.get_extra("_spectrum_request", {}) if event is not None else {}
            sys_p = request_data.get("system", self._last_sys.get(umo))
            if not sys_p:
                logger.warning("[spectrum] V20 跳过：没有缓存到 system prompt")
                return None
            prov = await self._pick_provider(umo)
            if prov is None:
                return None
            resp = await freeze_mod.call_model(prov,
                system_prompt=sys_p,
                prompt=self._V20_REASK.format(raw=raw[:2000], error=error),
                contexts=request_data.get("contexts", self._last_ctx.get(umo)) or None,
                extra_user_content_parts=(list(request_data.get("extra_user_content_parts", []))
                    + temporary_context("【这一轮的来信】\n" + request_data["prompt"]
                                        if request_data.get("prompt") else "")),
            )
            text = (getattr(resp, "completion_text", "") or "").strip()
            if not text:
                logger.warning("[spectrum] V20 重问返回空")
                return None
            tz = str(self._cfg("timezone", "Asia/Shanghai"))
            return parse_decision(normalize_tags(text), clock.now(tz))
        except Exception:
            logger.exception("[spectrum] V20 重问异常，交回调用方走终态兜底")
            return None
    async def _pick_provider(self, umo: str, *, frontend: bool = False, selected_id=None):
        pid = (str(selected_id or "").strip() if frontend else
               str(self._cfg("provider_id", "") or "").strip())
        if pid:
            try:
                prov = self.context.get_provider_by_id(pid)
                if prov is not None:
                    return prov
                logger.warning("[spectrum] 配置的 provider %r 没找到，退回默认", pid)
            except Exception:
                logger.exception("[spectrum] 按 id 取 provider 失败，退回默认")
        try:
            return await self.context.get_using_provider_async(umo)
        except Exception:
            logger.exception("[spectrum] 连默认 provider 都拿不到")
            return None

    async def _delivery_loop(self) -> None:
        interval = max(5, int(self._cfg("poll_seconds", 10)))
        while True:
            try:
                await asyncio.sleep(interval)
                await freeze_mod.tick_probe()
                await freeze_mod.wait_if_frozen()
                await self.l2.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                freeze_mod.record_failure("scheduler", error)
                logger.exception("[spectrum] 投递循环异常")


    async def _run_tool_calls(self, event, umo: str, calls: list[dict]) -> dict[str, int]:
        """在本轮可用工具中执行至多四次文本调用，持续核对运行代次。"""
        tool_set = event.get_extra("_spectrum_request", {}).get("tools", self._last_tools.get(umo))
        if tool_set is None:
            return {}
        names = [getattr(t, "name", "") for t in
                 (getattr(tool_set, "tools", None) or [])]
        allowed = {n for n in names if n}

        try:
            from astrbot.core.agent.run_context import ContextWrapper
            from astrbot.core.astr_agent_context import AstrAgentContext
        except Exception:
            logger.exception("[spectrum·工具] 拿不到执行上下文类型，跳过工具调用")
            return {}

        wrapper = ContextWrapper(
            AstrAgentContext(context=self.context, event=event)
        )

        for call in calls[:4]:
            self._check_turn_runtime(event)
            name = str(call.get("name") or "")
            args = call.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}

            if name not in allowed:
                logger.warning(
                    "[spectrum·工具] 模型想调 %r，但它不在本轮可用列表里，已拒绝", name
                )
                continue

            tool = tool_set.get_tool(name)
            if tool is None:
                logger.warning("[spectrum·工具] %r 在 ToolSet 里找不到", name)
                continue


            props = ((getattr(tool, "parameters", None) or {}).get("properties") or {})
            valid = {k: v for k, v in args.items() if (not props or k in props)}


            if name == _TOOL_SEND_NAME:
                _tgt = str(valid.get("session") or "").strip()
                if _tgt == umo:
                    logger.warning(
                        "[spectrum·工具] 拒绝「发给自己」：session 就是当前会话 %s",
                        _tgt.replace("default:", ""),
                    )
                    continue

            try:
                logger.info("[spectrum·工具] 调用 %s，参数 %s",
                            name, json.dumps(valid, ensure_ascii=False)[:200])
                outcome = await tool.call(wrapper, **valid)
                logger.info("[spectrum·工具] %s 执行完成", name)
                if name == _TOOL_SEND_NAME:
                    result_text = str(outcome)
                    marker = "Message sent to session "
                    if not result_text.startswith(marker):
                        logger.warning("[spectrum·工具] %s 未送达：%s",
                                       name, result_text[:160])
            except freeze_mod.FrozenSkipped:
                raise
            except Exception as error:
                freeze_mod.record_failure("tool", error)

                logger.exception("[spectrum·工具] %s 执行失败", name)
            self._check_turn_runtime(event)
        return dict(event.get_extra("_spectrum_delivery_receipts", {}) or {})


    @filter.command("叙述事实")
    async def observe_cmd(self, event: AstrMessageEvent, scene: str = ""):
        """解析外部事实，经内容规则与受众判定后写入；成功与拒绝均不回复。"""
        umo = event.unified_msg_origin


        text = observed_scene_from(getattr(event, "message_str", "") or "", scene)
        if not text:
            logger.info("[spectrum·看见] 没解析出场景，跳过（原文 %r）",
                        getattr(event, "message_str", ""))
            return


        reason = observed_reject_reason(text)
        if reason:
            logger.info("[spectrum·看见] 被合理性审查拦下：%s（原文 %.60s）",
                        reason, text)
            return


        try:
            aud = observed_audience(self.store, self.l2.life, text)
        except Exception:
            logger.exception("[spectrum·看见] 受众判定失败，**按拒绝处理**")
            aud = None
        if aud is None:
            logger.info("[spectrum·看见] 点名人物没有可用会话，拒写（原文 %.60s）", text)
            return

        try:
            eid = self.store.upsert_observed(
                text, umo=umo, life_id=self.l2.life.current_life_id(),
                audience=aud)
        except Exception:
            logger.exception("[spectrum·看见] 写入失败")
            return
        logger.info("[spectrum·看见] 记下一条亲眼看见的事（#%s，%s）：%.60s",
                    eid, _aud.render_obj(aud), text)


        return

    @filter.command("叙述查看")
    async def observed_list_cmd(self, event: AstrMessageEvent, _arg: str = ""):
        """列出最近十条外部事实记录及受众，供核对静默写入结果。"""
        try:
            rows = self.store.observed_rows(limit=10)
        except Exception:
            logger.exception("[spectrum·看见] 读取失败")
            yield event.plain_result("读取失败，看看日志。")
            return
        if not rows:
            yield event.plain_result("还没有注入过。用法：/叙述事实 一段场景")
            return
        yield event.plain_result(
            "〔我眼前的世界〕最近的在前\n" + render_observed_rows(rows))

    @filter.command("叙述删除")
    async def observed_delete_cmd(self, event: AstrMessageEvent, action: str = ""):
        """按范围删除叙述来源；不回滚已整理到其他记录的内容。"""
        cands = [(action or "").strip()]
        try:
            cands.append(re.sub(r"^[／/]?\s*叙述删除\s*", "",
                                (getattr(event, "message_str", "") or "")).strip())
        except Exception:
            pass
        mode = next((m for m in (observed_delete_mode(c) for c in cands) if m), None)
        if mode is None:
            yield event.plain_result(
                "用法：/叙述删除 all（全部删）｜最近（删最近 3 条）"
                "｜历史（只保留最近 3 条）")
            return
        try:
            n = self.store.delete_observed(mode, keep=3)
        except Exception:
            logger.exception("[spectrum·看见] 删除失败")
            yield event.plain_result("删除失败，看看日志。")
            return
        logger.warning("[spectrum·看见] /叙述删除 %s → 删了 %d 条", mode, n)
        if mode == "all":
            yield event.plain_result(f"已全部删除（{n} 条）。")
        elif mode == "recent":
            yield event.plain_result(f"已删除最近 {n} 条。")
        else:
            yield event.plain_result(f"已删除 {n} 条，保留最近 3 条。")

    @filter.command("光谱核心")
    async def spectrum_cmd(self, event: AstrMessageEvent, action: str = ""):
        """处理会话接管、承诺、模式、生活、睡眠和冻结管理指令。"""
        umo = event.unified_msg_origin
        raw = f"{action or ''} {getattr(event, 'message_str', '') or ''}"
        tokens = raw.replace("光谱核心", " ").split()
        a = tokens[0].lower() if tokens else "status"
        args = tokens[1:]

        if a == "list":
            lines = ["〔承诺〕"]
            for r in self.store.list_intents(umo):
                due = (datetime.fromtimestamp(r["not_before"]).strftime("%m-%d %H:%M")
                       if r["not_before"] else "?")
                lines.append(f"  #{r['id']} [{r['status']}] {due}  {r['summary'][:24]}")
            lines.append("〔延迟待发〕")
            for r in self.store.list_pending(umo):
                due = (datetime.fromtimestamp(r["due_ts"]).strftime("%m-%d %H:%M")
                       if r["due_ts"] else "?")
                lines.append(f"  #{r['id']} [{r['status']}] {due}  {r['content'][:24]}")
            yield event.plain_result("\n".join(lines))
            return

        if a == "cancel":
            if not args:
                yield event.plain_result("用法：/光谱核心 cancel <id>（id 见 /光谱核心 list）")
                return
            iid = args[0]
            if not iid.isdigit():
                yield event.plain_result("id 必须是数字")
                return
            iid = int(iid)
            n1 = self.store.set_intent_status(iid, "cancelled")
            n2 = self.store.mark_pending(iid, "cancelled")
            yield event.plain_result(f"已取消 intent #{iid}（{n1} 行）/ pending #{iid}（{n2} 行）")
            return

        if a == "on":
            self.store.set_state(umo, "fallback", "0")
            self.store.set_state(umo, "fail_streak", "0")
            yield event.plain_result("光谱核心已对本会话重新启用。")
            return
        if a == "off":
            self.store.set_state(umo, "fallback", "1")
            yield event.plain_result("光谱核心已对本会话关闭，恢复普通回复模式。")
            return


        if a in ("prefill", "think", "夺舍"):
            yield event.plain_result("正式版使用普通角色合约与框架原生工具。夺舍续写仅在仓库的 experimental/possession 实验分支提供。")
            return
        if a in ("default", "contract", "合约", "默认", "mode"):
            yield event.plain_result("当前为普通角色合约模式；角色思考记录由插件配置控制，工具由 AstrBot 原生执行。")
            return

        if a in ("where", "位置", "主线", "life"):
            owner = self.l2.life.get_owner()

            st = _render_life_state(self.l2.life.get_state_text())
            plans = self.l2.life.render_plans() or "（无）"
            if owner:
                who = owner.get("with") or "?"
                sess = str(owner.get("session", "")).replace("default:", "")
                since = owner.get("since")
                when = (datetime.fromtimestamp(since).strftime("%m-%d %H:%M")
                        if since else "?")
                own_line = f"〔主线归属〕跟「{who}」外出中（{when} 起）\n              会话：{sess}"
            else:
                own_line = "〔主线归属〕她自己（没有外出）"
            yield event.plain_result(
                f"{own_line}\n"
                f"〔当前现状〕{st}\n"
                f"〔行程〕\n{plans}\n"
                f"（所有会话都能贡献主线内容；物理位置由「外出中」那条锁住）"
            )
            return

        if a in ("out", "外出", "出门"):


            tgt = args[0] if args else umo
            if ":" not in tgt:
                yield event.plain_result(
                    "会话地址形如 `default:FriendMessage:123456789`。\n"
                    "不填则默认归到**本会话**。地址可用 /光谱核心 sessions 查。")
                return
            pid = L2._person_of_umo(tgt)
            who = (self.store.get_person_name(pid) if pid
                   else self.store.get_state(tgt, "group_name") or tgt)
            _prev = self.l2.life.current_life_id()
            self.l2.life.set_owner(session=tgt, who=who or tgt)

            if _prev and _prev != self.l2.life.current_life_id():
                self.l2.queue_life_title(_prev)
            yield event.plain_result(
                f"已把主线交给「{who or tgt}」的会话。\n"
                f"在她 /光谱核心 back 之前，别的会话只能读、不能改主线。")
            return

        if a in ("back", "回来", "回家", "归位"):
            old = self.l2.life.get_owner()
            _prev = self.l2.life.current_life_id()
            self.l2.life.set_owner(session="")
            if _prev and _prev != self.l2.life.current_life_id():
                self.l2.queue_life_title(_prev)
            if old:
                yield event.plain_result(
                    f"已把主线归还给她自己（原为跟「{old.get('with') or '?'}」外出）。")
            else:
                yield event.plain_result("主线本来就在她自己手里，无需归还。")
            return

        if a == "set" or a in ("现状", "sethome"):

            text = " ".join(args).strip()
            if not text:
                yield event.plain_result("用法：/光谱核心 set <她此刻的现状>")
                return


            self.l2.life.set_state(text, source=umo)
            yield event.plain_result(f"已改写主线现状：\n{text}")
            return

        if a in ("sessions", "会话"):
            rows = self.store.known_sessions(exclude=None)
            if not rows:
                yield event.plain_result("（还没有已知会话）")
                return
            lines = ["〔已知会话地址〕"]
            for r in rows:
                mark = " ← 本会话" if r["session"] == umo else ""
                lines.append(f"  {r['session']}  {r.get('label','')}{mark}")
            yield event.plain_result("\n".join(lines))
            return

        if a == "sleep":
            mins = int(args[0]) if args and args[0].isdigit() else 0
            if self._cfg("l2_sleep_enabled", False) or mins > 0:
                until = None
                if mins > 0:
                    import time as _t
                    until = _t.time() + mins * 60
                self.store.set_sleep_asleep(until)


                _ow = self.l2.life.get_owner()
                if _ow:
                    self.l2.life.clear_owner_only()
                self.l2.close_life_and_queue_title()
                suffix = f"，预计 {mins} 分钟后醒" if mins > 0 else "，等她自然醒"
                extra = f"\n（主线已从「{_ow.get('with') or '?'}」归还）" if _ow else ""
                yield event.plain_result(
                    f"已强制入睡{suffix}（睡着期间所有会话都不回）{extra}")
            else:
                yield event.plain_result("l2_sleep_enabled=false，无法强制入睡。")
            return
        if a == "wake":
            self.store.set_sleep_awake()


            self.l2.open_segment("home")
            inbox = self.store.take_sleep_inbox()
            yield event.plain_result(f"已唤醒。睡着时错过的未读 {len(inbox)} 条已清空。")
            return


        if a in ("冻结", "解冻", "冻结状态", "冻结重置"):
            yield event.plain_result(freeze_mod.manual(a))
            return

        last = self.store.last_write_ts(umo)
        waiting = self.store.waiting_count(umo)
        slp = self.store.get_sleep()
        if slp.get("until"):
            sleep_text = "睡中（预计 %s 醒，未读 %d 条）" % (
                datetime.fromtimestamp(slp["until"]).strftime("%m-%d %H:%M"),
                len(self.store.get_sleep_inbox()),
            )
        else:
            sleep_text = "清醒"
        _ow = self.l2.life.get_owner()
        owner_text = (f"跟「{_ow.get('with') or '?'}」外出中" if _ow else "她自己")
        yield event.plain_result(
            "光谱核心状态\n"
            f"· 全局开关：{self._cfg('enabled', False)}\n"
            f"· 睡眠（全局）：{sleep_text}\n"
            f"· 主线归属：{owner_text}"
            f"{'（本会话是位置主人）' if self.l2.life.is_owner(umo) else ''}\n"
            f"· 本会话接管：{'否（已退避）' if self.store.get_state(umo, 'fallback') == '1' else '是'}\n"
            f"· 连续解析失败：{self.store.get_state(umo, 'fail_streak', '0')}\n"
            f"· 待发延迟消息：{waiting}\n"
            f"· 上次写作：{datetime.fromtimestamp(last).isoformat(timespec='seconds') if last else '无'}\n"
            "\n〔主线指令〕where 查位置 · out [会话] 设外出 · back 归位 · set <话> 改现状 · sessions 列会话"
        )
