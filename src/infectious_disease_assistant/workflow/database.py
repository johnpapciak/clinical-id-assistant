import hashlib
from contextlib import contextmanager
from importlib.resources import files
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from infectious_disease_assistant.config import DatabaseSettings
from infectious_disease_assistant.models.clinical_summary import ClinicalSummary
from infectious_disease_assistant.models.investigation import InvestigationRequest, InvestigationResponse
from infectious_disease_assistant.models.workflow import AgentRunRecord, CurrentUser, FollowupAction, ToolEvent
from infectious_disease_assistant.workflow.actions import create_followup_task
from infectious_disease_assistant.workflow.audit import utc_now
from infectious_disease_assistant.workflow.errors import ReviewConflictError, WorkflowDatabaseError, WorkflowNotFoundError


class WorkflowStore:
    def __init__(self, settings: DatabaseSettings | None = None):
        self.settings = settings

    @contextmanager
    def connection(self):
        try:
            settings = self.settings if self.settings is not None else DatabaseSettings()
            with psycopg.connect(settings.database_url.get_secret_value(), connect_timeout=5, row_factory=dict_row) as connection:
                yield connection
        except (psycopg.Error, ValidationError):
            raise WorkflowDatabaseError("Workflow database unavailable; check DATABASE_URL and run workflow init") from None

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(817214603)")
            connection.execute("""CREATE TABLE IF NOT EXISTS ida_workflow_migrations (
                version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now()
            )""")
            migrations = files("infectious_disease_assistant.workflow").joinpath("sql")
            for migration in sorted(migrations.iterdir(), key=lambda item: item.name):
                if not migration.name.endswith(".sql"):
                    continue
                sql = migration.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                previous = connection.execute("SELECT checksum FROM ida_workflow_migrations WHERE version = %s", (migration.name,)).fetchone()
                if previous:
                    if previous["checksum"] != checksum:
                        raise WorkflowDatabaseError("An applied workflow migration has changed")
                    continue
                connection.execute(sql)
                connection.execute("INSERT INTO ida_workflow_migrations (version, checksum) VALUES (%s, %s)", (migration.name, checksum))

    def _load(self, connection, investigation_id: UUID) -> InvestigationResponse:
        row = connection.execute("""
            SELECT i.*, s.summary_id, s.content AS summary,
                   to_jsonb(r) - 'investigation_id' AS agent_run,
                   COALESCE((SELECT jsonb_agg(to_jsonb(t) - 'agent_run_id' ORDER BY t.started_at, t.tool_event_id)
                             FROM ida_tool_events t WHERE t.agent_run_id = r.agent_run_id), '[]'::jsonb) AS tool_events,
                   (SELECT to_jsonb(a) - 'investigation_id' - 'summary_id' FROM ida_approval_decisions a
                    WHERE a.investigation_id = i.investigation_id) AS approval,
                   (SELECT to_jsonb(f) FROM ida_followup_tasks f WHERE f.investigation_id = i.investigation_id) AS followup_task
            FROM ida_investigations i JOIN ida_agent_runs r USING (investigation_id)
            LEFT JOIN ida_clinical_summaries s USING (investigation_id)
            WHERE i.investigation_id = %s
        """, (investigation_id,)).fetchone()
        if row is None:
            raise WorkflowNotFoundError("Investigation not found")
        return InvestigationResponse.model_validate(row)

    def get(self, investigation_id: UUID) -> InvestigationResponse:
        with self.connection() as connection:
            return self._load(connection, investigation_id)

    def patient_id_for(self, investigation_id: UUID) -> str:
        with self.connection() as connection:
            row = connection.execute("SELECT patient_id FROM ida_investigations WHERE investigation_id = %s",
                                     (investigation_id,)).fetchone()
            if row is None:
                raise WorkflowNotFoundError("Investigation not found")
            return row["patient_id"]

    def pending(self, limit: int = 50, *, patient_ids: frozenset[str] | None = None) -> list[InvestigationResponse]:
        if not 1 <= limit <= 100:
            raise ValueError("Pending review limit must be between 1 and 100")
        with self.connection() as connection:
            rows = connection.execute("""SELECT investigation_id FROM ida_investigations
                WHERE status = 'awaiting_review' AND (%s::text[] IS NULL OR patient_id = ANY(%s::text[]))
                ORDER BY created_at, investigation_id LIMIT %s FOR SHARE""",
                (None if patient_ids is None else sorted(patient_ids),
                 None if patient_ids is None else sorted(patient_ids), limit)).fetchall()
            return [self._load(connection, row["investigation_id"]) for row in rows]

    def begin(self, request: InvestigationRequest, run: AgentRunRecord) -> UUID:
        investigation_id = uuid4()
        with self.connection() as connection:
            connection.execute("""INSERT INTO ida_investigations
                (investigation_id, patient_id, microbiology_result_id, status, created_at, updated_at)
                VALUES (%s, %s, %s, 'running', %s, %s)""",
                (investigation_id, request.patient_id, request.microbiology_result_id, run.started_at, run.started_at))
            connection.execute("""INSERT INTO ida_agent_runs
                (agent_run_id, investigation_id, trace_id, status, started_at)
                VALUES (%s, %s, %s, 'running', %s)""", (run.agent_run_id, investigation_id, run.trace_id, run.started_at))
        return investigation_id

    def _save_audit(self, connection, run: AgentRunRecord, events: list[ToolEvent]) -> None:
        connection.execute("""UPDATE ida_agent_runs SET status = %s, completed_at = %s,
            model_name = %s, configuration_version = %s, error_type = %s WHERE agent_run_id = %s""",
            (run.status, run.completed_at, run.model_name, run.configuration_version, run.error_type, run.agent_run_id))
        for event in events:
            connection.execute("""INSERT INTO ida_tool_events
                (tool_event_id, agent_run_id, tool_call_id, tool_name, status, started_at, completed_at, record_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tool_event_id) DO UPDATE SET status = EXCLUDED.status,
                    completed_at = EXCLUDED.completed_at, record_count = EXCLUDED.record_count""",
                (event.tool_event_id, run.agent_run_id, event.tool_call_id, event.tool_name, event.status,
                 event.started_at, event.completed_at, event.record_count))

    def save_audit(self, run: AgentRunRecord, events: list[ToolEvent]) -> None:
        with self.connection() as connection:
            self._save_audit(connection, run, events)

    def complete(self, investigation_id: UUID, summary: ClinicalSummary, run: AgentRunRecord, events: list[ToolEvent]) -> InvestigationResponse:
        with self.connection() as connection:
            row = connection.execute("SELECT status FROM ida_investigations WHERE investigation_id = %s FOR UPDATE", (investigation_id,)).fetchone()
            if row is None or row["status"] != "running":
                raise ReviewConflictError("Only a running investigation can complete")
            self._save_audit(connection, run, events)
            connection.execute("""INSERT INTO ida_clinical_summaries
                (summary_id, investigation_id, agent_run_id, content, created_at) VALUES (%s, %s, %s, %s, %s)""",
                (uuid4(), investigation_id, run.agent_run_id, Jsonb(summary.model_dump(mode="json")), utc_now()))
            connection.execute("UPDATE ida_investigations SET status = 'awaiting_review', updated_at = %s WHERE investigation_id = %s", (utc_now(), investigation_id))
            return self._load(connection, investigation_id)

    def fail(self, investigation_id: UUID, run: AgentRunRecord, events: list[ToolEvent]) -> None:
        with self.connection() as connection:
            row = connection.execute("SELECT status FROM ida_investigations WHERE investigation_id = %s FOR UPDATE", (investigation_id,)).fetchone()
            if row and row["status"] == "running":
                self._save_audit(connection, run, events)
                connection.execute("UPDATE ida_investigations SET status = 'failed', updated_at = %s WHERE investigation_id = %s", (utc_now(), investigation_id))

    def review(self, investigation_id: UUID, user: CurrentUser, *, reject: bool = False,
               note: str | None = None, modified_action: FollowupAction | None = None) -> InvestigationResponse:
        with self.connection() as connection:
            connection.execute("SELECT investigation_id FROM ida_investigations WHERE investigation_id = %s FOR UPDATE", (investigation_id,))
            investigation = self._load(connection, investigation_id)
            action = None if reject else modified_action or (investigation.summary.proposed_action if investigation.summary else None)
            decision = "rejected" if reject else "modified" if modified_action is not None else "approved"
            previous = investigation.approval
            if previous is not None:
                if (previous.reviewer_id, previous.decision, previous.note, previous.approved_action) == (user.reviewer_id, decision, note, action):
                    return investigation
                raise ReviewConflictError("This investigation already has a different review decision")
            if investigation.status != "awaiting_review" or investigation.summary_id is None:
                raise ReviewConflictError("The investigation is not awaiting review")
            approval_id = uuid4()
            connection.execute("""INSERT INTO ida_approval_decisions
                (approval_id, investigation_id, summary_id, reviewer_id, decision, note, approved_action, decided_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (approval_id, investigation_id, investigation.summary_id, user.reviewer_id, decision, note,
                 Jsonb(action.model_dump(mode="json")) if action is not None else None, utc_now()))
            if action is not None:
                create_followup_task(connection, approval_id)
            connection.execute("UPDATE ida_investigations SET status = %s, updated_at = %s WHERE investigation_id = %s",
                               ("rejected" if reject else "approved", utc_now(), investigation_id))
            return self._load(connection, investigation_id)
