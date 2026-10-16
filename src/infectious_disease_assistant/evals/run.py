import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter

from agents import flush_traces
from agents.exceptions import ModelBehaviorError
from pydantic import TypeAdapter, ValidationError

from infectious_disease_assistant.agent import investigate_patient
from infectious_disease_assistant.config import AgentSettings, Settings
from infectious_disease_assistant.evals.models import EvalCase
from infectious_disease_assistant.evals.replay import ReplayCase, ScriptedModel, attack_records, synthetic_transport
from infectious_disease_assistant.evals.scoring import build_report, score_case
from infectious_disease_assistant.guardrails import SafetyViolation, validate_safe_summary
from infectious_disease_assistant.models.clinical_summary import ClinicalSummary
from infectious_disease_assistant.observability import ExecutionObservation


def load_dataset(directory: Path):
    corpus = (directory / 'cases.json').read_bytes()
    replays = (directory / 'replays.json').read_bytes()
    cases = TypeAdapter(list[EvalCase]).validate_json(corpus)
    scripts = TypeAdapter(dict[str, ReplayCase]).validate_json(replays)
    if not cases:
        raise ValueError('Evaluation dataset must contain cases')
    if len({case.case_id for case in cases}) != len(cases) or set(scripts) != {case.case_id for case in cases}:
        raise ValueError('Evaluation case IDs and replay IDs must match uniquely')
    return cases, scripts, hashlib.sha256(corpus).hexdigest(), hashlib.sha256(replays).hexdigest()


async def evaluate_case(case: EvalCase, script: ReplayCase, mode: str, data_dir: Path):
    observation = ExecutionObservation()
    summary, category, error_type, valid = None, None, None, None
    started = perf_counter()
    model = ScriptedModel(script) if mode == 'scripted' else None
    try:
        if case.kind == 'guardrail_attack':
            with synthetic_transport(data_dir, script):
                observation.tool_results = attack_records(case.patient_id, script)
                candidate = ClinicalSummary.model_validate(script.attack_candidate)
                valid = True
                summary = validate_safe_summary(candidate, case.patient_id, observation.tool_results,
                                                microbiology_result_id=case.microbiology_result_id)
        else:
            options = {'model_override': model} if model is not None else {}
            async def run():
                return await investigate_patient(case.patient_id, microbiology_result_id=case.microbiology_result_id,
                    guideline_question=case.guideline_question, observation=observation, **options)
            if mode == 'scripted':
                with synthetic_transport(data_dir, script):
                    summary = await run()
            else:
                summary = await run()
            valid = True
    except SafetyViolation as error:
        category, error_type, valid = error.category, type(error).__name__, True
    except Exception as error:
        error_type = type(error).__name__
        if isinstance(error, ModelBehaviorError):
            valid = False
        observation.error_type = error_type
    observation.latency_ms = round((perf_counter() - started) * 1000, 2)
    return score_case(case, summary, observation, structured_valid=valid, blocked_category=category,
                      error_type=error_type, scripted=mode == 'scripted', simulated_calls=model.calls if model else 0)


async def evaluate(mode: str, directory: Path, data_dir: Path):
    cases, scripts, corpus_hash, replay_hash = load_dataset(directory)
    if mode == 'live':
        try:
            AgentSettings()
        except ValidationError:
            return build_report(mode, [], len(cases), corpus_hash, replay_hash, 'OpenAI configuration unavailable; set OPENAI_MODEL and OPENAI_API_KEY')
    results = []
    for case in cases:
        results.append(await evaluate_case(case, scripts[case.case_id], mode, data_dir))
    return build_report(mode, results, len(cases), corpus_hash, replay_hash)


def main() -> int:
    parser = argparse.ArgumentParser(description='Evaluate synthetic investigations; scripted replay is not live model validation.')
    parser.add_argument('--mode', choices=['scripted', 'live'], default='scripted')
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--output', type=Path, default=Path('eval-results/latest.json'))
    args = parser.parse_args()
    try:
        settings = Settings()
        report = asyncio.run(evaluate(args.mode, args.dataset or settings.data_dir / 'evals', settings.data_dir))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report.model_dump_json(indent=2) + '\n', encoding='utf-8')
    except (OSError, ValueError):
        print('Error: evaluation inputs/configuration or report output path are invalid.', file=sys.stderr)
        return 2
    finally:
        if args.mode == 'live':
            flush_traces()
    if report.run_error:
        print(f'{report.mode}: unavailable ({report.total_cases}/{report.planned_cases} cases run).')
    else:
        print(f'{report.mode}: {report.passed_cases}/{report.total_cases} cases passed; {report.blocked_attacks} safety attacks blocked.')
    if report.clinical_latency_ms:
        suffix = ' (replay, not model latency)' if report.mode == 'scripted' else ''
        print(f"Clinical pipeline latency: median={report.clinical_latency_ms['median']:.1f} ms, p95={report.clinical_latency_ms['p95']:.1f} ms{suffix}")
    print('Failures: ' + (', '.join(f'{name}={count}' for name, count in report.failure_categories.items()) or 'none'))
    if report.run_error:
        print(report.run_error)
    print(f'JSON report: {args.output}')
    return 2 if report.run_error else 0 if report.passed_cases == report.total_cases else 1


if __name__ == '__main__':
    raise SystemExit(main())
