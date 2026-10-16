import argparse
import sys

from infectious_disease_assistant.workflow.database import WorkflowStore
from infectious_disease_assistant.workflow.errors import WorkflowDatabaseError


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize persistent synthetic review workflow tables.")
    parser.add_argument("command", choices=["init"])
    parser.parse_args()
    try:
        WorkflowStore().initialize()
    except WorkflowDatabaseError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print("Workflow database initialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
