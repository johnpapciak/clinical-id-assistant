from copy import deepcopy

import httpx
import pytest

from infectious_disease_assistant.fhir.errors import FhirParseError, FhirResourceNotFoundError, FhirResponseError
from infectious_disease_assistant.services.patients import (
    PatientNotFoundError, get_allergies, get_medications, get_microbiology_results,
    get_patient_context, get_previous_infections, get_recent_labs,
)


def bundle(resource):
    return {"resourceType": "Bundle", "type": "searchset", "total": 1, "entry": [{"resource": resource}]}


def test_medication_call_only_requests_patient_and_medications(fhir_api):
    medications = get_medications("PATIENT_001")

    assert [medication.name for medication in medications] == ["cephalexin", "lisinopril"]
    assert [request.url.path for request in fhir_api.requests] == [
        "/fhir/metadata", "/fhir/Patient", "/fhir/MedicationRequest",
    ]
    query = fhir_api.requests[-1].url.params
    assert query["patient"] == "Patient/patient-001"
    assert query["status"] == "active"


def test_allergy_retrieval_preserves_recorded_reaction(fhir_api):
    allergy = get_allergies("PATIENT_003")[0]
    assert (allergy.substance, allergy.reaction, allergy.severity) == ("penicillin", "anaphylaxis", "severe")
    assert fhir_api.requests[-1].url.path == "/fhir/AllergyIntolerance"


def test_lab_retrieval_uses_date_and_category_filters(fhir_api):
    labs = get_recent_labs("PATIENT_001")
    assert [(lab.value, lab.unit) for lab in labs] == [(1.8, "mg/dL"), (12.4, "10^3/uL")]
    assert fhir_api.requests[-1].url.path == "/fhir/Observation"
    assert fhir_api.requests[-1].url.params["date"] == "ge2026-09-15"
    assert fhir_api.requests[-1].url.params["category"].endswith("|routine-lab")


def test_microbiology_resolves_specimen_and_observations(fhir_api):
    recent, previous = get_microbiology_results("PATIENT_001")
    assert recent.result_id == "MICRO_001_RECENT"
    assert recent.specimen == "urine"
    assert recent.organism == previous.organism == "Escherichia coli"
    assert recent.quantity == ">100,000 CFU/mL"
    assert recent.susceptibilities[0].interpretation == "resistant"
    paths = {request.url.path for request in fhir_api.requests}
    assert "/fhir/DiagnosticReport" in paths
    assert "/fhir/Specimen/micro-001-recent-specimen" in paths
    assert "/fhir/Observation/micro-001-recent-organism" in paths
    assert "/fhir/Observation/micro-001-recent-susceptibility-0" in paths
    assert "/fhir/MedicationRequest" not in paths


@pytest.mark.parametrize("service", [get_medications, get_allergies, get_recent_labs, get_microbiology_results, get_previous_infections])
def test_empty_searches_return_empty_lists(fhir_api, service):
    assert service("PATIENT_005") == []


def test_no_growth_has_no_invented_organism():
    result = get_microbiology_results("PATIENT_004")[0]
    assert result.outcome == "no_growth"
    assert result.organism is None
    assert result.susceptibilities == []


def test_preflight_retrieves_only_context(fhir_api):
    context = get_patient_context("PATIENT_001")
    assert context.age == 67
    assert context.reference_date.isoformat() == "2026-10-15"
    assert [request.url.path for request in fhir_api.requests] == ["/fhir/metadata", "/fhir/Patient"]


def test_patient_not_found_is_distinct_from_empty_results(fhir_api):
    with pytest.raises(PatientNotFoundError):
        get_medications("PATIENT_999")
    assert len(fhir_api.requests) == 2


@pytest.mark.parametrize("field", ["medicationCodeableConcept", "dosageInstruction"])
def test_incomplete_medication_response_is_rejected(fhir_api, field):
    medication = deepcopy(fhir_api.store.resources["MedicationRequest/patient-001-med-0"])
    del medication[field]
    fhir_api.responses["/fhir/MedicationRequest"] = httpx.Response(200, json=bundle(medication))
    with pytest.raises(FhirParseError):
        get_medications("PATIENT_001")


@pytest.mark.parametrize("field, value", [
    ("valueQuantity", None), ("effectiveDateTime", "not-a-date"), ("status", "invented-status"),
])
def test_malformed_labs_do_not_become_empty_results(fhir_api, field, value):
    lab = deepcopy(fhir_api.store.resources["Observation/patient-001-lab-0"])
    lab[field] = value
    fhir_api.responses["/fhir/Observation"] = httpx.Response(200, json=bundle(lab))
    with pytest.raises(FhirParseError):
        get_recent_labs("PATIENT_001")


def test_cross_patient_resources_are_rejected(fhir_api):
    medication = deepcopy(fhir_api.store.resources["MedicationRequest/patient-001-med-0"])
    medication["subject"]["reference"] = "Patient/patient-002"
    fhir_api.responses["/fhir/MedicationRequest"] = httpx.Response(200, json=bundle(medication))
    with pytest.raises(FhirParseError, match="different patient"):
        get_medications("PATIENT_001")


def test_cross_patient_references_are_rejected(fhir_api):
    specimen = deepcopy(fhir_api.store.resources["Specimen/micro-001-recent-specimen"])
    specimen["subject"]["reference"] = "Patient/patient-002"
    fhir_api.responses["/fhir/Specimen/micro-001-recent-specimen"] = httpx.Response(200, json=specimen)
    with pytest.raises(FhirParseError, match="different patient"):
        get_microbiology_results("PATIENT_001")


def test_missing_referenced_specimen_is_an_error(fhir_api):
    del fhir_api.store.resources["Specimen/micro-001-recent-specimen"]
    with pytest.raises(FhirResourceNotFoundError):
        get_microbiology_results("PATIENT_001")


def test_external_result_references_are_never_followed(fhir_api):
    report = fhir_api.store.resources["DiagnosticReport/micro-001-recent"]
    report["result"][0]["reference"] = "https://example.com/Observation/real"
    with pytest.raises(FhirResponseError, match="local typed FHIR references"):
        get_microbiology_results("PATIENT_001")
    assert all(request.url.host == "127.0.0.1" for request in fhir_api.requests)


def test_culture_outcome_cannot_disagree_with_observations(fhir_api):
    report = fhir_api.store.resources["DiagnosticReport/micro-001-recent"]
    report["conclusionCode"][0]["coding"][0]["code"] = "no_growth"
    with pytest.raises(FhirParseError, match="disagree"):
        get_microbiology_results("PATIENT_001")


def test_unsupported_susceptibility_interpretation_is_rejected(fhir_api):
    observation = fhir_api.store.resources["Observation/micro-001-recent-susceptibility-0"]
    observation["valueCodeableConcept"]["coding"][0]["code"] = "unsupported"
    with pytest.raises(FhirParseError, match="susceptibility"):
        get_microbiology_results("PATIENT_001")


def test_history_requires_explicit_organism(fhir_api):
    fhir_api.store.resources["Condition/patient-001-history-0"]["extension"] = []
    with pytest.raises(FhirParseError, match="recorded organism"):
        get_previous_infections("PATIENT_001")


def test_multiple_matching_patients_are_rejected(fhir_api):
    duplicate = deepcopy(fhir_api.store.resources["Patient/patient-001"])
    duplicate["id"] = "duplicate-patient"
    fhir_api.store.resources["Patient/duplicate-patient"] = duplicate
    with pytest.raises(FhirParseError, match="multiple resources"):
        get_medications("PATIENT_001")
