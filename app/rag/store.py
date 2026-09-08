# -*- coding: utf-8 -*-
"""RAG 存储访问层：PostgreSQL + pgvector（schema fitness）。
连接串：DATABASE_URL 环境变量（.env 可配）；缺省用本机 postgres 信任认证。
仅承载向量（embeddings）；图存储已统一迁往 Neo4j（app.graph.store.GraphStore）。"""
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

    # ---- 向量（唯一职责；图已迁移 Neo4j） ----

    def clear_all(self) -> None:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("TRUNCATE fitness.embeddings RESTART IDENTITY")

    def counts(self) -> dict:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM fitness.embeddings")
            r = cur.fetchone()
            return {"embeddings": int(r[0])}