"""规范化、去重和更新长期认知，保存原始往来来源。
证据检查采用文本覆盖规则，不进行独立的语义事实判定。"""


from __future__ import annotations

import time
import unicodedata


def normalize_fact(text: str) -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).lower()
    return "".join(c for c in s if c.isalnum())


PROMISE_PREFIX = "【约定】"


SOURCE_IDS_MAX = 20


def merge_entry_ids(existing: str, new: "list[int]") -> str:
    out: list[int] = []
    for part in str(existing or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) not in out:
            out.append(int(part))
    for i in new or []:
        if i not in out:
            out.append(i)
    return ",".join(str(i) for i in out[:SOURCE_IDS_MAX])


def refresh_promise_memory(store, umo: str, mid: int, text: str,
                           source_entry_ids=None) -> int:
    """按指定ID更新承诺认知文字，并合并来源记录。"""
    t = str(text or "").strip()
    if not t or int(mid) <= 0:
        return 0
    src = [int(i) for i in (source_entry_ids or []) if str(i).strip()]
    with store._lock:
        for r in store._memory_rows(umo):
            if int(r["id"]) == int(mid):
                return store._memory_refresh(
                    int(mid), time.time(),
                    merge_entry_ids(r["source_entry_ids"], src), t)
    return 0


EVIDENCE_MIN_COVER = 0.5


def _alnum(text) -> str:
    return "".join(c for c in str(text or "") if c.isalnum())


def bigrams(text: str) -> "set[str]":
    t = _alnum(text)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def cover(grams: "set[str]", source: str) -> float:
    if not grams:
        return 1.0
    s = _alnum(source)
    return sum(1 for g in grams if g in s) / len(grams)


def evidence_grounded(text: str, source: str) -> "tuple[bool, float]":
    """用相邻字符覆盖率检查认知文本与来源文本的对应程度。"""
    grams = bigrams(text)
    if not grams:
        return True, 1.0
    cov = cover(grams, source)
    return cov >= EVIDENCE_MIN_COVER, cov


def evidence_entry_ids(text: str, lines: "list[tuple[int, str]]") -> "list[int]":
    grams = bigrams(text)
    if not grams or not lines:
        return []
    scored = [(int(eid), cover(grams, line)) for eid, line in lines]
    hit = [eid for eid, c in scored if c >= EVIDENCE_MIN_COVER]
    if hit:
        return hit
    return [max(scored, key=lambda p: p[1])[0]]


def upsert_factual_memory(store, umo: str, text: str,
                          source_entry_ids: "list[int] | None" = None) -> str:
    """相同规范文本刷新时间与来源，否则插入新认知。"""
    t = str(text or "").strip()
    if not t:
        return ""


    key = normalize_fact(t) or t
    src = [int(i) for i in (source_entry_ids or []) if str(i).strip()]
    now = time.time()
    with store._lock:


        for r in store._memory_rows(umo):
            if normalize_fact(r["text"]) != key:
                continue
            store._memory_refresh(int(r["id"]), now,
                                  merge_entry_ids(r["source_entry_ids"], src))
            return "refresh"
        store._memory_insert(umo, t, 0.5, now, merge_entry_ids("", src))
    return "new"


DUP_MIN_SIM = 0.85


DUP_MIN_CHARS = 20


def similar(a, b) -> float:
    ga, gb = bigrams(a), bigrams(b)
    if not ga or not gb:
        return 0.0
    if normalize_fact(a) == normalize_fact(b):
        return 1.0
    return min(cover(ga, b), cover(gb, a))


def is_duplicate(a, b) -> bool:
    if min(len(_alnum(a)), len(_alnum(b))) < DUP_MIN_CHARS:
        return str(a or "").strip() == str(b or "").strip()
    return similar(a, b) >= DUP_MIN_SIM


def upsert_person_memory(store, person_id: str, text: str, *,
                         origin_umo: str = "", scope: str = "public",
                         importance: float = 0.5) -> int:
    t = str(text or "").strip()
    if not person_id or not t:
        return -1
    if scope not in ("public", "private"):
        scope = "public"
    with store._lock:
        for r in store._person_memory_rows(person_id):

            if r["scope"] == scope and is_duplicate(t, r["text"]):
                return -int(r["id"])
        return store._person_memory_insert(person_id, t, origin_umo,
                                           scope, importance)
