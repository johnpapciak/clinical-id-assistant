from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from infectious_disease_assistant.rag.errors import EmbeddingError, GuidelineDatabaseError
from infectious_disease_assistant.rag.service import ingest_guidelines, search_guidelines


def test_ingestion_prepares_text_embeddings_and_source_metadata(rag_settings, test_embedder):
    store = Mock()
    result = ingest_guidelines(settings=rag_settings, embedder=test_embedder, store=store)
    prepared, model, dimensions = store.replace_documents.call_args.args

    assert result.document_count == len(prepared) == 3
    assert result.chunk_count == sum(len(item.chunks) for item in prepared)
    assert result.embedding_model == model == "test-embedding"
    assert result.embedding_dimensions == dimensions == 3
    assert len(test_embedder.calls) == 3
    for item, texts in zip(prepared, test_embedder.calls, strict=True):
        assert texts == [chunk.text for chunk in item.chunks]
        assert len(item.embeddings) == len(item.chunks)
        assert item.document.license == "CC0-1.0"
        assert all(chunk.content_sha256 == item.document.content_sha256 for chunk in item.chunks)
        assert [chunk.chunk_index for chunk in item.chunks] == list(range(len(item.chunks)))


def test_embedding_failure_never_starts_persistence(rag_settings, test_embedder, monkeypatch):
    store = Mock()
    monkeypatch.setattr(test_embedder, "embed", Mock(side_effect=EmbeddingError("Unavailable")))
    with pytest.raises(EmbeddingError):
        ingest_guidelines(settings=rag_settings, embedder=test_embedder, store=store)
    store.replace_documents.assert_not_called()


def test_search_embeds_clean_query_and_returns_structured_matches(rag_settings, test_embedder, guideline_match):
    store = Mock(search=Mock(return_value=[guideline_match]))
    results = search_guidelines("  creatinine review  ", 2, settings=rag_settings, embedder=test_embedder, store=store)
    assert results == [guideline_match]
    assert test_embedder.calls == [["creatinine review"]]
    store.search.assert_called_once_with([1.0, 0.1, 0.1], model="test-embedding", dimensions=3, top_k=2)
    assert results[0].source == guideline_match.source
    assert results[0].section == "Creatinine review"
    assert results[0].distance == 0.1
    assert results[0].similarity == 0.9


def test_no_results_is_empty_list(rag_settings, test_embedder):
    assert search_guidelines("culture", settings=rag_settings, embedder=test_embedder,
                             store=Mock(search=Mock(return_value=[]))) == []


@pytest.mark.parametrize("query, top_k", [("", 3), (" \n\t", 3), (None, 3), ("x" * 2001, 3),
                                         ("culture", 0), ("culture", 11), ("culture", True)])
def test_bad_search_input_fails_before_configuration_or_embedding(query, top_k, monkeypatch):
    settings = Mock(side_effect=AssertionError("Configuration should not load"))
    monkeypatch.setattr("infectious_disease_assistant.rag.service.RagSettings", settings)
    with pytest.raises(ValidationError):
        search_guidelines(query, top_k)
    settings.assert_not_called()


def test_incompatible_embedding_provider_is_rejected(rag_settings, test_embedder):
    test_embedder.model = "different-model"
    with pytest.raises(EmbeddingError, match="does not match"):
        search_guidelines("culture", settings=rag_settings, embedder=test_embedder, store=Mock())
    assert test_embedder.calls == []


def test_malformed_embedding_is_rejected_before_database(rag_settings, test_embedder, monkeypatch):
    store = Mock()
    monkeypatch.setattr(test_embedder, "embed", Mock(return_value=[[1.0]]))
    with pytest.raises(EmbeddingError, match="dimensions"):
        search_guidelines("culture", settings=rag_settings, embedder=test_embedder, store=store)
    store.search.assert_not_called()


def test_database_failure_propagates(rag_settings, test_embedder):
    store = Mock(search=Mock(side_effect=GuidelineDatabaseError("Unavailable")))
    with pytest.raises(GuidelineDatabaseError):
        search_guidelines("culture", settings=rag_settings, embedder=test_embedder, store=store)
