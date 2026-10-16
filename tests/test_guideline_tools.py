import asyncio
import json
from datetime import date
from unittest.mock import Mock

import pytest
from agents.tool_context import ToolContext

from infectious_disease_assistant.rag.errors import GuidelineDatabaseError
from infectious_disease_assistant.tools.guideline_tools import search_guidelines
from infectious_disease_assistant.tools.patient_tools import PatientContext


def tool_context(patient_id="PATIENT_001"):
    return ToolContext(
        context=PatientContext(patient_id, date(2026, 10, 15)),
        tool_name="search_guidelines", tool_call_id="rag-test", tool_arguments="{}",
    )


def test_tool_formats_matches_and_preserves_stable_indices(guideline_match, monkeypatch):
    other = guideline_match.model_copy(update={"chunk_id": "c" * 64, "chunk_index": 1, "text": "Other review context"})
    service = Mock(side_effect=[[guideline_match], [other, guideline_match], []])
    monkeypatch.setattr("infectious_disease_assistant.tools.guideline_tools.service.search_guidelines", service)
    context = tool_context()
    first = json.loads(asyncio.run(search_guidelines.on_invoke_tool(context, '{"query":"creatinine", "top_k":2}')))
    second = json.loads(asyncio.run(search_guidelines.on_invoke_tool(context, '{"query":"renal", "top_k":2}')))
    empty = json.loads(asyncio.run(search_guidelines.on_invoke_tool(context, '{"query":"unrelated", "top_k":2}')))

    assert first["records"][0] == guideline_match.model_dump(mode="json")
    assert second["records"] == [guideline_match.model_dump(mode="json"), other.model_dump(mode="json")]
    assert second["matched_chunk_ids"] == [other.chunk_id, guideline_match.chunk_id]
    assert empty["matched_chunk_ids"] == []
    assert empty["records"] == second["records"]
    assert context.context.tool_results["search_guidelines"] == [guideline_match, other]
    assert "not patient facts" in first["notice"]
    assert service.call_args_list[0].args == ("creatinine", 2)
    assert "patient_id" not in search_guidelines.params_json_schema["properties"]
    assert set(search_guidelines.params_json_schema["properties"]) == {"query", "top_k"}


def test_empty_search_records_call_without_fabricating_sources(monkeypatch):
    monkeypatch.setattr("infectious_disease_assistant.tools.guideline_tools.service.search_guidelines", Mock(return_value=[]))
    context = tool_context("PATIENT_005")
    payload = json.loads(asyncio.run(search_guidelines.on_invoke_tool(context, '{"query":"culture", "top_k":3}')))
    assert payload["records"] == []
    assert payload["matched_chunk_ids"] == []
    assert context.context.tool_results == {"search_guidelines": []}


def test_tool_failure_aborts_and_does_not_record_success(monkeypatch):
    monkeypatch.setattr("infectious_disease_assistant.tools.guideline_tools.service.search_guidelines", Mock(side_effect=GuidelineDatabaseError("Unavailable")))
    context = tool_context()
    with pytest.raises(GuidelineDatabaseError):
        asyncio.run(search_guidelines.on_invoke_tool(context, '{"query":"culture", "top_k":3}'))
    assert context.context.tool_results == {}
