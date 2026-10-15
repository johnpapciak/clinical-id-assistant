import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from agents.exceptions import MaxTurnsExceeded
from openai import OpenAIError

from infectious_disease_assistant import agent_demo
from infectious_disease_assistant.agent import DECISION_SUPPORT_NOTICE, AgentDemoError


def test_cli_prints_one_review(agent_model, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agent_demo", "PATIENT_001"])
    run = AsyncMock(return_value=f"{DECISION_SUPPORT_NOTICE}\n\nMock summary.")
    monkeypatch.setattr(agent_demo, "investigate_patient", run)

    assert agent_demo.main() == 0
    output = capsys.readouterr()
    assert output.out.count(DECISION_SUPPORT_NOTICE) == 1
    assert output.out.endswith("Mock summary.\n")
    assert output.err == ""
    assert run.await_args.args == ("PATIENT_001",)
    assert isinstance(run.await_args.kwargs["hooks"], agent_demo.ToolUsageHooks)


@pytest.mark.parametrize("patient_id, message", [
    ("invalid", "Patient ID must match"), ("PATIENT_999", "Unknown synthetic patient"),
])
def test_cli_patient_errors_make_no_model_call(agent_model, monkeypatch, capsys, patient_id, message):
    monkeypatch.setattr(sys, "argv", ["agent_demo", patient_id])

    assert agent_demo.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert message in output.err
    assert "Traceback" not in output.err
    agent_model.assert_not_awaited()


def test_cli_configuration_error(agent_model, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agent_demo", "PATIENT_001"])
    monkeypatch.delenv("OPENAI_API_KEY")

    assert agent_demo.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "OPENAI_API_KEY" in output.err
    assert "test-key-not-real" not in output.err
    agent_model.assert_not_awaited()


def test_cli_data_error(agent_model, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["agent_demo", "PATIENT_001"])
    monkeypatch.setenv("IDA_DATA_DIR", str(tmp_path))

    assert agent_demo.main() == 1
    assert "Could not load synthetic patient data" in capsys.readouterr().err
    agent_model.assert_not_awaited()


@pytest.mark.parametrize("error, message", [
    (OpenAIError("sensitive upstream text"), "OpenAI request failed"),
    (MaxTurnsExceeded("sensitive upstream text"), "agent run failed"),
    (AgentDemoError("The agent returned no final review"), "no final review"),
])
def test_cli_run_errors_are_predictable(agent_model, monkeypatch, capsys, error, message):
    monkeypatch.setattr(sys, "argv", ["agent_demo", "PATIENT_001"])
    monkeypatch.setattr(agent_demo, "investigate_patient", AsyncMock(side_effect=error))

    assert agent_demo.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert message in output.err
    assert "sensitive upstream text" not in output.err
    assert "test-key-not-real" not in output.err
    assert "Traceback" not in output.err


def test_module_help_needs_no_api_credentials(agent_model, monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("OPENAI_MODEL")
    result = subprocess.run(
        [sys.executable, "-m", "infectious_disease_assistant.agent_demo", "--help"],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0
    assert "patient_id" in result.stdout
    assert result.stderr == ""
