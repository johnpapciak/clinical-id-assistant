import asyncio
import io
import json
import logging
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, Mock

import pytest
from agents.exceptions import ModelBehaviorError
from agents.tool_context import ToolContext
from fastapi.testclient import TestClient
from pydantic import ValidationError

from infectious_disease_assistant.agent import agent_configuration_version, investigate_patient
from infectious_disease_assistant.api import create_app
from infectious_disease_assistant.authorization import (
    PatientAccessDeniedError, access_user, authorization_scope, authorize_patient,
)
from infectious_disease_assistant.config import Settings
from infectious_disease_assistant.current_user import get_current_user
from infectious_disease_assistant.models.investigation import InvestigationRequest
from infectious_disease_assistant.models.workflow import ApproveRequest, CurrentUser, RejectRequest
from infectious_disease_assistant.observability import JsonEventFormatter, configure_logging
from infectious_disease_assistant.services import patients
from infectious_disease_assistant.services.investigations import InvestigationService
from infectious_disease_assistant.tools.patient_tools import PATIENT_TOOLS, PatientContext

USER = CurrentUser(reviewer_id='REVIEWER_001', assigned_patient_ids={'PATIENT_001'})
OTHER = CurrentUser(reviewer_id='REVIEWER_002', assigned_patient_ids={'PATIENT_002'})
SENSITIVE = ('Escherichia coli', 'cephalexin', 'urticaria', 'test-key-not-real', 'synthetic-secret-password')
GETTERS = [patients.get_patient_context, patients.get_patient_summary,
           *[getattr(patients, tool.name) for tool in PATIENT_TOOLS]]


def context(tool, user=USER, patient_id='PATIENT_001', arguments='{}'):
    from datetime import date
    return ToolContext(context=PatientContext(patient_id, date(2026, 10, 15), user=user),
                       tool_name=tool.name, tool_call_id='test-call', tool_arguments=arguments)


