from contextlib import contextmanager
from datetime import date, timedelta
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from infectious_disease_assistant.fhir import models as fhir
from infectious_disease_assistant.fhir import parsing
from infectious_disease_assistant.fhir.client import FhirClient
from infectious_disease_assistant.fhir.constants import HISTORY_SYSTEM, LAB_CATEGORY_SYSTEM, PATIENT_IDENTIFIER_SYSTEM
from infectious_disease_assistant.fhir.errors import FhirParseError
from infectious_disease_assistant.models.patient import (
    Allergy, Diagnosis, Encounter, LabResult, Medication, MicrobiologyResult, PatientContextInfo,
    PatientId, PatientSummary, PreviousInfection,
)

RECENT_LAB_LOOKBACK_DAYS = 30
_patient_id_adapter = TypeAdapter(PatientId)
T = TypeVar("T", bound=BaseModel)


class InvalidPatientIdError(ValueError):
    """The patient ID does not match PATIENT_ followed by three digits."""


class PatientNotFoundError(LookupError):
    """A valid patient ID is absent from the synthetic FHIR endpoint."""


@contextmanager
def _patient_client(patient_id: str):
    try:
        validated_id = _patient_id_adapter.validate_python(patient_id, strict=True)
    except ValidationError as error:
        raise InvalidPatientIdError("Patient ID must match PATIENT_ followed by three digits") from error
    with FhirClient() as client:
        matches = client.search("Patient", {"identifier": f"{PATIENT_IDENTIFIER_SYSTEM}|{validated_id}"})
        if not matches:
            raise PatientNotFoundError(f"Unknown synthetic patient: {validated_id}")
        if len(matches) != 1:
            raise FhirParseError("Patient identifier matched multiple resources")
        patient = parsing.parse_resource(fhir.Patient, matches[0])
        if parsing.business_identifier(patient.identifier, PATIENT_IDENTIFIER_SYSTEM) != validated_id:
            raise FhirParseError("Patient search returned a mismatched identifier")
        yield client, patient, client.reference_date()


def _search(client: FhirClient, patient: fhir.Patient, model: type[T], kind: str, **params: str) -> list[T]:
    reference = f"Patient/{patient.id}"
    resources = client.search(kind, {"patient": reference, **params})
    parsed = [parsing.parse_resource(model, resource, reference) for resource in resources]
    if "category" in params:
        system, code = params["category"].split("|", 1)
        if any(not any(coding.system == system and coding.code == code
                       for category in item.category for coding in category.coding) for item in parsed):
            raise FhirParseError("FHIR search returned an unexpected category")
    return parsed


def _medications(client: FhirClient, patient: fhir.Patient) -> list[Medication]:
    requests = _search(client, patient, fhir.MedicationRequest, "MedicationRequest", status="active")
    return [parsing.medication(item) for item in requests if item.status == "active"]


def _allergies(client: FhirClient, patient: fhir.Patient) -> list[Allergy]:
    resources = _search(client, patient, fhir.AllergyIntolerance, "AllergyIntolerance")
    for item in resources:
        clinical = parsing.concept_code(item.clinicalStatus, "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical")
        verification = parsing.concept_code(item.verificationStatus, "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification")
        if clinical != "active" or verification != "confirmed":
            raise FhirParseError("This demo supports active, confirmed allergy records only")
    return [parsing.allergy(item) for item in resources]


def _labs(client: FhirClient, patient: fhir.Patient, reference_date: date) -> list[LabResult]:
    cutoff = reference_date - timedelta(days=RECENT_LAB_LOOKBACK_DAYS)
    observations = _search(client, patient, fhir.Observation, "Observation",
                           category=f"{LAB_CATEGORY_SYSTEM}|routine-lab", date=f"ge{cutoff.isoformat()}")
    labs = [parsing.lab(item) for item in observations if item.status in {"final", "amended", "corrected"}]
    return sorted((item for item in labs if cutoff <= item.collected_on <= reference_date),
                  key=lambda item: item.collected_on, reverse=True)


