from datetime import date, datetime
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from infectious_disease_assistant.fhir import models as fhir
from infectious_disease_assistant.fhir.constants import (
    CULTURE_SYSTEM, ENCOUNTER_IDENTIFIER_SYSTEM, INTERPRETATION_SYSTEM,
    OBSERVATION_SYSTEM, ORGANISM_URL, REPORT_IDENTIFIER_SYSTEM, TREATMENT_URL,
)
from infectious_disease_assistant.fhir.errors import FhirParseError
from infectious_disease_assistant.models import patient as domain

T = TypeVar("T", bound=BaseModel)


def parse_resource(model: type[T], payload: dict, patient_reference: str | None = None) -> T:
    try:
        resource = model.model_validate(payload)
    except ValidationError as error:
        raise FhirParseError(f"Malformed or unsupported FHIR {model.__name__}") from error
    if patient_reference is not None:
        subject = getattr(resource, "patient", None) or getattr(resource, "subject", None)
        if subject is None or subject.reference != patient_reference:
            raise FhirParseError("FHIR resource belongs to a different patient")
    return resource


def concept_text(concept: fhir.CodeableConcept) -> str:
    text = concept.text or next((item.display for item in concept.coding if item.display), None)
    if not text or not text.strip():
        raise FhirParseError("FHIR coded concept has no supported display text")
    return text


def concept_code(concept: fhir.CodeableConcept, system: str) -> str:
    codes = [item.code for item in concept.coding if item.system == system]
    if len(codes) != 1:
        raise FhirParseError("Expected one code from the supported FHIR code system")
    return codes[0]


def business_identifier(identifiers: list[fhir.Identifier], system: str) -> str:
    values = [item.value for item in identifiers if item.system == system]
    if len(values) != 1:
        raise FhirParseError("Expected one synthetic business identifier")
    return values[0]


def as_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


def patient_age(patient: fhir.Patient, reference_date: date) -> int | None:
    if patient.birthDate is None:
        return None
    age = reference_date.year - patient.birthDate.year - (
        (reference_date.month, reference_date.day) < (patient.birthDate.month, patient.birthDate.day)
    )
    if not 0 <= age <= 130:
        raise FhirParseError("Patient birth date is outside the synthetic age range")
    return age


def medication(resource: fhir.MedicationRequest) -> domain.Medication:
    dosage = resource.dosageInstruction[0]
    quantity = dosage.doseAndRate[0].doseQuantity
    if resource.status not in {"active", "completed", "stopped"}:
        raise FhirParseError("Medication status is unsupported by the domain model")
    return domain.Medication(
        name=concept_text(resource.medicationCodeableConcept),
        dose=f"{quantity.value.normalize():f} {quantity.unit}",
        route=concept_text(dosage.route), frequency=concept_text(dosage.timing.code),
        status=resource.status,
    )


def allergy(resource: fhir.AllergyIntolerance) -> domain.Allergy:
    reactions = [concept_text(item) for reaction in resource.reaction for item in reaction.manifestation]
    severities = [item.severity for item in resource.reaction if item.severity is not None]
    severity = max(severities, key={"mild": 0, "moderate": 1, "severe": 2}.get) if severities else None
    return domain.Allergy(
        substance=concept_text(resource.code),
        reaction="; ".join(dict.fromkeys(reactions)) if reactions else None, severity=severity,
    )


def lab(resource: fhir.Observation) -> domain.LabResult:
    if resource.valueQuantity is None:
        raise FhirParseError("Numeric lab Observation requires valueQuantity")
    flags = [concept_code(item, INTERPRETATION_SYSTEM) for item in resource.interpretation]
    if len(flags) > 1 or any(flag not in {"L", "N", "H"} for flag in flags):
        raise FhirParseError("Unsupported lab interpretation")
    return domain.LabResult(
        name=concept_text(resource.code), value=float(resource.valueQuantity.value),
        unit=resource.valueQuantity.unit, collected_on=as_date(resource.effectiveDateTime),
        flag={"L": "low", "N": "normal", "H": "high"}.get(flags[0]) if flags else None,
    )


