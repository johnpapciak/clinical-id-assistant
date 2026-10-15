import argparse
import asyncio
import sys

from agents import flush_traces
from agents.exceptions import AgentsException
from openai import OpenAIError
from pydantic import ValidationError

from infectious_disease_assistant.agent import AgentDemoError, investigate_patient
from infectious_disease_assistant.config import Settings
from infectious_disease_assistant.data_loader import DataLoadError
from infectious_disease_assistant.models.clinical_summary import ClinicalSummary
from infectious_disease_assistant.observability import configure_logging
from infectious_disease_assistant.services.patients import InvalidPatientIdError, PatientNotFoundError
from infectious_disease_assistant.services.summary_validation import SummaryEvidenceError


def format_summary(summary: ClinicalSummary) -> str:
    return summary.model_dump_json(indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Investigate synthetic microbiology with JSON output and SDK tracing.",
    )
    parser.add_argument("patient_id", help="Synthetic ID, for example PATIENT_001")
    args = parser.parse_args()
    try:
        configure_logging(Settings().log_level)
        summary = asyncio.run(investigate_patient(args.patient_id))
    except (
        InvalidPatientIdError, PatientNotFoundError, DataLoadError,
        AgentDemoError, SummaryEvidenceError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except ValidationError:
        print("Error: check configuration, including OPENAI_MODEL and OPENAI_API_KEY.", file=sys.stderr)
        return 1
    except OpenAIError as error:
        print(
            f"Error: OpenAI request failed ({type(error).__name__}). "
            "Check API access, OPENAI_MODEL, and OPENAI_API_KEY.",
            file=sys.stderr,
        )
        return 1
    except AgentsException as error:
        print(f"Error: agent run failed ({type(error).__name__}).", file=sys.stderr)
        return 1
    finally:
        flush_traces()
    print(format_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
