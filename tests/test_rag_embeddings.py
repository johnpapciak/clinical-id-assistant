from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from openai import APIConnectionError
import httpx2

from infectious_disease_assistant.rag.embeddings import OpenAIEmbeddings, validate_embeddings
from infectious_disease_assistant.rag.errors import EmbeddingError


@pytest.mark.parametrize("vectors, count, dimensions", [
    ([], 1, 3), ([[1.0]], 1, 3), ([[0, 0, 0]], 1, 3),
    ([[float("nan"), 1, 0]], 1, 3), ([[float("inf"), 1, 0]], 1, 3),
    ([[True, 1, 0]], 1, 3), ([["1", 1, 0]], 1, 3),
])
def test_invalid_embeddings_fail_predictably(vectors, count, dimensions):
    with pytest.raises(EmbeddingError):
        validate_embeddings(vectors, count, dimensions)


def test_embedding_client_uses_configuration_and_restores_response_order(rag_settings, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    client = MagicMock()
    client.embeddings.create.return_value = SimpleNamespace(data=[
        SimpleNamespace(index=1, embedding=[0, 1, 0]), SimpleNamespace(index=0, embedding=[1, 0, 0]),
    ])
    factory = MagicMock()
    factory.return_value.__enter__.return_value = client
    monkeypatch.setattr("infectious_disease_assistant.rag.embeddings.OpenAI", factory)
    provider = OpenAIEmbeddings(rag_settings)
    assert provider.embed(["first", "second"]) == [[1, 0, 0], [0, 1, 0]]
    client.embeddings.create.assert_called_once_with(model="test-embedding", input=["first", "second"], dimensions=3)
    assert provider.embed([]) == []
    assert client.embeddings.create.call_count == 1


@pytest.mark.parametrize("indices", [[0, 0], [1, 2], [0]])
def test_unexpected_embedding_indices_rejected(rag_settings, monkeypatch, indices):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    factory = MagicMock()
    factory.return_value.__enter__.return_value.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=index, embedding=[1, 0, 0]) for index in indices],
    )
    monkeypatch.setattr("infectious_disease_assistant.rag.embeddings.OpenAI", factory)
    with pytest.raises(EmbeddingError, match="indices"):
        OpenAIEmbeddings(rag_settings).embed(["first", "second"])


def test_embedding_api_errors_do_not_expose_payload_or_key(rag_settings, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    factory = MagicMock()
    factory.return_value.__enter__.return_value.embeddings.create.side_effect = APIConnectionError(
        message="synthetic-test-key and request contents", request=httpx2.Request("POST", "https://api.openai.com/v1/embeddings"),
    )
    monkeypatch.setattr("infectious_disease_assistant.rag.embeddings.OpenAI", factory)
    with pytest.raises(EmbeddingError) as caught:
        OpenAIEmbeddings(rag_settings).embed(["text"])
    assert "synthetic-test-key" not in str(caught.value)
    assert "request contents" not in str(caught.value)
