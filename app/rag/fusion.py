# -*- coding: utf-8 -*-
"""多路检索的**组合层** —— 角色分离，不融合分数。

## 为什么不融合分数

实测（`scripts/eval_rag.py --split 0.5`，留出 test n=104）：
    dense   准 0.83 / LEAK 4  / 弃权准 0.83
    lexical 准 0.87 / LEAK 1  / 弃权准 0.96
    hybrid  准 0.67 / LEAK 21 / 弃权准 0.12   ← RRF 融合，**比单路都差**
`_rrf` 自己的 docstring 写了原因：「融合分数对**无关 query 也恒有值**，
所以它没有'该弃权'的信息」。⇒ 本模块**不做任何跨通道加权或 RRF**。

## 角色分离

    graph   → 候选集扩展 + 结构化事实
    dense   → 候选集内定序
    lexical → 旁证（严格相等）

⚠ **本波只实现"结构化事实"这一半**：`compose` **不改候选集、不改 top-1**。
规格 §3.6 不变式 4 与 §6.4 要求旧通道行为逐条不变，而让图扩展集参与排序
会改变 top-1 —— 那是需要独立验收的行为变更。
⚠ P3 已实测（`docs/SDD/2026-09-23-graph-channel-probe.md` §4）：rerank 在图扩展集上的
**净修 = 0**（门禁触发）⇒ `rank_ids` 保留实现但**不接线**。故 Phase B 重开这一波时，
不得把 rerank 当作已知收益来用。

## 降级方向（与判决层相反，别搞混）

外部依赖失败（Neo4j 连不上 / 原语抛异常）⇒ **全降级**：返回空 facts，不抛。
判决所需证据缺失 ⇒ **fail-closed**：由 `policy.decide` 弃权。
全降级管"依赖活不活"，fail-closed 管"证据够不够"。见规格 §4。
"""
from __future__ import annotations

from app.rag.policy import REASON_GRAPH_DOWN, REASON_GRAPH_EMPTY

# facts 的键集合（固定形状）。
# ⚠ 键**不省略**：渲染侧按固定形状读，省键会让消费方长出"有时有键有时没有"
# 的分支 —— 那是本仓"同源漂移"的经典入口（见 MEMORY.md 架构铁律）。
# ⚠ 探针 P5 若证明禁忌链走不通，本元组缩为 ("alternatives", "peers")。
#   P5 已实测 > 0（活图 75.0% / 数据 77.2%）⇒ 禁忌键保留。
_FACT_KEYS: tuple[str, ...] = ("alternatives", "peers", "contraindications")


def empty_facts() -> dict:
    """facts 的空形态：三个键恒在，值为空列表。"""
    return {k: [] for k in _FACT_KEYS}


def compose(graph, seed_id: str | None) -> dict:
    """图侧事实拼装。返回 `{"facts": {...}, "reason": str | None}`。

    `graph` 为 None（Neo4j 不可用）⇒ `REASON_GRAPH_DOWN`；
    `seed_id` 为 None（播种未过 `MIN_SCORE` 门槛）⇒ `REASON_GRAPH_EMPTY`。
    两者都返回空 facts，**不抛**。

    单个原语抛异常 ⇒ 只该键退化为空列表，其余照常（一个通道坏不拖垮全部）。
    """
    facts = empty_facts()
    if graph is None:
        return {"facts": facts, "reason": REASON_GRAPH_DOWN}
    if not seed_id:
        return {"facts": facts, "reason": REASON_GRAPH_EMPTY}
    from app.rag import retriever
    for key, fn in (("alternatives", retriever.graph_family_alternatives),
                    ("peers", retriever.graph_muscle_peers),
                    ("contraindications", retriever.graph_contraindicated)):
        try:
            facts[key] = list(fn(graph, seed_id))
        except Exception:
            facts[key] = []
    return {"facts": facts, "reason": None}
