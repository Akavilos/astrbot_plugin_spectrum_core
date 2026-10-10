"""受众表示与准入判断。
全局受众用星号表示，会话受众用集合表示；未知受众不向指定查看者放行。"""


import re


GLOBAL = "*"


_SEP = "|"


def render(aud) -> str:
    if aud is None:
        return ""
    if aud == GLOBAL:
        return GLOBAL
    if isinstance(aud, str):

        return aud
    items = sorted({str(x) for x in aud if x})
    return _SEP.join(items)


def parse(raw) -> "object":
    if raw is None:
        return None

    if isinstance(raw, dict):
        t = str(raw.get("type") or "").strip().lower()
        if t == "global":
            return GLOBAL
        if t in ("session", "sessions", "multiple"):
            umos = raw.get("umos")
            if isinstance(umos, str):
                umos = [umos]
            if not isinstance(umos, (list, tuple)):
                return None
            items = {str(u) for u in umos if str(u).strip()}
            return frozenset(items) if items else None
        return None
    if isinstance(raw, (list, tuple, set, frozenset)):
        items = {str(u) for u in raw if str(u).strip()}
        return frozenset(items) if items else None
    s = str(raw).strip()
    if not s:
        return None
    if s == GLOBAL:
        return GLOBAL
    items = {p for p in (x.strip() for x in s.split(_SEP)) if p}
    return frozenset(items) if items else None


def render_obj(aud) -> dict:
    if aud == GLOBAL:
        return {"type": "global"}
    if isinstance(aud, (set, frozenset, list, tuple)):
        items = sorted({str(x) for x in aud if x})
        if items:
            return {"type": "session", "umos": items}
    return {"type": "unknown"}


def admit(aud, viewer: str) -> bool:
    """查看者必须存在，且受众为全局或明确包含该会话。"""
    if not viewer:
        return False
    if aud is None:
        return False
    if aud == GLOBAL:
        return True
    if isinstance(aud, str):
        aud = parse(aud)
        return admit(aud, viewer)
    try:
        return viewer in aud
    except TypeError:
        return False


def sql_admit(column: str = "audience") -> str:
    return f"({column}='{GLOBAL}' OR ('{_SEP}'||{column}||'{_SEP}') LIKE ?)"


def sql_param(viewer: str) -> str:
    return f"%{_SEP}{viewer}{_SEP}%"


def for_session_write(umo: str):
    return frozenset({umo}) if umo else None


def for_life_write(owner: dict, umo: str):
    sess = str((owner or {}).get("session") or "").strip()
    if sess:
        return frozenset({sess})
    return GLOBAL


_NAMLESS_PERSON = re.compile(
    r"(?<!其)[他她]|对方|某人|别人|那个人|这个人|那人|这人")


def has_nameless_person_reference(text) -> bool:
    return bool(text) and bool(_NAMLESS_PERSON.search(str(text)))


def for_item(names, owner: dict, name_to_umos, *, text=None) -> "object":
    """把已识别人名映射到会话；无法落实受众或只有未指明的人称时返回未知。"""
    items = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not items:
        if text is not None and has_nameless_person_reference(text):
            return None
        return GLOBAL
    out: set[str] = set()
    for n in items:
        try:
            for u in (name_to_umos(n) or ()):
                if u:
                    out.add(str(u))
        except Exception:
            continue
    if not out:
        return None
    sess = str((owner or {}).get("session") or "").strip()
    if sess:
        out.add(sess)
    return frozenset(out)
