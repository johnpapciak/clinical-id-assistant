import argparse
import sys

from pydantic import ValidationError

from infectious_disease_assistant.fhir.errors import FhirError
from infectious_disease_assistant.services.patients import (
    InvalidPatientIdError,
    PatientNotFoundError,
    get_patient_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Look up deterministic synthetic patient data.")
    parser.add_argument("patient_id", nargs="?", help="Synthetic ID, for example PATIENT_001")
    args = parser.parse_args()
    try:
        patient_id = args.patient_id if args.patient_id is not None else input("Patient ID: ")
        summary = get_patient_summary(patient_id)
    except (InvalidPatientIdError, PatientNotFoundError, FhirError, ValidationError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except EOFError:
        print("Error: a patient ID is required", file=sys.stderr)
        return 1
    print(summary.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
