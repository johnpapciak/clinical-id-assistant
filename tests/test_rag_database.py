import hashlib
from importlib.resources import files
from unittest.mock import MagicMock

import psycopg
import pytest
from psycopg.sql import Composable

from infectious_disease_assistant.rag.database import GuidelineStore, PreparedDocument
from infectious_disease_assistant.rag.errors import EmbeddingError, GuidelineDatabaseError
from infectious_disease_assistant.rag.models import GuidelineChunk, GuidelineDocument


@pytest.fixture
def database_connection(monkeypatch):
    factory = MagicMock()
    connection = factory.return_value.__enter__.return_value
    monkeypatch.setattr("infectious_disease_assistant.rag.database.psycopg.connect", factory)
    return connection


def prepared_document(match):
    return PreparedDocument(
        GuidelineDocument.model_validate(match.model_dump(include=set(GuidelineDocument.model_fields))),
        [GuidelineChunk.model_validate(match.model_dump(exclude={"distance", "similarity"}))], [[1.0, 0.0, 0.0]],
    )


def test_persistence_keeps_document_and_chunk_metadata_parameterized(rag_settings, guideline_match, database_connection):
    store = GuidelineStore(rag_settings)
    prepared = prepared_document(guideline_match)
    store.replace_documents([prepared], "test-embedding", 3)
    calls = database_connection.execute.call_args_list
    document_params = calls[0].args[1]
    assert document_params == (guideline_match.document_id, guideline_match.title, guideline_match.source,
                               "CC0-1.0", "a" * 64, "test-embedding", 3)
    assert calls[1].args[1] == (guideline_match.document_id,)
    cursor = database_connection.cursor.return_value.__enter__.return_value
    params = cursor.executemany.call_args.args[1][0]
    assert params == ("b" * 64, guideline_match.document_id, "Creatinine review", 0,
                      guideline_match.text, "[1.0, 0.0, 0.0]", 3)
    assert guideline_match.text not in cursor.executemany.call_args.args[0]


def test_invalid_vectors_prevent_database_connection(rag_settings, guideline_match, database_connection):
    prepared = prepared_document(guideline_match)
    prepared.embeddings[0] = [0, 0, 0]
    with pytest.raises(EmbeddingError):
        GuidelineStore(rag_settings).replace_documents([prepared], "test-embedding", 3)
    database_connection.execute.assert_not_called()


def test_search_uses_pgvector_cosine_and_embedding_profile(rag_settings, guideline_match, database_connection):
    database_connection.execute.return_value.fetchall.return_value = [guideline_match.model_dump(exclude={"similarity"})]
    results = GuidelineStore(rag_settings).search([1, 0, 0], model="test-embedding", dimensions=3, top_k=2)
    sql, params = database_connection.execute.call_args.args
    assert "<=> %s::vector" in sql
    assert "ORDER BY distance, c.chunk_id" in sql
    assert params == ("[1, 0, 0]", "test-embedding", 3, 3, 2)
    assert results == [guideline_match]
    assert results[0].citation() == guideline_match.citation()


def test_initialize_applies_bundled_migration(rag_settings, database_connection):
    database_connection.execute.return_value.fetchone.return_value = None
    GuidelineStore(rag_settings).initialize()
    sql_calls = [query.as_string() if isinstance(query, Composable) else query
                 for call in database_connection.execute.call_args_list for query in [call.args[0]]]
    migration = next(sql for sql in sql_calls if "CREATE EXTENSION" in sql)
    assert "CREATE TABLE ida_guideline_documents" in migration
    assert "embedding vector NOT NULL" in migration
    assert "CREATE TABLE ida_guideline_chunks" in migration
    assert any("INSERT INTO \"ida_guideline_migrations\"" in sql for sql in sql_calls)


def test_initialize_skips_applied_migration(rag_settings, database_connection):
    sql = files("infectious_disease_assistant.rag").joinpath("sql/001_guidelines.sql").read_text()
    database_connection.execute.return_value.fetchone.return_value = {"checksum": hashlib.sha256(sql.encode()).hexdigest()}
    GuidelineStore(rag_settings).initialize()
    assert not any(isinstance(call.args[0], str) and "CREATE EXTENSION" in call.args[0]
                   for call in database_connection.execute.call_args_list)


def test_changed_migration_is_rejected(rag_settings, database_connection):
    database_connection.execute.return_value.fetchone.return_value = {"checksum": "changed"}
    with pytest.raises(GuidelineDatabaseError, match="has changed"):
        GuidelineStore(rag_settings).initialize()


def test_transport_failure_is_sanitized(rag_settings, monkeypatch):
    factory = MagicMock(side_effect=psycopg.OperationalError("synthetic-test-only password leaked"))
    monkeypatch.setattr("infectious_disease_assistant.rag.database.psycopg.connect", factory)
    with pytest.raises(GuidelineDatabaseError) as caught:
        GuidelineStore(rag_settings).initialize()
    assert "synthetic-test-only" not in str(caught.value)
