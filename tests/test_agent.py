import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents import Runner
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError, UserError
from openai.types.responses import Response, ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from infectious_disease_assistant.agent import (
    MAX_AGENT_TURNS,
    AgentDemoError,
    investigate_patient,
)
from infectious_disease_assistant.fhir.errors import FhirError
from infectious_disease_assistant.models.summary_draft import SummaryDraft
from infectious_disease_assistant.models.clinical_summary import CLINICAL_DISCLAIMER, ClinicalSummary
from infectious_disease_assistant.services import patients
from infectious_disease_assistant.services.patients import InvalidPatientIdError, PatientNotFoundError
from infectious_disease_assistant.services.summary_validation import SummaryEvidenceError
from infectious_disease_assistant.tools.patient_tools import PATIENT_TOOLS


def tool_response(names, turn=0):
    return Response(
        output=[ResponseFunctionToolCall(
            type="function_call", name=name, arguments="{}", call_id=f"call-{turn}-{index}",
        ) for index, name in enumerate(names)],
        id=f"resp-tool-{turn}", created_at=0, model="test-model", object="response",
        parallel_tool_calls=True, tool_choice="auto", tools=[],
    )


def final_response(summary):
    if isinstance(summary, ClinicalSummary):
        fields = {name: getattr(summary, name) for name in SummaryDraft.model_fields if name in ClinicalSummary.model_fields}
        draft = SummaryDraft(**fields, findings=[reference for finding in summary.important_findings for reference in finding.evidence],
                             propose_followup=summary.proposed_action is not None)
        text = draft.model_dump_json()
    else:
        text = summary
    return Response(
        output=[ResponseOutputMessage(
            id="message-final", type="message", role="assistant", status="completed",
            content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
        )],
        id="resp-final", created_at=0, model="test-model", object="response",
        parallel_tool_calls=True, tool_choice="auto", tools=[],
    )


@pytest.mark.parametrize("patient_id, plan", [
    ("PATIENT_001", [["get_microbiology_results"], ["get_medications", "get_allergies", "get_recent_labs", "get_previous_infections"]]),
    ("PATIENT_004", [["get_microbiology_results"]]),
])
def test_sdk_executes_only_model_requested_tools(agent_model, patient_id, plan, caplog, sdk_traces, make_clinical_summary):
    caplog.set_level("INFO", logger="infectious_disease_assistant")
    requested = [name for names in plan for name in names]
    expected = make_clinical_summary(patient_id, requested)
    caplog.clear()
    agent_model.side_effect = [
        *(tool_response(names, turn) for turn, names in enumerate(plan)),
        final_response(expected),
    ]

    review = asyncio.run(investigate_patient(patient_id))

    assert review == expected
    assert review.disclaimer == CLINICAL_DISCLAIMER
    assert agent_model.await_count == len(plan) + 1
    completed = [record.event_fields["tool_name"] for record in caplog.records if record.message == "tool_completed"]
    assert sorted(completed) == sorted(requested)
    assert caplog.records[0].message == "investigation_started"
    assert caplog.records[-1].message == "investigation_completed"
    assert "Escherichia coli" not in caplog.text
    assert "test-key-not-real" not in caplog.text

    final_input = agent_model.await_args_list[-1].args[1]
    outputs = [item for item in final_input if isinstance(item, dict) and item.get("type") == "function_call_output"]
    assert len(outputs) == len(requested)
    for output in outputs:
        payload = json.loads(output["output"])
        assert payload["patient_id"] == patient_id
        assert payload["reference_date"] == "2026-10-15"
        assert isinstance(payload["records"], list)
    assert json.loads(outputs[0]["output"])["records"][0]["outcome"] == (
        "growth" if patient_id == "PATIENT_001" else "no_growth"
    )
    assert len(sdk_traces.traces) == 1
    responses = [span.span_data for span in sdk_traces.spans if span.span_data.type == "response"]
    assert len(responses) == len(plan) + 1
    assert SummaryDraft.model_validate_json(responses[-1].response.output[0].content[0].text).patient_id == review.patient_id
    assert "new microbiology result" in str(responses[0].input)
    assert sdk_traces.traces[0].tracing_api_key == "test-key-not-real"
    assert "test-key-not-real" not in json.dumps(sdk_traces.traces[0].export())
    functions = [span.span_data for span in sdk_traces.spans if span.span_data.type == "function"]
    assert sorted(span.name for span in functions) == sorted(requested)
    assert all(span.input == "{}" for span in functions)
    assert json.loads(functions[0].output)["patient_id"] == patient_id
    final_span = next(span for span in sdk_traces.spans if getattr(span.span_data, "name", None) == "validated_clinical_summary")
    assert final_span.span_data.data["summary"] == review.model_dump(mode="json")
    assert caplog.records[-1].event_fields["trace_id"] == sdk_traces.traces[0].trace_id


