"""注册光谱数据面板接口，并把显式编辑操作交给存储与生活模块。"""


from __future__ import annotations
from typing import Any
from quart import request
import time
from datetime import datetime

from . import worldbook as _worldbook

class PageApi:
    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self.store = plugin.store
        self.prefix = "/astrbot_plugin_spectrum_core/page"

    def register_routes(self) -> None:
        register = self.plugin.context.register_web_api


        register(f"{self.prefix}/overview", self.get_overview, ["GET"], "光谱核心概览统计")


        register(f"{self.prefix}/intents", self.list_intents, ["GET"], "列表所有承诺")
        register(f"{self.prefix}/intents/add", self.add_intent_manual, ["POST"], "手动增加承诺")
        register(f"{self.prefix}/intents/update", self.update_intent, ["POST"], "更新/改期承诺")
        register(f"{self.prefix}/intents/cancel", self.cancel_intent, ["POST"], "取消承诺")


        register(f"{self.prefix}/pending", self.list_pending, ["GET"], "列表所有延迟消息")
        register(f"{self.prefix}/pending/update", self.update_pending, ["POST"], "手动修正消息内容/时刻")
        register(f"{self.prefix}/pending/cancel", self.cancel_pending, ["POST"], "取消延迟消息")


        register(f"{self.prefix}/entries", self.list_entries, ["GET"], "列表往来记录")


        register(f"{self.prefix}/entries/by_ids", self.list_entries_by_ids, ["GET"],
                 "按 id 取往来原文（展开认知来源）")
        register(f"{self.prefix}/entries/update", self.update_entry, ["POST"], "手动修正往来记录文本")
        register(f"{self.prefix}/entries/delete", self.delete_entry, ["POST"], "彻底删除往来记录")


        register(f"{self.prefix}/life/state", self.get_life_state, ["GET"], "读全局生活主线")
        register(f"{self.prefix}/life/state/set", self.set_life_state, ["POST"], "改全局生活主线")

        register(f"{self.prefix}/lives", self.list_lives, ["GET"], "生活段列表（时间轴）")
        register(f"{self.prefix}/lives/detail", self.get_life, ["GET"], "单段生活详情")
        register(f"{self.prefix}/lives/update", self.rename_life, ["POST"], "改生活段的字段（标题/类型/起止/跟谁）")
        register(f"{self.prefix}/lives/delete", self.delete_life, ["POST"], "删掉一段生活（连带段里的记录）")
        register(f"{self.prefix}/lives/unlock", self.unlock_life, ["POST"], "解除外出锁（让她归家）")

        register(f"{self.prefix}/l3/state", self.get_l3_state, ["GET"], "读取 L3 状态")
        register(f"{self.prefix}/l3/update", self.update_l3_state, ["POST"], "更新 L3 状态")
        register(f"{self.prefix}/l3/reset_clock", self.reset_clock, ["POST"], "重置叙事时钟")
        register(f"{self.prefix}/l3/clear_volatile", self.clear_volatile, ["POST"], "清空待合流变动")


        register(f"{self.prefix}/memories", self.list_memories, ["GET"], "列表长期记忆")
        register(f"{self.prefix}/memories/add", self.add_memory, ["POST"], "手动添加记忆")
        register(f"{self.prefix}/memories/update", self.update_memory, ["POST"], "编辑记忆")
        register(f"{self.prefix}/memories/delete", self.delete_memory, ["POST"], "删除记忆")


        register(f"{self.prefix}/persons", self.list_persons, ["GET"], "列表人物档案")
        register(f"{self.prefix}/persons/detail", self.get_person_detail, ["GET"], "人物档案详情")
        register(f"{self.prefix}/persons/update", self.update_person, ["POST"], "编辑人物档案")
        register(f"{self.prefix}/persons/delete", self.delete_person, ["POST"], "删除人物档案")
        register(f"{self.prefix}/persons/memories/add", self.add_person_memory, ["POST"], "手动添加关于某人的认知")
        register(f"{self.prefix}/persons/memories/delete", self.delete_person_memory, ["POST"], "删除关于某人的认知")


        register(f"{self.prefix}/umos", self.list_umos, ["GET"], "列表所有 UMO 会话标识")


        register(f"{self.prefix}/director/state", self.get_director_state, ["GET"], "读导演故事状态")
        register(f"{self.prefix}/director/state/set", self.set_director_state, ["POST"], "改导演故事状态（白名单字段）")
        register(f"{self.prefix}/director/storylets", self.list_director_storylets, ["GET"], "列表所有 storylet")
        register(f"{self.prefix}/director/storylets/upsert", self.upsert_director_storylet, ["POST"], "写/改一条 storylet")
        register(f"{self.prefix}/director/storylets/toggle", self.toggle_director_storylet, ["POST"], "启用/禁用一条 storylet")

        register(f"{self.prefix}/director/fires", self.list_director_fires, ["GET"], "列表触发转录（只读）")
        register(f"{self.prefix}/director/threads", self.list_director_threads, ["GET"], "列表叙事线")
        register(f"{self.prefix}/director/threads/mutate", self.mutate_director_thread, ["POST"], "开/收/作废一条叙事线")
        register(f"{self.prefix}/director/vars", self.get_director_vars, ["GET"], "读 Tier2 变量声明")
        register(f"{self.prefix}/director/vars/set", self.set_director_vars, ["POST"], "写 Tier2 变量声明")
        register(f"{self.prefix}/director/simulate", self.simulate_director, ["POST"], "干跑 advance() 返回回合表")


        register(f"{self.prefix}/tools", self.list_tools, ["GET"], "列表当前已注册的工具（自动读取）")
        register(f"{self.prefix}/tools/note", self.set_tool_note, ["POST"], "写/清一个工具的用法说明")


        register(f"{self.prefix}/worldbook/list", self.list_worldbook, ["GET"], "列表世界书条目（含全局配置）")
        register(f"{self.prefix}/worldbook/upsert", self.upsert_worldbook, ["POST"], "新建/更新一条世界书条目")
        register(f"{self.prefix}/worldbook/delete", self.delete_worldbook, ["POST"], "删除世界书条目")
        register(f"{self.prefix}/worldbook/toggle", self.toggle_worldbook, ["POST"], "启用/停用一条世界书条目")

    def ok(self, data: Any = None):
        return {"status": "ok", "data": data}

    def error(self, message: str):
        return {"status": "error", "message": message}

    async def get_overview(self):
        return self.ok({
            "counts": {
                "intents": self.store.count_rows("intents"),
                "pending": self.store.count_rows("pending"),
                "entries": self.store.count_rows("entries"),
            },
            "enabled": self.plugin._cfg("enabled", False),
            "l2_commitments": self.plugin._cfg("l2_commitments_enabled", False),
            "l2_advance": self.plugin._cfg("l2_advance_enabled", False),
        })


    async def get_director_state(self):
        return self.ok(self.store.get_story_state())

    async def set_director_state(self):
        body = await request.get_json() or {}
        if not isinstance(body, dict) or not body:
            return self.error("没有要改的字段")
        allowed = {"tension", "arc_target", "phase", "scene_id", "ending_name",
                   "intervention_budget"}
        upd = {k: body[k] for k in body if k in allowed}
        if not upd:
            return self.error("没有白名单内的字段；可改：%s" % "、".join(sorted(allowed)))
        cur = self.store.get_story_state()
        cur.update(upd)
        self.store.set_story_state(cur)
        return self.ok({"updated": sorted(upd)})

    async def list_director_storylets(self):
        rows = self.store.storylets_all(include_disabled=True)
        return self.ok({"storylets": [dict(r) for r in rows]})

    @staticmethod
    def _json_field(body, key):
        v = body.get(key)
        if v is None:
            return None
        if isinstance(v, str):
            return v
        import json
        return json.dumps(v, ensure_ascii=False)

    async def upsert_director_storylet(self):
        body = await request.get_json() or {}
        sid = str(body.get("sid") or "").strip()
        if not sid:
            return self.error("缺少 sid")
        fields = {}
        for k in ("kind", "enabled", "scope", "scene_id", "got_scene_id",
                  "ending_name", "k_turns", "priority", "weight"):
            if body.get(k) is not None:
                fields[k] = body[k]
        for k in ("narrative", "condition", "depends_on", "effects",
                  "eval_annot", "meta"):
            v = self._json_field(body, k)
            if v is not None:
                fields[k] = v
        try:
            self.store.upsert_storylet(sid, **fields)
        except ValueError as e:
            return self.error(str(e))
        return self.ok({"sid": sid})

    async def toggle_director_storylet(self):
        body = await request.get_json() or {}
        sid = str(body.get("sid") or "").strip()
        if not sid:
            return self.error("缺少 sid")
        enabled = bool(body.get("enabled"))
        self.store.set_storylet_enabled(sid, enabled)
        return self.ok({"sid": sid, "enabled": enabled})


    @staticmethod
    def _tool_source(mgr, tool) -> str:
        name = str(getattr(tool, "name", "") or "")
        try:
            if mgr is not None and mgr.is_builtin_tool(name):
                return "框架内置"
        except Exception:
            pass
        path = str(getattr(tool, "handler_module_path", "") or "")
        if not path:
            return ""

        parts = path.split(".")
        for p in parts:
            if p.startswith("astrbot_plugin_"):
                return p
        return parts[-2] if len(parts) >= 2 else path

    def _her_specs(self) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        cache = getattr(getattr(self.plugin, "l2", None), "_tool_names", None) or {}
        for specs in cache.values():
            for s in (specs or []):
                if not isinstance(s, dict):
                    continue
                n = str(s.get("name") or "")
                if not n or n in seen:
                    continue
                seen.add(n)
                out.append({
                    "name": n,
                    "description": str(s.get("description") or "").strip(),
                })
        return out

    def _enumerate_tools(self) -> tuple[list[dict], str]:
        hers = self._her_specs()
        catalog, cat_src = self._catalog_tools()
        by = {t["name"]: t for t in catalog}

        if not hers:


            return (catalog, f"{cat_src} · 还没收到过消息，先列框架注册表") if catalog \
                else ([], "枚举失败")

        out: list[dict] = []
        for s in hers:
            n = s["name"]
            m = by.get(n, {})
            out.append({
                "name": n,
                "description": s["description"] or str(m.get("description") or "").strip(),
                "active": bool(m.get("active", True)),
                "source": m.get("source") or "（不在框架注册表里）",
            })
        return out, f"她当前持有的工具 · {len(out)} 个 · 来源标注取自框架注册表"

    def _catalog_tools(self) -> tuple[list[dict], str]:
        ctx = getattr(self.plugin, "context", None)
        getter = getattr(ctx, "get_llm_tool_manager", None)
        mgr = None
        if callable(getter):
            try:
                mgr = getter()
            except Exception:
                mgr = None

        if mgr is not None:


            raw: list = []
            try:
                raw += list(getattr(mgr.get_full_tool_set(), "tools", None) or [])
            except Exception:
                pass
            try:
                raw += list(mgr.iter_builtin_tools() or [])
            except Exception:
                pass

            out, seen = [], set()
            for t in raw:
                name = str(getattr(t, "name", "") or "")
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append({
                    "name": name,
                    "description": str(getattr(t, "description", "") or "").strip(),
                    "active": bool(getattr(t, "active", True)),
                    "source": self._tool_source(mgr, t),
                })
            if out:
                return out, "框架工具管理器"


        out: list[dict] = []
        seen: set[str] = set()
        try:
            cache = getattr(getattr(self.plugin, "l2", None), "_tool_names", None) or {}
            for specs in cache.values():
                for s in (specs or []):
                    n = str((s or {}).get("name") or "")
                    if not n or n in seen:
                        continue
                    seen.add(n)
                    out.append({
                        "name": n,
                        "description": str(s.get("description") or "").strip(),
                        "active": True,
                        "source": "（会话缓存）",
                    })
        except Exception:
            pass
        return out, ("会话缓存兜底（拿不到框架工具管理器）" if out else "枚举失败")

    async def list_tools(self):
        tools, source = self._enumerate_tools()
        notes = self.store.tool_notes_all()
        names = {t["name"] for t in tools}
        for t in tools:
            t["note"] = notes.get(t["name"], "")
        return self.ok({
            "tools": tools,
            "source": source,


            "notes_orphan": sorted(n for n in notes if n not in names),
        })

    async def set_tool_note(self):
        body = await request.get_json() or {}
        name = str(body.get("name") or "").strip()
        if not name:
            return self.error("缺少 name")
        note = str(body.get("note") or "").strip()
        self.store.tool_note_set(name, note)
        return self.ok({"name": name, "note": note})

    async def list_director_fires(self):
        try:
            limit = int(request.args.get("limit", 500))
        except Exception:
            limit = 500
        rows = self.store.director_fires_all(limit=limit)
        return self.ok({"fires": [dict(r) for r in rows]})

    async def list_director_threads(self):
        state = request.args.get("state", "")
        rows = self.store.threads_all(state)
        return self.ok({"threads": [dict(r) for r in rows]})

    async def mutate_director_thread(self):
        body = await request.get_json() or {}
        tid = str(body.get("tid") or "").strip()
        action = str(body.get("action") or "").strip()
        if not tid:
            return self.error("缺少 tid")
        if action == "open":
            self.store.open_thread(
                tid, title=str(body.get("title") or ""),
                element=str(body.get("element") or ""),
                visibility=str(body.get("visibility") or ""),
                scope=str(body.get("scope") or ""),
                kind=str(body.get("kind") or ""),
                payoff_form=str(body.get("payoff_form") or ""),
                planned_payoff=str(body.get("planned_payoff") or ""),
                trigger=(body.get("trigger") if isinstance(body.get("trigger"), dict) else None),
                open_beat=int(body.get("open_beat") or 0),
                due_beats=int(body.get("due_beats") or 0))
        elif action == "pay":
            self.store.pay_thread(tid)
        elif action == "close":
            self.store.close_thread(tid)
        elif action == "abandon":
            self.store.abandon_thread(tid, reason=str(body.get("reason") or ""))
        else:
            return self.error("未知 action；可填 open / pay / close / abandon")
        return self.ok({"tid": tid, "action": action})

    async def get_director_vars(self):
        import json
        raw = self.store.get_state("global", "director_vars", "") or "{}"
        try:
            decls = json.loads(raw)
        except Exception:
            decls = {}
        return self.ok({"vars": decls if isinstance(decls, dict) else {}})

    async def set_director_vars(self):
        body = await request.get_json() or {}
        decls = body.get("vars")
        if not isinstance(decls, dict):
            return self.error("vars 必须是对象")
        import json
        self.store.set_state("global", "director_vars",
                             json.dumps(decls, ensure_ascii=False))
        return self.ok()

    async def simulate_director(self):
        try:
            director = self.plugin.l2.director
        except Exception as e:
            return self.error(f"导演不可用：{e}")
        try:
            plan = await director.advance(time.time())
        except Exception as e:
            return self.error(f"advance 干跑失败：{e}")
        return self.ok({
            "turns": [{
                "kind": t.kind, "sid": t.sid, "umo": t.umo,
                "targets": list(t.targets),
            } for t in plan.turns],
            "considered": plan.considered,
            "dropped": plan.dropped,
            "deferred": plan.deferred,
            "note": plan.note,
            "phase": plan.phase,
        })


    async def get_life_state(self):
        from .life import LifeLine
        life = LifeLine(self.store)
        return self.ok({
            "state": life.get_state_text(),
            "owner": life.get_owner(),
            "plans": life.render_plans(),
            "life_id": life.current_life_id(),
        })

    async def set_life_state(self):
        from .life import LifeLine
        body = await request.get_json() or {}
        text = str(body.get("state") or "").strip()
        if not text:
            return self.error("状态不能为空")
        LifeLine(self.store).set_state(text, source="panel")
        return self.ok({"state": text})


    async def list_lives(self):
        args = request.args
        try:
            limit = int(args.get("limit", 50))
            offset = int(args.get("offset", 0))
        except Exception:
            limit, offset = 50, 0
        rows = self.store.list_lives(limit=limit, offset=offset)
        current = (self.store.get_state("global", "life_current_id", "") or "")
        return self.ok({
            "current": current,
            "count": len(rows),
            "lives": [{
                "life_id": r["life_id"],
                "kind": r["kind"],
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "owner_session": r["owner_session"],
                "with_who": r["with_who"],
                "title": r["title"],
                "summary": r["summary"],
                "ongoing": r["ended_at"] is None,
            } for r in rows],
        })

    async def get_life(self):
        life_id = request.args.get("life_id", "")
        if not life_id:
            return self.error("缺少 life_id")
        row = self.store.get_life(life_id)
        if not row:
            return self.error("没有这段生活：%s" % life_id)


        try:
            total = self.store._db.execute(
                "SELECT COUNT(*) FROM entries WHERE life_id=?", (life_id,)
            ).fetchone()[0]
        except Exception:
            total = 0
        entries = self.store.entries_of_life(life_id, limit=2000)
        out = []
        for e in entries:
            meta = self.store.parse_meta(e["meta"]) if hasattr(self.store, "parse_meta") else {}
            out.append({
                "id": e["id"],
                "kind": e["kind"],
                "ts": e["ts"],
                "text": e["text"],
                "sender": (meta.get("sender") or "") if isinstance(meta, dict) else "",
                "umo": e["umo"],
            })
        return self.ok({
            "life": {
                "life_id": row["life_id"], "kind": row["kind"],
                "started_at": row["started_at"], "ended_at": row["ended_at"],
                "owner_session": row["owner_session"], "with_who": row["with_who"],
                "title": row["title"], "summary": row["summary"],
                "ongoing": row["ended_at"] is None,
            },
            "entries": out,
            "total": total,
            "shown": len(out),
        })

    @staticmethod
    def _life_ts(v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        s = str(v or "").strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    async def rename_life(self):
        body = await request.get_json() or {}
        life_id = body.get("life_id")
        if not life_id:
            return self.error("缺少 life_id")
        fields: dict = {}
        for k in ("title", "summary", "with_who"):
            if k in body:
                fields[k] = str(body.get(k) or "")
        if "kind" in body:
            kind = str(body.get("kind") or "").strip()
            if kind not in ("home", "outing"):
                return self.error("类型只能是 home / outing")
            fields["kind"] = kind
        if "started_at" in body and str(body.get("started_at") or "").strip():
            ts = self._life_ts(body.get("started_at"))
            if ts is None:
                return self.error("开始时间认不出，用 2026-10-04T21:30:00 这种格式")
            fields["started_at"] = ts
        if "ended_at" in body:
            v = body.get("ended_at")
            if v in (None, ""):
                fields["ended_at"] = None
            else:
                ts = self._life_ts(v)
                if ts is None:
                    return self.error("结束时间认不出，用 2026-10-04T21:30:00 这种格式")
                fields["ended_at"] = ts
        if not fields:
            return self.error("没有要改的字段")
        try:
            self.store.update_life_fields(life_id, **fields)
        except ValueError as e:
            return self.error(str(e))


        released = False
        if fields.get("ended_at"):
            from .life import LifeLine
            released = LifeLine(self.store).release_current(life_id)
        return self.ok({"life_id": life_id, "updated": sorted(fields),
                        "released": released})

    async def delete_life(self):
        from .life import LifeLine
        body = await request.get_json() or {}
        life_id = str(body.get("life_id") or "").strip()
        if not life_id:
            return self.error("缺少 life_id")
        n = LifeLine(self.store).delete_life(life_id)
        if not n.get("lives"):
            return self.error("没有这一段")
        return self.ok(n)

    async def unlock_life(self):
        from .life import LifeLine
        life = LifeLine(self.store)
        before = life.get_owner()
        life.set_owner(session="")
        return self.ok({"was": before, "now": life.current_life_id()})

    async def get_l3_state(self):
        args = request.args
        umo = args.get("umo")
        if not umo: return self.error("缺少 umo")


        from .life import LifeLine
        return self.ok({
            "continuity": self.store.get_continuity(umo),
            "volatile": self.store.get_volatile_changes(umo),
            "plan": {},
            "willingness": self.store.get_willingness(umo),


            "last_summary": self.store.last_summary_ts(umo),
            "life": {
                "state": LifeLine(self.store).get_state_text(),
                "owner": LifeLine(self.store).get_owner(),
            },
        })

    async def update_l3_state(self):
        body = await request.get_json() or {}
        umo = body.get("umo")
        if not umo: return self.error("缺少 umo")

        if "willingness" in body:
            try: self.store.set_willingness(umo, float(body["willingness"]))
            except: pass

        return self.ok()

    async def clear_volatile(self):
        body = await request.get_json() or {}
        umo = body.get("umo")
        if not umo: return self.error("缺少 umo")
        self.store.clear_volatile_changes(umo)
        return self.ok()

    async def reset_clock(self):
        body = await request.get_json() or {}
        umo = body.get("umo")
        if not umo: return self.error("缺少 umo")
        self.store.set_last_write_ts(umo, time.time())
        return self.ok()

    async def list_intents(self):
        args = request.args
        limit = int(args.get("limit", 100))
        offset = int(args.get("offset", 0))
        umo = args.get("umo")

        status = None if args.get("all") == "1" else "waiting"

        rows = self.store.list_all_intents(limit=limit, offset=offset, status=status, umo=umo)
        return self.ok({
            "items": [dict(r) for r in rows],
            "total": self.store.count_rows("intents", umo=umo)
        })

    async def update_intent(self):
        body = await request.get_json() or {}
        iid = body.get("id")
        summary = body.get("summary")
        not_before_iso = body.get("not_before")

        if not iid: return self.error("缺少 id")

        sql = "UPDATE intents SET id=id"
        params = []
        if summary:
            sql += ", summary=?"
            params.append(summary)
        if not_before_iso:
            try:
                dt = datetime.fromisoformat(not_before_iso.replace("Z", "+00:00"))
                sql += ", not_before=?"
                params.append(dt.timestamp())
                sql += ", status='waiting'"
            except: return self.error("时间格式错误")

        sql += " WHERE id=?"
        params.append(iid)

        with self.store._lock:
            self.store._db.execute(sql, params)
            self.store._db.commit()
        return self.ok()

    async def add_intent_manual(self):
        body = await request.get_json() or {}
        umo = body.get("umo")
        summary = body.get("summary")
        not_before_iso = body.get("not_before")
        if not umo or not summary or not not_before_iso:
            return self.error("信息不全")

        try:
            dt = datetime.fromisoformat(not_before_iso.replace("Z", "+00:00"))
            ts = dt.timestamp()
            self.store.add_intent(umo, "manual", summary, ts)
            return self.ok()
        except: return self.error("时间格式错误")

    async def cancel_intent(self):
        body = await request.get_json() or {}
        iid = body.get("id")
        if not iid: return self.error("缺少 id")
        count = self.store.set_intent_status(iid, "cancelled")
        return self.ok({"count": count})

    async def list_pending(self):
        args = request.args
        limit = int(args.get("limit", 100))
        offset = int(args.get("offset", 0))
        umo = args.get("umo")

        status = None if args.get("all") == "1" else "active"

        rows = self.store.list_all_pending(limit=limit, offset=offset, status=status, umo=umo)
        return self.ok({
            "items": [dict(r) for r in rows],
            "total": self.store.count_rows("pending", umo=umo)
        })

    async def update_pending(self):
        body = await request.get_json() or {}
        pid = body.get("id")
        content = body.get("content")
        due_iso = body.get("due_at")

        if not pid: return self.error("缺少 id")
        if body.get("retry"):
            if not self.store.retry_uncertain_pending(pid):
                return self.error("只有中断后结果待核对的消息可恢复")
            return self.ok()

        due_ts = None
        if due_iso:
            try:
                dt = datetime.fromisoformat(due_iso.replace("Z", "+00:00"))
                due_ts = dt.timestamp()
            except ValueError:
                return self.error("时间格式错误")
        if not self.store.update_pending(pid, content=content, due_ts=due_ts):
            return self.error("消息不存在或已开始发送，不能修改在途消息")
        return self.ok()

    async def cancel_pending(self):
        body = await request.get_json() or {}
        iid = body.get("id")
        if not iid: return self.error("缺少 id")
        if not self.store.mark_pending(iid, "cancelled"):
            return self.error("消息不存在或已开始发送，不能撤回在途消息")
        return self.ok()

    async def list_entries(self):
        args = request.args
        limit = int(args.get("limit", 100))
        offset = int(args.get("offset", 0))
        kind = args.get("kind")
        umo = args.get("umo")

        rows = self.store.list_all_entries(limit=limit, offset=offset, kind=kind, umo=umo)
        return self.ok({
            "items": [dict(r) for r in rows],
            "total": self.store.count_rows("entries", umo=umo)
        })

    async def list_entries_by_ids(self):
        raw = (request.args.get("ids") or "").strip()
        ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        items = []
        for r in self.store.entries_by_ids(ids):
            sender = str(self.store.parse_meta(r["meta"]).get("sender") or "").strip()
            if r["kind"] == "delivered":
                who = "我"
            else:
                who = sender if sender and sender not in ("某人", "对方", "群友") else "对方"
            try:
                when = datetime.fromtimestamp(float(r["ts"])).strftime("%m-%d %H:%M")
            except Exception:
                when = "时间不详"
            items.append({"id": int(r["id"]), "when": when, "who": who,
                          "kind": str(r["kind"]), "text": str(r["text"] or "")})
        return self.ok({"items": items})


    async def list_persons(self):
        args = request.args
        limit = int(args.get("limit", 100))
        offset = int(args.get("offset", 0))
        rows = self.store.list_all_persons(limit=limit, offset=offset)
        items = []
        for r in rows:
            aliases = [a["alias"] for a in self.store.list_person_aliases(r["person_id"])]
            items.append({
                "person_id": r["person_id"],
                "name": r["name"],
                "impression": r["impression"],
                "affinity": r["affinity"],
                "trust": r["trust"],
                "aliases": aliases,
                "session_count": len(self.store.list_person_sessions(r["person_id"])),
                "memory_count": len(self.store.get_person_memories(r["person_id"], limit=999)),
                "last_seen": r["last_seen"],
            })
        return self.ok({"items": items, "total": self.store.count_persons()})

    async def get_person_detail(self):
        person_id = request.args.get("person_id")
        if not person_id:
            return self.error("缺少 person_id")
        person = self.store.get_person(person_id)
        if not person:
            return self.error("人物不存在")
        return self.ok({
            "person": dict(person),
            "aliases": [dict(r) for r in self.store.list_person_aliases(person_id)],
            "sessions": [dict(r) for r in self.store.list_person_sessions(person_id)],
            "memories": [dict(r) for r in self.store.get_person_memories(person_id, limit=200)],
        })

    async def update_person(self):
        body = await request.get_json() or {}
        person_id = body.get("person_id")
        if not person_id:
            return self.error("缺少 person_id")
        fields = {}
        for k in ("name", "impression"):
            if k in body:
                fields[k] = str(body[k])
        for k in ("affinity", "trust"):
            if k in body:
                try:
                    fields[k] = float(body[k])
                except (TypeError, ValueError):
                    pass
        self.store.update_person(person_id, **fields)
        return self.ok()

    async def delete_person(self):
        body = await request.get_json() or {}
        person_id = body.get("person_id")
        if not person_id:
            return self.error("缺少 person_id")
        self.store.delete_person(person_id)
        return self.ok()

    async def add_person_memory(self):
        body = await request.get_json() or {}
        person_id = body.get("person_id")
        text = body.get("text")
        if not person_id or not text:
            return self.error("缺少 person_id 或 text")

        self.store.add_person_memory(
            person_id, text,
            origin_umo=body.get("origin_umo", ""),
            scope=body.get("scope", "public"),
            importance=body.get("importance", 0.5),
        )
        return self.ok()

    async def delete_person_memory(self):
        body = await request.get_json() or {}
        mid = body.get("id")
        if not mid:
            return self.error("缺少 id")
        self.store.delete_person_memory(mid)
        return self.ok()

    async def list_umos(self):
        umos = self.store.list_umos()
        result = []
        for umo in umos:
            g_name = self.store.get_state(umo, "group_name")
            is_group = "GroupMessage" in umo
            label = umo.split(":")[-1]
            if is_group:
                label = f"〔群〕{g_name or label}"
            else:

                label = f"〔私〕{self.store.last_sender_of(umo) or label}"
            result.append({"id": umo, "label": label, "is_group": is_group})
        return self.ok(result)

    async def update_entry(self):
        body = await request.get_json() or {}
        eid = body.get("id")
        text = body.get("text")
        if not eid or text is None: return self.error("缺少 id 或 text")
        self.store.update_entry_text(eid, text)
        return self.ok()

    async def delete_entry(self):
        body = await request.get_json() or {}
        eid = body.get("id")
        if not eid: return self.error("缺少 id")
        with self.store._lock:
            self.store._db.execute("DELETE FROM entries WHERE id=?", (eid,))
            self.store._db.commit()


        self.store._drop_vectors("entries", [int(eid)])
        return self.ok()

    async def list_memories(self):
        args = request.args
        umo = args.get("umo")
        limit = int(args.get("limit", 100))
        offset = int(args.get("offset", 0))

        rows = self.store.list_all_memories(limit=limit, offset=offset, umo=umo)
        return self.ok({
            "items": [dict(r) for r in rows],
            "total": self.store.count_rows("memories", umo=umo)
        })

    async def add_memory(self):
        body = await request.get_json() or {}
        umo = body.get("umo")
        text = body.get("text")
        if not umo or not text: return self.error("缺少 umo 或 text")
        self.store.add_factual_memory(umo, text, body.get("importance", 0.5))
        return self.ok()

    async def update_memory(self):
        body = await request.get_json() or {}
        mid = body.get("id")
        text = body.get("text")
        if not mid or not text: return self.error("缺少 id 或 text")
        self.store.update_factual_memory(mid, text, body.get("importance"))
        return self.ok()

    async def delete_memory(self):
        body = await request.get_json() or {}
        mid = body.get("id")
        if not mid: return self.error("缺少 id")
        self.store.delete_memory(mid)
        return self.ok()


    _WB_INT_COLS = (
        "selective", "constant", "order_value", "case_sensitive",
        "match_whole_words", "use_regex", "probability", "use_group_scoring",
        "prioritize", "vectorized", "ignore_budget", "sticky", "cooldown",
        "delay", "scan_depth", "exclude_recursion", "prevent_recursion",
        "delay_until_recursion", "enabled",
    )

    @staticmethod
    def _wb_coerce(body) -> dict:
        fields: dict = {}
        for k in ("title", "text", "keys", "secondary", "group_name"):
            if k in body:
                fields[k] = str(body.get(k) or "")
        if "group_weight" in body:
            try:
                fields["group_weight"] = float(body.get("group_weight"))
            except (TypeError, ValueError):
                pass
        for k in PageApi._WB_INT_COLS:
            if k not in body:
                continue
            v = body.get(k)
            if isinstance(v, bool):
                fields[k] = 1 if v else 0
                continue
            try:
                fields[k] = int(v)
            except (TypeError, ValueError):
                pass
        if "selective_logic" in body:

            fields["selective_logic"] = _worldbook.normalize_logic(
                body.get("selective_logic"))
        if "position" in body:

            p = str(body.get("position") or "").strip().lower()
            fields["position"] = (p if p in _worldbook.POSITIONS
                                  else _worldbook.POSITION_L3)
        return fields

    async def list_worldbook(self):
        rows = self.store.worldbook_all(include_disabled=True)
        items = []
        for r in rows:
            d = dict(r)


            d["tokens"] = _worldbook.estimate_tokens(d.get("text"))
            items.append(d)
        cfg = self.plugin._cfg
        return self.ok({
            "items": items,
            "total": len(items),
            "config": {
                "enabled": bool(cfg("worldbook_enabled", False)),
                "scan_depth": int(cfg("worldbook_scan_depth", 2) or 0),
                "max": int(cfg("worldbook_max", 0) or 0),
                "budget_percent": int(cfg("worldbook_budget_percent", 25) or 0),
                "context_tokens": int(cfg("worldbook_context_tokens", 32000) or 0),
                "token_budget": int(cfg("worldbook_token_budget", 0) or 0),
                "recursion": bool(cfg("worldbook_recursion", False)),
                "max_steps": int(cfg("worldbook_max_steps", 3) or 0),
                "vector_enabled": bool(cfg("worldbook_vector_enabled", False)),
            },
        })

    async def upsert_worldbook(self):
        body = await request.get_json() or {}
        fields = self._wb_coerce(body)
        if not fields:
            return self.error("没有可写入的字段")


        if not str(fields.get("text") or "").strip():
            return self.error("正文不能为空")
        try:
            eid = self.store.upsert_worldbook(body.get("id"), **fields)
        except ValueError as e:
            return self.error(str(e))
        return self.ok({"id": eid})

    async def delete_worldbook(self):
        body = await request.get_json() or {}
        eid = body.get("id")
        if not eid:
            return self.error("缺少 id")

        return self.ok({"count": self.store.delete_worldbook(int(eid))})

    async def toggle_worldbook(self):
        body = await request.get_json() or {}
        eid = body.get("id")
        if not eid:
            return self.error("缺少 id")
        enabled = bool(body.get("enabled"))
        self.store.set_worldbook_enabled(int(eid), enabled)
        return self.ok({"id": int(eid), "enabled": enabled})
