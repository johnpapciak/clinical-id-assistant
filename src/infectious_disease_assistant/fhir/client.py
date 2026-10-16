import re
from datetime import date

import httpx
from pydantic import ValidationError

from infectious_disease_assistant.config import Settings
from infectious_disease_assistant.fhir.constants import REFERENCE_DATE_URL, RESOURCE_TYPES, SYNTHETIC_ONLY_URL
from infectious_disease_assistant.fhir.errors import (
    FhirResourceNotFoundError, FhirResponseError, FhirTransportError,
)
from infectious_disease_assistant.fhir.models import CapabilityStatement, SearchBundle


def create_http_client() -> httpx.Client:
    return httpx.Client(
        base_url=Settings().fhir_base_url.rstrip("/") + "/",
        timeout=5.0, follow_redirects=False, trust_env=False,
        headers={"Accept": "application/fhir+json"},
    )


class FhirClient:
    def __init__(self, http_client: httpx.Client | None = None):
        self.http = http_client if http_client is not None else create_http_client()
        self._owns_client = http_client is None
        self._reference_date: date | None = None

    def __enter__(self) -> "FhirClient":
        return self

    def __exit__(self, *args) -> None:
        if self._owns_client:
            self.http.close()

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict:
        try:
            response = self.http.get(path, params=params)
        except httpx.RequestError as error:
            raise FhirTransportError("Synthetic FHIR endpoint unavailable") from error
        if response.status_code == 404:
            raise FhirResourceNotFoundError("FHIR resource not found")
        if response.status_code != 200:
            raise FhirResponseError(f"FHIR endpoint returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise FhirResponseError("FHIR endpoint returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise FhirResponseError("FHIR response must be a JSON object")
        return payload

    def reference_date(self) -> date:
        if self._reference_date is None:
            try:
                metadata = CapabilityStatement.model_validate(self._get("metadata"))
            except ValidationError as error:
                raise FhirResponseError("Expected FHIR R4 capability metadata") from error
            markers = [item for item in metadata.extension if item.url == SYNTHETIC_ONLY_URL]
            dates = [item for item in metadata.extension if item.url == REFERENCE_DATE_URL]
            if len(markers) != 1 or markers[0].valueBoolean is not True:
                raise FhirResponseError("Endpoint must explicitly declare synthetic-only data")
            if len(dates) != 1 or dates[0].valueDate is None:
                raise FhirResponseError("Endpoint must supply a synthetic reference date")
            self._reference_date = dates[0].valueDate
        return self._reference_date

    def search(self, resource_type: str, params: dict[str, str]) -> list[dict]:
        self._check_resource_type(resource_type)
        self.reference_date()
        try:
            bundle = SearchBundle.model_validate(self._get(resource_type, params))
        except ValidationError as error:
            raise FhirResponseError("Expected a FHIR searchset Bundle") from error
        if any(link.relation == "next" for link in bundle.link):
            raise FhirResponseError("Pagination is unsupported by this synthetic adapter")
        resources = [entry.resource for entry in bundle.entry]
        if bundle.total is not None and bundle.total != len(resources):
            raise FhirResponseError("Incomplete FHIR search response")
        if any(resource.get("resourceType") != resource_type for resource in resources):
            raise FhirResponseError("Unexpected resource type in FHIR search response")
        return resources

    def read(self, resource_type: str, resource_id: str) -> dict:
        self._check_resource_type(resource_type)
        if resource_id in {".", ".."} or re.fullmatch(r"[A-Za-z0-9.-]{1,64}", resource_id) is None:
            raise FhirResponseError("Invalid FHIR resource ID")
        self.reference_date()
        resource = self._get(f"{resource_type}/{resource_id}")
        if resource.get("resourceType") != resource_type or resource.get("id") != resource_id:
            raise FhirResponseError("FHIR read returned a mismatched resource")
        return resource

    def read_reference(self, reference: str, expected_type: str) -> dict:
        parts = reference.split("/")
        if len(parts) != 2 or parts[0] != expected_type:
            raise FhirResponseError("Only local typed FHIR references are supported")
        return self.read(parts[0], parts[1])

    @staticmethod
    def _check_resource_type(resource_type: str) -> None:
        if resource_type not in RESOURCE_TYPES:
            raise FhirResponseError("Unsupported FHIR resource type")
