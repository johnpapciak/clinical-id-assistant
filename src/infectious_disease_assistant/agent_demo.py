import argparse
import asyncio
import sys

from agents import Agent, RunContextWrapper, RunHooks, Tool
from agents.exceptions import AgentsException
from openai import OpenAIError
from pydantic import ValidationError

from infectious_disease_assistant.agent import AgentDemoError, investigate_patient
from infectious_disease_assistant.data_loader import DataLoadError
from infectious_disease_assistant.services.patients import InvalidPatientIdError, PatientNotFoundError
from infectious_disease_assistant.tools.patient_tools import PatientContext


class ToolUsageHooks(RunHooks[PatientContext]):
    async def on_tool_start(
        self, context: RunContextWrapper[PatientContext], agent: Agent[PatientContext], tool: Tool,
    ) -> None:
        print(f"Tool requested: {tool.name} ({context.context.patient_id})", file=sys.stderr)

    async def on_tool_end(
        self, context: RunContextWrapper[PatientContext], agent: Agent[PatientContext],
        tool: Tool, result: object,
    ) -> None:
        print(f"Tool completed: {tool.name}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Review synthetic microbiology with one agent.")
    parser.add_argument("patient_id", help="Synthetic ID, for example PATIENT_001")
    args = parser.parse_args()
    try:
        review = asyncio.run(investigate_patient(args.patient_id, hooks=ToolUsageHooks()))
    except (InvalidPatientIdError, PatientNotFoundError, DataLoadError, AgentDemoError) as error:
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
    print(review)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