def test_runner_has_one_agent_scoped_context_and_bounded_execution(agent_model, monkeypatch, make_clinical_summary):
    agent_model.side_effect = [tool_response(["get_microbiology_results"]), final_response(make_clinical_summary("PATIENT_002", ["get_microbiology_results"]))]
    runner = AsyncMock(wraps=Runner.run)
    monkeypatch.setattr(Runner, "run", runner)

    asyncio.run(investigate_patient("PATIENT_002"))

    args = runner.await_args
    agent = args.args[0]
    assert agent.name == "Infectious Disease Clinical Assistant"
    assert agent.model.model == "test-model"
    assert agent.handoffs == []
    assert agent.mcp_servers == []
    assert [tool.name for tool in agent.tools] == [tool.name for tool in PATIENT_TOOLS] + ["search_guidelines"]
    assert agent.model_settings.tool_choice == "auto"
    assert agent.model_settings.store is True
    assert agent.output_type is SummaryDraft
    assert args.kwargs["context"].patient_id == "PATIENT_002"
    assert args.kwargs["run_config"].tracing_disabled is False
    assert args.kwargs["run_config"].trace_include_sensitive_data is True
    assert args.kwargs["max_turns"] == MAX_AGENT_TURNS
    assert "new microbiology result" in args.kwargs["input"]
    assert "cephalexin" not in args.kwargs["input"]
    assert agent_model.await_count == 2


@pytest.mark.parametrize("patient_id, error", [
    ("invalid", InvalidPatientIdError), ("PATIENT_999", PatientNotFoundError),
])
def test_patient_is_validated_before_model_invocation(agent_model, patient_id, error):
    with pytest.raises(error):
        asyncio.run(investigate_patient(patient_id))
    agent_model.assert_not_awaited()


@pytest.mark.parametrize("output", [None, "", "  ", []])
def test_missing_final_review_is_an_error(agent_model, monkeypatch, output):
    monkeypatch.setattr(Runner, "run", AsyncMock(return_value=SimpleNamespace(final_output=output)))

    with pytest.raises(AgentDemoError, match="no valid clinical summary"):
        asyncio.run(investigate_patient("PATIENT_001"))


def test_repeated_tool_calls_hit_turn_limit(agent_model, monkeypatch):
    monkeypatch.setattr("infectious_disease_assistant.agent.MAX_AGENT_TURNS", 2)
    agent_model.side_effect = [
        tool_response(["get_microbiology_results"], turn=0),
        tool_response(["get_microbiology_results"], turn=1),
    ]

    with pytest.raises(MaxTurnsExceeded):
        asyncio.run(investigate_patient("PATIENT_001"))
    assert agent_model.await_count == 2


def test_service_failure_aborts_run_before_final_response(agent_model, monkeypatch):
    agent_model.side_effect = [
        tool_response(["get_microbiology_results"]), final_response("Must not be returned"),
    ]

    def fail_lookup(patient_id):
        raise FhirError("Synthetic fixture unavailable")

    monkeypatch.setattr(patients, "get_microbiology_results", fail_lookup)

    with pytest.raises(UserError, match="Synthetic fixture unavailable") as caught:
        asyncio.run(investigate_patient("PATIENT_001"))
    assert isinstance(caught.value.__cause__, FhirError)
    assert agent_model.await_count == 1


def test_sparse_patient_has_clean_empty_output(agent_model, make_clinical_summary):
    expected = make_clinical_summary("PATIENT_005", ["get_microbiology_results"])
    agent_model.side_effect = [tool_response(["get_microbiology_results"]), final_response(expected)]

    summary = asyncio.run(investigate_patient("PATIENT_005"))

    assert summary.organism is None
    assert summary.specimen_type is None
    assert summary.relevant_labs == []
    assert summary.relevant_medications == []
    assert summary.important_findings == []
    assert summary.missing_information == ["No microbiology results recorded."]


def test_sdk_rejects_malformed_structured_output(agent_model):
    agent_model.side_effect = [tool_response(["get_microbiology_results"]), final_response('{"patient_id": 5}')]

    with pytest.raises(ModelBehaviorError):
        asyncio.run(investigate_patient("PATIENT_001"))


def test_unsupported_output_fails_without_completed_event(agent_model, make_clinical_summary, caplog):
    caplog.set_level("INFO", logger="infectious_disease_assistant")
    unsupported = make_clinical_summary("PATIENT_001", ["get_microbiology_results"])
    unsupported.organism = "Fabricated organism"
    agent_model.side_effect = [tool_response(["get_microbiology_results"]), final_response(unsupported)]

    with pytest.raises(SummaryEvidenceError):
        asyncio.run(investigate_patient("PATIENT_001"))
    assert caplog.records[-1].message == "investigation_failed"
    assert caplog.records[-1].event_fields["error_type"] == "SafetyViolation"
    assert "investigation_completed" not in caplog.text
    assert "Fabricated organism" not in caplog.text


