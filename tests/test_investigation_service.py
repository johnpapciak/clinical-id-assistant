import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from agents.exceptions import ModelBehaviorError, UserError
from openai import OpenAIError
from pydantic import ValidationError

from infectious_disease_assistant.fhir.errors import FhirTransportError
from infectious_disease_assistant.models.investigation import InvestigationRequest
from infectious_disease_assistant.rag.errors import GuidelineDatabaseError
from infectious_disease_assistant.services import investigations
from infectious_disease_assistant.services.investigations import (
    InvestigationError, InvestigationNotFoundError, InvestigationService,
    InvestigationSummaryError, InvestigationUnavailableError, MicrobiologyResultNotFoundError,
)
from infectious_disease_assistant.services.patients import PatientNotFoundError


@pytest.fixture
def investigation_request():
    return InvestigationRequest(patient_id="PATIENT_001", microbiology_result_id="MICRO_001_RECENT")


@pytest.fixture
def coordinator(monkeypatch, make_clinical_summary):
    mock = AsyncMock(return_value=make_clinical_summary(tools=["get_microbiology_results"]))
    monkeypatch.setattr(investigations, "investigate_patient", mock)
    return mock


def test_create_then_get_without_http(investigation_request, coordinator):
    service = InvestigationService()
    result = asyncio.run(service.create(investigation_request))
    assert result.status == "completed"
    assert result.created_at.utcoffset().total_seconds() == 0
    assert result.summary.patient_id == result.patient_id == "PATIENT_001"
    assert result.summary.microbiology_result_id == result.microbiology_result_id == "MICRO_001_RECENT"
    assert service.get(result.investigation_id) == result
    coordinator.assert_awaited_once_with("PATIENT_001", microbiology_result_id="MICRO_001_RECENT")


def test_history_returns_independent_copies(investigation_request, coordinator):
    service = InvestigationService()
    created = asyncio.run(service.create(investigation_request))
    created.summary.organism = "Mutated outside the service"
    first = service.get(created.investigation_id)
    assert first.summary.organism == "Escherichia coli"
    first.summary.organism = None
    assert service.get(created.investigation_id).summary.organism == "Escherichia coli"


def test_bounded_history_evicts_oldest_completed_investigation(investigation_request, coordinator):
    service = InvestigationService(history_limit=1)
    first = asyncio.run(service.create(investigation_request))
    second = asyncio.run(service.create(investigation_request))
    with pytest.raises(InvestigationNotFoundError):
        service.get(first.investigation_id)
    assert service.get(second.investigation_id) == second
    assert first.investigation_id != second.investigation_id


def test_history_is_per_service_instance(investigation_request, coordinator):
    result = asyncio.run(InvestigationService().create(investigation_request))
    with pytest.raises(InvestigationNotFoundError):
        InvestigationService().get(result.investigation_id)


def test_unknown_investigation():
    with pytest.raises(InvestigationNotFoundError):
        InvestigationService().get(uuid4())


def test_nonpositive_history_limit_rejected():
    with pytest.raises(ValueError):
        InvestigationService(history_limit=0)


@pytest.mark.parametrize("patient, result, error", [
    ("PATIENT_999", "RESULT_001", PatientNotFoundError),
    ("PATIENT_001", "RESULT_999", MicrobiologyResultNotFoundError),
    ("PATIENT_002", "MICRO_001_RECENT", MicrobiologyResultNotFoundError),
    ("PATIENT_005", "RESULT_001", MicrobiologyResultNotFoundError),
])
def test_patient_and_event_validated_before_agent(patient, result, error, coordinator):
    request = InvestigationRequest(patient_id=patient, microbiology_result_id=result)
    with pytest.raises(error):
        asyncio.run(InvestigationService().create(request))
    coordinator.assert_not_awaited()


def test_historical_result_is_passed_to_coordinator(coordinator, make_clinical_summary):
    from infectious_disease_assistant.services.patients import get_microbiology_results
    prior = get_microbiology_results("PATIENT_001")[1]
    summary = make_clinical_summary(tools=["get_microbiology_results"])
    summary.microbiology_result_id, summary.organism, summary.specimen_type = prior.result_id, prior.organism, prior.specimen
    coordinator.return_value = summary
    request = InvestigationRequest(patient_id="PATIENT_001", microbiology_result_id=prior.result_id)
    result = asyncio.run(InvestigationService().create(request))
    assert result.summary.microbiology_result_id == "MICRO_001_PRIOR"
    coordinator.assert_awaited_once_with("PATIENT_001", microbiology_result_id="MICRO_001_PRIOR")


@pytest.mark.parametrize("field, value", [("patient_id", "PATIENT_002"), ("microbiology_result_id", "MICRO_001_PRIOR"),
                                         ("organism", "Invented"), ("specimen_type", "blood")])
def test_summary_must_match_request_and_fhir_event(investigation_request, coordinator, field, value):
    setattr(coordinator.return_value, field, value)
    service = InvestigationService()
    with pytest.raises(InvestigationSummaryError):
        asyncio.run(service.create(investigation_request))
    assert not service._completed


@pytest.mark.parametrize("value", [None, "summary", {}])
def test_non_summary_results_rejected(investigation_request, coordinator, value):
    coordinator.return_value = value
    with pytest.raises(InvestigationSummaryError):
        asyncio.run(InvestigationService().create(investigation_request))


@pytest.mark.parametrize("error, expected", [
    (FhirTransportError("secret-test-key"), InvestigationUnavailableError),
    (GuidelineDatabaseError("secret-test-key"), InvestigationUnavailableError),
    (OpenAIError("secret-test-key"), InvestigationUnavailableError),
    (ValidationError.from_exception_data("config", []), InvestigationUnavailableError),
    (ModelBehaviorError("secret-test-key"), InvestigationSummaryError),
    (RuntimeError("secret-test-key"), InvestigationError),
])
def test_failures_are_sanitized_and_not_saved(investigation_request, coordinator, error, expected, caplog):
    coordinator.side_effect = error
    service = InvestigationService()
    with pytest.raises(expected) as caught:
        asyncio.run(service.create(investigation_request))
    assert "secret-test-key" not in str(caught.value)
    assert "secret-test-key" not in caplog.text
    assert not service._completed


def test_tool_dependency_failure_is_classified_as_unavailable(investigation_request, coordinator):
    error = UserError("secret-test-key")
    error.__cause__ = GuidelineDatabaseError("secret-database-password")
    coordinator.side_effect = error
    with pytest.raises(InvestigationUnavailableError):
        asyncio.run(InvestigationService().create(investigation_request))


def test_fhir_preflight_failure_never_invokes_agent(investigation_request, coordinator, monkeypatch):
    monkeypatch.setattr(investigations.patients, "get_microbiology_results", Mock(side_effect=FhirTransportError("secret URL")))
    with pytest.raises(InvestigationUnavailableError):
        asyncio.run(InvestigationService().create(investigation_request))
    coordinator.assert_not_awaited()
