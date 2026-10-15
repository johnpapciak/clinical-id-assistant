import json
import subprocess
import sys
import httpx

import pytest

from infectious_disease_assistant.__main__ import main
from infectious_disease_assistant.models.patient import PatientSummary
from infectious_disease_assistant.services.patients import get_patient_summary


def test_lookup_command(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["infectious_disease_assistant", "PATIENT_001"])
    assert main() == 0
    result = capsys.readouterr()
    assert result.err == ""
    assert PatientSummary.model_validate_json(result.out) == get_patient_summary("PATIENT_001")


def test_module_entry_point_help(tmp_path):
    result = subprocess.run([sys.executable, "-m", "infectious_disease_assistant", "--help"],
                            cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "patient_id" in result.stdout


def test_interactive_prompt(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["infectious_disease_assistant"])
    prompts = []

    def enter_patient_id(prompt):
        prompts.append(prompt)
        return "PATIENT_005"

    monkeypatch.setattr("builtins.input", enter_patient_id)

    assert main() == 0
    output = capsys.readouterr()
    assert prompts == ["Patient ID: "]
    assert json.loads(output.out)["patient_id"] == "PATIENT_005"
    assert output.err == ""


@pytest.mark.parametrize("patient_id, message", [
    ("PATIENT_999", "Unknown synthetic patient: PATIENT_999"),
    ("invalid", "Patient ID must match"),
])
def test_lookup_errors(monkeypatch, capsys, patient_id, message):
    monkeypatch.setattr(sys, "argv", ["infectious_disease_assistant", patient_id])

    assert main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert message in output.err
    assert "Traceback" not in output.err


def test_fhir_endpoint_error(monkeypatch, fhir_api, capsys):
    monkeypatch.setattr(sys, "argv", ["infectious_disease_assistant", "PATIENT_001"])
    fhir_api.responses["/fhir/metadata"] = httpx.Response(503, json={"resourceType": "OperationOutcome"})

    assert main() == 1
    assert "FHIR endpoint returned HTTP 503" in capsys.readouterr().err


def test_invalid_configuration_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["infectious_disease_assistant", "PATIENT_001"])
    monkeypatch.setenv("IDA_LOG_LEVEL", "invalid")

    assert main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "Error:" in output.err
    assert "log_level" in output.err
    assert "Traceback" not in output.err


def test_missing_interactive_input(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["infectious_disease_assistant"])

    def end_input(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", end_input)

    assert main() == 1
    assert "a patient ID is required" in capsys.readouterr().err
