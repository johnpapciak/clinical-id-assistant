import asyncio
import json
from datetime import date

import pytest
from agents import FunctionTool
from agents.exceptions import ModelBehaviorError
from agents.tool_context import ToolContext

from infectious_disease_assistant.data_loader import DataLoadError
from infectious_disease_assistant.services import patients
from infectious_disease_assistant.tools.patient_tools import PATIENT_TOOLS, PatientContext


def invoke(tool, patient_id="PATIENT_001", arguments="{}"):
    context = ToolContext(
        context=PatientContext(patient_id, date(2026, 10, 15)),
        tool_name=tool.name, tool_call_id="test-call", tool_arguments=arguments,
    )
    return asyncio.run(tool.on_invoke_tool(context, arguments))


@pytest.mark.parametrize("patient_id", [f"PATIENT_{number:03}" for number in range(1, 6)])
@pytest.mark.parametrize("tool", PATIENT_TOOLS, ids=lambda tool: tool.name)
def test_tool_preserves_typed_service_data(tool, patient_id):
    payload = json.loads(invoke(tool, patient_id))

    assert payload["patient_id"] == patient_id
    assert payload["reference_date"] == "2026-10-15"
    expected = getattr(patients, tool.name)(patient_id)
    assert payload["records"] == [record.model_dump(mode="json") for record in expected]


@pytest.mark.parametrize("tool", PATIENT_TOOLS, ids=lambda tool: tool.name)
def test_tool_schema_keeps_patient_selection_in_local_context(tool):
    assert isinstance(tool, FunctionTool)
    assert tool.strict_json_schema is True
    assert tool.params_json_schema["properties"] == {}
    assert tool.params_json_schema["additionalProperties"] is False


@pytest.mark.parametrize("tool", PATIENT_TOOLS, ids=lambda tool: tool.name)
def test_tool_uses_selected_patient_only(tool, monkeypatch):
    called_with = []

    def record_lookup(patient_id):
        called_with.append(patient_id)
        return []

    monkeypatch.setattr(patients, tool.name, record_lookup)
    payload = json.loads(invoke(tool, "PATIENT_003"))

    assert called_with == ["PATIENT_003"]
    assert payload["patient_id"] == "PATIENT_003"


@pytest.mark.parametrize("tool", PATIENT_TOOLS, ids=lambda tool: tool.name)
@pytest.mark.parametrize("patient_id, error", [
    ("PATIENT_999", patients.PatientNotFoundError), ("invalid", patients.InvalidPatientIdError),
])
def test_patient_errors_propagate_without_model_visible_fallback(tool, patient_id, error):
    with pytest.raises(error):
        invoke(tool, patient_id)


def test_data_failure_propagates(monkeypatch, tmp_path):
    monkeypatch.setenv("IDA_DATA_DIR", str(tmp_path))

    with pytest.raises(DataLoadError):
        invoke(PATIENT_TOOLS[0])


def test_invalid_json_is_rejected():
    with pytest.raises(ModelBehaviorError):
        invoke(PATIENT_TOOLS[0], arguments="not JSON")
