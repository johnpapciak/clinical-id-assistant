import asyncio
import io
import json
import logging

import pytest

from infectious_disease_assistant.agent import investigate_patient
from infectious_disease_assistant.observability import (
    JsonEventFormatter, configure_logging, log_event, trace_url,
)
from infectious_disease_assistant.services.patients import InvalidPatientIdError, PatientNotFoundError

TRACE_ID = "trace_" + "a" * 32


def test_json_event_formatter_has_stable_fields():
    record = logging.LogRecord("infectious_disease_assistant", logging.INFO, "", 0, "tool_completed", (), None)
    record.created = 0
    record.event_fields = {"patient_id": "PATIENT_001", "trace_id": TRACE_ID, "record_count": 2}

    payload = json.loads(JsonEventFormatter().format(record))

    assert payload == {
        "timestamp": "1970-01-01T00:00:00+00:00", "level": "INFO", "event": "tool_completed",
        "patient_id": "PATIENT_001", "trace_id": TRACE_ID, "record_count": 2,
    }


def test_application_logs_only_event_metadata_and_do_not_duplicate_handlers():
    stream = io.StringIO()
    configure_logging(stream=stream)
    configure_logging(stream=stream)
    log_event("tool_completed", patient_id="PATIENT_001", trace_id=TRACE_ID,
              tool_name="get_recent_labs", record_count=2)
    log_event("investigation_completed", patient_id="PATIENT_001", trace_id=TRACE_ID,
              duration_ms=12.5, completed=True)
    records = [json.loads(line) for line in stream.getvalue().splitlines()]

    assert len(records) == 2
    assert records[0]["record_count"] == 2
    assert records[1]["duration_ms"] == 12.5
    assert records[1]["trace_url"] == trace_url(TRACE_ID)
    assert not {"records", "summary", "arguments", "results", "api_key"} & records[0].keys()


def test_failure_log_has_error_type_without_exception_payload():
    stream = io.StringIO()
    configure_logging(stream=stream)
    log_event("investigation_failed", patient_id="PATIENT_001", trace_id=TRACE_ID, error_type="OpenAIError")

    payload = json.loads(stream.getvalue())
    assert payload["level"] == "ERROR"
    assert payload["error_type"] == "OpenAIError"
    assert "trace_url" not in payload


@pytest.mark.parametrize("patient_id, error, logged_id", [
    ("PATIENT_999", PatientNotFoundError, "PATIENT_999"),
    ("not-a-synthetic-ID", InvalidPatientIdError, None),
])
def test_unknown_or_invalid_patient_logs_failure_without_api_or_trace(agent_model, sdk_traces, caplog, patient_id, error, logged_id):
    caplog.set_level(logging.INFO, logger="infectious_disease_assistant")

    with pytest.raises(error):
        asyncio.run(investigate_patient(patient_id, trace_id=TRACE_ID))

    events = [record.message for record in caplog.records]
    assert events == ["investigation_started", "investigation_failed"]
    assert caplog.records[-1].event_fields["patient_id"] == logged_id
    assert caplog.records[-1].event_fields["error_type"] == error.__name__
    assert caplog.records[-1].event_fields["trace_id"] == TRACE_ID
    agent_model.assert_not_awaited()
    assert sdk_traces.traces == []


def test_trace_url_uses_sdk_trace_id():
    assert trace_url(TRACE_ID) == f"https://platform.openai.com/traces/trace?trace_id={TRACE_ID}"


@pytest.mark.parametrize("invalid_id", ["", "trace_short", "trace_" + "?" * 32, "https://example.com"])
def test_invalid_trace_ids_are_rejected(invalid_id):
    with pytest.raises(ValueError, match="Invalid SDK trace ID"):
        trace_url(invalid_id)
