import math
from typing import Protocol

from openai import OpenAI, OpenAIError

from infectious_disease_assistant.config import EmbeddingCredentials, RagSettings
from infectious_disease_assistant.rag.errors import EmbeddingError


class EmbeddingProvider(Protocol):
    model: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def validate_embeddings(vectors: list[list[float]], count: int, dimensions: int) -> list[list[float]]:
    if len(vectors) != count:
        raise EmbeddingError("Embedding count does not match input count")
    for vector in vectors:
        if len(vector) != dimensions:
            raise EmbeddingError("Embedding dimensions do not match configuration")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
            raise EmbeddingError("Embeddings must contain finite numbers")
        if not any(value != 0 for value in vector):
            raise EmbeddingError("Cosine search requires nonzero embeddings")
    return vectors


class OpenAIEmbeddings:
    def __init__(self, settings: RagSettings):
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions
        self.api_key = EmbeddingCredentials().openai_api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            with OpenAI(api_key=self.api_key.get_secret_value(), timeout=60.0, max_retries=1) as client:
                response = client.embeddings.create(
                    model=self.model, input=texts, dimensions=self.dimensions,
                )
        except OpenAIError:
            raise EmbeddingError("Embedding request failed; check API access and embedding configuration") from None
        entries = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in entries] != list(range(len(texts))):
            raise EmbeddingError("Embedding response has unexpected indices")
        return validate_embeddings([item.embedding for item in entries], len(texts), self.dimensions)
