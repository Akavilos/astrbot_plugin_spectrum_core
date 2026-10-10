"""为目标会话读取生活、叙述事实和内心记录，并构造本轮世界快照。
快照保留世界版本与受众筛选结果，供前台和后台检查生成期间的状态变化。"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from . import audience as _aud


@dataclass
class SessionContext:
    viewer: str | None = None


    life_state: str = ""

    scripts: tuple = ()


    observed: tuple = ()


    state_ok: tuple = ()
    state_denied: tuple = ()


    script_ok: tuple = ()
    script_denied: tuple = ()


    used_items: bool = False

    state_malformed: int = 0

    def log_fields(self) -> str:
        return (
            f"target={_short(self.viewer)} "
            f"context_life_state_ids={list(self.state_ok)} "
            f"filtered_life_state_ids={list(self.state_denied)} "
            f"context_script_ids={list(self.script_ok)} "
            f"filtered_script_ids={list(self.script_denied)}"
        )


@dataclass(frozen=True)
class WorldSnapshot:
    version: str
    life_id: str
    context: SessionContext
    companion: str
    plans: str
    shared_events: str


@dataclass(frozen=True)
class TurnContext:
    turn_id: str
    session: str
    actor: str
    world: WorldSnapshot


def build_world_snapshot(store, life, umo: str, viewer: str | None,
                         render_events) -> WorldSnapshot:
    """在共同读锁中读取世界版本、生活与会话上下文，形成一次生成的输入快照。"""
    with store.world_read():
        session = build_context_for_session(store, life, umo, viewer)
        owner = life.get_owner()
        companion = str(owner.get("with") or "") if owner.get("session") != umo else ""
        return WorldSnapshot(store.world_version(), life.current_life_id(), session,
                             companion, life.render_plans(), render_events())


def new_turn(session: str, actor: str, world: WorldSnapshot) -> TurnContext:
    return TurnContext(uuid4().hex, session, actor, world)


def render_current_world(world: WorldSnapshot) -> str:
    parts = ["【我此刻重新看到的共同环境】", world.context.life_state,
             world.shared_events, world.plans]
    if world.companion:
        parts.append("我目前与" + world.companion + "在一起。")
    parts.extend(world.context.observed)
    return "\n".join(p for p in parts if p)


def _short(umo) -> str:
    return str(umo or "-").replace("default:", "")[:24]


def filter_life_state(life, umo: str, viewer: str | None):
    """按逐条受众筛选现状；没有逐条结构时检查整段受众。"""
    if viewer is None:
        return life.get_state_text(), (), (), False, 0

    its = life.items()
    if not its:
        ok = bool(_aud.admit(life.get_audience(), viewer))
        return (life.get_state_text() if ok else ""), \
               ((0,) if ok else ()), (() if ok else (0,)), False, 0

    kept: list[str] = []
    ok_idx: list[int] = []
    denied: list[int] = []
    malformed = 0
    for i, it in enumerate(its):
        if not isinstance(it, dict):
            malformed += 1
            continue
        if _aud.admit(_aud.parse(it.get("audience")), viewer):
            txt = str(it.get("text") or "")
            if txt:
                kept.append(txt)
                ok_idx.append(i)
        else:
            denied.append(i)
    return " ".join(kept), tuple(ok_idx), tuple(denied), True, malformed


def filter_scripts(store, life, umo: str, viewer: str | None, limit: int = 3):
    life_id = life.current_life_id()

    limit = min(limit, 1)
    rows = store.recent_script_rows(umo, limit=limit, viewer=viewer, life_id=life_id)
    window = store.recent_script_window_ids(umo=umo, life_id=life_id, limit=limit)
    ok = tuple(i for i, _ in rows)
    denied = tuple(i for i in window if i not in set(ok))
    return tuple(t[-360:] for _, t in rows), ok, denied


def filter_observed(store, life, viewer: str | None, limit: int = 2):
    if viewer is None:
        return tuple(str(r["text"]) for r in store.observed_rows(limit=limit))
    from . import audience as _aud
    out = []
    for r in store.observed_rows(limit=limit):
        try:
            aud = _aud.parse(r["audience"])
        except Exception:
            continue
        if _aud.admit(aud, viewer):
            out.append(str(r["text"]))
    return tuple(out)


def build_context_for_session(store, life, umo: str,
                              viewer: str | None, limit: int = 3) -> SessionContext:
    """集中筛选指定会话可见的现状、内心记录与外部观测。"""
    text, s_ok, s_denied, used_items, malformed = filter_life_state(life, umo, viewer)
    scripts, c_ok, c_denied = filter_scripts(store, life, umo, viewer, limit)
    return SessionContext(
        viewer=viewer,
        life_state=text,
        scripts=scripts,
        observed=filter_observed(store, life, viewer),
        state_ok=s_ok,
        state_denied=s_denied,
        script_ok=c_ok,
        script_denied=c_denied,
        used_items=used_items,
        state_malformed=malformed,
    )
