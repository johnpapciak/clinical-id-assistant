import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from uuid import UUID, uuid4

from agents.exceptions import AgentsException
from openai import OpenAIError
from pydantic import ValidationError

from infectious_disease_assistant.agent import AgentDemoError, investigate_patient
from infectious_disease_assistant.fhir.errors import FhirError
from infectious_disease_assistant.models.clinical_summary import ClinicalSummary
from infectious_disease_assistant.models.investigation import InvestigationRequest, InvestigationResponse
from infectious_disease_assistant.observability import LOGGER
from infectious_disease_assistant.rag.errors import RagError
from infectious_disease_assistant.services import patients
from infectious_disease_assistant.services.summary_validation import SummaryEvidenceError


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
    message = "The investigation was not found in this process's history."


class InvestigationUnavailableError(InvestigationError):
    code = "investigation_unavailable"
    message = "An investigation dependency is unavailable or not configured."


class InvestigationSummaryError(InvestigationError):
    code = "invalid_clinical_summary"
    message = "The coordinator did not return a valid summary for the requested event."


class InvestigationService:
    def __init__(self, history_limit: int = 100):
        if history_limit < 1:
            raise ValueError("History limit must be positive")
        self.history_limit = history_limit
        self._completed: OrderedDict[UUID, InvestigationResponse] = OrderedDict()

    async def create(self, request: InvestigationRequest) -> InvestigationResponse:
        created_at = datetime.now(timezone.utc)
        try:
            cultures = await asyncio.to_thread(patients.get_microbiology_results, request.patient_id)
            target = next((item for item in cultures if item.result_id == request.microbiology_result_id), None)
            if target is None:
                raise MicrobiologyResultNotFoundError()
            summary = await investigate_patient(request.patient_id, microbiology_result_id=request.microbiology_result_id)
            if not isinstance(summary, ClinicalSummary) or (
                summary.patient_id, summary.microbiology_result_id, summary.organism, summary.specimen_type
            ) != (request.patient_id, target.result_id, target.organism, target.specimen):
                raise InvestigationSummaryError()
            investigation = InvestigationResponse(
                investigation_id=uuid4(), patient_id=request.patient_id,
                microbiology_result_id=request.microbiology_result_id, created_at=created_at,
                summary=summary,
            )
        except (patients.PatientNotFoundError, InvestigationError):
            raise
        except Exception as error:
            LOGGER.error("investigation_request_failed", extra={"event_fields": {"error_type": type(error).__name__}})
            if isinstance(error, (FhirError, RagError, OpenAIError, ValidationError)):
                raise InvestigationUnavailableError() from None
            if isinstance(error, AgentsException):
                if isinstance(error.__cause__, (FhirError, RagError, OpenAIError, ValidationError)):
                    raise InvestigationUnavailableError() from None
                raise InvestigationSummaryError() from None
            if isinstance(error, (AgentDemoError, SummaryEvidenceError)):
                raise InvestigationSummaryError() from None
            raise InvestigationError() from None
        self._completed[investigation.investigation_id] = investigation.model_copy(deep=True)
        while len(self._completed) > self.history_limit:
            self._completed.popitem(last=False)
        return investigation

    def get(self, investigation_id: UUID) -> InvestigationResponse:
        investigation = self._completed.get(investigation_id)
        if investigation is None:
            raise InvestigationNotFoundError()
        return investigation.model_copy(deep=True)