def test_agent_can_combine_fhir_tools_with_optional_rag(agent_model, guideline_match, monkeypatch, make_clinical_summary, sdk_traces, caplog):
    from unittest.mock import Mock
    from infectious_disease_assistant.models.clinical_summary import ClinicalFinding, EvidenceReference

    caplog.set_level("INFO", logger="infectious_disease_assistant")
    search = Mock(return_value=[guideline_match])
    monkeypatch.setattr("infectious_disease_assistant.tools.guideline_tools.service.search_guidelines", search)
    expected = make_clinical_summary(tools=["get_microbiology_results", "get_recent_labs", "search_guidelines"])
    expected.important_findings.append(ClinicalFinding(
        text="Synthetic guideline context is cited for clinician review.",
        evidence=[EvidenceReference(tool_name="search_guidelines", record_index=0)],
    ))
    expected.guideline_sources = [guideline_match.citation()]
    rag_response = tool_response(["search_guidelines"], 2)
    rag_response.output[0].arguments = '{"query":"creatinine and renal laboratory review context", "top_k":2}'
    agent_model.side_effect = [
        tool_response(["get_microbiology_results"]), tool_response(["get_recent_labs"], 1),
        rag_response, final_response(expected),
    ]
    summary = asyncio.run(investigate_patient("PATIENT_001", guideline_question="What renal review context is missing?"))
    assert summary == expected
    assert summary.guideline_sources[0].source == guideline_match.source
    search.assert_called_once_with("creatinine and renal laboratory review context", 2)
    functions = [span.span_data for span in sdk_traces.spans if span.span_data.type == "function"]
    assert [span.name for span in functions] == ["get_microbiology_results", "get_recent_labs", "search_guidelines"]
    assert json.loads(functions[-1].input)["top_k"] == 2
    assert json.loads(functions[-1].output)["records"][0]["chunk_id"] == guideline_match.chunk_id
    assert guideline_match.text not in caplog.text
    assert caplog.records[-1].message == "investigation_completed"


def test_no_rag_call_needs_no_database_or_embedding_settings(agent_model, monkeypatch, make_clinical_summary):
    from unittest.mock import Mock
    forbidden = Mock(side_effect=AssertionError("RAG configuration must be lazy"))
    monkeypatch.setattr("infectious_disease_assistant.rag.service.RagSettings", forbidden)
    agent_model.side_effect = [tool_response(["get_microbiology_results"]),
                              final_response(make_clinical_summary("PATIENT_004", ["get_microbiology_results"]))]
    summary = asyncio.run(investigate_patient("PATIENT_004"))
    assert summary.guideline_sources == []
    forbidden.assert_not_called()


def test_rag_database_failure_aborts_sdk_investigation(agent_model, monkeypatch):
    from unittest.mock import Mock
    from infectious_disease_assistant.rag.errors import GuidelineDatabaseError
    monkeypatch.setattr("infectious_disease_assistant.tools.guideline_tools.service.search_guidelines",
                        Mock(side_effect=GuidelineDatabaseError("Guideline database unavailable")))
    rag_response = tool_response(["search_guidelines"], 1)
    rag_response.output[0].arguments = '{"query":"culture context", "top_k":3}'
    agent_model.side_effect = [tool_response(["get_microbiology_results"]), rag_response]
    with pytest.raises(UserError) as caught:
        asyncio.run(investigate_patient("PATIENT_001"))
    assert isinstance(caught.value.__cause__, GuidelineDatabaseError)
    assert agent_model.await_count == 2


def test_named_historical_microbiology_event_is_investigated(agent_model, make_clinical_summary):
    expected = make_clinical_summary(tools=["get_microbiology_results"])
    prior = patients.get_microbiology_results("PATIENT_001")[1]
    expected.microbiology_result_id, expected.organism, expected.specimen_type = prior.result_id, prior.organism, prior.specimen
    expected.important_findings[0].evidence[0].record_index = 1
    agent_model.side_effect = [tool_response(["get_microbiology_results"]), final_response(expected)]
    summary = asyncio.run(investigate_patient("PATIENT_001", microbiology_result_id="MICRO_001_PRIOR"))
    assert summary == expected
    assert "MICRO_001_PRIOR" in str(agent_model.await_args_list[0].args[1])


def test_named_event_cannot_be_silently_replaced_by_newest_result(agent_model, make_clinical_summary):
    expected = make_clinical_summary(tools=["get_microbiology_results"])
    agent_model.side_effect = [tool_response(["get_microbiology_results"]), final_response(expected)]
    with pytest.raises(SummaryEvidenceError, match="unsupported_fact_or_citation"):
        asyncio.run(investigate_patient("PATIENT_001", microbiology_result_id="MICRO_001_PRIOR"))


def test_malformed_event_id_rejected_before_model(agent_model):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        asyncio.run(investigate_patient("PATIENT_001", microbiology_result_id="real-ehr-id"))
    agent_model.assert_not_awaited()
