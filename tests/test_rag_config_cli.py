from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from infectious_disease_assistant.config import EmbeddingCredentials, RagSettings
from infectious_disease_assistant.rag.__main__ import main
from infectious_disease_assistant.rag.errors import GuidelineDatabaseError


def test_database_secret_hidden_and_init_needs_no_api_key(rag_settings, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert rag_settings.embedding_dimensions == 3
    assert "synthetic-test-only" not in repr(rag_settings)
    assert "synthetic-test-only" not in rag_settings.model_dump_json()


@pytest.mark.parametrize("options", [
    {"DATABASE_URL": ""}, {"DATABASE_URL": "http://localhost"},
    {"chunk_words": 20, "chunk_overlap": 20}, {"chunk_overlap": -1},
    {"embedding_dimensions": 0}, {"OPENAI_EMBEDDING_MODEL": " "},
])
def test_bad_configuration(options):
    with pytest.raises(ValidationError):
        RagSettings(_env_file=None, **({"DATABASE_URL": "postgresql://localhost/demo"} | options))


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_embedding_key_rejected(value):
    with pytest.raises(ValidationError):
        EmbeddingCredentials(_env_file=None, OPENAI_API_KEY=value)


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/demo")
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "custom-embedding")
    monkeypatch.setenv("IDA_EMBEDDING_DIMENSIONS", "256")
    monkeypatch.setenv("IDA_CHUNK_WORDS", "60")
    monkeypatch.setenv("IDA_CHUNK_OVERLAP", "10")
    settings = RagSettings(_env_file=None)
    assert (settings.embedding_model, settings.embedding_dimensions, settings.chunk_words, settings.chunk_overlap) == ("custom-embedding", 256, 60, 10)


def test_init_command(rag_settings, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rag", "init"])
    monkeypatch.setattr("infectious_disease_assistant.rag.__main__.RagSettings", Mock(return_value=rag_settings))
    store = Mock()
    monkeypatch.setattr("infectious_disease_assistant.rag.__main__.GuidelineStore", Mock(return_value=store))
    assert main() == 0
    store.initialize.assert_called_once_with()
    assert "initialized" in capsys.readouterr().out


def test_search_command(guideline_match, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rag", "search", "creatinine", "--top-k", "2"])
    search = Mock(return_value=[guideline_match])
    monkeypatch.setattr("infectious_disease_assistant.rag.__main__.search_guidelines", search)
    assert main() == 0
    search.assert_called_once_with("creatinine", 2)
    output = capsys.readouterr().out
    assert '"source": "Project-authored synthetic guideline"' in output
    assert '"distance": 0.1' in output


def test_ingest_command(rag_settings, test_embedder, monkeypatch, capsys):
    from infectious_disease_assistant.rag.service import ingest_guidelines
    result = ingest_guidelines(settings=rag_settings, embedder=test_embedder, store=Mock())
    monkeypatch.setattr("sys.argv", ["rag", "ingest"])
    ingest = Mock(return_value=result)
    monkeypatch.setattr("infectious_disease_assistant.rag.__main__.ingest_guidelines", ingest)
    assert main() == 0
    ingest.assert_called_once_with(None)
    assert '"document_count": 3' in capsys.readouterr().out


def test_empty_query_command_is_sanitized(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rag", "search", " "])
    assert main() == 1
    assert capsys.readouterr().err.startswith("Error: check RAG inputs")


def test_database_error_command(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rag", "search", "creatinine"])
    monkeypatch.setattr("infectious_disease_assistant.rag.__main__.search_guidelines", Mock(side_effect=GuidelineDatabaseError("Unavailable")))
    assert main() == 1
    assert capsys.readouterr().err == "Error: Unavailable\n"
