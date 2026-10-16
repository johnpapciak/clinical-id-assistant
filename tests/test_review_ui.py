from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from infectious_disease_assistant.api import create_app
from infectious_disease_assistant.current_user import get_current_user
from infectious_disease_assistant.guardrails import SAFE_FOLLOWUP
from infectious_disease_assistant.models.workflow import CurrentUser
from infectious_disease_assistant.review_client import ReviewClient

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

APP = Path(__file__).resolve().parents[1] / "ui" / "app.py"


def click(app, label):
    next(button for button in app.button if button.label == label).click().run()
    assert not app.exception


def enter(app, label, value):
    next(widget for widget in [*app.text_input, *app.text_area] if widget.label == label).set_value(value)


@pytest.fixture
def ui(monkeypatch, workflow_store, make_clinical_summary, guideline_match, sdk_traces):
    summary = make_clinical_summary()
    summary.proposed_action = SAFE_FOLLOWUP.model_copy(deep=True)
    summary.guideline_sources = [guideline_match.citation()]
    summary.evidence_sources.append("search_guidelines")
    coordinator = AsyncMock(return_value=summary)
    monkeypatch.setattr("infectious_disease_assistant.services.investigations.investigate_patient", coordinator)
    backend = create_app(store=workflow_store)
    backend.dependency_overrides[get_current_user] = lambda: CurrentUser(
        reviewer_id="REVIEWER_001", assigned_patient_ids={"PATIENT_001"},
    )
    monkeypatch.setattr("infectious_disease_assistant.review_client.ReviewClient",
                        lambda settings: ReviewClient(settings, client=TestClient(backend)))
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    assert not app.exception
    return app, workflow_store, coordinator


def test_create_review_and_approve_through_http(ui):
    app, store, coordinator = ui
    assert "SYNTHETIC DATA ONLY" in app.warning[0].value
    assert "Not autonomous medical advice" in app.warning[0].value
    assert not store.records
    click(app, "Investigate")
    record = next(iter(store.records.values()))
    assert record.status == "awaiting_review" and record.followup_task is None
    assert any("awaiting_review" in item.value for item in app.info)
    assert any("Escherichia coli" in item.value for item in app.text)
    headings = {item.value for item in app.subheader}
    assert {"Relevant medications", "Relevant allergies", "Relevant labs", "Previous infections", "Evidence sources", "Guideline sources", "Proposed follow-up action"} <= headings
    assert any("title" in table.value.columns and table.value.iloc[0]["title"] == "Synthetic renal context"
               for table in app.dataframe)
    assert any(SAFE_FOLLOWUP.title == item.value for item in app.text)
    assert any("human review only" in item.value for item in app.info)
    enter(app, "Review note (required for rejection)", "Reviewed the synthetic evidence.")
    click(app, "Approve")
    approved = next(iter(store.records.values()))
    assert approved.status == "approved" and approved.followup_task.simulated
    assert approved.approval.note == "Reviewed the synthetic evidence."
    task_id = approved.followup_task.task_id
    assert any("Simulated follow-up task created" in item.value for item in app.success)
    assert not any(button.label in {"Approve", "Reject"} for button in app.button)
    click(app, "Refresh status")
    assert next(iter(store.records.values())).followup_task.task_id == task_id
    assert coordinator.await_count == 1


def test_rejection_needs_note_and_creates_no_task(ui):
    app, store, _ = ui
    click(app, "Investigate")
    click(app, "Reject")
    assert any("requires a note" in error.value for error in app.error)
    assert next(iter(store.records.values())).status == "awaiting_review"
    enter(app, "Review note (required for rejection)", "Additional synthetic context required.")
    click(app, "Reject")
    rejected = next(iter(store.records.values()))
    assert rejected.status == "rejected" and rejected.followup_task is None
    assert any("rejected" in item.value for item in app.info)
    assert not any(button.label == "Approve" for button in app.button)


def test_pending_reviews_reopen_in_a_fresh_session(ui):
    first, store, _ = ui
    click(first, "Investigate")
    second = AppTest.from_file(str(APP), default_timeout=10).run()
    assert "investigation_id" not in second.session_state
    click(second, "Load pending reviews")
    assert len(second.selectbox[0].options) == 1
    click(second, "Open pending investigation")
    assert second.session_state.investigation_id == str(next(iter(store.records)))
    assert any("awaiting_review" in item.value for item in second.info)


def test_invalid_and_unauthorized_patient_never_call_coordinator(ui):
    app, store, coordinator = ui
    enter(app, "Synthetic patient ID", "not-a-synthetic-id")
    click(app, "Investigate")
    assert any("Enter synthetic IDs" in item.value for item in app.error)
    enter(app, "Synthetic patient ID", "PATIENT_002")
    enter(app, "Synthetic microbiology result ID", "MICRO_002_RECENT")
    click(app, "Investigate")
    assert any("not assigned" in item.value for item in app.error)
    assert not store.records
    coordinator.assert_not_awaited()


def test_sparse_summary_and_open_by_id(ui):
    app, store, coordinator = ui
    summary = coordinator.return_value
    for name in ["relevant_medications", "relevant_allergies", "relevant_labs", "previous_infections", "important_findings", "guideline_sources"]:
        setattr(summary, name, [])
    summary.proposed_action = None
    click(app, "Investigate")
    assert any("does not establish absence" in item.value for item in app.caption)
    assert any("No guideline sources" in item.value for item in app.caption)
    assert any("No action proposed" in item.value for item in app.caption)
    identifier = str(next(iter(store.records)))
    second = AppTest.from_file(str(APP), default_timeout=10).run()
    enter(second, "Investigation ID", identifier)
    click(second, "Open investigation")
    click(second, "Approve")
    assert next(iter(store.records.values())).status == "approved"
    assert next(iter(store.records.values())).followup_task is None


def test_other_reviewer_decision_is_refreshed_before_ui_can_approve(ui):
    app, store, _ = ui
    click(app, "Investigate")
    identifier = next(iter(store.records))
    store.review(identifier, CurrentUser(reviewer_id="REVIEWER_002", assigned_patient_ids={"PATIENT_001"}),
                 reject=True, note="Reviewed elsewhere.")
    click(app, "Approve")
    assert store.get(identifier).status == "rejected"
    assert store.get(identifier).followup_task is None
    assert any("rejected" in item.value for item in app.info)


def test_invalid_ui_configuration_is_safe(ui, monkeypatch):
    monkeypatch.setenv("IDA_UI_API_URL", "http://user:SECRET_FIXTURE@localhost:8000")
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    assert not app.exception
    assert app.error[0].value.startswith("Invalid UI configuration")
    assert "SECRET_FIXTURE" not in app.error[0].value
