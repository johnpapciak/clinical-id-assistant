import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TextIO

from agents import Agent, RunContextWrapper, RunHooks, Tool
from agents.items import ModelResponse
from pydantic import BaseModel

from infectious_disease_assistant.models.clinical_summary import ToolName
from infectious_disease_assistant.tools.patient_tools import PatientContext
from infectious_disease_assistant.workflow.audit import RunAudit

LOGGER = logging.getLogger("infectious_disease_assistant")
LOG_FIELDS = {
    "patient_id", "reviewer_id", "operation", "trace_id", "trace_url", "tool_name",
    "record_count", "duration_ms", "error_type",
}


@dataclass
class ToolCallObservation:
    tool_call_id: str
    tool_name: str
    arguments_json: str
    completed: bool = False


@dataclass
class ExecutionObservation:
    tool_calls: list[ToolCallObservation] = field(default_factory=list)
    tool_results: dict[ToolName, list[BaseModel]] = field(default_factory=dict)
    model_name: str | None = None
    latency_ms: float | None = None
    error_type: str | None = None
    usage: dict[str, int] | None = None

    def capture_usage(self, usage: Any) -> None:
        self.usage = {name: getattr(usage, name) for name in ("requests", "input_tokens", "output_tokens", "total_tokens")}


class JsonEventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = record.getMessage()
        return json.dumps({
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "event": event if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", event) else "application_event",
            **{key: value for key, value in getattr(record, "event_fields", {}).items() if key in LOG_FIELDS},
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
    def __init__(self, trace_id: str, audit: RunAudit | None = None, observation: ExecutionObservation | None = None):
        self.trace_id = trace_id
        self.audit = audit
        self.observation = observation

    async def on_llm_end(
        self, context: RunContextWrapper[PatientContext], agent: Agent[PatientContext], response: ModelResponse,
    ) -> None:
        if self.observation is not None:
            self.observation.capture_usage(context.usage)

    async def on_tool_start(
        self, context: RunContextWrapper[PatientContext], agent: Agent[PatientContext], tool: Tool,
    ) -> None:
        if self.observation is not None:
            self.observation.tool_calls.append(ToolCallObservation(context.tool_call_id, tool.name, context.tool_arguments))
            self.observation.capture_usage(context.usage)
        if self.audit is not None:
            self.audit.tool_started(context.tool_call_id, tool.name)
        log_event(
            "tool_requested", patient_id=context.context.patient_id,
            trace_id=self.trace_id, tool_name=tool.name,
        )

    async def on_tool_end(
        self, context: RunContextWrapper[PatientContext], agent: Agent[PatientContext],
        tool: Tool, result: object,
    ) -> None:
        if self.observation is not None:
            call = next(item for item in self.observation.tool_calls if item.tool_call_id == context.tool_call_id)
            call.completed = True
            self.observation.capture_usage(context.usage)
        if self.audit is not None:
            self.audit.tool_completed(context.tool_call_id, len(context.context.tool_results.get(tool.name, ())))
        log_event(
            "tool_completed", patient_id=context.context.patient_id,
            trace_id=self.trace_id, tool_name=tool.name,
            record_count=len(context.context.tool_results.get(tool.name, ())),
        )

    async def on_agent_end(self, context: RunContextWrapper[PatientContext], agent: Agent[PatientContext], output: Any) -> None:
        if self.observation is not None:
            self.observation.capture_usage(context.usage)
