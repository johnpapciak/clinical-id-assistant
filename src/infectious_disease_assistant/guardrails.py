from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from infectious_disease_assistant.models.clinical_summary import ClinicalFinding, ClinicalSummary, ToolName
from infectious_disease_assistant.models.summary_draft import SummaryDraft
from infectious_disease_assistant.models.workflow import FollowupAction
from infectious_disease_assistant.rag.models import GuidelineMatch
from infectious_disease_assistant.services.summary_validation import SummaryEvidenceError, validate_summary_evidence

FINDING_TEXT: dict[ToolName, str] = {
    "get_microbiology_results": "A recorded microbiology result is available.",
    "get_medications": "A recorded prescription is available for clinician review.",
    "get_allergies": "A recorded medication allergy is available for clinician review.",
    "get_recent_labs": "A recorded laboratory result is available for clinician review.",
    "get_previous_infections": "An explicitly recorded previous infection is available for clinician review.",
    "search_guidelines": "Synthetic guideline context is cited for clinician review.",
}
REVIEW_TEXT = {
    "symptom_context": "Can the clinician confirm symptoms and specimen collection context?",
    "medication_context": "Can the clinician review the recorded prescription and allergy context?",
    "renal_context": "Can the clinician review the recorded laboratory values and baseline renal context?",
    "guideline_context": "Can the clinician assess whether the cited synthetic guideline context is relevant?",
}
SAFE_FOLLOWUP = FollowupAction(
    title="Review synthetic microbiology result",
    description="A clinician should review the recorded result, supporting evidence, and missing context.",
)
SECTIONS: dict[ToolName, str] = {
    "get_medications": "Medication", "get_allergies": "Allergy",
    "get_recent_labs": "Laboratory", "get_previous_infections": "Previous infection",
}


class SafetyViolation(SummaryEvidenceError):
    def __init__(self, category: str):
        self.category = category
        super().__init__(f"Clinical safety validation rejected the summary ({category})")


def missing_information(tool_results: Mapping[ToolName, Sequence[BaseModel]]) -> list[str]:
    if not tool_results.get("get_microbiology_results"):
        return ["No microbiology results recorded."]
    missing = []
    for tool, label in SECTIONS.items():
        if tool not in tool_results:
            missing.append(f"{label} records were not reviewed.")
        elif not tool_results[tool]:
            missing.append(f"No {label.lower()} entries were recorded in the retrieved data.")
    return missing


def validate_safe_presentation(summary: ClinicalSummary) -> None:
    for finding in summary.important_findings:
        if len(finding.evidence) != 1 or finding.text != FINDING_TEXT[finding.evidence[0].tool_name]:
            raise SafetyViolation("unsafe_narrative")
    if any(item not in REVIEW_TEXT.values() for item in summary.items_for_clinician_review):
        raise SafetyViolation("unsafe_review_instruction")
    if summary.proposed_action is not None and summary.proposed_action != SAFE_FOLLOWUP:
        raise SafetyViolation("unsafe_action_proposal")
    allowed_missing = {"No microbiology results recorded."} | {
        text for label in SECTIONS.values() for text in (
            f"{label} records were not reviewed.",
            f"No {label.lower()} entries were recorded in the retrieved data.",
        )
    }
    if any(item not in allowed_missing for item in summary.missing_information):
        raise SafetyViolation("unsupported_missing_context")


def validate_safe_summary(
    summary: ClinicalSummary, patient_id: str, tool_results: Mapping[ToolName, Sequence[BaseModel]],
    *, microbiology_result_id: str | None = None,
) -> ClinicalSummary:
    try:
        validate_summary_evidence(summary, patient_id, tool_results, microbiology_result_id=microbiology_result_id)
    except SummaryEvidenceError:
        raise SafetyViolation("unsupported_fact_or_citation") from None
    validate_safe_presentation(summary)
    if summary.missing_information != missing_information(tool_results):
        raise SafetyViolation("unsupported_missing_context")
    return summary


def render_summary(
    draft: SummaryDraft, patient_id: str, tool_results: Mapping[ToolName, Sequence[BaseModel]],
    *, microbiology_result_id: str | None = None,
) -> ClinicalSummary:
    findings, citations = [], []
    seen = set()
    for reference in draft.findings:
        records = tool_results.get(reference.tool_name, ())
        if reference.record_index >= len(records):
            raise SafetyViolation("unsupported_fact_or_citation")
        findings.append(ClinicalFinding(text=FINDING_TEXT[reference.tool_name], evidence=[reference]))
        record = records[reference.record_index]
        if reference.tool_name == "search_guidelines":
            if not isinstance(record, GuidelineMatch):
                raise SafetyViolation("unsupported_fact_or_citation")
            if record.chunk_id not in seen:
                citations.append(record.citation())
                seen.add(record.chunk_id)
    summary = ClinicalSummary(
        **draft.model_dump(exclude={"findings", "review_topics", "propose_followup"}),
        important_findings=findings, evidence_sources=list(tool_results), guideline_sources=citations,
        missing_information=missing_information(tool_results),
        items_for_clinician_review=[REVIEW_TEXT[topic] for topic in dict.fromkeys(draft.review_topics)],
        proposed_action=SAFE_FOLLOWUP.model_copy(deep=True) if draft.propose_followup else None,
    )
    return validate_safe_summary(summary, patient_id, tool_results, microbiology_result_id=microbiology_result_id)
