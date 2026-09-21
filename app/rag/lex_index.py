# -*- coding: utf-8 -*-
"""字符 bigram BM25 词法索引 —— **评测与运行期共用的唯一实现**。

## 为什么要有这个模块

这段算法原先只活在 `scripts/eval_rag.py` 里（脚本局部类）。接线 `exercise_cue`
判决层时，运行期也**必须**用同一份实现算词法旁证 —— 否则评测里那条
"精度 1.000" 在线上复现不出来。

⚠ **同源漂移**是本仓反复出现的坑（`MEMORY.md` 架构铁律）：
同一份数据/算法有多个产出点 → 差异不报错、只静默降级。
所以这里**抽成单一构造器**，`scripts/eval_rag.py` 改为 `import` 本模块
（先例：`build_clarify_options`、`lib/parts.py`、`lib/negation.py`）。

⚠ **算法逐字搬自 `scripts/eval_rag.py`（2026-09-20 版本），不得改动** ——
改了会让历史评测数字全部失效。搬移的正确性由
`scripts/eval_rag.py --retriever all` 的数字**逐字不变**来保证。

## 为什么不用 pg_trgm

实测它在 2 字中文上算不出相似度（'深蹲' vs '深蹲膝盖姿势要点' = 0.200，
低于默认阈值 0.3）。字符 bigram 没这个问题。
"""
from __future__ import annotations

import math
from collections import Counter

# 进程内缓存：chunk_type → 索引。索引建一次、多次查询复用（BM25 的手册口径）。
# ⚠ 必须在写操作后**显式清缓存**，否则会读到陈旧的语料（有 `clear_cache()`）。
_CACHE: dict[str, "LexIndex"] = {}


def _grams(t: str) -> list[str]:
    """字符二元组。去空白后取相邻两字；不足 2 字时返回整串（保底非空）。"""
    t = "".join(t.split())
    return [t[i:i + 2] for i in range(len(t) - 1)] or [t]


class LexIndex:
    """字符二元组 BM25 —— 中文不依赖分词器的词法基线。索引建一次，多次查询复用。"""

    def __init__(self, docs: list[tuple[str, str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.ids = [i for i, _ in docs]
        self.corpus = [_grams(t) for _, t in docs]
        self.lens = [len(g) for g in self.corpus]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        self.df: Counter = Counter()
        for g in self.corpus:
            self.df.update(set(g))
        self.n = len(self.corpus) or 1

    def ranked(self, query: str, n: int | None = None) -> list[tuple[str, float]]:
        """按 BM25 降序的全部（或前 n 个）(id, score)。RRF 融合需要排名而非仅 top1。"""
        if not self.corpus:
            return []
        qterms = _grams(query)
        scored = []
        for doc_id, g, dl in zip(self.ids, self.corpus, self.lens):
            tf = Counter(g)
            s = 0.0
            for term in qterms:
                if term not in self.df:
                    continue
                idf = math.log(1 + (self.n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                s += idf * tf[term] * (self.k1 + 1) / (
                    tf[term] + self.k1 * (1 - self.b + self.b * dl / self.avg))
            scored.append((doc_id, s))
        scored.sort(key=lambda kv: -kv[1])
        return scored[:n] if n else scored

    def top1(self, query: str) -> tuple[str | None, float]:
        r = self.ranked(query, 1)
        return r[0] if r else (None, 0.0)


def build_for_chunk_type(store, chunk_type: str) -> "LexIndex | None":
    """从 PG 的 `fitness.embeddings` 建索引（`content` 为正文，与向量通道同源）。

    ⚠ 全降级：PG 不可达 → 返回 `None`，**不抛**（本项目架构铁律）。
    """
    try:
        with store.conn() as c, c.cursor() as cur:
            cur.execute("SELECT source_ref->>'id', content FROM "
                        "fitness.embeddings WHERE chunk_type=%s", (chunk_type,))
            docs = [(i, x or "") for i, x in cur.fetchall()]
    except Exception:
        return None
    if not docs:
        return None
    from exercise_repo import norm_zh          # lib/ 在 sys.path（脚本与 app 均如此）
    return LexIndex([(i, norm_zh(t)) for i, t in docs])


def cached_for_chunk_type(store, chunk_type: str) -> "LexIndex | None":
    """`build_for_chunk_type` 的进程内缓存版。建索引约 1324 条 bigram（实测 <1s）。"""
    if chunk_type not in _CACHE:
        idx = build_for_chunk_type(store, chunk_type)
        if idx is None:
            return None                        # 不缓存失败，下次还会重试
        _CACHE[chunk_type] = idx
    return _CACHE[chunk_type]


def cached_top1_id(store, chunk_type: str, query: str) -> str | None:
    """取 query 在该 chunk_type 语料上的词法 top1 id（拿不到就 `None`）。

    ⚠ `query` 必须先过 `expand_aliases + norm_zh`（与 `scripts/eval_rag.py`
    的 `policy_top1` 同一口径），由调用方负责 —— 别名展开属动作域知识，
    不该塞进这个通用索引模块。
    """
    idx = cached_for_chunk_type(store, chunk_type)
    if idx is None:
        return None
    rid, _ = idx.top1(query)
    return rid


def clear_cache() -> None:
    """清进程内缓存。测试与"语料变更后"都必须调用。"""
    _CACHE.clear()
