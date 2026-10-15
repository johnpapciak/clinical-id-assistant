from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError

from infectious_disease_assistant.config import Settings
from infectious_disease_assistant.fhir.client import FhirClient
from infectious_disease_assistant.fhir.constants import PATIENT_IDENTIFIER_SYSTEM
from infectious_disease_assistant.fhir.errors import FhirResourceNotFoundError, FhirResponseError, FhirTransportError
from infectious_disease_assistant.fhir.server import capability_statement


def test_read_and_search_use_fhir_http_paths(fhir_api):
    with FhirClient() as client:
        patient = client.read("Patient", "patient-001")
        resources = client.search("Patient", {"identifier": f"{PATIENT_IDENTIFIER_SYSTEM}|PATIENT_001"})

    assert patient == resources[0]
    assert [request.url.path for request in fhir_api.requests] == [
        "/fhir/metadata", "/fhir/Patient/patient-001", "/fhir/Patient",
    ]
    assert all(request.method == "GET" for request in fhir_api.requests)
    assert fhir_api.requests[-1].url.params["identifier"] == f"{PATIENT_IDENTIFIER_SYSTEM}|PATIENT_001"
    assert client.http.is_closed


def test_empty_search_bundle_is_not_an_error():
    with FhirClient() as client:
        assert client.search("Patient", {"identifier": f"{PATIENT_IDENTIFIER_SYSTEM}|PATIENT_999"}) == []


def test_missing_resource_has_distinct_error():
    with FhirClient() as client, pytest.raises(FhirResourceNotFoundError):
        client.read("Patient", "patient-999")


@pytest.mark.parametrize("payload", [
    [], {}, {"resourceType": "OperationOutcome"},
    {"resourceType": "Bundle", "type": "collection"},
    {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": {"resourceType": "Observation"}}]},
    {"resourceType": "Bundle", "type": "searchset", "total": 1},
    {"resourceType": "Bundle", "type": "searchset", "entry": None},
])
def test_malformed_or_unexpected_search_response(fhir_api, payload):
    fhir_api.responses["/fhir/Patient"] = httpx.Response(200, json=payload)
    with FhirClient() as client, pytest.raises(FhirResponseError):
        client.search("Patient", {})


def test_non_json_response(fhir_api):
    fhir_api.responses["/fhir/Patient"] = httpx.Response(200, text="<html>unexpected</html>")
    with FhirClient() as client, pytest.raises(FhirResponseError, match="invalid JSON"):
        client.search("Patient", {})


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_http_failures_are_not_empty_results(fhir_api, status):
    fhir_api.responses["/fhir/Patient"] = httpx.Response(status, json={"resourceType": "OperationOutcome"},
                                                       headers={"Location": "https://example.com/real-ehr"})
    with FhirClient() as client, pytest.raises(FhirResponseError, match=f"HTTP {status}"):
        client.search("Patient", {})
    assert all(request.url.host == "127.0.0.1" for request in fhir_api.requests)


def test_transport_failure_has_safe_predictable_message(fhir_api):
    def timeout(request):
        raise httpx.ReadTimeout("private upstream details", request=request)

    fhir_api.responses["/fhir/metadata"] = timeout
    with FhirClient() as client, pytest.raises(FhirTransportError, match="endpoint unavailable") as caught:
        client.search("Patient", {})
    assert "private upstream details" not in str(caught.value)


def test_pagination_is_rejected_without_following_external_links(fhir_api):
    fhir_api.responses["/fhir/Patient"] = httpx.Response(200, json={
        "resourceType": "Bundle", "type": "searchset",
        "link": [{"relation": "next", "url": "https://example.com/real-ehr"}],
    })
    with FhirClient() as client, pytest.raises(FhirResponseError, match="Pagination"):
        client.search("Patient", {})
    assert len(fhir_api.requests) == 2


@pytest.mark.parametrize("field, value", [("resourceType", "Observation"), ("id", "patient-002")])
def test_read_identity_is_checked(fhir_api, field, value):
    payload = deepcopy(fhir_api.store.resources["Patient/patient-001"])
    payload[field] = value
    fhir_api.responses["/fhir/Patient/patient-001"] = httpx.Response(200, json=payload)
    with FhirClient() as client, pytest.raises(FhirResponseError, match="mismatched"):
        client.read("Patient", "patient-001")


@pytest.mark.parametrize("reference", [
    "https://example.com/Patient/patient-001", "//example.com/Patient/patient-001",
    "Patient/../metadata", "Patient/..", "Patient/.",
    "Patient/patient-001?secret=value", "Observation/lab-1",
])
def test_only_local_typed_references_are_allowed(fhir_api, reference):
    with FhirClient() as client, pytest.raises(FhirResponseError):
        client.read_reference(reference, "Patient")
    assert fhir_api.requests == []


@pytest.mark.parametrize("change", ["missing-marker", "false-marker", "wrong-version", "missing-date"])
def test_endpoint_must_declare_supported_synthetic_data(fhir_api, change):
    metadata = capability_statement(fhir_api.store)
    if change == "missing-marker":
        metadata["extension"] = metadata["extension"][1:]
    elif change == "false-marker":
        metadata["extension"][0]["valueBoolean"] = False
    elif change == "wrong-version":
        metadata["fhirVersion"] = "5.0.0"
    else:
        metadata["extension"] = metadata["extension"][:1]
    fhir_api.responses["/fhir/metadata"] = httpx.Response(200, json=metadata)
    with FhirClient() as client, pytest.raises(FhirResponseError):
        client.search("Patient", {})
    assert [request.url.path for request in fhir_api.requests] == ["/fhir/metadata"]


@pytest.mark.parametrize("url", [
    "https://example.com/fhir", "http://example.com/fhir", "https://localhost/fhir",
    "http://user:password@localhost/fhir", "http://127.0.0.1:bad/fhir",
    "http://localhost/fhir?token=value", "http://localhost/fhir#fragment",
    "http://localhost/other-api",
])
def test_default_client_configuration_stays_local(monkeypatch, url):
    monkeypatch.setenv("IDA_FHIR_BASE_URL", url)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
