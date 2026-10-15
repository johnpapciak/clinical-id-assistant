import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from agents import RunContextWrapper, function_tool
from pydantic import BaseModel

from infectious_disease_assistant.services import patients


@dataclass(frozen=True)
class PatientContext:
    patient_id: str
    reference_date: date


def _serialize(context: PatientContext, records: Sequence[BaseModel]) -> str:
    return json.dumps({
        "patient_id": context.patient_id,
        "reference_date": context.reference_date.isoformat(),
        "records": [record.model_dump(mode="json") for record in records],
    })


@function_tool(failure_error_function=None)
async def get_medications(context: RunContextWrapper[PatientContext]) -> str:
    """Read active prescriptions recorded for the selected synthetic patient."""
    return _serialize(context.context, patients.get_medications(context.context.patient_id))


@function_tool(failure_error_function=None)
async def get_allergies(context: RunContextWrapper[PatientContext]) -> str:
    """Read recorded medication allergies; empty means no entries recorded."""
    return _serialize(context.context, patients.get_allergies(context.context.patient_id))


@function_tool(failure_error_function=None)
async def get_recent_labs(context: RunContextWrapper[PatientContext]) -> str:
    """Read labs from the inclusive 30-day lookback ending at the dataset reference date."""
    return _serialize(context.context, patients.get_recent_labs(context.context.patient_id))


@function_tool(failure_error_function=None)
async def get_microbiology_results(context: RunContextWrapper[PatientContext]) -> str:
    """Read recent and historical cultures, newest first, through the reference date."""
    return _serialize(context.context, patients.get_microbiology_results(context.context.patient_id))


@function_tool(failure_error_function=None)
async def get_previous_infections(context: RunContextWrapper[PatientContext]) -> str:
    """Read explicit infection history before the reference date; cultures alone are not infections."""
    return _serialize(context.context, patients.get_previous_infections(context.context.patient_id))


PATIENT_TOOLS = [
    get_medications,
    get_allergies,
    get_recent_labs,
    get_microbiology_results,
    get_previous_infections,
]
