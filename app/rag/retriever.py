# -*- coding: utf-8 -*-
"""RAG 检索：向量语义召回（HNSW）+ pg_trgm 文本相似兜底（W3 补图扩展）。"""
from __future__ import annotations
from app.rag.store import PgStore


def embed_query(embedder, text: str) -> list[float]:
    return embedder.embed_one(text)


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