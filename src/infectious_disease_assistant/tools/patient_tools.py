import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from agents import function_tool
from agents.exceptions import ModelBehaviorError
from agents.tool_context import ToolContext
from pydantic import BaseModel

from infectious_disease_assistant.authorization import access_user, authorization_scope, authorize_patient
from infectious_disease_assistant.models.clinical_summary import ToolName
from infectious_disease_assistant.models.workflow import CurrentUser
from infectious_disease_assistant.services import patients


@dataclass(frozen=True)
class PatientContext:
    patient_id: str
    reference_date: date
    tool_results: dict[ToolName, list[BaseModel]] = field(default_factory=dict)
    user: CurrentUser = field(default_factory=access_user)


def _serialize(context: PatientContext, tool_name: ToolName, records: Sequence[BaseModel]) -> str:
    context.tool_results[tool_name] = list(records)
    return json.dumps({
        "patient_id": context.patient_id,
        "reference_date": context.reference_date.isoformat(),
        "records": [record.model_dump(mode="json") for record in records],
    })


async def _retrieve(context: ToolContext[PatientContext], tool_name: ToolName) -> str:
    if json.loads(context.tool_arguments) != {}:
        raise ModelBehaviorError("Patient tools accept no model-supplied arguments")
    patient = context.context
    with authorization_scope(patient.user):
        authorize_patient(patient.patient_id, operation=tool_name)
        patient.tool_results.setdefault(tool_name, [])
        records = await asyncio.to_thread(getattr(patients, tool_name), patient.patient_id)
        return _serialize(patient, tool_name, records)


@function_tool(failure_error_function=None)
async def get_medications(context: ToolContext[PatientContext]) -> str:
    """Read active prescriptions recorded for the selected synthetic patient."""
    return await _retrieve(context, "get_medications")


@function_tool(failure_error_function=None)
async def get_allergies(context: ToolContext[PatientContext]) -> str:
    """Read recorded medication allergies; empty means no entries recorded."""
    return await _retrieve(context, "get_allergies")


@function_tool(failure_error_function=None)
async def get_recent_labs(context: ToolContext[PatientContext]) -> str:
    """Read labs from the inclusive 30-day lookback ending at the dataset reference date."""
    return await _retrieve(context, "get_recent_labs")


@function_tool(failure_error_function=None)
async def get_microbiology_results(context: ToolContext[PatientContext]) -> str:
    """Read recent and historical cultures, newest first, through the reference date."""
    return await _retrieve(context, "get_microbiology_results")


@function_tool(failure_error_function=None)
async def get_previous_infections(context: ToolContext[PatientContext]) -> str:
    """Read explicit infection history before the reference date; cultures alone are not infections."""
    return await _retrieve(context, "get_previous_infections")


PATIENT_TOOLS = [
    get_medications,
    get_allergies,
    get_recent_labs,
    get_microbiology_results,
    get_previous_infections,
]
