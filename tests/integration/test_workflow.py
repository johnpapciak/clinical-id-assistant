from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from infectious_disease_assistant.api import create_app
from infectious_disease_assistant.guardrails import SAFE_FOLLOWUP
from infectious_disease_assistant.models.investigation import InvestigationRequest
from infectious_disease_assistant.models.workflow import AgentRunRecord, CurrentUser, FollowupAction
from infectious_disease_assistant.workflow.actions import create_followup_task
from infectious_disease_assistant.workflow.audit import utc_now
from infectious_disease_assistant.workflow.database import WorkflowStore
from infectious_disease_assistant.workflow.errors import ActionNotApprovedError, ReviewConflictError, WorkflowDatabaseError

pytestmark=pytest.mark.integration


@pytest.fixture
def pending(workflow_settings,make_clinical_summary):
    store=WorkflowStore(workflow_settings)
    summary=make_clinical_summary(tools=['get_microbiology_results'])
    summary.proposed_action=SAFE_FOLLOWUP.model_copy(deep=True)
    run=AgentRunRecord(trace_id='trace_'+uuid4().hex,started_at=utc_now(),model_name='test-model',configuration_version='a'*64)
    key=store.begin(InvestigationRequest(patient_id='PATIENT_001',microbiology_result_id='MICRO_001_RECENT'),run)
    run.status,run.completed_at='succeeded',utc_now()
    return store.complete(key,summary,run,[])


def test_http_agent_review_persists_across_app_instances(workflow_settings,agent_model,make_clinical_summary):
    from test_agent import final_response,tool_response
    expected=make_clinical_summary(tools=['get_microbiology_results'])
    expected.proposed_action=SAFE_FOLLOWUP.model_copy(deep=True)
    agent_model.side_effect=[tool_response(['get_microbiology_results']),final_response(expected)]
    with TestClient(create_app(WorkflowStore(workflow_settings))) as client:
        created=client.post('/investigations',json={'patient_id':'PATIENT_001','microbiology_result_id':'MICRO_001_RECENT'})
        assert created.status_code==201,created.text
        result=created.json(); key=result['investigation_id']
        assert result['status']=='awaiting_review'
        assert result['followup_task'] is None and result['approval'] is None
        assert result['agent_run']['model_name']=='test-model'
        assert len(result['agent_run']['configuration_version'])==64
        assert result['agent_run']['status']=='succeeded'
        assert result['tool_events'][0]['tool_name']=='get_microbiology_results'
        assert result['tool_events'][0]['status']=='completed'
        assert result['tool_events'][0]['record_count']==2
        assert set(result['tool_events'][0])=={'tool_event_id','tool_call_id','tool_name','status','started_at','completed_at','record_count'}
    with TestClient(create_app(WorkflowStore(workflow_settings))) as restarted:
        assert restarted.get(f'/investigations/{key}').json()==result
        assert restarted.get('/investigations/pending-review').json()[0]['investigation_id']==key
        approved=restarted.post(f'/investigations/{key}/approve',json={'note':'Synthetic case reviewed.'})
        assert approved.status_code==200,approved.text
        assert approved.json()['status']=='approved'
        assert approved.json()['followup_task']['simulated'] is True
        again=restarted.post(f'/investigations/{key}/approve',json={'note':'Synthetic case reviewed.'})
        assert again.json()==approved.json()
        assert restarted.get('/investigations/pending-review').json()==[]
        print({'investigation_id':key,'created_status':result['status'],'review_status':approved.json()['status'],
               'task_id':approved.json()['followup_task']['task_id'],'action':'create_followup_task','simulated':True})


