import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from agents import flush_traces
from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from infectious_disease_assistant.config import Settings
from infectious_disease_assistant.authorization import PatientAccessDeniedError, authorization_scope
from infectious_disease_assistant.current_user import get_current_user
from infectious_disease_assistant.models.workflow import ApproveRequest, CurrentUser, RejectRequest
from infectious_disease_assistant.workflow.database import WorkflowStore
from infectious_disease_assistant.workflow.errors import ReviewConflictError
from infectious_disease_assistant.models.investigation import (
    ErrorResponse, HealthResponse, InvestigationRequest, InvestigationResponse, ValidationIssue,
)
from infectious_disease_assistant.event_logging import LOGGER, configure_logging
from infectious_disease_assistant.services.investigations import (
    InvestigationError, InvestigationNotFoundError, InvestigationService,
    InvestigationSummaryError, InvestigationUnavailableError, MicrobiologyResultNotFoundError,
)
from infectious_disease_assistant.services.patients import PatientNotFoundError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(Settings().log_level)
    try:
        yield
    finally:
        await asyncio.to_thread(flush_traces)


def get_investigation_service(request: Request) -> InvestigationService:
    return request.app.state.investigations


async def patient_access_scope(user: Annotated[CurrentUser, Depends(get_current_user)]) -> AsyncIterator[None]:
    with authorization_scope(user):
        yield


InvestigationDependency = Annotated[InvestigationService, Depends(get_investigation_service)]
investigations = APIRouter(prefix="/investigations", tags=["investigations"], dependencies=[Depends(patient_access_scope)])


@investigations.post("", response_model=InvestigationResponse, status_code=status.HTTP_201_CREATED)
async def create_investigation(
    body: InvestigationRequest, response: Response, service: InvestigationDependency,
) -> InvestigationResponse:
    result = await service.create(body)
    response.headers["Location"] = f"/investigations/{result.investigation_id}"
    return result


@investigations.get("/pending-review", response_model=list[InvestigationResponse])
async def pending_review(service: InvestigationDependency, limit: int = Query(default=50, ge=1, le=100)) -> list[InvestigationResponse]:
    return await asyncio.to_thread(service.pending, limit)


@investigations.post("/{investigation_id}/approve", response_model=InvestigationResponse)
async def approve_investigation(
    investigation_id: UUID, body: ApproveRequest, service: InvestigationDependency,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> InvestigationResponse:
    return await asyncio.to_thread(service.approve, investigation_id, user, body)


@investigations.post("/{investigation_id}/reject", response_model=InvestigationResponse)
async def reject_investigation(
    investigation_id: UUID, body: RejectRequest, service: InvestigationDependency,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> InvestigationResponse:
    return await asyncio.to_thread(service.reject, investigation_id, user, body)


@investigations.get("/{investigation_id}", response_model=InvestigationResponse)
async def get_investigation(investigation_id: UUID, service: InvestigationDependency) -> InvestigationResponse:
    return await asyncio.to_thread(service.get, investigation_id)


async def handle_investigation_error(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, PatientAccessDeniedError):
        payload = ErrorResponse(code="patient_access_denied", message="Access to this synthetic patient is denied.")
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, ReviewConflictError):
        payload = ErrorResponse(code="review_conflict", message="The investigation cannot accept this review decision.")
        code = status.HTTP_409_CONFLICT
    elif isinstance(error, PatientNotFoundError):
        payload = ErrorResponse(code="patient_not_found", message="The synthetic patient was not found.")
        code = status.HTTP_404_NOT_FOUND
    else:
        payload = ErrorResponse(code=error.code, message=error.message)
        code = {
            MicrobiologyResultNotFoundError: status.HTTP_404_NOT_FOUND,
            InvestigationNotFoundError: status.HTTP_404_NOT_FOUND,
            InvestigationUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
            InvestigationSummaryError: status.HTTP_502_BAD_GATEWAY,
        }.get(type(error), status.HTTP_500_INTERNAL_SERVER_ERROR)
    return JSONResponse(status_code=code, content=payload.model_dump(mode="json"))


async def handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    payload = ErrorResponse(
        code="validation_error", message="Request validation failed.",
        issues=[ValidationIssue(
            location=[
                part if isinstance(part, int) or part in {
                    "body", "path", "query", "patient_id", "microbiology_result_id", "investigation_id",
                    "note", "modified_action", "title", "description", "kind", "limit",
                } else "unknown_field" for part in item["loc"]
            ],
            code=item["type"],
        ) for item in error.errors()],
    )
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content=payload.model_dump(mode="json"))


async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    LOGGER.error("api_request_failed", extra={"event_fields": {"error_type": type(error).__name__}})
    payload = ErrorResponse(code="internal_error", message="The request could not be completed.")
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload.model_dump(mode="json"))


def create_app(store: WorkflowStore | None = None) -> FastAPI:
    app = FastAPI(title="Synthetic Infectious Disease Assistant", lifespan=lifespan, debug=False)
    app.state.investigations = InvestigationService(store)
    app.add_exception_handler(ReviewConflictError, handle_investigation_error)
    app.add_exception_handler(InvestigationError, handle_investigation_error)
    app.add_exception_handler(PatientNotFoundError, handle_investigation_error)
    app.add_exception_handler(PatientAccessDeniedError, handle_investigation_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
    app.include_router(investigations, responses={
        code: {"model": ErrorResponse} for code in (403, 404, 409, 422, 500, 502, 503)
    })

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    return app


app = create_app()
