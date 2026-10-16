import json

import pytest

from infectious_disease_assistant.fhir.constants import RESOURCE_TYPES
from infectious_disease_assistant.fhir.errors import FhirParseError
from infectious_disease_assistant.fhir.server import load_fixtures


def test_all_required_fhir_resource_types_are_present(fhir_api):
    types = {resource["resourceType"] for resource in fhir_api.store.resources.values()}
    assert types == set(RESOURCE_TYPES)
    assert len([key for key in fhir_api.store.resources if key.startswith("Patient/")]) == 5


def test_fixture_values_are_not_changed_by_loading(fhir_bundle):
    store = load_fixtures()
    assert list(store.resources.values()) == [entry["resource"] for entry in fhir_bundle["entry"]]
    assert store.reference_date.isoformat() == "2026-10-15"


def test_explicit_fixture_path(tmp_path, fhir_bundle):
    path = tmp_path / "bundle.json"
    fhir_bundle["timestamp"] = "2026-10-01T00:00:00Z"
    path.write_text(json.dumps(fhir_bundle))
    assert load_fixtures(path).reference_date.isoformat() == "2026-10-01"


@pytest.mark.parametrize("content", [b"not JSON", b"\xff", b"[]", b'{}', b'{"resourceType":"Patient"}'])
def test_malformed_fixtures_fail_predictably(tmp_path, content):
    path = tmp_path / "bundle.json"
    path.write_bytes(content)
    with pytest.raises(FhirParseError, match="Could not load synthetic FHIR fixtures"):
        load_fixtures(path)


def test_missing_fixture_file(tmp_path):
    with pytest.raises(FhirParseError) as caught:
        load_fixtures(tmp_path / "missing.json")
    assert isinstance(caught.value.__cause__, FileNotFoundError)


def test_duplicate_resources_are_rejected(tmp_path, fhir_bundle):
    fhir_bundle["entry"].append(fhir_bundle["entry"][0])
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(fhir_bundle))
    with pytest.raises(FhirParseError):
        load_fixtures(path)


def test_fixtures_require_synthetic_marker(tmp_path, fhir_bundle):
    fhir_bundle["meta"]["tag"] = []
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(fhir_bundle))
    with pytest.raises(FhirParseError):
        load_fixtures(path)


def test_fixture_ids_follow_fhir_rules(tmp_path, fhir_bundle):
    fhir_bundle["entry"][0]["resource"]["id"] = "PATIENT_001"
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(fhir_bundle))
    with pytest.raises(FhirParseError):
        load_fixtures(path)
