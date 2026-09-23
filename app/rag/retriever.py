# -*- coding: utf-8 -*-
"""RAG 检索：向量语义召回（HNSW，PG）+ 图检索（Cypher，Neo4j）。
图/向量各自异常降级为空，绝不抛出（项目"异常静默"传统）。

调用方直接组合图检索与向量检索；这里只提供单步原语（见 2026-09-14 变更说明）。"""
from __future__ import annotations
import os

# 证据召回的相关性下限（余弦相似度）。低于此值的命中直接丢弃。
#
# 实测标定（2026-09-14，bge-m3，本机 1390 条语料，10 条相关 + 8 条无关查询）：
#   science_doc    相关 min/p50/max = 0.478 / 0.588 / 0.686
#                  无关 min/p50/max = 0.374 / 0.468 / 0.493
#   exercise_cue   相关 min/p50/max = 0.312 / 0.574 / 0.670
#                  无关 min/p50/max = 0.321 / 0.499 / 0.516
#
# ⚠ **两个分布是重叠的**（"今天天气怎么样""写一首诗"这类完全无关的中文查询也能
# 拿到 0.49~0.52）。也就是说**不存在能把相关/无关干净分开的阈值**——dense 检索器
# 在中文上有很高的相似度地板。0.55 取的是"高于实测噪声上限"，即：宁可让部分该召回
# 的被丢掉（precision 优先），也不要把随机知识块当证据挂到答案上（那是幻觉形状的
# 失败）。这不是好阈值，只是当前能站住的阈值。
#
# 真正的解法是加 rerank 或 dense+sparse 混合召回，不是继续调这个数。
#
# 2026-09-14 用 scripts/eval_rag.py 在 37 条人工标注（25 正 / 12 负）上实测本值：
#     阈值   证据准  错证率  召回  LEAK  Acc
#     0.40   0.56   0.44   0.80   11   0.57
#     0.55   0.68   0.32   0.76    4   0.73   ← 当前值
#     0.60   0.75   0.25   0.72    1   0.78
#     0.65   0.94   0.06   0.68    0   0.78
#     0.70   1.00   0.00   0.36    0   0.57
# 看起来 0.60~0.65 更好，**但先别改**：同一份标注做 dev/test 留出后，dev 上
# 0.60/0.65 的 precision 是 0.91/1.00，留出的 test 上只有 0.62 —— n=37 支持不了
# 阈值调优，那个甜点是过拟合。要动这个数，先扩标注集（尤其缺**改写式 query**：
# 现在的正样本多为"原词命中"，对稠密检索不利、对词法有利），再跑 eval_rag.py。
#
# 2026-09-20 第二次复现（162 条）时结论仍是"别改"：dev 选出的 0.614 与未调的 0.55
# 在留出集上 Acc 都是 0.829，看着没有样本外收益。
#
# ⚠⚠ **2026-09-20 晚：上面这条被推翻了 —— 那是"负样本太少"造成的假象。**
# 上一条自己写了前置条件："要动这个数，先扩标注集"。本次把它做了：
# 标注集 162 → 221 条，**负样本 17 → 50**（旧集全量才 17 个负样本，进 test 的更少）。
# 负样本一补齐，0.55 的问题立刻现形（`--split 0.5`，全量 test n=104）：
#     阈值     证据准   召回   弃权准   WRONG  LEAK
#     0.50     0.670   0.863   0.042    11    23
#     0.55     0.734   0.863   0.417    11    14   ← 旧值
#     0.60     0.819   0.850   0.792    10     5   ← 新值
#     0.624    0.827   0.838   0.833    10     4
#     0.65     0.878   0.812   0.917     7     2
#     0.70     0.912   0.650   0.958     4     1
# 仅 science_doc（= **唯一在线通道**，test n=47）：0.55 时 准 0.514 / LEAK 12 /
# 弃权准 0.455；0.60 时 准 0.692 / LEAK 3 / 弃权准 0.864。
# ⇒ **0.55 太低，会把大量无关知识块当证据挂上去**（22 个负样本里漏了 12 个）。
#   旧值是在"几乎只有正样本"的集合上选出来的，所以从来没暴露过。
# ⇒ 新值取 **0.60**：0.60~0.65 是一段**单调的宽平台**（不是 2026-09-14 那个尖峰），
#   取平台**下沿**以最小化召回代价（0.863→0.850）。若偏 precision 优先，0.65 也
#   站得住（准 0.878 / LEAK 2，代价是召回 0.812）—— 那是**产品口径**，未擅自取。
#
# ⚠ 仍然成立、别推翻的两条：
#   · 相关 0.5394~0.8708 vs 无关 0.2968~0.7003 —— **两个分布重叠**，所以
#     **没有哪个阈值能把 LEAK 打到 0**（0.70 仍有 1）。判决的其余部分已移交
#     `app/rag/policy.py`（按 chunk_type 分化 + 词法旁证 + fail-closed）：
#     一致闸门在同集上是 准 0.969 / **LEAK 0 / 弃权准 1.000**，且**对阈值不敏感**
#     （0.55 与 0.624 结果完全相同）—— 这才是"零自由度"想要的稳健性。
#   · 见 `docs/superpowers/specs/2026-09-20-vector-search-optimization-design.md`。
MIN_SCORE = float(os.environ.get("RAG_MIN_SCORE", "0.60"))


