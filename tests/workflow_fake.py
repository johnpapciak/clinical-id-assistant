"""Offline test double; production always uses PostgreSQL."""
from uuid import uuid4

from infectious_disease_assistant.models.investigation import InvestigationResponse
from infectious_disease_assistant.models.workflow import ApprovalDecision, FollowupTask
from infectious_disease_assistant.workflow.audit import utc_now
from infectious_disease_assistant.workflow.errors import ReviewConflictError, WorkflowNotFoundError


class FakeWorkflowStore:
    def __init__(self):
        self.records = {}

    def begin(self, request, run):
        key = uuid4()
        self.records[key] = InvestigationResponse(
            investigation_id=key, **request.model_dump(), created_at=run.started_at,
            updated_at=run.started_at, agent_run=run.model_copy(deep=True),
        )
        return key

    def get(self, key):
        if key not in self.records:
            raise WorkflowNotFoundError()
        return self.records[key].model_copy(deep=True)

    def patient_id_for(self, key):
        if key not in self.records:
            raise WorkflowNotFoundError()
        return self.records[key].patient_id

    def pending(self, limit=50, *, patient_ids=None):
        if not 1 <= limit <= 100:
            raise ValueError('Pending review limit must be between 1 and 100')
        return [row.model_copy(deep=True) for row in self.records.values() if row.status == 'awaiting_review'
                and (patient_ids is None or row.patient_id in patient_ids)][:limit]

    def save_audit(self, run, events):
        row = next(row for row in self.records.values() if row.agent_run.agent_run_id == run.agent_run_id)
        row.agent_run = run.model_copy(deep=True)
        row.tool_events = [event.model_copy(deep=True) for event in events]

    def complete(self, key, summary, run, events):
        self.save_audit(run, events)
        row = self.records[key]
        row.status, row.summary_id, row.summary = 'awaiting_review', uuid4(), summary.model_copy(deep=True)
        row.updated_at = utc_now()
        return self.get(key)

    def fail(self, key, run, events):
        self.save_audit(run, events)
        self.records[key].status = 'failed'
        self.records[key].updated_at = utc_now()

    def review(self, key, user, *, reject=False, note=None, modified_action=None):
        row = self.get(key)
        action = None if reject else modified_action or (row.summary.proposed_action if row.summary else None)
        decision = 'rejected' if reject else 'modified' if modified_action is not None else 'approved'
        previous = row.approval
        if previous:
            if (previous.reviewer_id, previous.decision, previous.note, previous.approved_action) == (user.reviewer_id, decision, note, action):
                return row
            raise ReviewConflictError()
        if row.status != 'awaiting_review':
            raise ReviewConflictError()
        row.approval = ApprovalDecision(approval_id=uuid4(), reviewer_id=user.reviewer_id, decision=decision,
                                        note=note, decided_at=utc_now(), approved_action=action)
        if action:
            row.followup_task = FollowupTask(task_id=uuid4(), investigation_id=key, approval_id=row.approval.approval_id,
                                            patient_id=row.patient_id, title=action.title, description=action.description, created_at=utc_now())
        row.status, row.updated_at = ('rejected' if reject else 'approved'), utc_now()
        self.records[key] = row.model_copy(deep=True)
        return row
