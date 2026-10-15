import pytest
from pydantic import ValidationError

from infectious_disease_assistant.agent_demo import format_summary
from infectious_disease_assistant.models.clinical_summary import (
    CLINICAL_DISCLAIMER, ClinicalFinding, ClinicalSummary, EvidenceReference,
)
from infectious_disease_assistant.services import patients
from infectious_disease_assistant.services.summary_validation import SummaryEvidenceError, validate_summary_evidence


def retrieved_results(patient_id="PATIENT_001"):
    return {name: getattr(patients, name)(patient_id) for name in [
        "get_medications", "get_allergies", "get_recent_labs",
        "get_microbiology_results", "get_previous_infections",
    ]}


def test_summary_round_trip_preserves_clinical_values(make_clinical_summary):
    summary = make_clinical_summary()
    validated = ClinicalSummary.model_validate_json(summary.model_dump_json())

    assert validated == summary
    assert validated.organism == "Escherichia coli"
    assert validated.specimen_type == "urine"
    assert validated.relevant_medications[0].dose == "500 mg"
    assert validated.relevant_labs[0].value == 1.8
    assert validated.previous_infections[0].organism == "Escherichia coli"
    assert validate_summary_evidence(validated, "PATIENT_001", retrieved_results()) is validated


def test_missing_optional_information_has_clean_defaults():
    summary = ClinicalSummary(patient_id="PATIENT_005")

    assert summary.organism is None
    assert summary.specimen_type is None
    assert summary.microbiology_result_id is None
    assert summary.relevant_medications == []
    assert summary.relevant_allergies == []
    assert summary.relevant_labs == []
    assert summary.previous_infections == []
    assert summary.important_findings == []
    assert summary.missing_information == []
    assert summary.items_for_clinician_review == []
    assert summary.evidence_sources == []
    assert summary.disclaimer == CLINICAL_DISCLAIMER


@pytest.mark.parametrize("field, value", [
    ("patient_id", "REAL_001"), ("disclaimer", "Autonomous treatment advice"),
    ("relevant_medications", "cephalexin"), ("unexpected_field", "value"),
    ("evidence_sources", ["invented_tool"]), ("missing_information", ["  "]),
    ("important_findings", [{"text": "Uncited finding", "evidence": []}]),
    ("important_findings", [{"text": "Finding", "evidence": [{"tool_name": "get_recent_labs", "record_index": -1}]}]),
])
def test_invalid_structured_output(field, value):
    with pytest.raises(ValidationError):
        ClinicalSummary.model_validate({"patient_id": "PATIENT_001", field: value})


def test_wrong_patient_rejected(make_clinical_summary):
    with pytest.raises(SummaryEvidenceError, match="patient does not match"):
        validate_summary_evidence(make_clinical_summary(), "PATIENT_002", retrieved_results())


def test_summary_requires_microbiology_retrieval():
    with pytest.raises(SummaryEvidenceError, match="must be retrieved"):
        validate_summary_evidence(ClinicalSummary(patient_id="PATIENT_001"), "PATIENT_001", {})


@pytest.mark.parametrize("sources", [["get_microbiology_results"], ["get_microbiology_results"] * 2])
def test_sources_must_match_actual_calls(make_clinical_summary, sources):
    summary = make_clinical_summary()
    summary.evidence_sources = sources

    with pytest.raises(SummaryEvidenceError):
        validate_summary_evidence(summary, "PATIENT_001", retrieved_results())


@pytest.mark.parametrize("field, value", [
    ("organism", "Fabricated organism"), ("specimen_type", "blood"),
    ("microbiology_result_id", "MICRO_001_PRIOR"),
])
def test_microbiology_must_match_newest_retrieved_result(make_clinical_summary, field, value):
    summary = make_clinical_summary()
    setattr(summary, field, value)

    with pytest.raises(SummaryEvidenceError, match="newest retrieved result"):
        validate_summary_evidence(summary, "PATIENT_001", retrieved_results())


@pytest.mark.parametrize("field, attribute, value", [
    ("relevant_medications", "dose", "fabricated dose"),
    ("relevant_allergies", "reaction", "fabricated reaction"),
    ("relevant_labs", "value", 99.0),
    ("previous_infections", "organism", "fabricated organism"),
])
def test_changed_clinical_values_are_rejected(make_clinical_summary, field, attribute, value):
    summary = make_clinical_summary()
    setattr(getattr(summary, field)[0], attribute, value)

    with pytest.raises(SummaryEvidenceError, match="unsupported records"):
        validate_summary_evidence(summary, "PATIENT_001", retrieved_results())


def test_uncalled_tool_cannot_supply_summary_records(make_clinical_summary):
    summary = make_clinical_summary(tools=["get_microbiology_results"])
    summary.relevant_allergies = patients.get_allergies("PATIENT_001")

    with pytest.raises(SummaryEvidenceError, match="unsupported records"):
        validate_summary_evidence(summary, "PATIENT_001", {
            "get_microbiology_results": patients.get_microbiology_results("PATIENT_001"),
        })


def test_relevant_records_can_be_a_subset(make_clinical_summary):
    summary = make_clinical_summary()
    summary.relevant_medications = summary.relevant_medications[:1]
    summary.relevant_labs = []

    assert validate_summary_evidence(summary, "PATIENT_001", retrieved_results()) == summary


@pytest.mark.parametrize("tool_name, record_index", [("get_recent_labs", 99), ("get_medications", 0)])
def test_finding_cannot_cite_unretrieved_record(make_clinical_summary, tool_name, record_index):
    summary = make_clinical_summary(tools=["get_microbiology_results"])
    summary.important_findings = [ClinicalFinding(
        text="Unsupported finding.", evidence=[EvidenceReference(tool_name=tool_name, record_index=record_index)],
    )]

    with pytest.raises(SummaryEvidenceError, match="not retrieved"):
        validate_summary_evidence(summary, "PATIENT_001", {
            "get_microbiology_results": patients.get_microbiology_results("PATIENT_001"),
        })


def test_no_growth_and_unknown_allergy_details_are_preserved(make_clinical_summary):
    summary = make_clinical_summary("PATIENT_004")

    assert summary.organism is None
    assert summary.specimen_type == "blood"
    assert summary.relevant_allergies[0].reaction is None
    assert summary.relevant_allergies[0].severity is None
    assert validate_summary_evidence(summary, "PATIENT_004", retrieved_results("PATIENT_004")) == summary


def test_empty_cultures_cannot_supply_an_organism(make_clinical_summary):
    summary = make_clinical_summary("PATIENT_005", ["get_microbiology_results"])
    summary.organism = "Fabricated organism"

    with pytest.raises(SummaryEvidenceError, match="newest retrieved result"):
        validate_summary_evidence(summary, "PATIENT_005", {"get_microbiology_results": []})


def test_json_formatting_is_repeatable_and_preserves_dates(make_clinical_summary):
    summary = make_clinical_summary()
    formatted = format_summary(summary)

    assert formatted == format_summary(summary)
    assert formatted.startswith('{\n  "patient_id": "PATIENT_001",')
    assert '"collected_on": "2026-10-14"' in formatted
    assert CLINICAL_DISCLAIMER in formatted
    assert ClinicalSummary.model_validate_json(formatted) == summary
