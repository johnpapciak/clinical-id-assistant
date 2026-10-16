import pytest
from psycopg import sql

from infectious_disease_assistant.rag.database import GuidelineStore
from infectious_disease_assistant.rag.errors import GuidelineDatabaseError
from infectious_disease_assistant.workflow.database import WorkflowStore
from infectious_disease_assistant.workflow.errors import WorkflowDatabaseError

pytestmark = pytest.mark.integration


def test_reinitializing_both_migration_sets_preserves_ledgers(postgres_settings, workflow_settings):
    guideline, workflow = GuidelineStore(postgres_settings), WorkflowStore(workflow_settings)
    with workflow.connection() as connection:
        before = {ledger: connection.execute(sql.SQL("SELECT * FROM {} ORDER BY version").format(sql.Identifier(ledger))).fetchall()
                  for ledger in ["ida_guideline_migrations", "ida_workflow_migrations"]}
    assert all(before.values())
    guideline.initialize()
    workflow.initialize()
    with workflow.connection() as connection:
        after = {ledger: connection.execute(sql.SQL("SELECT * FROM {} ORDER BY version").format(sql.Identifier(ledger))).fetchall()
                 for ledger in before}
    assert after == before


@pytest.mark.parametrize("area,ledger,error", [
    ("guideline", "ida_guideline_migrations", GuidelineDatabaseError),
    ("workflow", "ida_workflow_migrations", WorkflowDatabaseError),
])
def test_applied_migration_changes_are_rejected(postgres_settings, workflow_settings, area, ledger, error):
    store = GuidelineStore(postgres_settings) if area == "guideline" else WorkflowStore(workflow_settings)
    with store.connection() as connection:
        connection.execute(sql.SQL("UPDATE {} SET checksum = %s").format(sql.Identifier(ledger)), ("changed",))
    with pytest.raises(error, match="has changed"):
        store.initialize()
