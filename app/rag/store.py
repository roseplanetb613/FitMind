# -*- coding: utf-8 -*-
"""RAG 存储访问层：PostgreSQL + pgvector（schema fitness）。
连接串：DATABASE_URL 环境变量（.env 可配）；缺省用本机 postgres 信任认证。"""
from __future__ import annotations
import os
from pathlib import Path
import psycopg
from app.core.llm import load_dotenv

load_dotenv()
DEFAULT_DSN = os.environ.get("DATABASE_URL",
                             "postgresql://postgres@127.0.0.1:5432/postgres")


class PgStore:
    def __init__(self, dsn: str = DEFAULT_DSN):
        self.dsn = dsn

    @classmethod
    def connect(cls, dsn: str = DEFAULT_DSN) -> "PgStore":
        return cls(dsn)

    def conn(self):
        return psycopg.connect(self.dsn, autocommit=True)

    def apply_schema(self) -> None:
        sql = (Path(__file__).resolve().parent / "schema.sql").read_text(
            encoding="utf-8")
        with self.conn() as c, c.cursor() as cur:
            cur.execute(sql)

    # ---- 图 ----
    def upsert_node(self, kind: str, name: str, meta: dict | None = None) -> int:
        with self.conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO fitness.graph_nodes(kind,name,meta) VALUES(%s,%s,%s) "
                "ON CONFLICT (name) DO UPDATE SET kind=EXCLUDED.kind, "
                "meta=EXCLUDED.meta RETURNING id", (kind, name,
                                                    psycopg.types.json.Jsonb(
                                                        meta or {})))
            row = cur.fetchone()
            return int(row[0])

    def upsert_edge(self, src_id: int, dst_id: int, rel: str) -> None:
        with self.conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO fitness.graph_edges(src_id,dst_id,rel) "
                "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (src_id, dst_id, rel))

    def node_id(self, name: str) -> int | None:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("SELECT id FROM fitness.graph_nodes WHERE name=%s", (name,))
            r = cur.fetchone()
            return int(r[0]) if r else None

    def node(self, name: str) -> dict | None:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("SELECT id,kind,name,meta FROM fitness.graph_nodes "
                        "WHERE name=%s", (name,))
            r = cur.fetchone()
            return ({"id": int(r[0]), "kind": r[1], "name": r[2], "meta": r[3]}
                    if r else None)

    def clear_all(self) -> None:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("TRUNCATE fitness.graph_edges, fitness.graph_nodes, "
                        "fitness.embeddings RESTART IDENTITY")

    def counts(self) -> dict:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("SELECT (SELECT count(*) FROM fitness.graph_nodes), "
                        "(SELECT count(*) FROM fitness.graph_edges), "
                        "(SELECT count(*) FROM fitness.embeddings)")
            r = cur.fetchone()
            return {"nodes": int(r[0]), "edges": int(r[1]),
                    "embeddings": int(r[2])}