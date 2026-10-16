import json
import logging
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
from unittest.mock import AsyncMock

import pytest
import httpx
from agents.tracing import TracingProcessor, set_trace_processors, set_tracing_disabled

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
    set_tracing_disabled(False)
    yield processor
    set_tracing_disabled(True)
    set_trace_processors([])


@pytest.fixture(autouse=True)
def configure_synthetic_data(monkeypatch):
    monkeypatch.setenv("IDA_DATA_DIR", str(DATA_DIR))
    monkeypatch.setenv("IDA_FHIR_BASE_URL", "http://127.0.0.1:8765/fhir")
    monkeypatch.setenv("IDA_CURRENT_USER_ID", "REVIEWER_001")
    monkeypatch.setenv("IDA_PATIENT_ASSIGNMENTS", json.dumps({
        "REVIEWER_001": [*[f"PATIENT_{number:03}" for number in range(1, 6)], "PATIENT_999"],
    }))


@pytest.fixture
def fhir_bundle():
    return json.loads((DATA_DIR / "fhir" / "bundle.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def fhir_api(monkeypatch):
    from infectious_disease_assistant.fhir.server import handle_request, load_fixtures

    api = SimpleNamespace(store=load_fixtures(DATA_DIR / "fhir" / "bundle.json"), requests=[], responses={})

    def respond(request):
        api.requests.append(request)
        override = api.responses.get(request.url.path)
        if override is not None:
            return override(request) if callable(override) else override
        status, payload = handle_request(api.store, request.method, request.url.path,
                                         parse_qs(request.url.query.decode(), keep_blank_values=True))
        return httpx.Response(status, json=payload, headers={"Content-Type": "application/fhir+json"})

    def client():
        from infectious_disease_assistant.config import Settings
        return httpx.Client(base_url=Settings().fhir_base_url.rstrip("/") + "/", transport=httpx.MockTransport(respond))

    monkeypatch.setattr("infectious_disease_assistant.fhir.client.create_http_client", client)
    return api


@pytest.fixture
def agent_model(monkeypatch, tmp_path, sdk_traces):
    from agents import OpenAIResponsesModel

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("IDA_TRACE_INCLUDE_PAYLOADS", "true")
    monkeypatch.setenv("IDA_STORE_MODEL_RESPONSES", "true")
    monkeypatch.delenv("OPENAI_AGENTS_DISABLE_TRACING", raising=False)
    model = AsyncMock(side_effect=AssertionError("Live model calls are disabled in tests"))
    monkeypatch.setattr(OpenAIResponsesModel, "_fetch_response", model)
    return model


@pytest.fixture
def make_clinical_summary():
    from infectious_disease_assistant.models.clinical_summary import ClinicalFinding, ClinicalSummary, EvidenceReference
    from infectious_disease_assistant.services.patients import get_patient_summary
    from infectious_disease_assistant.guardrails import missing_information

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
            missing_information=missing_information({name: getattr(patient, attribute) for name, attribute in {
                "get_microbiology_results": "microbiology_results", "get_medications": "medications",
                "get_allergies": "allergies", "get_recent_labs": "recent_labs", "get_previous_infections": "previous_infections",
            }.items() if name in tools}),
            evidence_sources=tools,
        )

    return make


@pytest.fixture
def rag_settings():
    from infectious_disease_assistant.config import RagSettings
    return RagSettings(
        _env_file=None, DATABASE_URL="postgresql://demo:synthetic-test-only@127.0.0.1:5433/unused",
        OPENAI_EMBEDDING_MODEL="test-embedding", embedding_dimensions=3,
        chunk_words=40, chunk_overlap=5, data_dir=DATA_DIR,
    )


@pytest.fixture
def test_embedder():
    class TestEmbeddings:
        model = "test-embedding"
        dimensions = 3

        def __init__(self):
            self.calls = []

        def embed(self, texts):
            self.calls.append(list(texts))
            vectors = []
            for text in texts:
                text = text.lower()
                if "creatinine" in text or "renal" in text:
                    vectors.append([1.0, 0.1, 0.1])
                elif "urine" in text or "culture" in text:
                    vectors.append([0.1, 1.0, 0.1])
                else:
                    vectors.append([0.1, 0.1, 1.0])
            return vectors
    return TestEmbeddings()


@pytest.fixture
def guideline_match():
    from infectious_disease_assistant.rag.models import GuidelineMatch
    return GuidelineMatch(
        document_id="synthetic-renal-context-v1", title="Synthetic renal context",
        source="Project-authored synthetic guideline", license="CC0-1.0",
        content_sha256="a" * 64, chunk_id="b" * 64,
        section="Creatinine review", chunk_index=0,
        text="Preserve creatinine value, unit, date, and flag; identify missing baseline context.",
        distance=0.1, similarity=0.9,
    )


@pytest.fixture
def workflow_store():
    from workflow_fake import FakeWorkflowStore
    return FakeWorkflowStore()