def embed_query(embedder, text: str) -> list[float]:
    return embedder.embed_one(text)


# ---- 图检索（Neo4j，Cypher 实现；store=GraphStore） ----

def graph_context(store, node_name: str, hops: int = 1,
                  rels: tuple[str, ...] | None = None) -> list[dict]:
    """从实体出发沿边遍历。返回 [{"name","kind","rel","depth"}]。"""
    return store.context(node_name, hops=hops, rels=rels)


def graph_muscle_exercises(store, muscle: str, limit: int = 8) -> list[dict]:
    """经 targets 反查锻炼该肌肉的动作（去重）。返回 [{"name","kind"}]。"""
    return store.muscle_exercises(muscle, limit=limit)


def graph_family_alternatives(store, exercise_id: str) -> list[dict]:
    """同族变体（可替代）：member_of → 同族其它动作。

    返回 [{"id","name_zh","kind"}]，同主目标肌的排前面，且有上限（见
    GraphStore.family_alternatives）。2026-09-14 前返回 [{"name","kind"}] 且
    `name` 存的是动作 id、无上限（f001 会返回 48 条）。"""
    return store.family_alternatives(exercise_id)


def seed_exercise(store, query_vec: list[float], embedder_name: str,
                  min_score: float | None = MIN_SCORE) -> str | None:
    """query 向量 → 种子动作 id（图检索的入口）。`None` = "不是动作域的问题"。

    补上本模块缺失的最后一环：`graph_*` 三个原语此前**只吃精确键**（动作 id /
    英文肌名），没有任何一条能从自然语言 query 进来。

    ⚠ **门槛复用 `MIN_SCORE`，本函数不新造阈值**（判决层的零自由度不变式）。
    低于门槛返回 `None` ⇒ 调用方**不扩展**。否则会把无关 query 硬接进图，
    产生 `policy` 那一类 LEAK 的形状（实测 `hybrid` 的 LEAK 就是这么来的）。

    ⚠ 只查 `exercise_cue`：`science_doc` 是散文、无实体可挂，图对它只能是 no-op。

    调用方负责 embed（与 `vector_search` 同一约定 —— `embed_query` 是独立原语）。
    """
    hits = vector_search(store, query_vec, embedder_name, top_k=1,
                         chunk_types=("exercise_cue",), min_score=min_score)
    if not hits:
        return None
    sr = hits[0].get("source_ref") or {}
    return sr.get("id") or None


def graph_muscle_peers(store, seed_id: str,
                       limit: int | None = None) -> list[dict]:
    """同主肌的其它动作（枚举）。返回 [{"id","name_zh","kind"}]。

    2 跳语义扩展 —— 这是词法与向量都做不到的能力：向量 top-k 只能给 k 条，
    无法"枚举某一肌群的全部动作"。

    ⚠ `limit` 缺省时**不传**，由 `GraphStore.muscle_peers` 用它自己的
    `_GRAPH_PEER_LIMIT` —— 上限**只有一个来源**。在这里再写一个 `= 6` 就是
    两处常量，改一处忘另一处 ⇒ 同源漂移（本仓架构铁律）。
    """
    if limit is None:
        return store.muscle_peers(seed_id)
    return store.muscle_peers(seed_id, limit=limit)


# ---- 向量检索 ----

def vector_search(store, query_vec: list[float], embedder_name: str,
                  top_k: int = 5, chunk_types: tuple[str, ...] | None = None,
                  include_pending: bool = True,
                  min_score: float | None = MIN_SCORE) -> list[dict]:
    """HNSW 余弦最近邻；返回证据 {content, source_ref, score, pending_review}。

    `min_score`：余弦相似度下限，低于它的命中直接丢弃（None = 不过滤）。

    2026-09-14：此前没有门槛，只有 `ORDER BY <=> LIMIT k`——表里只要有任何一行，
    就必然返回 k 条"最像的"，哪怕余弦 0.01。消费方是 `if hits:` 直接用 top1，于是
    不相关的知识块会被当证据挂到答案上。默认门槛见 MIN_SCORE。"""
    sql = ("SELECT content, source_ref, pending_review, "
           "1 - (embedding <=> %s::vector) AS score FROM fitness.embeddings")
    params: list = [str(query_vec)]
    conds = []
    if chunk_types:
        conds.append("chunk_type = ANY(%s)")
        params.append(list(chunk_types))
    if not include_pending:
        conds.append("pending_review = FALSE")
    if min_score is not None:
        conds.append("1 - (embedding <=> %s::vector) >= %s")
        params += [str(query_vec), float(min_score)]
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
    params += [str(query_vec), top_k]
    with store.conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    # 列序是 (content, source_ref, pending_review, score) —— 别按"score 在前"想当然。
    # 2026-09-14 前这里取的是 r[2]/r[3]，于是 score 恒为 float(pending_review)
    # （True→1.0，False→0.0）、pending_review 恒为 bool(score)，两个字段都是假的；
    # 而旧断言 `all(h["score"] >= 0)` 永远为真，从没暴露过。
    return [{"content": r[0], "source_ref": dict(r[1]),
             "score": round(float(r[3]), 4), "pending_review": bool(r[2])}
            for r in rows]