-- FitMind RAG schema（v2，PG 只做向量库；图已统一迁移 Neo4j）
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS fitness;

CREATE TABLE IF NOT EXISTS fitness.embeddings(
  id BIGSERIAL PRIMARY KEY,
  chunk_type TEXT NOT NULL,
  source_ref JSONB NOT NULL,
  content TEXT NOT NULL,
  embedding vector(1024),
  pending_review BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT now());
CREATE INDEX IF NOT EXISTS embeddings_hnsw ON fitness.embeddings
  USING hnsw (embedding vector_cosine_ops);

-- 2026-09-14：移除 pg_trgm 文本兜底索引（原 embeddings_trgm + 对应的 text_search()）。
-- 它从未被生产代码调用过，且实测在中文上不可用：pg_trgm 默认 similarity_threshold
-- 是 0.3，而 '深蹲' vs '深蹲膝盖姿势要点' 只有 0.200、'卧推' vs '杠铃卧推：推模式，
-- 难度2' 只有 0.067 —— 加 `WHERE content % %s` 会让它对中文查询永远返回空；不加则
-- 用不上 GIN 索引（similarity() 排序走不了索引）、退化成全表扫且返回不相关噪声。
-- 保留 DROP 是为了让已建过该索引的库收敛（CREATE ... IF NOT EXISTS 不会删旧索引）。
DROP INDEX IF EXISTS fitness.embeddings_trgm;

-- 图存储（graph_nodes/graph_edges）已删除：见 2026-09-07-graph-consolidation-neo4j.md
-- 数据由 app/rag/ingest.build() 幂等重建到 Neo4j（app/graph/store.py）