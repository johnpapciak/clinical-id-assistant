import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import TypeAdapter, ValidationError

from infectious_disease_assistant.current_user import get_current_user
from infectious_disease_assistant.models.patient import PatientId
from infectious_disease_assistant.models.workflow import CurrentUser

_principal: ContextVar[CurrentUser | None] = ContextVar("access_principal", default=None)
_patient_id = TypeAdapter(PatientId)
LOGGER = logging.getLogger("infectious_disease_assistant")


class InvalidPatientIdError(ValueError):
    """The patient ID does not match the synthetic token format."""


class PatientAccessDeniedError(PermissionError):
    def __init__(self):
        super().__init__("Access to this synthetic patient is denied.")


def access_user() -> CurrentUser:
    user = _principal.get()
    return user if user is not None else get_current_user()


@contextmanager
def authorization_scope(user: CurrentUser) -> Iterator[None]:
    token = _principal.set(user)
    try:
        yield
    finally:
        _principal.reset(token)


def security_event(event: str, *, user: CurrentUser, operation: str, patient_id: str | None = None) -> None:
    LOGGER.info(event, extra={"event_fields": {
        "reviewer_id": user.reviewer_id, "patient_id": patient_id, "operation": operation,
    }})


def authorize_patient(patient_id: str, *, operation: str = "patient_read") -> str:
    try:
        validated = _patient_id.validate_python(patient_id, strict=True)
    except ValidationError:
        raise InvalidPatientIdError("Patient ID must match PATIENT_ followed by three digits") from None
    user = access_user()
    allowed = validated in user.assigned_patient_ids
    security_event("patient_access_granted" if allowed else "patient_access_denied",
                   user=user, patient_id=validated, operation=operation)
    if not allowed:
        raise PatientAccessDeniedError()
    return validated
