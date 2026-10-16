import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row

from infectious_disease_assistant.config import RagSettings
from infectious_disease_assistant.rag.embeddings import validate_embeddings
from infectious_disease_assistant.rag.errors import GuidelineDatabaseError
from infectious_disease_assistant.rag.models import GuidelineChunk, GuidelineDocument, GuidelineMatch


@dataclass(frozen=True)
class PreparedDocument:
    document: GuidelineDocument
    chunks: list[GuidelineChunk]
    embeddings: list[list[float]]


class GuidelineStore:
    def __init__(self, settings: RagSettings):
        self.database_url = settings.database_url

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        try:
            with psycopg.connect(
                self.database_url.get_secret_value(), connect_timeout=5, row_factory=dict_row,
            ) as connection:
                yield connection
        except psycopg.Error:
            raise GuidelineDatabaseError(
                "Guideline database operation failed; check DATABASE_URL and run RAG init",
            ) from None

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(817214602)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS ida_guideline_migrations (
                    version text PRIMARY KEY, checksum text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            for migration in sorted(files("infectious_disease_assistant.rag").joinpath("sql").iterdir(), key=lambda item: item.name):
                if not migration.name.endswith(".sql"):
                    continue
                sql = migration.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                row = connection.execute(
                    "SELECT checksum FROM ida_guideline_migrations WHERE version = %s", (migration.name,),
                ).fetchone()
                if row:
                    if row["checksum"] != checksum:
                        raise GuidelineDatabaseError("An applied guideline migration has changed")
                    continue
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO ida_guideline_migrations (version, checksum) VALUES (%s, %s)",
                    (migration.name, checksum),
                )

    def replace_documents(self, documents: list[PreparedDocument], model: str, dimensions: int) -> None:
        for prepared in documents:
            validate_embeddings(prepared.embeddings, len(prepared.chunks), dimensions)
        with self.connection() as connection:
            for prepared in documents:
                document = prepared.document
                connection.execute("""
                    INSERT INTO ida_guideline_documents
                        (document_id, title, source, license, content_sha256, embedding_model, embedding_dimensions)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (document_id) DO UPDATE SET
                        title = EXCLUDED.title, source = EXCLUDED.source, license = EXCLUDED.license,
                        content_sha256 = EXCLUDED.content_sha256, embedding_model = EXCLUDED.embedding_model,
                        embedding_dimensions = EXCLUDED.embedding_dimensions, ingested_at = now()
                """, (document.document_id, document.title, document.source, document.license,
                      document.content_sha256, model, dimensions))
                connection.execute("DELETE FROM ida_guideline_chunks WHERE document_id = %s", (document.document_id,))
                with connection.cursor() as cursor:
                    cursor.executemany("""
                        INSERT INTO ida_guideline_chunks
                            (chunk_id, document_id, section, chunk_index, text, embedding, embedding_dimensions)
                        VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
                    """, [
                        (chunk.chunk_id, document.document_id, chunk.section, chunk.chunk_index,
                         chunk.text, json.dumps(embedding, allow_nan=False), dimensions)
                        for chunk, embedding in zip(prepared.chunks, prepared.embeddings, strict=True)
                    ])

    def search(self, vector: list[float], *, model: str, dimensions: int, top_k: int) -> list[GuidelineMatch]:
        validate_embeddings([vector], 1, dimensions)
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT c.chunk_id, d.document_id, d.title, d.source, d.license,
                       d.content_sha256, c.section, c.chunk_index, c.text,
                       c.embedding <=> %s::vector AS distance
                FROM ida_guideline_chunks c
                JOIN ida_guideline_documents d USING (document_id)
                WHERE d.embedding_model = %s AND d.embedding_dimensions = %s
                      AND c.embedding_dimensions = %s
                ORDER BY distance, c.chunk_id
                LIMIT %s
            """, (json.dumps(vector, allow_nan=False), model, dimensions, dimensions, top_k)).fetchall()
        results = []
        for row in rows:
            distance = max(0.0, min(2.0, float(row["distance"])))
            results.append(GuidelineMatch(**(row | {"distance": distance, "similarity": 1 - distance})))
        return results