def test_default_assignment_is_limited_and_unconfigured_users_are_denied(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('IDA_PATIENT_ASSIGNMENTS')
    assert get_current_user() == USER
    monkeypatch.setenv('IDA_CURRENT_USER_ID', 'REVIEWER_999')
    assert get_current_user().assigned_patient_ids == frozenset()
    with pytest.raises(PatientAccessDeniedError):
        patients.get_patient_summary('PATIENT_001')


def test_assignments_are_validated_from_environment(monkeypatch):
    monkeypatch.setenv('IDA_PATIENT_ASSIGNMENTS', '{"REVIEWER_002":["PATIENT_002"]}')
    monkeypatch.setenv('IDA_CURRENT_USER_ID', 'REVIEWER_002')
    assert get_current_user() == OTHER
    monkeypatch.setenv('IDA_PATIENT_ASSIGNMENTS', '{"REVIEWER_002":["synthetic-secret-password"]}')
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert 'synthetic-secret-password' not in str(caught.value)


def test_identity_is_immutable_and_defaults_to_no_access():
    assert CurrentUser(reviewer_id='REVIEWER_003').assigned_patient_ids == frozenset()
    with pytest.raises(ValidationError):
        USER.reviewer_id = 'REVIEWER_003'


@pytest.mark.parametrize('getter', GETTERS, ids=lambda getter: getter.__name__)
def test_permitted_service_access_succeeds(getter, fhir_api):
    with authorization_scope(USER):
        assert getter('PATIENT_001') is not None
    assert fhir_api.requests


@pytest.mark.parametrize('getter', GETTERS, ids=lambda getter: getter.__name__)
def test_direct_services_deny_access_before_any_http(getter, fhir_api):
    with authorization_scope(USER), pytest.raises(PatientAccessDeniedError):
        getter('PATIENT_002')
    assert not fhir_api.requests


@pytest.mark.parametrize('tool', PATIENT_TOOLS, ids=lambda tool: tool.name)
def test_tools_authorize_before_even_calling_the_domain_service(tool, monkeypatch, fhir_api):
    lookup = Mock()
    monkeypatch.setattr(patients, tool.name, lookup)
    ctx = context(tool, patient_id='PATIENT_002')
    with pytest.raises(PatientAccessDeniedError):
        asyncio.run(tool.on_invoke_tool(ctx, '{}'))
    lookup.assert_not_called()
    assert ctx.context.tool_results == {}
    assert not fhir_api.requests


@pytest.mark.parametrize('tool', PATIENT_TOOLS, ids=lambda tool: tool.name)
def test_tool_cannot_override_patient_or_identity_in_arguments(tool, fhir_api):
    arguments = '{"patient_id":"PATIENT_002","user":{"reviewer_id":"REVIEWER_999"}}'
    with pytest.raises(ModelBehaviorError):
        asyncio.run(tool.on_invoke_tool(context(tool, arguments=arguments), arguments))
    assert not fhir_api.requests


def test_tool_context_keeps_trusted_principal_and_does_not_serialize_it(fhir_api):
    tool = PATIENT_TOOLS[0]
    ctx = context(tool)
    with authorization_scope(OTHER):
        payload = asyncio.run(tool.on_invoke_tool(ctx, '{}'))
    assert json.loads(payload)['patient_id'] == 'PATIENT_001'
    assert 'assigned_patient_ids' not in payload and 'reviewer_id' not in payload
    assert 'Patient/patient-001' not in payload and 'MedicationRequest/' not in payload
    with pytest.raises(FrozenInstanceError):
        ctx.context.user = OTHER


def test_authorization_context_survives_threads_and_isolates_concurrent_tasks(fhir_api):
    async def check(user, allowed, denied):
        with authorization_scope(user):
            await asyncio.sleep(0)
            result = await asyncio.to_thread(patients.get_patient_context, allowed)
            with pytest.raises(PatientAccessDeniedError):
                await asyncio.to_thread(patients.get_patient_context, denied)
            assert access_user() == user
            return result.patient_id
    async def run():
        return await asyncio.gather(check(USER, 'PATIENT_001', 'PATIENT_002'),
                                    check(OTHER, 'PATIENT_002', 'PATIENT_001'))
    before = access_user()
    assert asyncio.run(run()) == ['PATIENT_001', 'PATIENT_002']
    assert access_user() == before


@pytest.fixture
def client(monkeypatch, make_clinical_summary, workflow_store, sdk_traces):
    coordinator = AsyncMock(return_value=make_clinical_summary(tools=['get_microbiology_results']))
    monkeypatch.setattr('infectious_disease_assistant.services.investigations.investigate_patient', coordinator)
    app = create_app(workflow_store)
    app.dependency_overrides[get_current_user] = lambda: USER
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, coordinator


def test_api_denies_before_fhir_agent_or_database_and_ignores_identity_headers(client, fhir_api, workflow_store):
    http, coordinator = client
    fhir_api.requests.clear()
    response = http.post('/investigations', json={'patient_id':'PATIENT_002','microbiology_result_id':'MICRO_002_RECENT'},
                         headers={'X-User-ID': 'REVIEWER_002', 'X-Patient-IDs': 'PATIENT_002'})
    assert response.status_code == 403
    assert response.json() == {'code':'patient_access_denied', 'message':'Access to this synthetic patient is denied.', 'issues':[]}
    assert not fhir_api.requests and not workflow_store.records
    coordinator.assert_not_awaited()


def test_denied_existing_and_unknown_patient_have_same_response(client, fhir_api):
    http, _ = client
    responses = [http.post('/investigations', json={'patient_id':patient,'microbiology_result_id':'RESULT_001'})
                 for patient in ('PATIENT_002','PATIENT_999')]
    assert responses[0].status_code == responses[1].status_code == 403
    assert responses[0].json() == responses[1].json()


def test_history_pending_and_review_are_patient_scoped(client, workflow_store, make_clinical_summary):
    http, coordinator = client
    first = http.post('/investigations', json={'patient_id':'PATIENT_001','microbiology_result_id':'MICRO_001_RECENT'})
    assert first.status_code == 201
    first_id = first.json()['investigation_id']
    http.app.dependency_overrides[get_current_user] = lambda: OTHER
    coordinator.return_value = make_clinical_summary('PATIENT_002', ['get_microbiology_results'])
    second = http.post('/investigations', json={'patient_id':'PATIENT_002','microbiology_result_id':'MICRO_002_RECENT'})
    assert second.status_code == 201
    assert [row['investigation_id'] for row in http.get('/investigations/pending-review?limit=1').json()] == [second.json()['investigation_id']]
    assert http.get(f'/investigations/{first_id}').status_code == 403
    for suffix, body in [('approve',{}), ('reject',{'note':'Not assigned'})]:
        assert http.post(f'/investigations/{first_id}/{suffix}', json=body).status_code == 403
    http.app.dependency_overrides[get_current_user] = lambda: USER
    assert http.get(f'/investigations/{first_id}').json()['status'] == 'awaiting_review'
    assert http.post(f'/investigations/{first_id}/approve',json={}).status_code == 200


def test_direct_application_services_cannot_skip_authorization(workflow_store, make_clinical_summary, monkeypatch, fhir_api):
    service = InvestigationService(workflow_store)
    coordinator = AsyncMock(return_value=make_clinical_summary(tools=['get_microbiology_results']))
    monkeypatch.setattr('infectious_disease_assistant.services.investigations.investigate_patient', coordinator)
    with authorization_scope(USER):
        row = asyncio.run(service.create(InvestigationRequest(patient_id='PATIENT_001',microbiology_result_id='MICRO_001_RECENT')))
    load = Mock(wraps=workflow_store.get)
    monkeypatch.setattr(workflow_store, 'get', load)
    with authorization_scope(OTHER), pytest.raises(PatientAccessDeniedError):
        service.get(row.investigation_id)
    load.assert_not_called()
    for decision in (lambda: service.approve(row.investigation_id, OTHER, ApproveRequest()),
                     lambda: service.reject(row.investigation_id, OTHER, RejectRequest(note='Not assigned'))):
        with pytest.raises(PatientAccessDeniedError):
            decision()
    assert workflow_store.get(row.investigation_id).approval is None


def test_unauthorized_agent_stops_before_model_or_fhir(agent_model, fhir_api):
    with authorization_scope(OTHER), pytest.raises(PatientAccessDeniedError):
        asyncio.run(investigate_patient('PATIENT_001'))
    agent_model.assert_not_awaited()
    assert not fhir_api.requests


@pytest.mark.parametrize('module', ['infectious_disease_assistant.__main__', 'infectious_disease_assistant.agent_demo'])
def test_cli_denial_is_safe_and_has_no_stack_trace(module, monkeypatch, capsys, fhir_api, sdk_traces):
    from importlib import import_module
    monkeypatch.setattr('sys.argv', ['demo', 'PATIENT_002'])
    with authorization_scope(USER):
        assert import_module(module).main() == 1
    output = capsys.readouterr()
    assert not output.out and 'Access to this synthetic patient is denied.' in output.err
    assert 'Traceback' not in output.err
    assert not fhir_api.requests


def test_high_level_security_events_contain_no_clinical_payloads(agent_model, make_clinical_summary):
    from test_agent import final_response, tool_response
    expected = make_clinical_summary()
    agent_model.side_effect = [tool_response([tool.name for tool in PATIENT_TOOLS]), final_response(expected)]
    stream = io.StringIO()
    configure_logging('DEBUG', stream)
    with authorization_scope(USER):
        asyncio.run(investigate_patient('PATIENT_001'))
        with pytest.raises(PatientAccessDeniedError):
            authorize_patient('PATIENT_002')
    text = stream.getvalue()
    assert all(value not in text for value in SENSITIVE)
    events = [json.loads(line) for line in text.splitlines()]
    assert {'patient_access_granted','patient_access_denied','tool_completed','investigation_completed'}.issubset({event['event'] for event in events})
    denied = next(event for event in events if event['event'] == 'patient_access_denied')
    assert denied['reviewer_id'] == 'REVIEWER_001' and denied['patient_id'] == 'PATIENT_002'


def test_formatter_drops_payload_fields_and_raw_exception_text():
    record = logging.LogRecord('infectious_disease_assistant', logging.ERROR, '', 0, 'Escherichia coli', (), None)
    record.event_fields = {'summary':'cephalexin', 'api_key':'test-key-not-real', 'records':['urticaria'], 'record_count':1}
    text = JsonEventFormatter().format(record)
    assert json.loads(text)['event'] == 'application_event'
    assert json.loads(text)['record_count'] == 1
    assert all(value not in text for value in SENSITIVE)


def test_default_traces_exclude_payloads_and_provider_storage_is_disabled(agent_model, sdk_traces, make_clinical_summary, monkeypatch):
    from test_agent import final_response, tool_response
    monkeypatch.delenv('IDA_TRACE_INCLUDE_PAYLOADS')
    monkeypatch.delenv('IDA_STORE_MODEL_RESPONSES')
    settings = Settings(_env_file=None)
    assert not settings.trace_include_payloads and not settings.store_model_responses
    agent_model.side_effect = [tool_response([tool.name for tool in PATIENT_TOOLS]), final_response(make_clinical_summary())]
    asyncio.run(investigate_patient('PATIENT_001'))
    for call in agent_model.await_args_list:
        assert call.args[2].store is False
    exported = json.dumps([span.export() for span in sdk_traces.spans])
    assert all(value not in exported for value in SENSITIVE)
    assert 'validated_clinical_summary' in exported
    before = agent_configuration_version()
    monkeypatch.setenv('IDA_TRACE_INCLUDE_PAYLOADS', 'true')
    assert agent_configuration_version() != before


@pytest.mark.parametrize('failure', [RuntimeError('test-key-not-real synthetic-secret-password'),
                                    ValidationError.from_exception_data('test-key-not-real', [])])
def test_api_failure_and_audit_do_not_expose_secret_values(client, workflow_store, failure):
    http, coordinator = client
    coordinator.side_effect = failure
    response = http.post('/investigations',json={'patient_id':'PATIENT_001','microbiology_result_id':'MICRO_001_RECENT'})
    assert response.status_code in {500,503}
    assert all(value not in response.text for value in SENSITIVE)
    assert 'Traceback' not in response.text
    for row in workflow_store.records.values():
        assert 'synthetic-secret-password' not in row.model_dump_json()


def test_malformed_authorization_configuration_has_safe_api_response(client, monkeypatch):
    http, _ = client
    http.app.dependency_overrides.clear()
    monkeypatch.setenv('IDA_PATIENT_ASSIGNMENTS', 'synthetic-secret-password')
    response = http.get('/investigations/pending-review')
    assert response.status_code == 500
    assert response.json()['code'] == 'internal_error'
    assert 'synthetic-secret-password' not in response.text and 'Traceback' not in response.text
