from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from infectious_disease_assistant.models.clinical_summary import ClinicalSummary, ToolName
from infectious_disease_assistant.models.patient import Allergy, HealthcareModel, LabResult, Medication, PatientId, PreviousInfection

Section = Literal['relevant_medications', 'relevant_allergies', 'relevant_labs', 'previous_infections']


class Expectations(HealthcareModel):
    organism: str | None
    specimen_type: str | None
    medications: list[Medication] = Field(default_factory=list)
    allergies: list[Allergy] = Field(default_factory=list)
    labs: list[LabResult] = Field(default_factory=list)
    previous_infections: list[PreviousInfection] = Field(default_factory=list)
    empty_sections: list[Section] = Field(default_factory=list)
    required_tools: list[ToolName] = Field(default_factory=list)
    forbidden_tools: list[ToolName] = Field(default_factory=list)
    guideline_retrieval: Literal['required', 'optional', 'forbidden'] = 'optional'
    required_documents: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)
    human_review_required: bool = True
    block_category: str | None = None


class EvalCase(HealthcareModel):
    case_id: str
    title: str
    patient_id: PatientId
    microbiology_result_id: str | None = None
    guideline_question: str | None = None
    kind: Literal['clinical', 'guardrail_attack'] = 'clinical'
    expectations: Expectations

    @model_validator(mode='after')
    def validate_kind(self):
        if (self.kind == 'guardrail_attack') != (self.expectations.block_category is not None):
            raise ValueError('Attack cases must specify an expected block category')
        return self


class CheckResult(HealthcareModel):
    dimension: str
    status: Literal['pass', 'fail', 'not_applicable']
    detail: str = ''


class ObservedCall(HealthcareModel):
    tool_name: str
    arguments_json: str
    completed: bool


class CaseResult(HealthcareModel):
    case_id: str
    kind: Literal['clinical', 'guardrail_attack']
    passed: bool
    structured_output_valid: bool | None
    blocked_category: str | None = None
    latency_ms: float | None = None
    model_name: str | None = None
    usage: dict[str, int] | None = None
    usage_source: Literal['scripted_not_billable', 'sdk_reported', 'unavailable'] = 'unavailable'
    simulated_model_calls: int = 0
    tool_calls: list[ObservedCall] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    summary: ClinicalSummary | None = None
    error_type: str | None = None


class DimensionStats(HealthcareModel):
    passed: int = 0
    failed: int = 0
    not_applicable: int = 0


class EvaluationReport(HealthcareModel):
    mode: Literal['scripted', 'live']
    generated_at: AwareDatetime
    corpus_sha256: str
    replay_sha256: str
    agent_configuration_version: str
    scoring_version: str = '1'
    model_judging: Literal['not_run'] = 'not_run'
    run_error: str | None = None
    planned_cases: int
    total_cases: int
    passed_cases: int
    blocked_attacks: int
    failure_categories: dict[str, int]
    clinical_latency_ms: dict[str, float] = Field(default_factory=dict)
    dimensions: dict[str, DimensionStats]
    cases: list[CaseResult]
