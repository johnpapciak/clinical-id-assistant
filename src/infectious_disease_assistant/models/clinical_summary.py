from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from infectious_disease_assistant.rag.models import GuidelineCitation
from infectious_disease_assistant.models.workflow import FollowupAction
from infectious_disease_assistant.models.patient import (
    Allergy, HealthcareModel, LabResult, Medication, PatientId, PreviousInfection,
)

CLINICAL_DISCLAIMER = (
    "Clinician decision support for human review only; "
    "not diagnosis or autonomous treatment advice."
)
ToolName = Literal[
    "get_medications", "get_allergies", "get_recent_labs",
    "get_microbiology_results", "get_previous_infections", "search_guidelines",
]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class EvidenceReference(HealthcareModel):
    tool_name: ToolName
    record_index: int = Field(ge=0)


class ClinicalFinding(HealthcareModel):
    text: NonEmptyText
    evidence: list[EvidenceReference] = Field(min_length=1)


class ClinicalSummary(HealthcareModel):
    patient_id: PatientId
    microbiology_result_id: str | None = None
    organism: str | None = None
    specimen_type: str | None = None
    relevant_medications: list[Medication] = Field(default_factory=list)
    relevant_allergies: list[Allergy] = Field(default_factory=list)
    relevant_labs: list[LabResult] = Field(default_factory=list)
    previous_infections: list[PreviousInfection] = Field(default_factory=list)
    important_findings: list[ClinicalFinding] = Field(default_factory=list)
    missing_information: list[NonEmptyText] = Field(default_factory=list)
    items_for_clinician_review: list[NonEmptyText] = Field(default_factory=list)
    evidence_sources: list[ToolName] = Field(default_factory=list)
    guideline_sources: list[GuidelineCitation] = Field(default_factory=list)
    proposed_action: FollowupAction | None = None
    disclaimer: Literal[CLINICAL_DISCLAIMER] = CLINICAL_DISCLAIMER
