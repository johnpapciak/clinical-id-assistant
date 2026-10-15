from datetime import date
from copy import deepcopy

import pytest
from pydantic import BaseModel

from infectious_disease_assistant.models.patient import (
    Allergy,
    LabResult,
    Medication,
    MicrobiologyResult,
    PatientSummary,
    PreviousInfection,
)
from infectious_disease_assistant.services.patients import (
    InvalidPatientIdError,
    PatientNotFoundError,
    get_allergies,
    get_medications,
    get_microbiology_results,
    get_patient_summary,
    get_previous_infections,
    get_recent_labs,
)

LIST_SERVICES = [
    (get_medications, "medications", Medication),
    (get_allergies, "allergies", Allergy),
    (get_recent_labs, "recent_labs", LabResult),
    (get_microbiology_results, "microbiology_results", MicrobiologyResult),
    (get_previous_infections, "previous_infections", PreviousInfection),
]
SERVICES = [get_patient_summary, *(item[0] for item in LIST_SERVICES)]


@pytest.mark.parametrize("patient_id", [f"PATIENT_{number:03}" for number in range(1, 6)])
def test_valid_patient_summary(patient_id):
    summary = get_patient_summary(patient_id)

    assert isinstance(summary, PatientSummary)
    assert summary.patient_id == patient_id
    assert summary.reference_date == date(2026, 10, 15)
    assert PatientSummary.model_validate_json(summary.model_dump_json()) == summary
    assert set(summary.model_dump()) == {
        "patient_id", "age", "reference_date", "medications", "allergies", "recent_labs",
        "microbiology_results", "previous_infections", "diagnoses", "encounters",
    }


@pytest.mark.parametrize("patient_id", [f"PATIENT_{number:03}" for number in range(1, 6)])
@pytest.mark.parametrize("service, field, model", LIST_SERVICES)
def test_service_returns_typed_summary_field(patient_id, service, field, model):
    results = service(patient_id)

    assert isinstance(results, list)
    assert all(isinstance(result, model) for result in results)
    assert results == getattr(get_patient_summary(patient_id), field)


@pytest.mark.parametrize("service", SERVICES)
def test_unknown_patient(service):
    with pytest.raises(PatientNotFoundError, match="Unknown synthetic patient: PATIENT_999"):
        service("PATIENT_999")


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("patient_id", ["patient_001", "PATIENT_01", "../patients.json"])
def test_invalid_patient_id(service, patient_id):
    with pytest.raises(InvalidPatientIdError, match="Patient ID must match"):
        service(patient_id)


@pytest.mark.parametrize("patient_id", [None, 1, b"PATIENT_001", "", " PATIENT_001", "PATIENT_001\n", "PATIENT_００１", "PATIENT_0001"])
def test_patient_id_validation_is_strict(patient_id):
    with pytest.raises(InvalidPatientIdError):
        get_patient_summary(patient_id)


def test_invalid_id_is_rejected_before_loading(monkeypatch, tmp_path):
    monkeypatch.setenv("IDA_DATA_DIR", str(tmp_path / "missing"))

    with pytest.raises(InvalidPatientIdError):
        get_patient_summary("not-a-patient")


def test_primary_patient_preserves_synthetic_values():
    summary = get_patient_summary("PATIENT_001")

    assert summary.age == 67
    assert [(item.name, item.dose, item.route, item.frequency) for item in summary.medications] == [
        ("cephalexin", "500 mg", "oral", "every 12 hours"),
        ("lisinopril", "10 mg", "oral", "daily"),
    ]
    assert summary.allergies[0].model_dump() == {
        "substance": "trimethoprim-sulfamethoxazole", "reaction": "urticaria", "severity": "moderate",
    }
    assert [(item.name, item.value, item.unit, item.flag) for item in summary.recent_labs] == [
        ("creatinine", 1.8, "mg/dL", "high"),
        ("white blood cell count", 12.4, "10^3/uL", "high"),
    ]
    recent, historical = summary.microbiology_results
    assert recent.result_id == "MICRO_001_RECENT"
    assert recent.specimen == "urine"
    assert recent.organism == historical.organism == "Escherichia coli"
    assert recent.collected_on == date(2026, 10, 13)
    assert historical.collected_on == date(2026, 6, 8)
    assert recent.quantity == ">100,000 CFU/mL"
    assert [(item.antibiotic, item.interpretation) for item in recent.susceptibilities] == [
        ("ampicillin", "resistant"), ("cefazolin", "susceptible"), ("nitrofurantoin", "susceptible"),
    ]
    infection = summary.previous_infections[0]
    assert infection.organism == "Escherichia coli"
    assert infection.diagnosed_on == date(2026, 6, 9)
    assert infection.treatment == "nitrofurantoin"
    assert [item.name for item in summary.diagnoses] == ["urinary tract infection", "hypertension"]
    assert summary.encounters[0].reason == "dysuria and urinary frequency"
    assert all(isinstance(item, BaseModel) for item in summary.diagnoses + summary.encounters)


