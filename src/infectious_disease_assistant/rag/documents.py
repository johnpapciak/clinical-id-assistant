import hashlib
import json
import re
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from infectious_disease_assistant.rag.errors import GuidelineDocumentError
from infectious_disease_assistant.rag.models import DocumentSource, GuidelineChunk, GuidelineDocument


def clean_text(text: str) -> str:
    return " ".join(text.split())


def load_sources(manifest_path: Path) -> list[DocumentSource]:
    try:
        sources = TypeAdapter(list[DocumentSource]).validate_python(
            json.loads(manifest_path.read_text(encoding="utf-8")),
        )
    except (OSError, UnicodeError, ValueError, ValidationError):
        raise GuidelineDocumentError("Guideline manifest is missing or invalid") from None
    if not sources or len({source.document_id for source in sources}) != len(sources):
        raise GuidelineDocumentError("Manifest must contain unique guideline documents")
    return sources


def extract_document(source: DocumentSource, directory: Path) -> str:
    base = directory.resolve()
    path = (base / source.path).resolve()
    if not path.is_relative_to(base) or path.suffix.lower() != ".md":
        raise GuidelineDocumentError("Guideline sources must be local Markdown files inside the manifest directory")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise GuidelineDocumentError("Guideline source could not be read") from None
    if not clean_text(text):
        raise GuidelineDocumentError("Guideline source is empty")
    return text


def chunk_document(
    source: DocumentSource, text: str, *, chunk_words: int, overlap: int,
) -> tuple[GuidelineDocument, list[GuidelineChunk]]:
    if chunk_words < 1 or overlap < 0 or overlap >= chunk_words:
        raise ValueError("Chunk size must be positive and overlap smaller than chunk size")
    document = GuidelineDocument(
        **source.model_dump(exclude={"path"}),
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    sections: list[tuple[str, str]] = []
    heading, lines = source.title, []
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            if clean_text(" ".join(lines)):
                sections.append((heading, clean_text(" ".join(lines))))
            heading, lines = match.group(1), []
        else:
            lines.append(line)
    if clean_text(" ".join(lines)):
        sections.append((heading, clean_text(" ".join(lines))))
    chunks = []
    for section, body in sections:
        words = body.split()
        for start in range(0, len(words), chunk_words - overlap):
            chunk_text = " ".join(words[start:start + chunk_words])
            index = len(chunks)
            identity = f"{document.document_id}:{document.content_sha256}:{index}:{section}:{chunk_text}"
            chunks.append(GuidelineChunk(
                **document.model_dump(), chunk_id=hashlib.sha256(identity.encode()).hexdigest(),
                section=section, chunk_index=index, text=chunk_text,
            ))
            if start + chunk_words >= len(words):
                break
    if not chunks:
        raise GuidelineDocumentError("Guideline document has no body text")
    return document, chunks
