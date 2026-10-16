import json
import socket
from functools import partial
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import pytest
import uvicorn

from infectious_disease_assistant.agent import investigate_patient
from infectious_disease_assistant.api import create_app
from infectious_disease_assistant.evals.replay import ReplayCase, ScriptedModel
from infectious_disease_assistant.fhir.client import create_http_client
from infectious_disease_assistant.fhir.server import load_fixtures, make_server
from infectious_disease_assistant.rag.service import ingest_guidelines, search_guidelines
from infectious_disease_assistant.review_client import ReviewClient, ReviewUISettings
from infectious_disease_assistant.workflow.database import WorkflowStore

pytestmark = pytest.mark.integration
AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fhir_http(monkeypatch, postgres_settings):
    with make_server(load_fixtures(postgres_settings.data_dir / "fhir/bundle.json"), port=0) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        monkeypatch.setenv("IDA_FHIR_BASE_URL", f"http://127.0.0.1:{server.server_port}/fhir")
        monkeypatch.setattr("infectious_disease_assistant.fhir.client.create_http_client", create_http_client)
        thread.start()
        try:
            yield
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture
def api_http(workflow_settings, sdk_traces):
    app = create_app(WorkflowStore(workflow_settings))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 10
            while not server.started:
                if not thread.is_alive() or monotonic() >= deadline:
                    pytest.fail("Demo API failed to start")
                sleep(0.01)
            yield url
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def test_streamlit_investigation_to_approved_task(
    monkeypatch, fhir_http, api_http, postgres_settings, workflow_settings, test_embedder, tmp_path,
):
    replay = ReplayCase.model_validate(json.loads((ROOT / "data/evals/replays.json").read_text())["CASE_002"])
    model = ScriptedModel(replay)
    monkeypatch.setattr("infectious_disease_assistant.services.investigations.investigate_patient",
                        partial(investigate_patient, model_override=model))
    monkeypatch.setattr("infectious_disease_assistant.tools.guideline_tools.service.search_guidelines",
                        partial(search_guidelines, settings=postgres_settings, embedder=test_embedder))
    ingested = ingest_guidelines(settings=postgres_settings, embedder=test_embedder)
    assert ingested.document_count == 3
    monkeypatch.setenv("IDA_UI_API_URL", api_http)
    app = AppTest.from_file(str(ROOT / "ui/app.py"), default_timeout=20).run()

    def click(label):
        next(button for button in app.button if button.label == label).click().run()
        assert not app.exception
        assert not app.error

    click("Investigate")
    key = app.session_state.investigation_id
    settings = ReviewUISettings(_env_file=None, ui_api_url=api_http)
    with ReviewClient(settings) as client:
        pending = client.get(key)
        assert pending.status == "awaiting_review"
        assert pending.approval is None and pending.followup_task is None
        assert pending.summary.organism == "Escherichia coli"
        assert pending.summary.relevant_labs[0].value == 1.8
        assert pending.summary.guideline_sources[0].document_id == "synthetic-renal-context-v1"
        assert "search_guidelines" in pending.summary.evidence_sources
        assert all(event.status == "completed" for event in pending.tool_events)
        assert key in {str(row.investigation_id) for row in client.pending()}
        click("Approve")
        approved = client.get(key)
        assert approved.status == "approved" and approved.followup_task.simulated
        assert any(item.value == "Simulated follow-up task created" for item in app.success)
        repeated = client.review(key, approve=True, note="")
        assert repeated.followup_task.task_id == approved.followup_task.task_id
        assert client.pending() == []
    with WorkflowStore(workflow_settings).connection() as connection:
        assert connection.execute("SELECT count(*) AS count FROM ida_followup_tasks").fetchone()["count"] == 1
    assert model.calls == len(replay.turns) + 1
    report = {
        "mode": "scripted_model_and_test_embeddings", "synthetic_data_only": True,
        "transports": ["Streamlit", "FastAPI HTTP", "FHIR HTTP", "PostgreSQL/pgvector"],
        "investigation_id": key, "patient_id": pending.patient_id,
        "organism": pending.summary.organism,
        "guideline_documents": [source.document_id for source in pending.summary.guideline_sources],
        "tools": [event.tool_name for event in pending.tool_events],
        "status_before_review": pending.status, "status_after_review": approved.status,
        "task_id": str(approved.followup_task.task_id), "duplicate_approval_created_task": False,
    }
    path = tmp_path / "demo-result.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    print(f"Demo report: {path}")
