import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import TextIO

from agents import Agent, RunContextWrapper, RunHooks, Tool

from infectious_disease_assistant.tools.patient_tools import PatientContext

LOGGER = logging.getLogger("infectious_disease_assistant")


class JsonEventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            **getattr(record, "event_fields", {}),
        }, sort_keys=True)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonEventFormatter())
    LOGGER.handlers = [handler]
    LOGGER.setLevel(level)
    LOGGER.propagate = False


def trace_url(trace_id: str) -> str:
    if re.fullmatch(r"trace_[a-zA-Z0-9]{32}", trace_id) is None:
        raise ValueError("Invalid SDK trace ID")
    return f"https://platform.openai.com/traces/trace?trace_id={trace_id}"


def log_event(
    event: str,
    *,
    patient_id: str | None,
    trace_id: str,
    tool_name: str | None = None,
    record_count: int | None = None,
    duration_ms: float | None = None,
    error_type: str | None = None,
    completed: bool = False,
) -> None:
    fields: dict[str, object] = {"patient_id": patient_id, "trace_id": trace_id}
    for name, value in {
        "tool_name": tool_name, "record_count": record_count,
        "duration_ms": duration_ms, "error_type": error_type,
    }.items():
        if value is not None:
            fields[name] = value
    if completed:
        fields["trace_url"] = trace_url(trace_id)
    LOGGER.log(logging.ERROR if error_type else logging.INFO, event, extra={"event_fields": fields})


class InvestigationHooks(RunHooks[PatientContext]):
    def __init__(self, trace_id: str):
        self.trace_id = trace_id

    async def on_tool_start(
        self, context: RunContextWrapper[PatientContext], agent: Agent[PatientContext], tool: Tool,
    ) -> None:
        log_event(
            "tool_requested", patient_id=context.context.patient_id,
            trace_id=self.trace_id, tool_name=tool.name,
        )

    async def on_tool_end(
        self, context: RunContextWrapper[PatientContext], agent: Agent[PatientContext],
        tool: Tool, result: object,
    ) -> None:
        log_event(
            "tool_completed", patient_id=context.context.patient_id,
            trace_id=self.trace_id, tool_name=tool.name,
            record_count=len(context.context.tool_results.get(tool.name, ())),
        )
