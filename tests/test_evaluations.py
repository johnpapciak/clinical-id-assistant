import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from agents.usage import Usage
from agents.exceptions import ModelBehaviorError
from openai.types.responses.response_usage import ResponseUsage

from infectious_disease_assistant.evals.run import evaluate, evaluate_case, load_dataset, main
from infectious_disease_assistant.evals.scoring import build_report, check_arguments, score_case
from infectious_disease_assistant.evals.replay import attack_records
from infectious_disease_assistant.agent import investigate_patient
from infectious_disease_assistant.guardrails import render_summary
from infectious_disease_assistant.models.summary_draft import SummaryDraft
from infectious_disease_assistant.observability import ExecutionObservation, ToolCallObservation
from infectious_disease_assistant.services.patients import get_patient_summary

DATA = Path(__file__).resolve().parents[1] / 'data'


@pytest.fixture
def dataset():
    return load_dataset(DATA / 'evals')


def baseline(dataset, index=0):
    cases, scripts, _, _ = dataset
    case = cases[index]
    script = scripts[case.case_id]
    records = attack_records(case.patient_id, script)
    summary = render_summary(SummaryDraft.model_validate(script.draft), case.patient_id, records, microbiology_result_id=case.microbiology_result_id)
    observation = ExecutionObservation(tool_results=records, latency_ms=10.0)
    observation.tool_calls = [ToolCallObservation(f'call-{turn}-{index}', call.tool_name, call.arguments_json, True)
                              for turn, calls in enumerate(script.turns) for index, call in enumerate(calls)]
    return case, summary, observation


def test_twenty_cases_cover_existing_synthetic_records_and_safety(dataset):
    cases, scripts, corpus_hash, replay_hash = dataset
    assert len(cases) == len(scripts) == 20
    assert sum(case.kind == 'clinical' for case in cases) == 15
    assert {case.patient_id for case in cases} == {f'PATIENT_{index:03}' for index in range(1,6)}
    assert len(corpus_hash) == len(replay_hash) == 64
    for case in cases:
        patient = get_patient_summary(case.patient_id)
        expected = case.expectations
        assert all(record in patient.medications for record in expected.medications)
        assert all(record in patient.allergies for record in expected.allergies)
        assert all(record in patient.recent_labs for record in expected.labs)
        assert all(record in patient.previous_infections for record in expected.previous_infections)
        assert expected.human_review_required


def test_frozen_guideline_sources_match_authored_documents(dataset):
    from infectious_disease_assistant.rag.documents import load_sources, extract_document, chunk_document
    actual = {}
    for source in load_sources(DATA/'guidelines/manifest.json'):
        _, chunks = chunk_document(source, extract_document(source, DATA/'guidelines'), chunk_words=120, overlap=20)
        actual.update({chunk.chunk_id: chunk for chunk in chunks})
    for script in dataset[1].values():
        for match in script.guideline_matches:
            assert match.model_dump(exclude={'distance','similarity'}) == actual[match.chunk_id].model_dump()