def test_no_prior_infection_is_inferred_from_positive_culture():
    assert get_microbiology_results("PATIENT_002")[0].organism == "Escherichia coli"
    assert get_previous_infections("PATIENT_002") == []
    assert get_medications("PATIENT_002") == []
    assert get_allergies("PATIENT_002") == []


def test_antibiotic_allergy_and_current_prescription():
    allergy = get_allergies("PATIENT_003")[0]
    assert (allergy.substance, allergy.reaction, allergy.severity) == ("penicillin", "anaphylaxis", "severe")
    assert get_medications("PATIENT_003")[0].name == "doxycycline"


def test_no_growth_partial_allergy_and_stopped_medication():
    summary = get_patient_summary("PATIENT_004")
    result = summary.microbiology_results[0]

    assert result.outcome == "no_growth"
    assert result.organism is None
    assert result.susceptibilities == []
    assert summary.allergies[0].substance == "ciprofloxacin"
    assert summary.allergies[0].reaction is None
    assert summary.allergies[0].severity is None
    assert [item.name for item in summary.medications] == ["amlodipine"]
    assert summary.recent_labs[0].value == 2.3
    assert summary.previous_infections[0].treatment is None


def test_missing_optional_information():
    summary = get_patient_summary("PATIENT_005")

    assert summary.age is None
    assert summary.medications == []
    assert summary.allergies == []
    assert summary.recent_labs == []
    assert summary.microbiology_results == []
    assert summary.previous_infections == []
    assert summary.diagnoses == []
    assert summary.encounters[0].encounter_id == "ENCOUNTER_005"


def test_recent_lab_boundaries_and_newest_first(fhir_api):
    resources = fhir_api.store.resources
    template = resources["Observation/patient-001-lab-0"]
    for key in list(resources):
        if key.startswith("Observation/patient-001-lab-"):
            del resources[key]
    for index, (name, collected_on) in enumerate([
        ("too old", "2026-09-14"), ("boundary", "2026-09-15"),
        ("future", "2026-10-16"), ("today", "2026-10-15"),
    ]):
        resource = deepcopy(template)
        resource.update(id=f"boundary-lab-{index}", effectiveDateTime=collected_on)
        resource["code"]["text"] = name
        resources[f"Observation/{resource['id']}"] = resource

    assert [item.name for item in get_recent_labs("PATIENT_001")] == ["today", "boundary"]


def test_reference_date_filters_and_orders_history(fhir_api):
    resources = fhir_api.store.resources
    culture = deepcopy(resources["DiagnosticReport/micro-004-recent"])
    history = resources["Condition/patient-001-history-0"]
    diagnosis = resources["Condition/patient-001-diagnosis-0"]
    encounter = resources["Encounter/encounter-001"]
    for key, resource in list(resources.items()):
        if resource["resourceType"] in {"DiagnosticReport", "Condition", "Encounter"} and resource["subject"]["reference"] == "Patient/patient-001":
            del resources[key]
    culture["subject"]["reference"] = "Patient/patient-001"
    culture["specimen"] = [{"reference": "Specimen/micro-001-recent-specimen"}]
    for index, (name, day) in enumerate([("old", "2026-01-01"), ("future", "2026-10-16"), ("today", "2026-10-15")]):
        resource = deepcopy(culture)
        resource.update(id=f"order-report-{index}", effectiveDateTime=day)
        resource["identifier"][0]["value"] = name
        resources[f"DiagnosticReport/{resource['id']}"] = resource
    for index, (name, day) in enumerate([
        ("old", "2026-01-01"), ("future", "2026-10-16"),
        ("today", "2026-10-15"), ("yesterday", "2026-10-14"),
    ]):
        resource = deepcopy(history)
        resource.update(id=f"order-history-{index}", recordedDate=day, onsetDateTime=day)
        resource["code"]["text"] = name
        resources[f"Condition/{resource['id']}"] = resource
    for index, (name, day) in enumerate([("future", "2026-10-16"), ("current", "2026-10-15")]):
        resource = deepcopy(diagnosis)
        resource.update(id=f"order-diagnosis-{index}", recordedDate=day)
        resource["code"]["text"] = name + " diagnosis"
        resources[f"Condition/{resource['id']}"] = resource
        resource = deepcopy(encounter)
        resource["id"] = f"order-encounter-{index}"
        resource["identifier"][0]["value"] = "today" if name == "current" else name
        resource["period"]["start"] = day
        resources[f"Encounter/{resource['id']}"] = resource
    summary = get_patient_summary("PATIENT_001")

    assert [item.result_id for item in summary.microbiology_results] == ["today", "old"]
    assert [item.diagnosis for item in summary.previous_infections] == ["yesterday", "old"]
    assert [item.name for item in summary.diagnoses] == ["current diagnosis"]
    assert [item.encounter_id for item in summary.encounters] == ["today"]


def test_results_are_repeatable_and_mutations_do_not_leak():
    original = get_patient_summary("PATIENT_001")
    changed = get_patient_summary("PATIENT_001")
    assert changed == original

    changed.medications[0].name = "changed"
    changed.microbiology_results[0].susceptibilities.clear()
    changed.allergies.clear()

    assert get_patient_summary("PATIENT_001") == original
