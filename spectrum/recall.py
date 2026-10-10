"""为原文回看生成检索词、排序锚点并选择有篇幅限制的邻近片段。"""


from __future__ import annotations

from typing import Sequence
from difflib import SequenceMatcher
import math


DEFAULT_RADIUS = 2


def recall_keys(query: str, cap: int = 24) -> list[str]:
    clean = "".join(c if c.isalnum() else " " for c in str(query or "")).strip()
    if not clean:
        return []
    tokens: list[str] = []
    joined = clean.replace(" ", "")[:64]
    if len(joined) >= 2:
        tokens.append(joined)
        for i in range(len(joined) - 1):
            tokens.append(joined[i:i + 2])
    for w in clean.split(" "):
        if len(w.strip()) >= 2:
            tokens.append(w.strip())
    uniq: list[str] = []
    for t in tokens:
        if t not in uniq:
            uniq.append(t)
        if len(uniq) >= max(1, int(cap)):
            break
    return uniq


def score_item(keys: Sequence[str], text: str) -> int:
    if not keys or not text:
        return 0
    low = str(text).casefold()
    return sum(1 for k in keys if k.casefold() in low)


def rank_anchors(scores: Sequence[int], min_score: int = 1) -> list[int]:
    idx = [i for i, s in enumerate(scores) if s >= min_score]
    idx.sort(key=lambda i: (-scores[i], -i))
    return idx


def rank_relevant_anchors(query: str, texts: Sequence[str], timestamps: Sequence[float]) -> list[int]:
    clean = "".join(c for c in query.casefold() if c.isalnum())[:256]
    if len(clean) < 2:
        return []
    normalized = ["".join(c for c in text.casefold() if c.isalnum()) for text in texts]
    keys = list(dict.fromkeys(clean[i:i + 2] for i in range(len(clean) - 1)))
    weights = {key: 1.0 + math.log((len(texts) + 1) /
               (1 + sum(key in text for text in normalized))) for key in keys}
    total = sum(weights.values())
    newest = max(timestamps, default=0.0)
    ranked = []
    for i, text in enumerate(normalized):
        blocks = SequenceMatcher(None, clean, text, autojunk=False).get_matching_blocks()
        matched = sum(block.size for block in blocks if block.size >= 2)
        coverage = min(1.0, matched / len(clean))

        if coverage < 0.5:
            continue
        relevance = sum(weight for key, weight in weights.items() if key in text) / total
        age_days = max(0.0, newest - timestamps[i]) / 86400.0
        recency = 1.0 / (1.0 + age_days)
        ranked.append((0.55 * coverage + 0.35 * relevance + 0.10 * recency, i))
    ranked.sort(reverse=True)
    return [i for _, i in ranked]


def pick_segment(texts: Sequence[str], anchor: int, budget: int,
                 radius: int = DEFAULT_RADIUS) -> tuple[int, int, bool]:
    n = len(texts)
    if n == 0:
        return (0, 0, False)
    anchor = max(0, min(int(anchor), n - 1))
    lo = hi = anchor
    used = len(texts[anchor])


    truncated = used > budget
    step = 0
    while step < radius:
        step += 1
        for i in (anchor + step, anchor - step):
            if i < 0 or i >= n:
                continue
            if used + len(texts[i]) + 1 > budget:
                truncated = True
                continue
            used += len(texts[i]) + 1
            if i < lo:
                lo = i
            if i > hi:
                hi = i
        if anchor - step < 0 and anchor + step >= n:
            break
    return (lo, hi + 1, truncated)
