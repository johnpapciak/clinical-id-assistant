import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from agents.tracing import TracingProcessor, set_trace_processors

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class CapturingTraceProcessor(TracingProcessor):
    def __init__(self):
        self.traces = []
        self.spans = []
        self.flush_count = 0

    def on_trace_start(self, trace):
        pass

    def on_trace_end(self, trace):
        self.traces.append(trace)

    def on_span_start(self, span):
        pass

    def on_span_end(self, span):
        self.spans.append(span)

    def shutdown(self):
        pass

    def force_flush(self):
        self.flush_count += 1


@pytest.fixture(autouse=True)
def restore_application_logger():
    logger = logging.getLogger("infectious_disease_assistant")
    previous = logger.handlers[:], logger.level, logger.propagate
    yield
    logger.handlers, level, logger.propagate = previous
    logger.setLevel(level)


@pytest.fixture
def sdk_traces():
    processor = CapturingTraceProcessor()
    set_trace_processors([processor])
    yield processor
    set_trace_processors([])


@pytest.fixture(autouse=True)
def configure_synthetic_data(monkeypatch):
    monkeypatch.setenv("IDA_DATA_DIR", str(DATA_DIR))


@pytest.fixture
def synthetic_payload():
    return json.loads((DATA_DIR / "patients.json").read_text(encoding="utf-8"))


@pytest.fixture
def write_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("IDA_DATA_DIR", str(tmp_path))

    def write(payload):
        path = tmp_path / "patients.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    return write


@pytest.fixture
def agent_model(monkeypatch, tmp_path, sdk_traces):
    from agents import OpenAIResponsesModel

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.delenv("OPENAI_AGENTS_DISABLE_TRACING", raising=False)
    model = AsyncMock(side_effect=AssertionError("Live model calls are disabled in tests"))
    monkeypatch.setattr(OpenAIResponsesModel, "_fetch_response", model)
    return model


@pytest.fixture
def make_clinical_summary():
    from infectious_disease_assistant.models.clinical_summary import ClinicalFinding, ClinicalSummary, EvidenceReference
    from infectious_disease_assistant.services.patients import get_patient_summary

    def make(patient_id="PATIENT_001", tools=None):
        if tools is None:
            tools = ["get_medications", "get_allergies", "get_recent_labs",
                     "get_microbiology_results", "get_previous_infections"]
        patient = get_patient_summary(patient_id)
        cultures = patient.microbiology_results if "get_microbiology_results" in tools else []
        latest = cultures[0] if cultures else None
        return ClinicalSummary(
            patient_id=patient_id,
            microbiology_result_id=latest.result_id if latest else None,
            organism=latest.organism if latest else None,
            specimen_type=latest.specimen if latest else None,
            relevant_medications=patient.medications if "get_medications" in tools else [],
            relevant_allergies=patient.allergies if "get_allergies" in tools else [],
            relevant_labs=patient.recent_labs if "get_recent_labs" in tools else [],
            previous_infections=patient.previous_infections if "get_previous_infections" in tools else [],
            important_findings=[ClinicalFinding(
                text="A recorded microbiology result is available.",
                evidence=[EvidenceReference(tool_name="get_microbiology_results", record_index=0)],
            )] if latest else [],
            missing_information=[] if latest else ["No microbiology results recorded."],
            evidence_sources=tools,
        )

    return make
