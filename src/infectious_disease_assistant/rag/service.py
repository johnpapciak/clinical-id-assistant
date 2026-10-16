from pathlib import Path

from infectious_disease_assistant.config import RagSettings
from infectious_disease_assistant.rag.database import GuidelineStore, PreparedDocument
from infectious_disease_assistant.rag.documents import chunk_document, extract_document, load_sources
from infectious_disease_assistant.rag.embeddings import EmbeddingProvider, OpenAIEmbeddings, validate_embeddings
from infectious_disease_assistant.rag.errors import EmbeddingError
from infectious_disease_assistant.rag.models import GuidelineMatch, GuidelineSearch, IngestionResult


def _embedding_provider(settings: RagSettings, provider: EmbeddingProvider | None) -> EmbeddingProvider:
    provider = provider if provider is not None else OpenAIEmbeddings(settings)
    if provider.model != settings.embedding_model or provider.dimensions != settings.embedding_dimensions:
        raise EmbeddingError("Embedding provider does not match the configured model and dimensions")
    return provider


def ingest_guidelines(
    manifest_path: Path | None = None, *, settings: RagSettings | None = None,
    embedder: EmbeddingProvider | None = None, store: GuidelineStore | None = None,
) -> IngestionResult:
    settings = settings if settings is not None else RagSettings()
    manifest_path = manifest_path if manifest_path is not None else settings.data_dir / "guidelines" / "manifest.json"
    sources = load_sources(manifest_path)
    documents = []
    for source in sources:
        document, chunks = chunk_document(
            source, extract_document(source, manifest_path.parent),
            chunk_words=settings.chunk_words, overlap=settings.chunk_overlap,
        )
        documents.append((document, chunks))
    provider = _embedding_provider(settings, embedder)
    prepared = [PreparedDocument(
        document, chunks,
        validate_embeddings(provider.embed([chunk.text for chunk in chunks]), len(chunks), provider.dimensions),
    ) for document, chunks in documents]
    database = store if store is not None else GuidelineStore(settings)
    database.replace_documents(prepared, provider.model, provider.dimensions)
    return IngestionResult(
        document_count=len(prepared), chunk_count=sum(len(item.chunks) for item in prepared),
        embedding_model=provider.model, embedding_dimensions=provider.dimensions,
    )


def search_guidelines(
    query: str, top_k: int = 3, *, settings: RagSettings | None = None,
    embedder: EmbeddingProvider | None = None, store: GuidelineStore | None = None,
) -> list[GuidelineMatch]:
    request = GuidelineSearch(query=query, top_k=top_k)
    settings = settings if settings is not None else RagSettings()
    provider = _embedding_provider(settings, embedder)
    vectors = validate_embeddings(provider.embed([request.query]), 1, provider.dimensions)
    database = store if store is not None else GuidelineStore(settings)
    return database.search(vectors[0], model=provider.model, dimensions=provider.dimensions, top_k=request.top_k)
