CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE TABLE ida_guideline_documents (
    document_id text PRIMARY KEY,
    title text NOT NULL,
    source text NOT NULL,
    license text NOT NULL,
    content_sha256 text NOT NULL,
    embedding_model text NOT NULL,
    embedding_dimensions integer NOT NULL CHECK (embedding_dimensions BETWEEN 1 AND 3072),
    ingested_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ida_guideline_chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES ida_guideline_documents(document_id) ON DELETE CASCADE,
    section text NOT NULL,
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    text text NOT NULL CHECK (length(text) > 0),
    embedding vector NOT NULL,
    embedding_dimensions integer NOT NULL,
    CHECK (vector_dims(embedding) = embedding_dimensions),
    UNIQUE (document_id, chunk_index)
);
