from unittest.mock import ANY, AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from agents.exceptions import ModelBehaviorError
from fastapi.testclient import TestClient
from openai import OpenAIError

from infectious_disease_assistant.api import create_app, get_investigation_service
from infectious_disease_assistant.fhir.errors import FhirTransportError
from infectious_disease_assistant.models.clinical_summary import ClinicalSummary
from infectious_disease_assistant.models.workflow import AgentRunRecord
from infectious_disease_assistant.models.investigation import ErrorResponse, InvestigationResponse
from infectious_disease_assistant.services import investigations

VALID_REQUEST = {"patient_id": "PATIENT_001", "microbiology_result_id": "MICRO_001_RECENT"}


@pytest.fixture
def coordinator(monkeypatch, make_clinical_summary, fhir_api):
    mock = AsyncMock(return_value=make_clinical_summary(tools=["get_microbiology_results"]))
    monkeypatch.setattr(investigations, "investigate_patient", mock)
    fhir_api.requests.clear()
    return mock


@pytest.fixture
def client(sdk_traces, workflow_store):
    with TestClient(create_app(store=workflow_store)) as client:
        yield client


def test_health_requires_no_agent_fhir_database_or_api_key(client, coordinator, monkeypatch, fhir_api):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert not fhir_api.requests
    coordinator.assert_not_awaited()


def test_valid_investigation_returns_created_summary_and_location(client, coordinator):
    response = client.post("/investigations", json=VALID_REQUEST)
    assert response.status_code == 201
    result = InvestigationResponse.model_validate(response.json())
    assert result.status == "awaiting_review"
    assert result.summary == coordinator.return_value
    assert result.patient_id == "PATIENT_001"
    assert result.microbiology_result_id == "MICRO_001_RECENT"
    assert response.headers["location"] == f"/investigations/{result.investigation_id}"
    coordinator.assert_awaited_once_with("PATIENT_001", microbiology_result_id="MICRO_001_RECENT", trace_id=ANY, audit=ANY)
    fetched = client.get(response.headers["location"])
    assert fetched.status_code == 200
    assert fetched.json() == response.json()
    assert coordinator.await_count == 1


@pytest.mark.parametrize("body", [
    {}, {"patient_id": "PATIENT_001"},
    {"patient_id": "REAL_001", "microbiology_result_id": "RESULT_001"},
    {"patient_id": "PATIENT_001", "microbiology_result_id": "actual-ehr-id"},
    {"patient_id": 1, "microbiology_result_id": "RESULT_001"},
    {"patient_id": "PATIENT_001", "microbiology_result_id": None},
    {**VALID_REQUEST, "clinical_text": "unrequested-clinical-text"},
    {**VALID_REQUEST, "openai_api_key": "secret-rejected-value"},
    [VALID_REQUEST], None,
])
def test_invalid_request_is_422_without_reflecting_inputs(client, coordinator, fhir_api, body):
    response = client.post("/investigations", json=body)
    assert response.status_code == 422
    error = ErrorResponse.model_validate(response.json())
    assert error.code == "validation_error"
    assert error.issues
    assert "secret-rejected-value" not in response.text
    assert "unrequested-clinical-text" not in response.text
    assert "input" not in response.text
    assert not fhir_api.requests
    coordinator.assert_not_awaited()


