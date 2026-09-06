# -*- coding: utf-8 -*-
"""RAG 检索：向量语义召回（HNSW）+ pg_trgm 文本相似兜底 + 图检索（W3/W4）。
混合编排 hybrid_retrieve：图上下文/替代 + 向量召回 → 证据，异常全降级。"""
from __future__ import annotations
from app.rag.store import PgStore


def embed_query(embedder, text: str) -> list[float]:
    return embedder.embed_one(text)


# ---- 图检索（W3） ----

def graph_context(store: PgStore, node_name: str, hops: int = 1,
                  rels: tuple[str, ...] | None = None) -> list[dict]:
    """从实体出发沿边遍历（递归 CTE）。返回 [{"name","kind","rel","depth"}]。
    rels 限定边类型（= ANY(%s)）；起始节点 rel 为 None。"""
    sql = """
        WITH RECURSIVE hop AS (
          SELECT n.id, n.kind, n.name, NULL::text AS rel, 0 AS depth
            FROM fitness.graph_nodes n WHERE n.name = %s
          UNION ALL
          SELECT d.id, d.kind, d.name, e.rel, h.depth + 1
            FROM hop h
            JOIN fitness.graph_edges e ON e.src_id = h.id
            JOIN fitness.graph_nodes d ON d.id = e.dst_id
           WHERE h.depth < %s"""
    params: list = [node_name, hops]
    if rels:
        sql += " AND e.rel = ANY(%s)"
        params.append(list(rels))
    sql += ") SELECT name, kind, rel, depth FROM hop ORDER BY depth"
    with store.conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [{"name": r[0], "kind": r[1], "rel": r[2], "depth": r[3]}
            for r in rows]


def graph_muscle_exercises(store: PgStore, muscle: str,
                           limit: int = 8) -> list[dict]:
    """经 targets 反查锻炼该肌肉的动作（去重）。返回 [{"name","kind"}]。"""
    sql = """
        SELECT DISTINCT n.name
          FROM fitness.graph_nodes n
          JOIN fitness.graph_edges e ON e.src_id = n.id
          JOIN fitness.graph_nodes m ON m.id = e.dst_id
         WHERE m.name = %s AND e.rel = 'targets' AND n.kind = 'exercise'
         LIMIT %s"""
    with store.conn() as c, c.cursor() as cur:
        cur.execute(sql, (muscle, limit))
        rows = cur.fetchall()
    return [{"name": r[0], "kind": "exercise"} for r in rows]


def graph_family_alternatives(store: PgStore, exercise_id: str) -> list[dict]:
    """同族变体（可替代）：member_of → 同族其它动作。返回 [{"name","kind"}]。"""
    sql = """
        SELECT DISTINCT g.name
          FROM fitness.graph_edges a
          JOIN fitness.graph_edges b
            ON b.rel = 'member_of' AND b.dst_id = a.dst_id
          JOIN fitness.graph_nodes g ON g.id = b.src_id
         WHERE a.src_id = (SELECT id FROM fitness.graph_nodes WHERE name = %s)
           AND a.rel = 'member_of'
           AND b.src_id <> a.src_id"""
    with store.conn() as c, c.cursor() as cur:
        cur.execute(sql, (exercise_id,))
        rows = cur.fetchall()
    return [{"name": r[0], "kind": "exercise"} for r in rows]


# ---- 混合编排（W4） ----

def hybrid_retrieve(store: PgStore, embedder, query: str, top_k: int = 5,
                    chunk_types: tuple[str, ...] | None = None,
                    entity_hint: str | None = None) -> dict:
    """GraphRAG 混合：图上下文/替代 + 向量召回 → 证据。
    返回 {"graph": [...], "vector": [...]}，pending_review 均透传。
    图/向量异常各自降级为 []，绝不抛出。"""
    out: dict = {"graph": [], "vector": []}

    if entity_hint:                                   # 图部分
        try:
            n = store.node(entity_hint)
            if n is not None:
                gc = graph_context(store, entity_hint, hops=1,
                                   rels=("pattern_of", "targets"))
                out["graph"].extend(gc)
                if n["kind"] == "exercise":
                    out["graph"].extend(graph_family_alternatives(
                        store, entity_hint))
                    # 取其 target muscle 的反查（锻炼同类肌群的动作）
                    mus = [x["name"] for x in gc
                           if x.get("rel") == "targets"
                           and x.get("kind") == "muscle"]
                    if mus:
                        out["graph"].extend(graph_muscle_exercises(store, mus[0]))
        except Exception:
            out["graph"] = []

    try:                                              # 向量部分
        qv = embed_query(embedder, query)
        ename = getattr(embedder, "model", "bge-m3")
        out["vector"] = vector_search(store, qv, ename, top_k=top_k,
                                      chunk_types=chunk_types)
    except Exception:
        out["vector"] = []
    return out


def vector_search(store: PgStore, query_vec: list[float], embedder_name: str,
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


def text_search(store: PgStore, text: str, top_k: int = 5) -> list[dict]:
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