-- FitMind RAG schema（v1，PG+pgvector 单库）
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

CREATE TABLE IF NOT EXISTS fitness.graph_nodes(
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL UNIQUE,
  meta JSONB DEFAULT '{}'::jsonb);
CREATE TABLE IF NOT EXISTS fitness.graph_edges(
  src_id BIGINT NOT NULL REFERENCES fitness.graph_nodes(id),
  dst_id BIGINT NOT NULL REFERENCES fitness.graph_nodes(id),
  rel TEXT NOT NULL,
  PRIMARY KEY (src_id, dst_id, rel));
CREATE INDEX IF NOT EXISTS graph_edges_src ON fitness.graph_edges(src_id);
CREATE INDEX IF NOT EXISTS graph_edges_dst ON fitness.graph_edges(dst_id);