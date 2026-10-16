import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from infectious_disease_assistant.models.investigation import InvestigationResponse
from infectious_disease_assistant.models.workflow import AgentRunRecord
from infectious_disease_assistant.review_client import ReviewAPIError, ReviewClient, ReviewUISettings


@pytest.fixture
def record(make_clinical_summary):
    now = datetime.now(timezone.utc)
    return InvestigationResponse(
        investigation_id=uuid4(), patient_id="PATIENT_001", microbiology_result_id="MICRO_001_RECENT",
        created_at=now, updated_at=now, status="awaiting_review", summary=make_clinical_summary(),
        agent_run=AgentRunRecord(trace_id="trace_synthetic", started_at=now),
    )


def client_for(handler):
    settings = ReviewUISettings(_env_file=None)
    return ReviewClient(settings, httpx.Client(base_url=settings.ui_api_url, transport=httpx.MockTransport(handler)))


def test_client_uses_api_contract_without_reviewer_identity(record):
    requests = []

    def respond(request):
        requests.append(request)
        payload = [record.model_dump(mode="json")] if request.url.path.endswith("pending-review") else record.model_dump(mode="json")
        return httpx.Response(200, json=payload)

    with client_for(respond) as client:
        assert client.create(" PATIENT_001 ", " MICRO_001_RECENT ") == record
        assert client.get(str(record.investigation_id)) == record
        assert client.pending() == [record]
        assert client.review(str(record.investigation_id), approve=True, note=" ") == record
        assert client.review(str(record.investigation_id), approve=False, note=" Missing context. ") == record
    assert [(r.method, r.url.path) for r in requests] == [
        ("POST", "/investigations"), ("GET", f"/investigations/{record.investigation_id}"),
        ("GET", "/investigations/pending-review"), ("POST", f"/investigations/{record.investigation_id}/approve"),
        ("POST", f"/investigations/{record.investigation_id}/reject"),
    ]
    assert json.loads(requests[0].content) == {"patient_id": "PATIENT_001", "microbiology_result_id": "MICRO_001_RECENT"}
    assert json.loads(requests[3].content) == {"note": None, "modified_action": None}
    assert json.loads(requests[4].content) == {"note": "Missing context."}
    assert all("authorization" not in request.headers for request in requests)


@pytest.mark.parametrize("status", [302, 403, 404, 409, 422, 500, 502, 503])
def test_http_errors_do_not_expose_backend_payloads(status):
    with client_for(lambda _: httpx.Response(status, json={"message": "SECRET_FIXTURE", "stack": "private traceback"})) as client:
        with pytest.raises(ReviewAPIError) as caught:
            client.pending()
    assert "SECRET_FIXTURE" not in str(caught.value)
    assert "traceback" not in str(caught.value)


@pytest.mark.parametrize("response", [httpx.Response(200, text="SECRET_FIXTURE"), httpx.Response(200, json={"secret": "SECRET_FIXTURE"})])
def test_malformed_responses_are_safe(response):
    with client_for(lambda _: response) as client:
        with pytest.raises(ReviewAPIError, match="invalid") as caught:
            client.get(str(uuid4()))
    assert "SECRET_FIXTURE" not in str(caught.value)


def test_invalid_pending_schema_is_rejected():
    with client_for(lambda _: httpx.Response(200, json=[{"summary": "SECRET_FIXTURE"}])) as client:
        with pytest.raises(ReviewAPIError, match="pending-review"):
            client.pending()


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
def test_network_failures_are_safe_and_not_retried(error):
    requests = []

    def fail(request):
        requests.append(request)
        raise error("SECRET_FIXTURE", request=request)

    with client_for(fail) as client:
        with pytest.raises(ReviewAPIError) as caught:
            client.create("PATIENT_001", "MICRO_001_RECENT")
    assert len(requests) == 1
    assert "SECRET_FIXTURE" not in str(caught.value)
    if error is httpx.ReadTimeout:
        assert "may have completed" in str(caught.value)


def test_invalid_inputs_do_not_make_http_requests():
    def unexpected(_):
        pytest.fail("Invalid input reached HTTP transport")

    with client_for(unexpected) as client:
        for operation in [
            lambda: client.create("real-name", "MICRO_001_RECENT"),
            lambda: client.create("PATIENT_001", "../anything"),
            lambda: client.get("../../health"),
            lambda: client.review(str(uuid4()), approve=False, note=" "),
            lambda: client.review(str(uuid4()), approve=True, note="x" * 1001),
        ]:
            with pytest.raises(ReviewAPIError):
                operation()


@pytest.mark.parametrize("url", ["https://example.com", "http://api:8001", "http://user:SECRET_FIXTURE@localhost:8000", "http://localhost:8000?secret=x", "http://localhost:8000/other", "http://localhost:invalid"])
def test_ui_configuration_rejects_unexpected_backend_urls(url):
    with pytest.raises(ValidationError):
        ReviewUISettings(_env_file=None, ui_api_url=url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:8080/", "http://[::1]:8000", "http://api:8000"])
def test_host_and_compose_api_configuration(url):
    assert ReviewUISettings(_env_file=None, ui_api_url=url).ui_api_url == url.rstrip("/")
