from typing import Literal

from pydantic import Field

from infectious_disease_assistant.models.clinical_summary import EvidenceReference
from infectious_disease_assistant.models.patient import Allergy, HealthcareModel, LabResult, Medication, PatientId, PreviousInfection

ReviewTopic = Literal["symptom_context", "medication_context", "renal_context", "guideline_context"]


class SummaryDraft(HealthcareModel):
    patient_id: PatientId
    microbiology_result_id: str | None = None
    organism: str | None = None
    specimen_type: str | None = None
    relevant_medications: list[Medication] = Field(default_factory=list)
    relevant_allergies: list[Allergy] = Field(default_factory=list)
    relevant_labs: list[LabResult] = Field(default_factory=list)
    previous_infections: list[PreviousInfection] = Field(default_factory=list)
    findings: list[EvidenceReference] = Field(default_factory=list)
    review_topics: list[ReviewTopic] = Field(default_factory=list)
    propose_followup: bool = False
