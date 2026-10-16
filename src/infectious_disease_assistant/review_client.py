from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import Field, TypeAdapter, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from infectious_disease_assistant.models.investigation import InvestigationRequest, InvestigationResponse
from infectious_disease_assistant.models.workflow import ApproveRequest, RejectRequest


class ReviewUISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IDA_", env_file=".env", extra="ignore", hide_input_in_errors=True)

    ui_api_url: str = "http://127.0.0.1:8000"
    ui_timeout_seconds: float = Field(default=180, ge=1, le=600, allow_inf_nan=False)

    @field_validator("ui_api_url")
    @classmethod
    def validate_backend_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        allowed = parsed.hostname in {"localhost", "127.0.0.1", "::1"} or (
            parsed.hostname == "api" and parsed.port == 8000
        )
        parsed.port
        if (parsed.scheme != "http" or not allowed or parsed.path not in {"", "/"}
                or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
            raise ValueError("Use a local demo API URL without credentials or query parameters")
        return value.rstrip("/")


class ReviewAPIError(RuntimeError):
    pass


ERROR_MESSAGES = {
    403: "The backend reviewer is not assigned to this synthetic patient.",
    404: "The synthetic patient, result, or investigation was not found.",
    409: "This investigation was already reviewed or cannot accept this decision. Refresh its status.",
    422: "Check the synthetic identifiers and review note. A rejection requires a note.",
    502: "The backend could not produce a valid clinical summary.",
    503: "The backend or one of its required services is unavailable.",
}


class ReviewClient:
    def __init__(self, settings: ReviewUISettings, client: httpx.Client | None = None):
        self.client = client or httpx.Client(
            base_url=settings.ui_api_url, timeout=settings.ui_timeout_seconds,
            trust_env=False, follow_redirects=False,
        )

    def __enter__(self) -> "ReviewClient":
        return self

    def __exit__(self, *_):
        self.client.close()

    def _request(self, method: str, path: str, body=None):
        try:
            response = self.client.request(method, path, json=body)
        except httpx.TimeoutException:
            raise ReviewAPIError(
                "The backend request timed out and may have completed. Load pending reviews or reopen "
                "the investigation before retrying; requests are not retried automatically."
            ) from None
        except httpx.RequestError:
            raise ReviewAPIError("Cannot reach the demo API. Check that the backend is running.") from None
        if not response.is_success:
            raise ReviewAPIError(ERROR_MESSAGES.get(response.status_code, "The backend request could not be completed."))
        try:
            return response.json()
        except ValueError:
            raise ReviewAPIError("The backend returned an invalid response.") from None

    def _investigation(self, method: str, path: str, body=None) -> InvestigationResponse:
        payload = self._request(method, path, body)
        try:
            return InvestigationResponse.model_validate(payload)
        except ValidationError:
            raise ReviewAPIError("The backend returned an invalid investigation response.") from None

    def create(self, patient_id: str, result_id: str) -> InvestigationResponse:
        try:
            body = InvestigationRequest(patient_id=patient_id.strip(), microbiology_result_id=result_id.strip())
        except ValidationError:
            raise ReviewAPIError("Enter synthetic IDs such as PATIENT_001 and MICRO_001_RECENT.") from None
        return self._investigation("POST", "/investigations", body.model_dump(mode="json"))

    def get(self, investigation_id: str) -> InvestigationResponse:
        return self._investigation("GET", f"/investigations/{self._identifier(investigation_id)}")

    def pending(self) -> list[InvestigationResponse]:
        payload = self._request("GET", "/investigations/pending-review")
        try:
            return TypeAdapter(list[InvestigationResponse]).validate_python(payload)
        except ValidationError:
            raise ReviewAPIError("The backend returned an invalid pending-review response.") from None

    def review(self, investigation_id: str, *, approve: bool, note: str) -> InvestigationResponse:
        identifier = self._identifier(investigation_id)
        try:
            body = ApproveRequest(note=note.strip() or None) if approve else RejectRequest(note=note.strip())
        except ValidationError:
            raise ReviewAPIError("A rejection requires a note; review notes must be at most 1000 characters.") from None
        decision = "approve" if approve else "reject"
        return self._investigation("POST", f"/investigations/{identifier}/{decision}", body.model_dump(mode="json"))

    @staticmethod
    def _identifier(value: str) -> UUID:
        try:
            return UUID(value.strip())
        except ValueError:
            raise ReviewAPIError("Enter the investigation UUID returned by the backend.") from None
