import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
from agents import Model
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
from pydantic import Field, JsonValue

from infectious_disease_assistant.authorization import authorization_scope
from infectious_disease_assistant.fhir.constants import PATIENT_IDENTIFIER_SYSTEM
from infectious_disease_assistant.fhir.server import handle_request, load_fixtures
from infectious_disease_assistant.models.patient import HealthcareModel
from infectious_disease_assistant.models.workflow import CurrentUser
from infectious_disease_assistant.rag.models import GuidelineMatch
from infectious_disease_assistant.services import patients


class ScriptedCall(HealthcareModel):
    tool_name: str
    arguments_json: str = '{}'


class ReplayCase(HealthcareModel):
    turns: list[list[ScriptedCall]] = Field(default_factory=list)
    draft: dict[str, JsonValue] = Field(default_factory=dict)
    guideline_matches: list[GuidelineMatch] = Field(default_factory=list)
    attack_candidate: dict[str, JsonValue] | None = None


class ScriptedModel(Model):
    def __init__(self, replay: ReplayCase):
        self.replay = replay
        self.calls = 0

    async def get_response(self, *args, **kwargs) -> ModelResponse:
        turn = self.calls
        self.calls += 1
        if turn < len(self.replay.turns):
            output = [ResponseFunctionToolCall(
                type='function_call', name=call.tool_name, arguments=call.arguments_json,
                call_id=f'replay-{turn}-{index}',
            ) for index, call in enumerate(self.replay.turns[turn])]
        elif turn == len(self.replay.turns):
            output = [ResponseOutputMessage(
                id='replay-final', type='message', role='assistant', status='completed',
                content=[ResponseOutputText(type='output_text', text=json.dumps(self.replay.draft), annotations=[])],
            )]
        else:
            raise RuntimeError('Scripted response sequence was exhausted')
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=f'replay-response-{turn}')

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError('Evaluation replay is non-streaming')
        yield


@contextmanager
def synthetic_transport(data_dir: Path, replay: ReplayCase):
    fixtures = load_fixtures(data_dir / 'fhir' / 'bundle.json')
    fixture_user = CurrentUser(reviewer_id='REVIEWER_001', assigned_patient_ids={
        identifier['value'] for resource in fixtures.resources.values() if resource['resourceType'] == 'Patient'
        for identifier in resource.get('identifier', []) if identifier['system'] == PATIENT_IDENTIFIER_SYSTEM
    })
    def respond(request):
        status, payload = handle_request(fixtures, request.method, request.url.path,
                                         parse_qs(request.url.query.decode(), keep_blank_values=True))
        return httpx.Response(status, json=payload)
    def client():
        return httpx.Client(base_url='http://127.0.0.1:8765/fhir/', transport=httpx.MockTransport(respond))
    with authorization_scope(fixture_user), patch('infectious_disease_assistant.fhir.client.create_http_client', client), patch(
        'infectious_disease_assistant.tools.guideline_tools.service.search_guidelines',
        return_value=replay.guideline_matches,
    ):
        yield


def attack_records(patient_id: str, replay: ReplayCase):
    records = {}
    allowed = {name: getattr(patients, name) for name in (
        'get_microbiology_results', 'get_medications', 'get_allergies', 'get_recent_labs', 'get_previous_infections',
    )}
    for turn in replay.turns:
        for call in turn:
            if call.tool_name == 'search_guidelines':
                records[call.tool_name] = replay.guideline_matches
            elif call.tool_name in allowed:
                records[call.tool_name] = allowed[call.tool_name](patient_id)
            else:
                raise ValueError('Unsupported tool in attack replay')
    return records
