-- FitMind RAG schema（v2，PG 只做向量库；图已统一迁移 Neo4j）
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
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
CREATE INDEX IF NOT EXISTS embeddings_trgm ON fitness.embeddings
  USING gin (content gin_trgm_ops);

-- 图存储（graph_nodes/graph_edges）已删除：见 2026-09-07-graph-consolidation-neo4j.md
-- 数据由 app/rag/ingest.build() 幂等重建到 Neo4j（app/graph/store.py）