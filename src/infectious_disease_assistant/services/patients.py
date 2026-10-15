from datetime import timedelta

from pydantic import TypeAdapter, ValidationError

from infectious_disease_assistant.data_loader import load_patient_data
from infectious_disease_assistant.models.patient import (
    Allergy,
    LabResult,
    Medication,
    MicrobiologyResult,
    PatientId,
    PatientSummary,
    PreviousInfection,
)

RECENT_LAB_LOOKBACK_DAYS = 30
_patient_id_adapter = TypeAdapter(PatientId)


class InvalidPatientIdError(ValueError):
    """The patient ID does not match PATIENT_ followed by three digits."""


class PatientNotFoundError(LookupError):
    """A valid patient ID is absent from the synthetic dataset."""


def get_patient_summary(patient_id: str) -> PatientSummary:
    try:
        validated_id = _patient_id_adapter.validate_python(patient_id, strict=True)
    except ValidationError as error:
        raise InvalidPatientIdError("Patient ID must match PATIENT_ followed by three digits") from error

    dataset = load_patient_data()
    patient = next((item for item in dataset.patients if item.patient_id == validated_id), None)
    if patient is None:
        raise PatientNotFoundError(f"Unknown synthetic patient: {validated_id}")

    reference_date = dataset.reference_date
    cutoff = reference_date - timedelta(days=RECENT_LAB_LOOKBACK_DAYS)
    return PatientSummary(
        patient_id=patient.patient_id,
        age=patient.age,
        reference_date=reference_date,
        medications=[item for item in patient.medications if item.status == "active"],
        allergies=patient.allergies,
        recent_labs=sorted(
            (item for item in patient.labs if cutoff <= item.collected_on <= reference_date),
            key=lambda item: item.collected_on,
            reverse=True,
        ),
        microbiology_results=sorted(
            (item for item in patient.microbiology_results if item.collected_on <= reference_date),
            key=lambda item: item.collected_on,
            reverse=True,
        ),
        previous_infections=sorted(
            (item for item in patient.previous_infections if item.diagnosed_on < reference_date),
            key=lambda item: item.diagnosed_on,
            reverse=True,
        ),
        diagnoses=sorted(
            (item for item in patient.diagnoses if item.recorded_on <= reference_date),
            key=lambda item: item.recorded_on,
            reverse=True,
        ),
        encounters=sorted(
            (item for item in patient.encounters if item.occurred_on <= reference_date),
            key=lambda item: item.occurred_on,
            reverse=True,
        ),
    )


def get_medications(patient_id: str) -> list[Medication]:
    return get_patient_summary(patient_id).medications


def get_allergies(patient_id: str) -> list[Allergy]:
    return get_patient_summary(patient_id).allergies


def get_recent_labs(patient_id: str) -> list[LabResult]:
    return get_patient_summary(patient_id).recent_labs


def get_microbiology_results(patient_id: str) -> list[MicrobiologyResult]:
    return get_patient_summary(patient_id).microbiology_results


def get_previous_infections(patient_id: str) -> list[PreviousInfection]:
    return get_patient_summary(patient_id).previous_infections
