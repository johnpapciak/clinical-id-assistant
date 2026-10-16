from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
DocumentId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class GuidelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentSource(GuidelineModel):
    document_id: DocumentId
    title: Text
    source: Text
    license: Text
    path: Text


class GuidelineDocument(GuidelineModel):
    document_id: DocumentId
    title: Text
    source: Text
    license: Text
    content_sha256: Sha256


class GuidelineCitation(GuidelineDocument):
    chunk_id: Sha256
    section: Text
    chunk_index: int = Field(ge=0)


class GuidelineChunk(GuidelineCitation):
    text: Text


class GuidelineMatch(GuidelineChunk):
    distance: float = Field(ge=0, le=2, allow_inf_nan=False)
    similarity: float = Field(ge=-1, le=1, allow_inf_nan=False)

    def citation(self) -> GuidelineCitation:
        return GuidelineCitation.model_validate(self.model_dump(include=set(GuidelineCitation.model_fields)))


class GuidelineSearch(GuidelineModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    top_k: int = Field(default=3, ge=1, le=10, strict=True)


class IngestionResult(GuidelineModel):
    document_count: int
    chunk_count: int
    embedding_model: str
    embedding_dimensions: int
