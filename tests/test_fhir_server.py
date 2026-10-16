from infectious_disease_assistant.fhir.server import handle_request
from infectious_disease_assistant.fhir.constants import PATIENT_IDENTIFIER_SYSTEM


def test_metadata_advertises_r4_and_supported_resources(fhir_api):
    status, metadata = handle_request(fhir_api.store, "GET", "/fhir/metadata", {})
    assert status == 200
    assert metadata["resourceType"] == "CapabilityStatement"
    assert metadata["fhirVersion"] == "4.0.1"
    assert {item["type"] for item in metadata["rest"][0]["resource"]} >= {
        "Patient", "MedicationRequest", "AllergyIntolerance", "Observation", "Condition", "Encounter", "DiagnosticReport",
    }


def test_search_by_patient_business_identifier(fhir_api):
    status, bundle = handle_request(fhir_api.store, "GET", "/fhir/Patient", {
        "identifier": [f"{PATIENT_IDENTIFIER_SYSTEM}|PATIENT_001"],
    })
    assert status == 200
    assert bundle["type"] == "searchset"
    assert bundle["total"] == 1
    assert bundle["entry"][0]["resource"]["id"] == "patient-001"


def test_missing_read_returns_operation_outcome(fhir_api):
    status, payload = handle_request(fhir_api.store, "GET", "/fhir/Patient/missing", {})
    assert status == 404
    assert payload["resourceType"] == "OperationOutcome"


def test_write_methods_are_rejected(fhir_api):
    for method in ["POST", "PUT", "PATCH", "DELETE"]:
        status, payload = handle_request(fhir_api.store, method, "/fhir/Patient", {})
        assert status == 405
        assert payload["issue"][0]["code"] == "not-supported"


def test_unsupported_search_parameters_are_rejected(fhir_api):
    status, payload = handle_request(fhir_api.store, "GET", "/fhir/Patient", {"name": ["someone"]})
    assert status == 400
    assert payload["resourceType"] == "OperationOutcome"


def test_invalid_date_filter_is_rejected(fhir_api):
    status, _ = handle_request(fhir_api.store, "GET", "/fhir/Observation", {"date": ["bad-date"]})
    assert status == 400
