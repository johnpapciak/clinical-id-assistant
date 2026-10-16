import subprocess
import sys


def test_domain_retrieval_safety_and_storage_import_without_agent_sdk():
    result = subprocess.run([
        sys.executable, "-c",
        "from infectious_disease_assistant.services import patients, summary_validation; "
        "from infectious_disease_assistant import guardrails, event_logging; "
        "from infectious_disease_assistant.workflow import database; "
        "from infectious_disease_assistant.rag import service; "
        "import sys; assert 'agents' not in sys.modules; assert 'fastapi' not in sys.modules",
    ], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
