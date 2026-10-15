from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

FhirId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9.-]{1,64}$")]


class FhirModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True, allow_inf_nan=False)


class Coding(FhirModel):
    system: str | None = None
    code: str
    display: str | None = None


class CodeableConcept(FhirModel):
    text: str | None = None
    coding: list[Coding] = Field(default_factory=list)


class Identifier(FhirModel):
    system: str
    value: str


class Reference(FhirModel):
    reference: str


class Extension(FhirModel):
    url: str
    valueDate: date | None = None
    valueBoolean: bool | None = None
    valueString: str | None = None


class Resource(FhirModel):
    id: FhirId


class Patient(Resource):
    resourceType: Literal["Patient"]
    identifier: list[Identifier]
    birthDate: date | None = None


class Quantity(FhirModel):
    value: Decimal
    unit: str


class DoseAndRate(FhirModel):
    doseQuantity: Quantity


class Timing(FhirModel):
    code: CodeableConcept


class Dosage(FhirModel):
    timing: Timing
    route: CodeableConcept
    doseAndRate: list[DoseAndRate] = Field(min_length=1, max_length=1)


class MedicationRequest(Resource):
    resourceType: Literal["MedicationRequest"]
    status: Literal["active", "on-hold", "cancelled", "completed", "entered-in-error", "stopped", "draft", "unknown"]
    intent: Literal["proposal", "plan", "order", "original-order", "reflex-order", "filler-order", "instance-order", "option"]
    subject: Reference
    medicationCodeableConcept: CodeableConcept
    dosageInstruction: list[Dosage] = Field(min_length=1, max_length=1)


class AllergyReaction(FhirModel):
    manifestation: list[CodeableConcept] = Field(min_length=1)
    severity: Literal["mild", "moderate", "severe"] | None = None


class AllergyIntolerance(Resource):
    resourceType: Literal["AllergyIntolerance"]
    patient: Reference
    code: CodeableConcept
    clinicalStatus: CodeableConcept
    verificationStatus: CodeableConcept
    reaction: list[AllergyReaction] = Field(default_factory=list)


class ObservationComponent(FhirModel):
    code: CodeableConcept
    valueString: str | None = None


class Observation(Resource):
    resourceType: Literal["Observation"]
    status: Literal["registered", "preliminary", "final", "amended", "corrected", "cancelled", "entered-in-error", "unknown"]
    subject: Reference
    code: CodeableConcept
    category: list[CodeableConcept] = Field(default_factory=list)
    effectiveDateTime: datetime | date
    valueQuantity: Quantity | None = None
    valueCodeableConcept: CodeableConcept | None = None
    interpretation: list[CodeableConcept] = Field(default_factory=list)
    component: list[ObservationComponent] = Field(default_factory=list)


class Condition(Resource):
    resourceType: Literal["Condition"]
    subject: Reference
    clinicalStatus: CodeableConcept
    category: list[CodeableConcept]
    code: CodeableConcept
    recordedDate: datetime | date
    onsetDateTime: datetime | date | None = None
    bodySite: list[CodeableConcept] = Field(default_factory=list)
    extension: list[Extension] = Field(default_factory=list)


class Period(FhirModel):
    start: datetime | date


class Encounter(Resource):
    resourceType: Literal["Encounter"]
    identifier: list[Identifier]
    subject: Reference
    status: Literal["planned", "arrived", "triaged", "in-progress", "onleave", "finished", "cancelled", "entered-in-error", "unknown"]
    class_: Coding = Field(alias="class")
    period: Period
    reasonCode: list[CodeableConcept] = Field(min_length=1)


class Specimen(Resource):
    resourceType: Literal["Specimen"]
    subject: Reference
    type: CodeableConcept


class DiagnosticReport(Resource):
    resourceType: Literal["DiagnosticReport"]
    identifier: list[Identifier]
    subject: Reference
    status: Literal["registered", "partial", "preliminary", "final", "amended", "corrected", "appended", "cancelled", "entered-in-error", "unknown"]
    category: list[CodeableConcept]
    code: CodeableConcept
    effectiveDateTime: datetime | date
    specimen: list[Reference] = Field(min_length=1, max_length=1)
    result: list[Reference] = Field(default_factory=list)
    conclusionCode: list[CodeableConcept] = Field(min_length=1, max_length=1)


class BundleEntry(FhirModel):
    resource: dict


class BundleLink(FhirModel):
    relation: str
    url: str


class SearchBundle(FhirModel):
    resourceType: Literal["Bundle"]
    type: Literal["searchset"]
    entry: list[BundleEntry] = Field(default_factory=list)
    total: int | None = Field(default=None, ge=0)
    link: list[BundleLink] = Field(default_factory=list)


class CapabilityStatement(FhirModel):
    resourceType: Literal["CapabilityStatement"]
    fhirVersion: Literal["4.0.1"]
    extension: list[Extension]


RESOURCE_MODELS = {
    "Patient": Patient, "MedicationRequest": MedicationRequest,
    "AllergyIntolerance": AllergyIntolerance, "Observation": Observation,
    "Condition": Condition, "Encounter": Encounter,
    "DiagnosticReport": DiagnosticReport, "Specimen": Specimen,
}