def microbiology(
    report: fhir.DiagnosticReport,
    specimen: fhir.Specimen,
    observations: list[fhir.Observation],
) -> domain.MicrobiologyResult:
    outcome = concept_code(report.conclusionCode[0], CULTURE_SYSTEM)
    if outcome not in {"growth", "no_growth"}:
        raise FhirParseError("Unsupported culture outcome")
    organism = None
    quantity = None
    susceptibilities = []
    for observation in observations:
        if observation.status not in {"final", "amended", "corrected"}:
            raise FhirParseError("Microbiology observations must be final")
        code = concept_code(observation.code, OBSERVATION_SYSTEM)
        if observation.valueCodeableConcept is None:
            raise FhirParseError("Microbiology observation requires a coded value")
        if code == "organism":
            if organism is not None:
                raise FhirParseError("Multiple organisms are unsupported by this demo")
            organism = concept_text(observation.valueCodeableConcept)
            for component in observation.component:
                if concept_code(component.code, OBSERVATION_SYSTEM) != "colony-count":
                    raise FhirParseError("Unsupported organism component")
                if quantity is not None or component.valueString is None:
                    raise FhirParseError("Invalid colony-count component")
                quantity = component.valueString
        elif code == "susceptibility":
            interpretation = concept_code(observation.valueCodeableConcept, INTERPRETATION_SYSTEM)
            if interpretation not in {"S", "I", "R"}:
                raise FhirParseError("Unsupported susceptibility interpretation")
            susceptibilities.append(domain.Susceptibility(
                antibiotic=concept_text(observation.code),
                interpretation={"S": "susceptible", "I": "intermediate", "R": "resistant"}[interpretation],
            ))
        else:
            raise FhirParseError("Unsupported microbiology observation")
    if (outcome == "growth" and organism is None) or (
        outcome == "no_growth" and (organism is not None or susceptibilities)
    ):
        raise FhirParseError("Culture outcome and observations disagree")
    return domain.MicrobiologyResult(
        result_id=business_identifier(report.identifier, REPORT_IDENTIFIER_SYSTEM),
        specimen=concept_text(specimen.type), collected_on=as_date(report.effectiveDateTime),
        outcome=outcome, organism=organism, quantity=quantity, susceptibilities=susceptibilities,
    )


def previous_infection(resource: fhir.Condition) -> domain.PreviousInfection:
    organisms = [item.valueString for item in resource.extension if item.url == ORGANISM_URL]
    treatments = [item.valueString for item in resource.extension if item.url == TREATMENT_URL]
    if len(organisms) != 1 or not organisms[0] or len(resource.bodySite) != 1 or len(treatments) > 1:
        raise FhirParseError("Infection history requires recorded organism and body site")
    return domain.PreviousInfection(
        diagnosis=concept_text(resource.code), organism=organisms[0],
        diagnosed_on=as_date(resource.onsetDateTime or resource.recordedDate),
        site=concept_text(resource.bodySite[0]), treatment=treatments[0] if treatments else None,
    )


def diagnosis(resource: fhir.Condition) -> domain.Diagnosis:
    status = concept_code(resource.clinicalStatus, "http://terminology.hl7.org/CodeSystem/condition-clinical")
    if status not in {"active", "resolved"}:
        raise FhirParseError("Unsupported diagnosis status")
    return domain.Diagnosis(name=concept_text(resource.code), recorded_on=as_date(resource.recordedDate), status=status)


def encounter(resource: fhir.Encounter) -> domain.Encounter:
    if resource.class_.code not in {"AMB", "EMER", "IMP"}:
        raise FhirParseError("Unsupported encounter class")
    return domain.Encounter(
        encounter_id=business_identifier(resource.identifier, ENCOUNTER_IDENTIFIER_SYSTEM),
        occurred_on=as_date(resource.period.start),
        setting={"AMB": "outpatient", "EMER": "emergency", "IMP": "inpatient"}[resource.class_.code],
        reason="; ".join(concept_text(item) for item in resource.reasonCode),
    )
