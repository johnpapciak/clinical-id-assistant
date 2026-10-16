from unittest.mock import MagicMock, Mock

import pytest
from pydantic import ValidationError

from infectious_disease_assistant.agent import agent_configuration_version
from infectious_disease_assistant.models.clinical_summary import ClinicalSummary
from infectious_disease_assistant.models.workflow import AgentRunRecord, FollowupAction
from infectious_disease_assistant.workflow.audit import RunAudit, utc_now
from infectious_disease_assistant.workflow.database import WorkflowStore
from infectious_disease_assistant.workflow.errors import WorkflowDatabaseError


def test_audit_records_only_high_level_events_and_finishes_interrupted_calls():
    persist=Mock()
    audit=RunAudit(AgentRunRecord(trace_id='trace_'+'a'*32,started_at=utc_now()),persist)
    audit.configure('configured-model','b'*64)
    audit.tool_started('call-a','get_microbiology_results')
    audit.tool_completed('call-a',2)
    audit.tool_started('call-b','get_recent_labs')
    audit.finish(RuntimeError('secret exception payload'))
    assert audit.run.status=='failed'
    assert audit.run.model_name=='configured-model'
    assert audit.run.error_type=='RuntimeError'
    assert audit.run.completed_at>=audit.run.started_at
    assert audit.events[0].status=='completed'
    assert audit.events[0].record_count==2
    assert audit.events[1].status=='interrupted'
    assert audit.events[1].completed_at is not None
    assert 'secret exception payload' not in audit.run.model_dump_json()
    assert persist.call_count==4
    assert not {'arguments','results','resources','api_key'} & audit.events[0].model_dump().keys()


def test_configuration_fingerprint_is_stable_and_changes_with_policy(monkeypatch):
    first=agent_configuration_version()
    assert len(first)==64 and first==agent_configuration_version()
    monkeypatch.setattr('infectious_disease_assistant.agent.MAX_AGENT_TURNS',99)
    assert first!=agent_configuration_version()


def test_proposal_is_typed_and_execution_not_part_of_summary():
    action=FollowupAction(title='Review synthetic specimen',description='Clarify specimen collection context.')
    summary=ClinicalSummary(patient_id='PATIENT_001',proposed_action=action)
    assert ClinicalSummary.model_validate_json(summary.model_dump_json()).proposed_action==action
    assert 'approval' not in summary.model_dump()
    with pytest.raises(ValidationError):
        FollowupAction(kind='send_real_prescription',title='Invalid capability',description='Unsupported')


def test_database_failure_hides_connection_details(monkeypatch,rag_settings):
    import psycopg
    monkeypatch.setattr('infectious_disease_assistant.workflow.database.psycopg.connect',Mock(side_effect=psycopg.OperationalError('secret password')))
    with pytest.raises(WorkflowDatabaseError) as caught:
        WorkflowStore(rag_settings).initialize()
    assert 'secret password' not in str(caught.value)


def test_workflow_migration_is_packaged_and_idempotent(monkeypatch,rag_settings):
    from hashlib import sha256
    from importlib.resources import files
    connection=Mock()
    factory=MagicMock()
    factory.return_value.__enter__.return_value=connection
    monkeypatch.setattr('infectious_disease_assistant.workflow.database.psycopg.connect',factory)
    migration=files('infectious_disease_assistant.workflow').joinpath('sql/001_workflow.sql').read_text()
    connection.execute.return_value.fetchone.return_value={'checksum':sha256(migration.encode()).hexdigest()}
    WorkflowStore(rag_settings).initialize()
    assert not any('CREATE TABLE ida_investigations' in call.args[0] for call in connection.execute.call_args_list)
    connection.execute.return_value.fetchone.return_value={'checksum':'changed'}
    with pytest.raises(WorkflowDatabaseError,match='has changed'):
        WorkflowStore(rag_settings).initialize()