def test_scripted_suite_runs_without_credentials_or_external_services(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_MODEL', raising=False)
    report = asyncio.run(evaluate('scripted', DATA/'evals', DATA))
    assert report.total_cases == report.passed_cases == 20
    assert report.blocked_attacks == 5
    assert report.failure_categories == {}
    assert report.model_judging == 'not_run'
    assert all(result.usage is None for result in report.cases)
    assert all(result.usage_source == 'scripted_not_billable' for result in report.cases)
    assert all(result.model_name == 'scripted-replay' for result in report.cases if result.kind == 'clinical')
    assert report.clinical_latency_ms['p95'] >= report.clinical_latency_ms['median'] >= 0
    assert all(result.latency_ms is not None for result in report.cases)
    assert all(result.simulated_model_calls >= 2 for result in report.cases if result.kind == 'clinical')


@pytest.mark.parametrize('field', ['relevant_medications','relevant_allergies','relevant_labs','previous_infections'])
def test_missing_required_facts_are_failures(dataset, field):
    case, summary, observation = baseline(dataset)
    setattr(summary, field, [])
    result = score_case(case, summary, observation, structured_valid=True)
    assert not result.passed
    assert next(check for check in result.checks if check.dimension == 'required_fact_completeness').status == 'fail'


def test_fabricated_facts_fail_grounding(dataset):
    case, summary, observation = baseline(dataset)
    summary.relevant_labs[0].value = 99.0
    result = score_case(case, summary, observation, structured_valid=True)
    assert next(check for check in result.checks if check.dimension == 'grounding_and_safety').status == 'fail'


def test_required_guideline_citation_is_not_satisfied_by_a_search_alone(dataset):
    case, summary, observation = baseline(dataset, 1)
    summary.guideline_sources = []
    result = score_case(case, summary, observation, structured_valid=True)
    assert next(check for check in result.checks if check.dimension == 'guideline_citations').status == 'fail'


def test_tool_selection_uses_observed_calls(dataset):
    case, summary, observation = baseline(dataset)
    observation.tool_calls = [call for call in observation.tool_calls if call.tool_name != 'get_allergies']
    result = score_case(case, summary, observation, structured_valid=True)
    assert next(check for check in result.checks if check.dimension == 'tool_selection').status == 'fail'


@pytest.mark.parametrize('tool, arguments', [
    ('get_medications','{"patient_id":"PATIENT_002"}'), ('get_allergies','[]'),
    ('search_guidelines','{"query":"PATIENT_001 renal", "top_k":3}'),
    ('search_guidelines','{"query":"renal", "top_k":true}'),
    ('search_guidelines','{"query":"renal", "top_k":11}'), ('search_guidelines','{"query":" "}'),
    ('create_followup_task','{}'), ('get_medications','invalid JSON'),
])
def test_argument_correctness_checks_actual_json_and_patient_scope(tool, arguments):
    observation = ExecutionObservation(tool_calls=[ToolCallObservation('call',tool,arguments,True)])
    assert not check_arguments(observation)


def test_api_failure_is_not_counted_as_bad_structured_output(dataset):
    result = score_case(dataset[0][0], None, ExecutionObservation(), structured_valid=None, error_type='FhirTransportError')
    assert not result.passed
    assert next(check for check in result.checks if check.dimension=='structured_output').status == 'not_applicable'
    assert next(check for check in result.checks if check.dimension=='execution').status == 'fail'


def test_unblocked_attack_is_a_failure(dataset):
    cases, scripts, _, _ = dataset
    script = scripts['CASE_016'].model_copy(deep=True)
    _, safe, _ = baseline(dataset)
    script.attack_candidate = safe.model_dump(mode='json')
    result = asyncio.run(evaluate_case(cases[15], script, 'scripted', DATA))
    assert not result.passed
    assert result.blocked_category is None


def test_report_aggregates_failures_without_a_model_judge(dataset):
    case, summary, observation = baseline(dataset)
    summary.relevant_allergies = []
    result = score_case(case, summary, observation, structured_valid=True)
    report = build_report('scripted',[result],1,dataset[2],dataset[3])
    assert report.failure_categories['required_fact_completeness'] == 1
    assert report.passed_cases == 0
    assert report.model_judging == 'not_run'
    assert json.loads(report.model_dump_json())['dimensions']['required_fact_completeness']['failed'] == 1


def test_live_configuration_failure_is_reported_as_unavailable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('OPENAI_MODEL',raising=False)
    report = asyncio.run(evaluate('live',DATA/'evals',DATA))
    assert report.run_error is not None
    assert report.planned_cases == 20 and report.total_cases == 0


def test_cli_saves_structured_report(monkeypatch, tmp_path, capsys):
    target = tmp_path/'result.json'
    monkeypatch.setattr('sys.argv',['evals','--dataset',str(DATA/'evals'),'--output',str(target)])
    assert main() == 0
    report = json.loads(target.read_text())
    assert report['mode'] == 'scripted' and report['passed_cases'] == 20
    assert '20/20' in capsys.readouterr().out


def test_sdk_usage_is_reported_without_inventing_embedding_costs(dataset):
    case, summary, observation = baseline(dataset)
    observation.model_name = 'test-model'
    observation.capture_usage(Usage(requests=2, input_tokens=10, output_tokens=5, total_tokens=15))
    live = score_case(case, summary, observation, structured_valid=True)
    assert live.usage == {'requests': 2, 'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}
    assert live.usage_source == 'sdk_reported'
    assert live.model_name == 'test-model'
    scripted = score_case(case, summary, observation, structured_valid=True, scripted=True)
    assert scripted.usage is None
    assert scripted.usage_source == 'scripted_not_billable'


def test_latency_statistics_exclude_injected_safety_attacks(dataset):
    case, summary, observation = baseline(dataset)
    fast = score_case(case, summary, observation, structured_valid=True)
    observation.latency_ms = 30.0
    slow = score_case(case, summary, observation, structured_valid=True)
    attack = slow.model_copy(update={'kind': 'guardrail_attack', 'latency_ms': 9000.0})
    report = build_report('scripted', [fast, slow, attack], 3, dataset[2], dataset[3])
    assert report.clinical_latency_ms == {'median': 20.0, 'p95': 30.0}


def test_empty_corpus_cannot_report_success(tmp_path):
    (tmp_path/'cases.json').write_text('[]')
    (tmp_path/'replays.json').write_text('{}')
    with pytest.raises(ValueError, match='must contain cases'):
        load_dataset(tmp_path)


def test_sdk_usage_survives_a_malformed_final_output(agent_model):
    from test_agent import final_response
    response = final_response('invalid JSON')
    response.usage = ResponseUsage.model_validate({
        'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15,
        'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
        'output_tokens_details': {'reasoning_tokens': 0},
    })
    agent_model.side_effect = [response]
    observation = ExecutionObservation()
    with pytest.raises(ModelBehaviorError):
        asyncio.run(investigate_patient('PATIENT_001', observation=observation))
    assert observation.usage == {'requests': 1, 'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}
