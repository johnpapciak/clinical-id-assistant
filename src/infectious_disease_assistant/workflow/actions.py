from uuid import UUID, uuid4

from infectious_disease_assistant.models.workflow import FollowupTask
from infectious_disease_assistant.workflow.audit import utc_now
from infectious_disease_assistant.workflow.errors import ActionNotApprovedError


def create_followup_task(connection, approval_id: UUID) -> FollowupTask:
    approval = connection.execute("""
        SELECT a.*, i.patient_id FROM ida_approval_decisions a
        JOIN ida_investigations i USING (investigation_id)
        WHERE approval_id = %s AND decision IN ('approved', 'modified')
          AND approved_action IS NOT NULL
    """, (approval_id,)).fetchone()
    if approval is None or approval["approved_action"].get("kind") != "create_followup_task":
        raise ActionNotApprovedError("A recorded approval is required")
    action = approval["approved_action"]
    connection.execute("""
        INSERT INTO ida_followup_tasks
            (task_id, investigation_id, approval_id, patient_id, title, description, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (approval_id) DO NOTHING
    """, (uuid4(), approval["investigation_id"], approval_id, approval["patient_id"],
          action["title"], action["description"], utc_now()))
    row = connection.execute("SELECT * FROM ida_followup_tasks WHERE approval_id = %s", (approval_id,)).fetchone()
    return FollowupTask.model_validate(row)
