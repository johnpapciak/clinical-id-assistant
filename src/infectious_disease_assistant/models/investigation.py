from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field

from infectious_disease_assistant.models.clinical_summary import ClinicalSummary
from infectious_disease_assistant.models.workflow import AgentRunRecord, ApprovalDecision, FollowupTask, ToolEvent
from infectious_disease_assistant.models.patient import HealthcareModel, MicrobiologyResultId, PatientId


class InvestigationRequest(HealthcareModel):
    model_config = ConfigDict(frozen=True)

    patient_id: PatientId
    microbiology_result_id: MicrobiologyResultId


class InvestigationResponse(HealthcareModel):
    investigation_id: UUID
    patient_id: PatientId
    microbiology_result_id: MicrobiologyResultId
    status: Literal["running", "awaiting_review", "approved", "rejected", "failed"] = "running"
    created_at: AwareDatetime
    summary: ClinicalSummary | None = None
    updated_at: AwareDatetime
    summary_id: UUID | None = None
    agent_run: AgentRunRecord
    tool_events: list[ToolEvent] = Field(default_factory=list)
    approval: ApprovalDecision | None = None
    followup_task: FollowupTask | None = None


class HealthResponse(HealthcareModel):
    status: Literal["ok"] = "ok"


class ValidationIssue(HealthcareModel):
    location: list[str | int]
    code: str


class ErrorResponse(HealthcareModel):
    code: str
    message: str
    issues: list[ValidationIssue] = Field(default_factory=list)
