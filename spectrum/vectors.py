"""保存文本向量旁路索引并进行余弦相似度检索。
向量来源由框架嵌入提供商提供，索引不可用时返回空结果供调用方继续处理。"""


from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("spectrum")


try:
    import numpy as np
except Exception:
    np = None


AVAILABLE = np is not None


FILE_FMT = "{db}.vectors_{table}.npz"


class VecSidecar:
    def __init__(self, db_path: Path, table: str) -> None:
        self.table = table
        self.path = db_path.parent / FILE_FMT.format(db=db_path.name, table=table)
        self.ids = None
        self.vecs = None
        self._loaded = False


    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if np is None:
            return
        try:
            if not self.path.exists():
                return
            with np.load(self.path) as z:
                ids, vecs = z["ids"], z["vecs"]
            if ids.ndim == 1 and vecs.ndim == 2 and len(ids) == len(vecs):
                self.ids, self.vecs = ids.astype(np.int64), vecs.astype(np.float32)
            else:
                logger.warning("[spectrum·向量] %s 形状不对（ids=%s vecs=%s），当作空",
                               self.path.name, ids.shape, vecs.shape)
        except Exception:
            logger.warning("[spectrum·向量] %s 读取失败，当作空（可从文本重建）",
                           self.path.name)

    def save(self) -> None:
        if np is None or not self._loaded:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.vecs is None or not len(self.ids):
                self.path.unlink(missing_ok=True)
                return
            np.savez_compressed(self.path, ids=self.ids, vecs=self.vecs)
        except Exception:
            logger.exception("[spectrum·向量] %s 写入失败（关键词召回不受影响）",
                             self.path.name)


    def missing(self, rows: list[tuple[int, str]]) -> list[tuple[int, str]]:
        if np is None:
            return []
        self.load()
        have = set(self.ids.tolist()) if self.vecs is not None else set()
        return [(i, t) for i, t in rows if i not in have and str(t or "").strip()]

    def add(self, pairs: list[tuple[int, list[float]]]) -> int:
        if np is None or not pairs:
            return 0
        self.load()
        have = set(self.ids.tolist()) if self.vecs is not None else set()
        new_ids, new_vecs = [], []
        for mid, vec in pairs:
            try:
                v = np.asarray(vec, dtype=np.float32).reshape(-1)
            except Exception:
                continue
            if not v.size or mid in have:
                continue

            if self.vecs is not None and v.size != self.vecs.shape[1]:
                continue
            new_ids.append(int(mid))
            new_vecs.append(v)
        if not new_ids:
            return 0
        m = np.stack(new_vecs)
        if self.vecs is None:
            self.ids, self.vecs = np.asarray(new_ids, np.int64), m
        else:
            self.ids = np.concatenate([self.ids, np.asarray(new_ids, np.int64)])
            self.vecs = np.concatenate([self.vecs, m], axis=0)
        self.save()
        return len(new_ids)

    def drop(self, ids: list[int]) -> None:
        if np is None:
            return
        self.load()
        if self.vecs is None:
            return
        kill = set(int(i) for i in ids)
        keep = [n for n, i in enumerate(self.ids.tolist()) if int(i) not in kill]
        if len(keep) == len(self.ids):
            return
        self.ids = self.ids[keep]
        self.vecs = self.vecs[keep]
        self.save()

    def prune(self, live: set) -> int:
        if np is None:
            return 0
        self.load()
        if self.ids is None:
            return 0
        keep = [n for n, i in enumerate(self.ids.tolist()) if int(i) in live]
        if len(keep) == len(self.ids):
            return 0
        gone = len(self.ids) - len(keep)
        self.ids = self.ids[keep]
        self.vecs = self.vecs[keep]
        self.save()
        return gone


    def search(self, qvec, k: int) -> list[int]:
        """按余弦相似度返回向量索引中的记录ID，维度不兼容时返回空列表。"""
        if np is None or k <= 0:
            return []
        try:
            self.load()
            if self.vecs is None or not len(self.ids):
                return []
            q = np.asarray(qvec, dtype=np.float32).reshape(-1)
            if q.size != self.vecs.shape[1]:
                return []
            qn = q / (np.linalg.norm(q) + 1e-9)
            vn = self.vecs / (np.linalg.norm(self.vecs, axis=1, keepdims=True) + 1e-9)
            sims = vn @ qn
            k = min(int(k), len(sims))
            idx = np.argpartition(-sims, k - 1)[:k]
            idx = idx[np.argsort(-sims[idx])]
            return [int(self.ids[i]) for i in idx]
        except Exception:
            logger.exception("[spectrum·向量] 检索失败，退回关键词（不影响本轮）")
            return []


async def embed_query(ctx, text: str):
    if not text:
        return None
    try:
        getter = getattr(ctx, "get_all_embedding_providers", None)
        provs = getter() if callable(getter) else None
        if not provs:
            return None
        v = await provs[0].get_embedding(str(text)[:500])
        return list(v) if v else None
    except Exception:
        logger.debug("[spectrum·向量] query 向量取不到，退回关键词", exc_info=True)
        return None


async def embed_many(ctx, texts: list[str]):
    if not texts:
        return None
    try:
        getter = getattr(ctx, "get_all_embedding_providers", None)
        provs = getter() if callable(getter) else None
        if not provs:
            return None
        return await provs[0].get_embeddings([str(t)[:500] for t in texts])
    except Exception:
        logger.warning("[spectrum·向量] 批量取向量失败，这批跳过", exc_info=True)
        return None


async def backfill(ctx, store, tables=("memories", "person_memories"),
                   batch: int = 32, max_rounds: int = 0) -> int:
    """从待编码文本分批获取嵌入并写回索引，达到轮数上限或无进展时结束。"""
    if not tables:
        return 0
    total = 0
    try:
        for table in tables:
            rounds = 0
            while not max_rounds or rounds < max_rounds:
                rounds += 1
                pend = store.pending_vectors(table, limit=batch)
                if not pend:
                    break
                vecs = await embed_many(ctx, [t for _, t in pend])
                if not vecs or len(vecs) != len(pend):
                    break
                n = store.add_vectors(table, list(zip([i for i, _ in pend], vecs)))
                total += n
                if n <= 0:
                    break
    except Exception:
        logger.exception("[spectrum·向量] 回填出错（只是这一层没铺满，关键词照旧）")
    return total


def status() -> dict:
    return {"available": AVAILABLE,
            "numpy": getattr(np, "__version__", None) if AVAILABLE else None}
