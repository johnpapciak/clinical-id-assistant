import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from infectious_disease_assistant.config import RagSettings
from infectious_disease_assistant.rag.database import GuidelineStore, PreparedDocument
from infectious_disease_assistant.rag.errors import GuidelineDatabaseError
from infectious_disease_assistant.rag.service import ingest_guidelines, search_guidelines

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres_settings(rag_settings):
    database_url = os.environ.get("IDA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set IDA_TEST_DATABASE_URL to a disposable PostgreSQL/pgvector database")
    schema = "ida_rag_test_" + uuid4().hex
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        # Prevent migration lookup from falling through to an initialized public schema.
        connection.execute(sql.SQL("""
            CREATE TABLE {}.ida_guideline_migrations (
                version text PRIMARY KEY, checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
        """).format(sql.Identifier(schema)))
    parts = urlsplit(database_url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "options"]
    query.append(("options", f"-csearch_path={schema},public"))
    scoped_url = urlunsplit(parts._replace(query=urlencode(query)))
    settings = RagSettings(
        _env_file=None, DATABASE_URL=scoped_url, OPENAI_EMBEDDING_MODEL="test-embedding",
        embedding_dimensions=3, data_dir=rag_settings.data_dir, chunk_words=40, chunk_overlap=5,
    )
    try:
        GuidelineStore(settings).initialize()
        yield settings
    finally:
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_real_pgvector_ingestion_persistence_and_retrieval(postgres_settings, test_embedder):
    store = GuidelineStore(postgres_settings)
    store.initialize()
    result = ingest_guidelines(settings=postgres_settings, embedder=test_embedder)
    matches = search_guidelines("creatinine", 2, settings=postgres_settings, embedder=test_embedder)
    assert result.document_count == 3
    assert result.chunk_count > 3
    assert len(matches) == 2
    assert all(match.document_id == "synthetic-renal-context-v1" for match in matches)
    assert all(match.distance == pytest.approx(0, abs=1e-6) for match in matches)
    assert all(match.similarity == pytest.approx(1, abs=1e-6) for match in matches)
    assert matches[0].license == "CC0-1.0"
    assert "Project-authored synthetic guideline" in matches[0].source
    assert len(matches[0].content_sha256) == 64
    assert matches[0].text
    with store.connection() as connection:
        rows = connection.execute("SELECT chunk_id, text, vector_dims(embedding) AS dims FROM ida_guideline_chunks").fetchall()
        document_count = connection.execute("SELECT count(*) AS count FROM ida_guideline_documents").fetchone()["count"]
    assert len(rows) == result.chunk_count
    assert document_count == result.document_count
    assert all(row["dims"] == 3 for row in rows)
    assert next(row["text"] for row in rows if row["chunk_id"] == matches[0].chunk_id) == matches[0].text

    repeated = ingest_guidelines(settings=postgres_settings, embedder=test_embedder)
    assert repeated == result
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) AS count FROM ida_guideline_chunks").fetchone()["count"] == result.chunk_count


def test_empty_database_and_incompatible_embedding_profiles(postgres_settings, test_embedder):
    assert search_guidelines("culture", settings=postgres_settings, embedder=test_embedder) == []
    ingest_guidelines(settings=postgres_settings, embedder=test_embedder)
    changed = postgres_settings.model_copy(update={"embedding_model": "different-test-model"})
    test_embedder.model = "different-test-model"
    assert search_guidelines("culture", settings=changed, embedder=test_embedder) == []
    test_embedder.model = "test-embedding"
    test_embedder.dimensions = 2
    original_embed = test_embedder.embed
    test_embedder.embed = lambda texts: [vector[:2] for vector in original_embed(texts)]
    changed = postgres_settings.model_copy(update={"embedding_dimensions": 2})
    assert search_guidelines("culture", settings=changed, embedder=test_embedder) == []


def test_failed_replacement_rolls_back_document_and_chunks(postgres_settings, test_embedder):
    from unittest.mock import Mock
    capture = Mock()
    ingest_guidelines(settings=postgres_settings, embedder=test_embedder, store=capture)
    prepared, model, dimensions = capture.replace_documents.call_args.args
    store = GuidelineStore(postgres_settings)
    store.replace_documents(prepared, model, dimensions)
    original = prepared[0]
    changed_document = original.document.model_copy(update={"title": "Must roll back"})
    duplicate_chunks = [original.chunks[0], original.chunks[0]]
    invalid = PreparedDocument(changed_document, duplicate_chunks, [original.embeddings[0]] * 2)
    with pytest.raises(GuidelineDatabaseError):
        store.replace_documents([invalid], model, dimensions)
    with store.connection() as connection:
        document = connection.execute("SELECT title FROM ida_guideline_documents WHERE document_id = %s",
                                      (original.document.document_id,)).fetchone()
        rows = connection.execute("SELECT text FROM ida_guideline_chunks WHERE document_id = %s ORDER BY chunk_index",
                                  (original.document.document_id,)).fetchall()
    assert document["title"] == original.document.title
    assert [row["text"] for row in rows] == [chunk.text for chunk in original.chunks]


def test_changed_document_replaces_old_chunks_atomically(postgres_settings, test_embedder):
    from infectious_disease_assistant.rag.documents import chunk_document, load_sources
    initial = ingest_guidelines(settings=postgres_settings, embedder=test_embedder)
    source = load_sources(postgres_settings.data_dir / "guidelines/manifest.json")[0]
    document, chunks = chunk_document(source, "# Updated section\nUpdated synthetic culture review context.", chunk_words=40, overlap=5)
    store = GuidelineStore(postgres_settings)
    store.replace_documents([PreparedDocument(document, chunks, test_embedder.embed([chunk.text for chunk in chunks]))],
                            test_embedder.model, test_embedder.dimensions)
    with store.connection() as connection:
        rows = connection.execute("SELECT text, chunk_id FROM ida_guideline_chunks WHERE document_id = %s",
                                  (source.document_id,)).fetchall()
        metadata = connection.execute("SELECT content_sha256 FROM ida_guideline_documents WHERE document_id = %s",
                                      (source.document_id,)).fetchone()
        count = connection.execute("SELECT count(*) AS count FROM ida_guideline_documents").fetchone()["count"]
    assert rows == [{"text": chunks[0].text, "chunk_id": chunks[0].chunk_id}]
    assert metadata["content_sha256"] == document.content_sha256
    assert count == initial.document_count
