class RagError(RuntimeError):
    """Guideline ingestion or retrieval failed."""


class GuidelineDocumentError(RagError):
    pass


class EmbeddingError(RagError):
    pass


class GuidelineDatabaseError(RagError):
    pass
