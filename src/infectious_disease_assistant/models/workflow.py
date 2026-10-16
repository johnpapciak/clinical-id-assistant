from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, ConfigDict, Field, StringConstraints

from infectious_disease_assistant.models.patient import HealthcareModel, PatientId

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
ReviewerId = Annotated[str, StringConstraints(pattern=r"^REVIEWER_[0-9]{3}$")]


class FollowupAction(HealthcareModel):
    kind: Literal["create_followup_task"] = "create_followup_task"
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    description: Text


class CurrentUser(HealthcareModel):
    model_config = ConfigDict(frozen=True)

    reviewer_id: ReviewerId
    assigned_patient_ids: frozenset[PatientId] = frozenset()


class ApproveRequest(HealthcareModel):
    note: Text | None = None
    modified_action: FollowupAction | None = None


class RejectRequest(HealthcareModel):
    note: Text


class AgentRunRecord(HealthcareModel):
    agent_run_id: UUID = Field(default_factory=uuid4)
    trace_id: str
    status: Literal["running", "succeeded", "failed"] = "running"
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    model_name: str | None = None
    configuration_version: str | None = None
    error_type: str | None = None


class ToolEvent(HealthcareModel):
    tool_event_id: UUID = Field(default_factory=uuid4)
    tool_call_id: str
    tool_name: str
    status: Literal["running", "completed", "interrupted"] = "running"
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    record_count: int | None = Field(default=None, ge=0)


class ApprovalDecision(HealthcareModel):
    approval_id: UUID
    reviewer_id: ReviewerId
    decision: Literal["approved", "modified", "rejected"]
    note: Text | None
    decided_at: AwareDatetime
    approved_action: FollowupAction | None


class FollowupTask(HealthcareModel):
    task_id: UUID
    investigation_id: UUID
    approval_id: UUID
    patient_id: PatientId
    title: str
    description: str
    created_at: AwareDatetime
    simulated: Literal[True] = True
