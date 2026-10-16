import json
from pathlib import Path

import pytest

from infectious_disease_assistant.rag.documents import chunk_document, clean_text, extract_document, load_sources
from infectious_disease_assistant.rag.errors import GuidelineDocumentError
from infectious_disease_assistant.rag.models import DocumentSource


@pytest.fixture
def source():
    return DocumentSource(document_id="demo-v1", title="Demo", source="Project-authored synthetic",
                          license="CC0-1.0", path="demo.md")


def test_clean_text_preserves_values_and_units():
    assert clean_text("  creatinine\t1.8 mg/dL\r\n on 2026-10-14  ") == "creatinine 1.8 mg/dL on 2026-10-14"


def test_chunking_preserves_sections_metadata_and_overlap(source):
    text = "# First\n one two three four five six seven\n## Second\n allergy unknown\n"
    document, chunks = chunk_document(source, text, chunk_words=4, overlap=1)

    assert [chunk.text for chunk in chunks] == ["one two three four", "four five six seven", "allergy unknown"]
    assert [chunk.section for chunk in chunks] == ["First", "First", "Second"]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
    assert len({chunk.chunk_id for chunk in chunks}) == 3
    assert chunks[0].document_id == document.document_id == source.document_id
    assert chunks[0].source == source.source
    assert chunks[0].license == "CC0-1.0"
    assert chunk_document(source, text, chunk_words=4, overlap=1) == (document, chunks)
    assert chunk_document(source, text + "Changed", chunk_words=4, overlap=1)[0].content_sha256 != document.content_sha256


def test_chunking_without_heading_uses_document_title(source):
    assert chunk_document(source, "body text", chunk_words=10, overlap=0)[1][0].section == "Demo"


@pytest.mark.parametrize("words, overlap", [(0, 0), (4, -1), (4, 4), (4, 5)])
def test_invalid_chunk_configuration(source, words, overlap):
    with pytest.raises(ValueError):
        chunk_document(source, "body", chunk_words=words, overlap=overlap)


def test_heading_only_document_rejected(source):
    with pytest.raises(GuidelineDocumentError, match="no body"):
        chunk_document(source, "# Heading\n", chunk_words=5, overlap=0)


@pytest.mark.parametrize("path", ["../outside.md", "/tmp/outside.md", "demo.pdf"])
def test_extraction_rejects_paths_outside_manifest_and_unsupported_formats(source, tmp_path, path):
    source = source.model_copy(update={"path": path})
    with pytest.raises(GuidelineDocumentError, match="local Markdown"):
        extract_document(source, tmp_path)


@pytest.mark.parametrize("contents", [None, "  \n\t"])
def test_missing_or_empty_document(source, tmp_path, contents):
    if contents is not None:
        (tmp_path / source.path).write_text(contents)
    with pytest.raises(GuidelineDocumentError):
        extract_document(source, tmp_path)


def test_symlink_escape_is_rejected(source, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("Outside")
    directory = tmp_path / "manifest"
    directory.mkdir()
    (directory / source.path).symlink_to(outside)
    with pytest.raises(GuidelineDocumentError, match="inside"):
        extract_document(source, directory)


@pytest.mark.parametrize("contents", ["invalid", "{}", "[]", '[{"document_id":"incomplete"}]'])
def test_invalid_manifest(tmp_path, contents):
    path = tmp_path / "manifest.json"
    path.write_text(contents)
    with pytest.raises(GuidelineDocumentError):
        load_sources(path)


def test_duplicate_document_ids_rejected(source, tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([source.model_dump()] * 2))
    with pytest.raises(GuidelineDocumentError, match="unique"):
        load_sources(path)


def test_committed_guidelines_are_explicitly_synthetic_and_licensed():
    directory = Path(__file__).resolve().parents[1] / "data" / "guidelines"
    sources = load_sources(directory / "manifest.json")
    assert len(sources) == 3
    for source in sources:
        text = extract_document(source, directory)
        assert source.license == "CC0-1.0"
        assert source.source.startswith("Project-authored synthetic guideline;")
        assert "CC0-1.0" in text
        assert "synthetic" in text.lower()