def test_assignment_filtering_precedes_pending_limit_and_blocks_review(workflow_settings, pending, make_clinical_summary, sdk_traces):
    from infectious_disease_assistant.current_user import get_current_user
    store = WorkflowStore(workflow_settings)
    summary = make_clinical_summary('PATIENT_002', ['get_microbiology_results'])
    run = AgentRunRecord(trace_id='trace_'+uuid4().hex, started_at=utc_now())
    key = store.begin(InvestigationRequest(patient_id='PATIENT_002', microbiology_result_id='MICRO_002_RECENT'), run)
    run.status, run.completed_at = 'succeeded', utc_now()
    store.complete(key, summary, run, [])
    app = create_app(store)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        reviewer_id='REVIEWER_002', assigned_patient_ids={'PATIENT_002'},
    )
    with TestClient(app) as client:
        rows = client.get('/investigations/pending-review?limit=1')
        assert rows.status_code == 200
        assert [row['investigation_id'] for row in rows.json()] == [str(key)]
        assert client.get(f'/investigations/{key}').status_code == 200
        assert client.get(f'/investigations/{pending.investigation_id}').status_code == 403
        assert client.post(f'/investigations/{pending.investigation_id}/approve', json={}).status_code == 403
        assert client.post(f'/investigations/{pending.investigation_id}/reject', json={'note':'Not assigned'}).status_code == 403
    assert store.get(pending.investigation_id).approval is None


def test_concurrent_approvals_execute_one_task(workflow_settings,pending):
    user=CurrentUser(reviewer_id='REVIEWER_001'); barrier=Barrier(4)
    def approve():
        barrier.wait(timeout=10)
        return WorkflowStore(workflow_settings).review(pending.investigation_id,user)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:approve(),range(4)))
    assert len({row.followup_task.task_id for row in results})==1
    assert len({row.approval.approval_id for row in results})==1
    with WorkflowStore(workflow_settings).connection() as connection:
        assert connection.execute('SELECT count(*) AS n FROM ida_followup_tasks').fetchone()['n']==1
        assert connection.execute('SELECT count(*) AS n FROM ida_approval_decisions').fetchone()['n']==1


def test_rejection_and_conflicting_decision(workflow_settings,pending):
    store=WorkflowStore(workflow_settings); user=CurrentUser(reviewer_id='REVIEWER_002')
    rejected=store.review(pending.investigation_id,user,reject=True,note='Missing context.')
    assert rejected.status=='rejected' and rejected.followup_task is None
    with pytest.raises(ReviewConflictError):
        store.review(pending.investigation_id,user)
    with store.connection() as connection:
        with pytest.raises(ActionNotApprovedError):
            create_followup_task(connection,rejected.approval.approval_id)


def test_modified_action_and_original_proposal_are_both_preserved(workflow_settings,pending):
    action=FollowupAction(title='Clarify specimen context',description='Synthetic reviewer requests collection details.')
    result=WorkflowStore(workflow_settings).review(pending.investigation_id,CurrentUser(reviewer_id='REVIEWER_001'),modified_action=action)
    assert result.approval.decision=='modified'
    assert result.approval.approved_action==action
    assert result.summary.proposed_action==pending.summary.proposed_action
    assert result.followup_task.title==action.title


def test_task_failure_rolls_back_review_and_status(workflow_settings,pending,monkeypatch):
    def fail(connection,approval_id):
        raise RuntimeError('Simulated insert failure')
    monkeypatch.setattr('infectious_disease_assistant.workflow.database.create_followup_task',fail)
    with pytest.raises(RuntimeError):
        WorkflowStore(workflow_settings).review(pending.investigation_id,CurrentUser(reviewer_id='REVIEWER_001'))
    result=WorkflowStore(workflow_settings).get(pending.investigation_id)
    assert result.status=='awaiting_review' and result.approval is None and result.followup_task is None


def test_task_cannot_execute_before_approval(workflow_settings,pending):
    store=WorkflowStore(workflow_settings)
    with store.connection() as connection:
        with pytest.raises(ActionNotApprovedError):
            create_followup_task(connection,uuid4())
        assert connection.execute('SELECT count(*) AS n FROM ida_followup_tasks').fetchone()['n']==0
    assert store.get(pending.investigation_id).status=='awaiting_review'


