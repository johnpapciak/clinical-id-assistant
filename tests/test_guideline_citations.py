import pytest

from infectious_disease_assistant.models.clinical_summary import ClinicalFinding, EvidenceReference
from infectious_disease_assistant.services.patients import get_microbiology_results
from infectious_disease_assistant.services.summary_validation import SummaryEvidenceError, validate_summary_evidence


def summary_and_records(make_clinical_summary, match):
    summary = make_clinical_summary(tools=["get_microbiology_results", "search_guidelines"])
    summary.important_findings.append(ClinicalFinding(
        text="Synthetic guideline context asks the reviewer to identify missing renal baseline information.",
        evidence=[EvidenceReference(tool_name="search_guidelines", record_index=0)],
    ))
    summary.guideline_sources = [match.citation()]
    records = {"get_microbiology_results": get_microbiology_results("PATIENT_001"), "search_guidelines": [match]}
    return summary, records


def test_retrieved_guideline_citation_round_trips(make_clinical_summary, guideline_match):
    summary, records = summary_and_records(make_clinical_summary, guideline_match)
    assert validate_summary_evidence(summary, "PATIENT_001", records) == summary
    restored = type(summary).model_validate_json(summary.model_dump_json())
    assert restored.guideline_sources == [guideline_match.citation()]
    assert restored.guideline_sources[0].section == "Creatinine review"


@pytest.mark.parametrize("field, value", [("source", "Invented source"), ("section", "Invented section"),
                                         ("chunk_id", "d" * 64), ("license", "Invented license"),
                                         ("content_sha256", "e" * 64)])
def test_changed_source_metadata_is_rejected(make_clinical_summary, guideline_match, field, value):
    summary, records = summary_and_records(make_clinical_summary, guideline_match)
    summary.guideline_sources = [summary.guideline_sources[0].model_copy(update={field: value})]
    with pytest.raises(SummaryEvidenceError, match="Guideline citations"):
        validate_summary_evidence(summary, "PATIENT_001", records)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unused", "uncalled", "out_of_range"])
def test_guideline_sources_must_be_retrieved_and_used(make_clinical_summary, guideline_match, change):
    summary, records = summary_and_records(make_clinical_summary, guideline_match)
    if change == "missing":
        summary.guideline_sources = []
    elif change == "duplicate":
        summary.guideline_sources *= 2
    elif change == "unused":
        summary.important_findings = summary.important_findings[:1]
    elif change == "uncalled":
        records.pop("search_guidelines")
        summary.evidence_sources.remove("search_guidelines")
    else:
        summary.important_findings[-1].evidence[0].record_index = 1
    with pytest.raises(SummaryEvidenceError):
        validate_summary_evidence(summary, "PATIENT_001", records)


def test_retrieved_but_unused_guidelines_need_no_citation(make_clinical_summary, guideline_match):
    summary, records = summary_and_records(make_clinical_summary, guideline_match)
    summary.important_findings = summary.important_findings[:1]
    summary.guideline_sources = []
    assert validate_summary_evidence(summary, "PATIENT_001", records) == summary


def test_empty_search_has_clean_sources(make_clinical_summary):
    summary = make_clinical_summary(tools=["get_microbiology_results", "search_guidelines"])
    records = {"get_microbiology_results": get_microbiology_results("PATIENT_001"), "search_guidelines": []}
    assert summary.guideline_sources == []
    assert validate_summary_evidence(summary, "PATIENT_001", records) == summary