def _microbiology(client: FhirClient, patient: fhir.Patient, reference_date: date) -> list[MicrobiologyResult]:
    reports = _search(client, patient, fhir.DiagnosticReport, "DiagnosticReport",
                      category="http://terminology.hl7.org/CodeSystem/v2-0074|MIC",
                      date=f"le{reference_date.isoformat()}")
    results = []
    reference = f"Patient/{patient.id}"
    for report in reports:
        if report.status not in {"final", "amended", "corrected"} or parsing.as_date(report.effectiveDateTime) > reference_date:
            continue
        specimen = parsing.parse_resource(fhir.Specimen,
            client.read_reference(report.specimen[0].reference, "Specimen"), reference)
        if len({item.reference for item in report.result}) != len(report.result):
            raise FhirParseError("DiagnosticReport contains duplicate result references")
        observations = [parsing.parse_resource(fhir.Observation,
            client.read_reference(item.reference, "Observation"), reference) for item in report.result]
        results.append(parsing.microbiology(report, specimen, observations))
    return sorted(results, key=lambda item: item.collected_on, reverse=True)


def _previous_infections(client: FhirClient, patient: fhir.Patient, reference_date: date) -> list[PreviousInfection]:
    conditions = _search(client, patient, fhir.Condition, "Condition", category=f"{HISTORY_SYSTEM}|infection-history")
    history = [parsing.previous_infection(item) for item in conditions]
    return sorted((item for item in history if item.diagnosed_on < reference_date),
                  key=lambda item: item.diagnosed_on, reverse=True)


def _diagnoses(client: FhirClient, patient: fhir.Patient, reference_date: date) -> list[Diagnosis]:
    conditions = _search(client, patient, fhir.Condition, "Condition",
                         category="http://terminology.hl7.org/CodeSystem/condition-category|problem-list-item")
    diagnoses = [parsing.diagnosis(item) for item in conditions]
    return sorted((item for item in diagnoses if item.recorded_on <= reference_date),
                  key=lambda item: item.recorded_on, reverse=True)


def _encounters(client: FhirClient, patient: fhir.Patient, reference_date: date) -> list[Encounter]:
    resources = _search(client, patient, fhir.Encounter, "Encounter")
    encounters = [parsing.encounter(item) for item in resources]
    return sorted((item for item in encounters if item.occurred_on <= reference_date),
                  key=lambda item: item.occurred_on, reverse=True)


def get_patient_context(patient_id: str) -> PatientContextInfo:
    with _patient_client(patient_id) as (_, patient, reference_date):
        return PatientContextInfo(patient_id=patient_id, reference_date=reference_date,
                                  age=parsing.patient_age(patient, reference_date))


def get_patient_summary(patient_id: str) -> PatientSummary:
    with _patient_client(patient_id) as (client, patient, reference_date):
        return PatientSummary(
            patient_id=patient_id, age=parsing.patient_age(patient, reference_date), reference_date=reference_date,
            medications=_medications(client, patient), allergies=_allergies(client, patient),
            recent_labs=_labs(client, patient, reference_date),
            microbiology_results=_microbiology(client, patient, reference_date),
            previous_infections=_previous_infections(client, patient, reference_date),
            diagnoses=_diagnoses(client, patient, reference_date), encounters=_encounters(client, patient, reference_date),
        )


def get_medications(patient_id: str) -> list[Medication]:
    with _patient_client(patient_id) as (client, patient, _):
        return _medications(client, patient)


def get_allergies(patient_id: str) -> list[Allergy]:
    with _patient_client(patient_id) as (client, patient, _):
        return _allergies(client, patient)


def get_recent_labs(patient_id: str) -> list[LabResult]:
    with _patient_client(patient_id) as (client, patient, reference_date):
        return _labs(client, patient, reference_date)


def get_microbiology_results(patient_id: str) -> list[MicrobiologyResult]:
    with _patient_client(patient_id) as (client, patient, reference_date):
        return _microbiology(client, patient, reference_date)


def get_previous_infections(patient_id: str) -> list[PreviousInfection]:
    with _patient_client(patient_id) as (client, patient, reference_date):
        return _previous_infections(client, patient, reference_date)
