import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import TextIO

LOGGER = logging.getLogger("infectious_disease_assistant")
LOG_FIELDS = {
    "patient_id", "reviewer_id", "operation", "trace_id", "trace_url", "tool_name",
    "record_count", "duration_ms", "error_type",
}


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
