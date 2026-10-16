from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from infectious_disease_assistant.api import create_app
from infectious_disease_assistant.guardrails import SAFE_FOLLOWUP
from infectious_disease_assistant.current_user import get_current_user
from infectious_disease_assistant.models.workflow import CurrentUser, FollowupAction
from infectious_disease_assistant.services import investigations
from infectious_disease_assistant.workflow.actions import create_followup_task
from infectious_disease_assistant.workflow.errors import ActionNotApprovedError


@pytest.fixture
def client(workflow_store, monkeypatch, make_clinical_summary, sdk_traces):
    summary = make_clinical_summary(tools=["get_microbiology_results"])
    summary.proposed_action = SAFE_FOLLOWUP.model_copy(deep=True)
    monkeypatch.setattr(investigations, "investigate_patient", AsyncMock(return_value=summary))
    app = create_app(store=workflow_store)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        reviewer_id="REVIEWER_002", assigned_patient_ids={"PATIENT_001"},
    )
    with TestClient(app) as client:
        yield client


def create(client):
    response = client.post('/investigations', json={'patient_id':'PATIENT_001', 'microbiology_result_id':'MICRO_001_RECENT'})
    assert response.status_code == 201
    return response.json()


def test_pending_review_has_proposal_without_execution(client):
    result = create(client)
    assert result['status'] == 'awaiting_review'
    assert result['summary']['proposed_action']['kind'] == 'create_followup_task'
    assert result['approval'] is None and result['followup_task'] is None
    pending = client.get('/investigations/pending-review')
    assert pending.status_code == 200
    assert pending.json()[0]['investigation_id'] == result['investigation_id']


def test_approval_records_reviewer_then_creates_simulated_task(client):
    result = create(client)
    approved = client.post(f"/investigations/{result['investigation_id']}/approve", json={'note':'Synthetic case reviewed.'})
    assert approved.status_code == 200
    body = approved.json()
    assert body['status'] == 'approved'
    assert body['approval']['decision'] == 'approved'
    assert body['approval']['reviewer_id'] == 'REVIEWER_002'
    assert body['followup_task']['simulated'] is True
    assert body['followup_task']['approval_id'] == body['approval']['approval_id']
    assert body['followup_task']['created_at'] >= body['approval']['decided_at']
    assert client.get('/investigations/pending-review').json() == []
    assert client.get(f"/investigations/{result['investigation_id']}").json() == body


def test_repeated_approval_returns_same_task_and_decision(client):
    result = create(client); path=f"/investigations/{result['investigation_id']}/approve"
    first=client.post(path,json={}); second=client.post(path,json={})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_rejection_never_creates_task_and_cannot_later_approve(client):
    result=create(client); base=f"/investigations/{result['investigation_id']}"
    rejected=client.post(base+'/reject',json={'note':'Insufficient synthetic context.'})
    assert rejected.status_code == 200
    assert rejected.json()['status'] == 'rejected'
    assert rejected.json()['approval']['decision'] == 'rejected'
    assert rejected.json()['followup_task'] is None
    assert client.post(base+'/approve',json={}).status_code == 409
    assert client.post(base+'/reject',json={'note':'Insufficient synthetic context.'}).json() == rejected.json()


def test_modified_action_retains_original_proposal(client):
    result=create(client); base=f"/investigations/{result['investigation_id']}"
    modified={'kind':'create_followup_task','title':'Clarify synthetic specimen context', 'description':'Reviewer requests specimen collection context.'}
    reviewed=client.post(base+'/approve',json={'modified_action':modified,'note':'Refined the follow-up question.'})
    assert reviewed.status_code == 200
    row=reviewed.json()
    assert row['approval']['decision'] == 'modified'
    assert row['approval']['approved_action'] == modified
    assert row['summary']['proposed_action'] == result['summary']['proposed_action']
    assert row['followup_task']['title'] == modified['title']
    assert client.post(base+'/approve',json={}).status_code == 409


def test_identity_is_dependency_not_request_field(client):
    result=create(client)
    response=client.post(f"/investigations/{result['investigation_id']}/approve",json={'reviewer_id':'REVIEWER_999'})
    assert response.status_code == 422
    assert client.get(f"/investigations/{result['investigation_id']}").json()['status'] == 'awaiting_review'


@pytest.mark.parametrize('path,body', [('/reject',{}),('/reject',{'note':' '}),('/approve',{'modified_action':{'kind':'send_real_order'}})])
def test_invalid_review_never_changes_state(client,path,body):
    result=create(client); base=f"/investigations/{result['investigation_id']}"
    assert client.post(base+path,json=body).status_code == 422
    assert client.get(base).json()['approval'] is None


def test_pending_limit_validation(client):
    assert client.get('/investigations/pending-review?limit=0').status_code == 422


def test_current_user_uses_server_configuration(monkeypatch):
    monkeypatch.setenv('IDA_CURRENT_USER_ID','REVIEWER_003')
    assert get_current_user().reviewer_id == 'REVIEWER_003'


def test_action_function_requires_an_existing_approval():
    from unittest.mock import Mock
    from uuid import uuid4
    connection=Mock(); connection.execute.return_value.fetchone.return_value=None
    with pytest.raises(ActionNotApprovedError):
        create_followup_task(connection,uuid4())
    assert connection.execute.call_count == 1


def test_approval_without_proposal_creates_no_task(client,monkeypatch,make_clinical_summary):
    monkeypatch.setattr(investigations,'investigate_patient',AsyncMock(return_value=make_clinical_summary(tools=['get_microbiology_results'])))
    result=create(client)
    approved=client.post(f"/investigations/{result['investigation_id']}/approve",json={})
    assert approved.status_code==200
    assert approved.json()['status']=='approved'
    assert approved.json()['followup_task'] is None


def test_review_requires_summary_to_be_ready(client,workflow_store):
    from infectious_disease_assistant.models.investigation import InvestigationRequest
    from infectious_disease_assistant.models.workflow import AgentRunRecord
    from infectious_disease_assistant.workflow.audit import utc_now
    key=workflow_store.begin(InvestigationRequest(patient_id='PATIENT_001',microbiology_result_id='MICRO_001_RECENT'),
                             AgentRunRecord(trace_id='trace_'+'a'*32,started_at=utc_now()))
    assert client.post(f'/investigations/{key}/approve',json={}).status_code==409
    assert workflow_store.get(key).followup_task is None