def test_malformed_json_is_422_without_echoing_body(client):
    response = client.post("/investigations", content='{"patient_id":"secret-rejected-value"', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "secret-rejected-value" not in response.text
    assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize("body, code", [
    ({"patient_id": "PATIENT_999", "microbiology_result_id": "RESULT_001"}, "patient_not_found"),
    ({"patient_id": "PATIENT_001", "microbiology_result_id": "RESULT_999"}, "microbiology_result_not_found"),
    ({"patient_id": "PATIENT_002", "microbiology_result_id": "MICRO_001_RECENT"}, "microbiology_result_not_found"),
    ({"patient_id": "PATIENT_005", "microbiology_result_id": "RESULT_001"}, "microbiology_result_not_found"),
])
def test_unknown_patient_or_result_is_404_before_agent(client, coordinator, body, code):
    response = client.post("/investigations", json=body)
    assert response.status_code == 404
    assert ErrorResponse.model_validate(response.json()).code == code
    coordinator.assert_not_awaited()


def test_unknown_investigation_is_404(client):
    response = client.get(f"/investigations/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "investigation_not_found"


def test_malformed_investigation_id_is_422(client):
    response = client.get("/investigations/secret-rejected-value")
    assert response.status_code == 422
    assert "secret-rejected-value" not in response.text
    assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize("exception, expected_status, code", [
    (OpenAIError("secret-api-key"), 503, "investigation_unavailable"),
    (FhirTransportError("postgresql://user:secret-password@host/db"), 503, "investigation_unavailable"),
    (ModelBehaviorError("secret-api-key"), 502, "invalid_clinical_summary"),
    (RuntimeError("secret-api-key"), 500, "investigation_failed"),
])
def test_failure_responses_do_not_expose_secrets(client, coordinator, exception, expected_status, code):
    coordinator.side_effect = exception
    response = client.post("/investigations", json=VALID_REQUEST)
    assert response.status_code == expected_status
    assert ErrorResponse.model_validate(response.json()).code == code
    assert "secret-api-key" not in response.text
    assert "secret-password" not in response.text
    assert "Traceback" not in response.text
    assert all(row.status == "failed" for row in client.app.state.investigations.store.records.values())


def test_missing_agent_configuration_is_503_without_secret_input(client, monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret-key")
    response = client.post("/investigations", json=VALID_REQUEST)
    assert response.status_code == 503
    assert response.json()["code"] == "investigation_unavailable"
    assert "synthetic-secret-key" not in response.text


def test_openapi_exposes_typed_request_response_and_error_schemas(client):
    schema = client.get("/openapi.json").json()
    post = schema["paths"]["/investigations"]["post"]
    assert post["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/InvestigationRequest")
    assert post["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/InvestigationResponse")
    assert post["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    assert schema["components"]["schemas"]["InvestigationRequest"]["additionalProperties"] is False
    assert set(schema["components"]["schemas"]["InvestigationRequest"]["properties"]) == {"patient_id", "microbiology_result_id"}
    assert "openai_api_key" not in str(schema).lower()


def test_route_uses_overridable_application_dependency(client, make_clinical_summary):
    from datetime import datetime, timezone
    record = InvestigationResponse(investigation_id=uuid4(), patient_id="PATIENT_001",
                                   microbiology_result_id="MICRO_001_RECENT", created_at=datetime.now(timezone.utc),
                                   summary=make_clinical_summary(), updated_at=datetime.now(timezone.utc),
                                   agent_run=AgentRunRecord(trace_id="trace_"+"a"*32, started_at=datetime.now(timezone.utc)))
    service = Mock(create=AsyncMock(return_value=record), get=Mock(return_value=record))
    client.app.dependency_overrides[get_investigation_service] = lambda: service
    response = client.post("/investigations", json=VALID_REQUEST)
    assert response.status_code == 201
    service.create.assert_awaited_once()
    assert service.create.await_args.args[0].model_dump() == VALID_REQUEST
    assert client.get(response.headers["location"]).status_code == 200
    service.get.assert_called_once_with(record.investigation_id)


def test_shutdown_flushes_standard_sdk_traces(sdk_traces, workflow_store):
    with TestClient(create_app(store=workflow_store)) as client:
        assert client.get("/health").status_code == 200
    assert sdk_traces.flush_count == 1


def test_api_runs_actual_sdk_and_clinical_tools(agent_model, sdk_traces, make_clinical_summary, workflow_store):
    from test_agent import final_response, tool_response
    expected = make_clinical_summary(tools=["get_microbiology_results"])
    agent_model.side_effect = [tool_response(["get_microbiology_results"]), final_response(expected)]
    with TestClient(create_app(store=workflow_store)) as client:
        response = client.post("/investigations", json=VALID_REQUEST)
        assert response.status_code == 201
        result = InvestigationResponse.model_validate(response.json())
        assert result.summary == expected
        assert ClinicalSummary.model_validate(response.json()["summary"]) == expected
        assert UUID(response.json()["investigation_id"]) == result.investigation_id
        assert client.get(response.headers["location"]).json() == response.json()
    assert agent_model.await_count == 2
    assert sdk_traces.traces
    assert "MICRO_001_RECENT" in str(agent_model.await_args_list[0].args[1])


def test_extra_field_names_are_not_reflected_as_validation_details(client):
    response = client.post("/investigations", json={**VALID_REQUEST, "secret-as-field-name": "secret-as-value"})
    assert response.status_code == 422
    assert response.json()["issues"][0]["location"] == ["body", "unknown_field"]
    assert "secret-as-" not in response.text


@pytest.mark.parametrize("malformed_response", [False, True])
def test_unexpected_errors_and_invalid_responses_are_sanitized(sdk_traces, malformed_response, workflow_store):
    service = Mock()
    if malformed_response:
        service.get.return_value = {"patient_id": "secret-api-key"}
    else:
        service.get.side_effect = RuntimeError("secret-api-key")
    app = create_app(store=workflow_store)
    app.dependency_overrides[get_investigation_service] = lambda: service
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/investigations/{uuid4()}")
        assert response.status_code == 500
        assert response.json()["code"] == "internal_error"
        assert "secret-api-key" not in response.text
        assert "Traceback" not in response.text
