import asyncio
from uuid import UUID

from agents import gen_trace_id

from agents.exceptions import AgentsException
from openai import OpenAIError
from pydantic import ValidationError

from infectious_disease_assistant.guardrails import validate_safe_presentation
from infectious_disease_assistant.agent import AgentDemoError, investigate_patient
from infectious_disease_assistant.fhir.errors import FhirError
from infectious_disease_assistant.models.clinical_summary import ClinicalSummary
from infectious_disease_assistant.models.investigation import InvestigationRequest, InvestigationResponse
from infectious_disease_assistant.observability import LOGGER
from infectious_disease_assistant.rag.errors import RagError
from infectious_disease_assistant.services import patients
from infectious_disease_assistant.services.summary_validation import SummaryEvidenceError
from infectious_disease_assistant.models.workflow import AgentRunRecord, ApproveRequest, CurrentUser, RejectRequest
from infectious_disease_assistant.workflow.audit import RunAudit, utc_now
from infectious_disease_assistant.workflow.database import WorkflowStore
from infectious_disease_assistant.workflow.errors import WorkflowDatabaseError, WorkflowNotFoundError


class InvestigationError(RuntimeError):
    code = "investigation_failed"
    message = "The investigation could not be completed."

    def __init__(self):
        super().__init__(self.message)


class MicrobiologyResultNotFoundError(InvestigationError):
    code = "microbiology_result_not_found"
    message = "The microbiology result was not found for this synthetic patient."


class InvestigationNotFoundError(InvestigationError):
    code = "investigation_not_found"
    message = "The investigation was not found."


class InvestigationUnavailableError(InvestigationError):
    code = "investigation_unavailable"
    message = "An investigation dependency is unavailable or not configured."


class InvestigationSummaryError(InvestigationError):
    code = "invalid_clinical_summary"
    message = "The coordinator did not return a valid summary for the requested event."


class InvestigationService:
    def __init__(self, store: WorkflowStore | None = None):
        self.store = store if store is not None else WorkflowStore()

    async def create(self, request: InvestigationRequest) -> InvestigationResponse:
        investigation_id = None
        audit = RunAudit(AgentRunRecord(trace_id=gen_trace_id(), started_at=utc_now()), self.store.save_audit)
        try:
            cultures = await asyncio.to_thread(patients.get_microbiology_results, request.patient_id)
            target = next((item for item in cultures if item.result_id == request.microbiology_result_id), None)
            if target is None:
                raise MicrobiologyResultNotFoundError()
            investigation_id = await asyncio.to_thread(self.store.begin, request, audit.run)
            summary = await investigate_patient(
                request.patient_id, microbiology_result_id=request.microbiology_result_id,
                trace_id=audit.run.trace_id, audit=audit,
            )
            if not isinstance(summary, ClinicalSummary) or (
                summary.patient_id, summary.microbiology_result_id, summary.organism, summary.specimen_type
            ) != (request.patient_id, target.result_id, target.organism, target.specimen):
                raise InvestigationSummaryError()
            validate_safe_presentation(summary)
            audit.finish()
            return await asyncio.to_thread(self.store.complete, investigation_id, summary, audit.run, audit.events)
        except (patients.PatientNotFoundError, MicrobiologyResultNotFoundError):
            raise
        except Exception as error:
            if investigation_id is not None:
                audit.finish(error)
                try:
                    await asyncio.to_thread(self.store.fail, investigation_id, audit.run, audit.events)
                except WorkflowDatabaseError:
                    LOGGER.error("investigation_audit_unavailable")
            if isinstance(error, InvestigationError):
                raise
            LOGGER.error("investigation_request_failed", extra={"event_fields": {"error_type": type(error).__name__}})
            if isinstance(error, (FhirError, RagError, OpenAIError, ValidationError, WorkflowDatabaseError)):
                raise InvestigationUnavailableError() from None
            if isinstance(error, AgentsException):
                if isinstance(error.__cause__, (FhirError, RagError, OpenAIError, ValidationError, WorkflowDatabaseError)):
                    raise InvestigationUnavailableError() from None
                raise InvestigationSummaryError() from None
            if isinstance(error, (AgentDemoError, SummaryEvidenceError)):
                raise InvestigationSummaryError() from None
            raise InvestigationError() from None

    def get(self, investigation_id: UUID) -> InvestigationResponse:
        try:
            return self.store.get(investigation_id)
        except WorkflowNotFoundError:
            raise InvestigationNotFoundError() from None
        except WorkflowDatabaseError:
            raise InvestigationUnavailableError() from None

    def pending(self, limit: int = 50) -> list[InvestigationResponse]:
        try:
            return self.store.pending(limit)
        except WorkflowDatabaseError:
            raise InvestigationUnavailableError() from None

    def approve(self, investigation_id: UUID, user: CurrentUser, request: ApproveRequest) -> InvestigationResponse:
        return self._review(investigation_id, user, note=request.note, modified_action=request.modified_action)

    def reject(self, investigation_id: UUID, user: CurrentUser, request: RejectRequest) -> InvestigationResponse:
        return self._review(investigation_id, user, reject=True, note=request.note)

    def _review(self, investigation_id: UUID, user: CurrentUser, **options) -> InvestigationResponse:
        try:
            return self.store.review(investigation_id, user, **options)
        except WorkflowNotFoundError:
            raise InvestigationNotFoundError() from None
        except WorkflowDatabaseError:
            raise InvestigationUnavailableError() from None