def test_database_trigger_rejects_unapproved_task_payload(workflow_settings,pending):
    store=WorkflowStore(workflow_settings)
    result=store.review(pending.investigation_id,CurrentUser(reviewer_id='REVIEWER_001'),reject=True,note='Not approved.')
    with pytest.raises(WorkflowDatabaseError):
        with store.connection() as connection:
            connection.execute('''INSERT INTO ida_followup_tasks
                (task_id,investigation_id,approval_id,patient_id,title,description,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                (uuid4(),pending.investigation_id,result.approval.approval_id,pending.patient_id,'Attempted bypass','No approval',utc_now()))
    assert store.get(pending.investigation_id).followup_task is None


def test_failed_agent_run_retains_audit_without_summary_or_action(workflow_settings,agent_model):
    from agents.exceptions import ModelBehaviorError
    from test_agent import tool_response
    agent_model.side_effect=[tool_response(['get_microbiology_results']),ModelBehaviorError('synthetic-secret-key')]
    with TestClient(create_app(WorkflowStore(workflow_settings))) as client:
        response=client.post('/investigations',json={'patient_id':'PATIENT_001','microbiology_result_id':'MICRO_001_RECENT'})
        assert response.status_code==502
        assert 'synthetic-secret-key' not in response.text
    store=WorkflowStore(workflow_settings)
    with store.connection() as connection:
        key=connection.execute('SELECT investigation_id FROM ida_investigations').fetchone()['investigation_id']
    result=store.get(key)
    assert result.status=='failed' and result.agent_run.status=='failed'
    assert result.agent_run.error_type=='ModelBehaviorError'
    assert result.tool_events[0].status=='completed'
    assert result.summary is None and result.approval is None and result.followup_task is None
    assert store.pending()==[]


def test_interrupted_tool_is_persisted_when_dependency_fails(workflow_settings,agent_model,monkeypatch):
    from test_agent import tool_response
    from infectious_disease_assistant.fhir.errors import FhirTransportError
    from infectious_disease_assistant.services import patients
    def fail(patient_id):
        raise FhirTransportError('secret transport credentials')
    monkeypatch.setattr(patients,'get_recent_labs',fail)
    agent_model.side_effect=[tool_response(['get_microbiology_results']),tool_response(['get_recent_labs'],1)]
    with TestClient(create_app(WorkflowStore(workflow_settings))) as client:
        response=client.post('/investigations',json={'patient_id':'PATIENT_001','microbiology_result_id':'MICRO_001_RECENT'})
        assert response.status_code==503
        assert 'secret transport credentials' not in response.text
    store=WorkflowStore(workflow_settings)
    with store.connection() as connection:
        key=connection.execute('SELECT investigation_id FROM ida_investigations').fetchone()['investigation_id']
    result=store.get(key)
    assert result.status=='failed'
    assert [event.status for event in result.tool_events]==['completed','interrupted']
    assert all(event.completed_at is not None for event in result.tool_events)


def test_racing_approval_and_rejection_have_one_terminal_decision(workflow_settings,pending):
    user=CurrentUser(reviewer_id='REVIEWER_001'); barrier=Barrier(2)
    def review(reject):
        barrier.wait(timeout=10)
        try:
            return WorkflowStore(workflow_settings).review(pending.investigation_id,user,reject=reject,note='Reviewed.')
        except ReviewConflictError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(review,[False,True]))
    winner=next(result for result in results if result is not None)
    assert sum(result is not None for result in results)==1
    assert (winner.followup_task is not None)==(winner.status=='approved')
    with WorkflowStore(workflow_settings).connection() as connection:
        assert connection.execute('SELECT count(*) AS n FROM ida_approval_decisions').fetchone()['n']==1
        assert connection.execute('SELECT count(*) AS n FROM ida_followup_tasks').fetchone()['n']==(1 if winner.status=='approved' else 0)
