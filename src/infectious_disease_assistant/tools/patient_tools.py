import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from agents import RunContextWrapper, function_tool
from pydantic import BaseModel

from infectious_disease_assistant.models.clinical_summary import ToolName
from infectious_disease_assistant.services import patients


@dataclass(frozen=True)
class PatientContext:
    patient_id: str
    reference_date: date
    tool_results: dict[ToolName, list[BaseModel]] = field(default_factory=dict)


def _serialize(context: PatientContext, tool_name: ToolName, records: Sequence[BaseModel]) -> str:
    context.tool_results[tool_name] = list(records)
    return json.dumps({
        "patient_id": context.patient_id,
        "reference_date": context.reference_date.isoformat(),
        "records": [record.model_dump(mode="json") for record in records],
    })


@function_tool(failure_error_function=None)
async def get_medications(context: RunContextWrapper[PatientContext]) -> str:
    """Read active prescriptions recorded for the selected synthetic patient."""
    return _serialize(context.context, "get_medications", patients.get_medications(context.context.patient_id))


@function_tool(failure_error_function=None)
async def get_allergies(context: RunContextWrapper[PatientContext]) -> str:
    """Read recorded medication allergies; empty means no entries recorded."""
    return _serialize(context.context, "get_allergies", patients.get_allergies(context.context.patient_id))


@function_tool(failure_error_function=None)
async def get_recent_labs(context: RunContextWrapper[PatientContext]) -> str:
    """Read labs from the inclusive 30-day lookback ending at the dataset reference date."""
    return _serialize(context.context, "get_recent_labs", patients.get_recent_labs(context.context.patient_id))


@function_tool(failure_error_function=None)
async def get_microbiology_results(context: RunContextWrapper[PatientContext]) -> str:
    """Read recent and historical cultures, newest first, through the reference date."""
    return _serialize(context.context, "get_microbiology_results", patients.get_microbiology_results(context.context.patient_id))


@function_tool(failure_error_function=None)
async def get_previous_infections(context: RunContextWrapper[PatientContext]) -> str:
    """Read explicit infection history before the reference date; cultures alone are not infections."""
    return _serialize(context.context, "get_previous_infections", patients.get_previous_infections(context.context.patient_id))


PATIENT_TOOLS = [
    get_medications,
    get_allergies,
    get_recent_labs,
    get_microbiology_results,
    get_previous_infections,
]
