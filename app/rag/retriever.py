# -*- coding: utf-8 -*-
"""RAG 检索：向量语义召回（HNSW，PG）+ 图检索（Cypher，Neo4j）。
统一后的双源编排 hybrid_retrieve：图上下文/替代（GraphStore）+ 向量召回（PgStore）；
图/向量各自异常降级为空，绝不抛出（项目"异常静默"传统）。"""
from __future__ import annotations


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
    """同族变体（可替代）：member_of → 同族其它动作。返回 [{"name","kind"}]。"""
    return store.family_alternatives(exercise_id)


# ---- 混合编排 ----

def hybrid_retrieve(store, embedder, query: str, top_k: int = 5,
                    chunk_types: tuple[str, ...] | None = None,
                    entity_hint: str | None = None,
                    graph=None) -> dict:
    """GraphRAG 混合：图上下文/替代（graph, 默认 GraphStore.get()）+ 向量召回（store）。
    返回 {"graph": [...], "vector": [...]}；图/向量异常各自降级为 []。"""
    out: dict = {"graph": [], "vector": []}

    if entity_hint and graph is not None:         # 图部分
        try:
            gc = graph_context(graph, entity_hint, hops=1,
                               rels=("pattern_of", "targets"))
            out["graph"].extend(gc)
            if any(x.get("kind") == "exercise" and x.get("depth") == 0
                   for x in gc):
                out["graph"].extend(graph_family_alternatives(
                    graph, entity_hint))
                mus = [x["name"] for x in gc
                       if x.get("rel") == "targets"
                       and x.get("kind") == "muscle"]
                if mus:
                    out["graph"].extend(graph_muscle_exercises(graph, mus[0]))
        except Exception:
            out["graph"] = []

    try:                                          # 向量部分
        qv = embed_query(embedder, query)
        ename = getattr(embedder, "model", "bge-m3")
        out["vector"] = vector_search(store, qv, ename, top_k=top_k,
                                      chunk_types=chunk_types)
    except Exception:
        out["vector"] = []
    return out


def vector_search(store, query_vec: list[float], embedder_name: str,
                  top_k: int = 5, chunk_types: tuple[str, ...] | None = None,
                  include_pending: bool = True) -> list[dict]:
    """HNSW 余弦最近邻；返回证据 {content, source_ref, score, pending_review}。"""
    sql = ("SELECT content, source_ref, pending_review, "
           "1 - (embedding <=> %s::vector) AS score FROM fitness.embeddings")
    params: list = [str(query_vec)]
    conds = []
    if chunk_types:
        conds.append("chunk_type = ANY(%s)")
        params.append(list(chunk_types))
    if not include_pending:
        conds.append("pending_review = FALSE")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
    params += [str(query_vec), top_k]
    with store.conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [{"content": r[0], "source_ref": dict(r[1]),
             "score": round(float(r[2]), 4), "pending_review": bool(r[3])}
            for r in rows]


def text_search(store, text: str, top_k: int = 5) -> list[dict]:
    """pg_trgm 文本相似兜底。"""
    sql = ("SELECT content, source_ref, pending_review, "
           "similarity(content, %s) AS score FROM fitness.embeddings "
           "ORDER BY score DESC LIMIT %s")
    with store.conn() as c, c.cursor() as cur:
        cur.execute(sql, (text, top_k))
        rows = cur.fetchall()
    return [{"content": r[0], "source_ref": dict(r[1]),
             "score": round(float(r[2]), 4), "pending_review": bool(r[3])}
            for r in rows]