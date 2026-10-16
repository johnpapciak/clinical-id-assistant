from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from infectious_disease_assistant.models.workflow import AgentRunRecord, ToolEvent


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RunAudit:
    run: AgentRunRecord
    persist: Callable[[AgentRunRecord, list[ToolEvent]], None]
    events: list[ToolEvent] = field(default_factory=list)

    def configure(self, model: str, version: str) -> None:
        self.run.model_name, self.run.configuration_version = model, version
        self.persist(self.run, self.events)

    def tool_started(self, call_id: str, name: str) -> None:
        self.events.append(ToolEvent(tool_call_id=call_id, tool_name=name, started_at=utc_now()))
        self.persist(self.run, self.events)

    def tool_completed(self, call_id: str, count: int) -> None:
        event = next(item for item in self.events if item.tool_call_id == call_id)
        event.status, event.completed_at, event.record_count = "completed", utc_now(), count
        self.persist(self.run, self.events)

    def finish(self, error: Exception | None = None) -> None:
        self.run.status = "failed" if error is not None else "succeeded"
        self.run.completed_at = utc_now()
        self.run.error_type = type(error).__name__ if error is not None else None
        for event in self.events:
            if event.status == "running":
                event.status, event.completed_at = "interrupted", self.run.completed_at
