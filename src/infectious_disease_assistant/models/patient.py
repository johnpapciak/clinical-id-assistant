from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

PatientId = Annotated[str, StringConstraints(pattern=r"^PATIENT_[0-9]{3}$")]


class HealthcareModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Medication(HealthcareModel):
    name: str
    dose: str
    route: str
    frequency: str
    status: Literal["active", "completed", "stopped"] = "active"


class Allergy(HealthcareModel):
    substance: str
    reaction: str | None = None
    severity: Literal["mild", "moderate", "severe"] | None = None


class LabResult(HealthcareModel):
    name: str
    value: float
    unit: str
    collected_on: date
    flag: Literal["low", "normal", "high"] | None = None


class Susceptibility(HealthcareModel):
    antibiotic: str
    interpretation: Literal["susceptible", "intermediate", "resistant"]


class MicrobiologyResult(HealthcareModel):
    result_id: str
    specimen: str
    collected_on: date
    outcome: Literal["growth", "no_growth"]
    organism: str | None = None
    quantity: str | None = None
    susceptibilities: list[Susceptibility] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_growth(self) -> "MicrobiologyResult":
        if self.outcome == "growth" and not self.organism:
            raise ValueError("Growth results require an organism")
        if self.outcome == "no_growth" and (self.organism or self.susceptibilities):
            raise ValueError("No-growth results cannot have an organism or susceptibilities")
        return self


class PreviousInfection(HealthcareModel):
    diagnosis: str
    organism: str
    diagnosed_on: date
    site: str
    treatment: str | None = None


class Diagnosis(HealthcareModel):
    name: str
    recorded_on: date
    status: Literal["active", "resolved"] = "active"


class Encounter(HealthcareModel):
    encounter_id: str
    occurred_on: date
    setting: Literal["outpatient", "emergency", "inpatient"]
    reason: str


class PatientRecord(HealthcareModel):
    patient_id: PatientId
    age: int | None = Field(default=None, ge=0, le=130)
    medications: list[Medication] = Field(default_factory=list)
    allergies: list[Allergy] = Field(default_factory=list)
    labs: list[LabResult] = Field(default_factory=list)
    microbiology_results: list[MicrobiologyResult] = Field(default_factory=list)
    previous_infections: list[PreviousInfection] = Field(default_factory=list)
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    encounters: list[Encounter] = Field(default_factory=list)


class PatientDataset(HealthcareModel):
    reference_date: date
    patients: list[PatientRecord]

    @model_validator(mode="after")
    def validate_unique_patients(self) -> "PatientDataset":
        patient_ids = [patient.patient_id for patient in self.patients]
        if len(patient_ids) != len(set(patient_ids)):
            raise ValueError("Patient IDs must be unique")
        return self


class PatientSummary(HealthcareModel):
    patient_id: PatientId
    age: int | None
    reference_date: date
    medications: list[Medication]
    allergies: list[Allergy]
    recent_labs: list[LabResult]
    microbiology_results: list[MicrobiologyResult]
    previous_infections: list[PreviousInfection]
    diagnoses: list[Diagnosis]
    encounters: list[Encounter]
