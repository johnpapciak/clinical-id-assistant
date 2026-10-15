import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents import ModelResponse, Runner, Usage
from agents.exceptions import MaxTurnsExceeded, UserError
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from infectious_disease_assistant.agent import (
    DECISION_SUPPORT_NOTICE,
    MAX_AGENT_TURNS,
    AgentDemoError,
    investigate_patient,
)
from infectious_disease_assistant.agent_demo import ToolUsageHooks
from infectious_disease_assistant.data_loader import DataLoadError
from infectious_disease_assistant.services import patients
from infectious_disease_assistant.services.patients import InvalidPatientIdError, PatientNotFoundError
from infectious_disease_assistant.tools.patient_tools import PATIENT_TOOLS


def tool_response(names, turn=0):
    return ModelResponse(
        output=[ResponseFunctionToolCall(
            type="function_call", name=name, arguments="{}", call_id=f"call-{turn}-{index}",
        ) for index, name in enumerate(names)],
        usage=Usage(), response_id=None,
    )


def final_response(text):
    return ModelResponse(
        output=[ResponseOutputMessage(
            id="message-final", type="message", role="assistant", status="completed",
            content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
        )],
        usage=Usage(), response_id=None,
    )


@pytest.mark.parametrize("patient_id, plan", [
    ("PATIENT_001", [["get_microbiology_results"], ["get_medications", "get_allergies", "get_recent_labs", "get_previous_infections"]]),
    ("PATIENT_004", [["get_microbiology_results"]]),
])
def test_sdk_executes_only_model_requested_tools(agent_model, patient_id, plan, capsys):
    agent_model.side_effect = [
        *(tool_response(names, turn) for turn, names in enumerate(plan)),
        final_response("Mock review of synthetic tool data for clinician review."),
    ]

    review = asyncio.run(investigate_patient(patient_id, hooks=ToolUsageHooks()))

    assert review.startswith(DECISION_SUPPORT_NOTICE)
    assert review.count("Mock review") == 1
    assert agent_model.await_count == len(plan) + 1
    requested = [name for names in plan for name in names]
    logs = capsys.readouterr()
    assert logs.out == ""
    for tool in PATIENT_TOOLS:
        assert (f"Tool requested: {tool.name} ({patient_id})" in logs.err) == (tool.name in requested)
        assert (f"Tool completed: {tool.name}" in logs.err) == (tool.name in requested)

    final_input = agent_model.await_args_list[-1].kwargs["input"]
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


def test_runner_has_one_agent_scoped_context_and_bounded_execution(agent_model, monkeypatch):
    runner = AsyncMock(return_value=SimpleNamespace(final_output="Mock review"))
    monkeypatch.setattr(Runner, "run", runner)

    asyncio.run(investigate_patient("PATIENT_002"))

    args = runner.await_args
    agent = args.args[0]
    assert agent.name == "Infectious Disease Clinical Assistant"
    assert agent.model.model == "test-model"
    assert agent.handoffs == []
    assert agent.mcp_servers == []
    assert [tool.name for tool in agent.tools] == [tool.name for tool in PATIENT_TOOLS]
    assert agent.model_settings.tool_choice == "auto"
    assert agent.model_settings.store is False
    assert args.kwargs["context"].patient_id == "PATIENT_002"
    assert args.kwargs["run_config"].tracing_disabled is True
    assert args.kwargs["max_turns"] == MAX_AGENT_TURNS
    assert "new microbiology result" in args.kwargs["input"]
    assert "cephalexin" not in args.kwargs["input"]
    agent_model.assert_not_awaited()


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

    with pytest.raises(AgentDemoError, match="no final review"):
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
        raise DataLoadError("Synthetic fixture unavailable")

    monkeypatch.setattr(patients, "get_microbiology_results", fail_lookup)

    with pytest.raises(UserError, match="Synthetic fixture unavailable") as caught:
        asyncio.run(investigate_patient("PATIENT_001"))
    assert isinstance(caught.value.__cause__, DataLoadError)
    assert agent_model.await_count == 1
