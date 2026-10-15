import pytest
from pydantic import ValidationError

from infectious_disease_assistant.data_loader import DataLoadError, load_patient_data
from infectious_disease_assistant.models.patient import PatientDataset, PatientRecord, PreviousInfection


def test_load_five_synthetic_patients():
    dataset = load_patient_data()

    assert isinstance(dataset, PatientDataset)
    assert [patient.patient_id for patient in dataset.patients] == [
        "PATIENT_001", "PATIENT_002", "PATIENT_003", "PATIENT_004", "PATIENT_005",
    ]
    assert all(isinstance(patient, PatientRecord) for patient in dataset.patients)


def test_loader_preserves_all_supplied_values(synthetic_payload):
    assert load_patient_data().model_dump(mode="json", exclude_unset=True) == synthetic_payload


def test_explicit_file_path_overrides_configuration(write_dataset, monkeypatch, tmp_path):
    path = write_dataset({"reference_date": "2026-10-15", "patients": [{"patient_id": "PATIENT_123"}]})
    monkeypatch.setenv("IDA_DATA_DIR", str(tmp_path / "missing"))

    assert load_patient_data(path).patients[0].patient_id == "PATIENT_123"


@pytest.mark.parametrize("content", [b"not json", b"\xff", b'{"patients": []}'])
def test_unreadable_or_invalid_data(tmp_path, content):
    path = tmp_path / "patients.json"
    path.write_bytes(content)

    with pytest.raises(DataLoadError, match="Could not load synthetic patient data") as caught:
        load_patient_data(path)
    assert caught.value.__cause__ is not None


def test_missing_file(tmp_path):
    with pytest.raises(DataLoadError) as caught:
        load_patient_data(tmp_path / "missing.json")
    assert isinstance(caught.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize("field, value", [
    ("patient_id", "REAL-123"), ("age", -1), ("age", 131), ("unexpected_field", "value"),
    ("labs", [{"name": "creatinine", "value": "invalid", "unit": "mg/dL", "collected_on": "2026-10-14"}]),
    ("labs", [{"name": "creatinine", "value": 1.0, "unit": "mg/dL", "collected_on": "not a date"}]),
    ("medications", [{"name": "test", "dose": "1 mg", "route": "oral", "frequency": "daily", "status": "unknown"}]),
    ("microbiology_results", [{"result_id": "TEST", "specimen": "urine", "collected_on": "2026-10-14", "outcome": "growth"}]),
    ("microbiology_results", [{"result_id": "TEST", "specimen": "urine", "collected_on": "2026-10-14", "outcome": "no_growth", "organism": "Escherichia coli"}]),
    ("microbiology_results", [{"result_id": "TEST", "specimen": "urine", "collected_on": "2026-10-14", "outcome": "no_growth", "susceptibilities": [{"antibiotic": "ampicillin", "interpretation": "resistant"}]}]),
])
def test_invalid_records_fail_validation(write_dataset, field, value):
    write_dataset({"reference_date": "2026-10-15", "patients": [{"patient_id": "PATIENT_001", field: value}]})

    with pytest.raises(DataLoadError) as caught:
        load_patient_data()
    assert isinstance(caught.value.__cause__, ValidationError)


def test_duplicate_patient_ids_are_rejected(synthetic_payload, write_dataset):
    synthetic_payload["patients"].append(synthetic_payload["patients"][0])
    write_dataset(synthetic_payload)

    with pytest.raises(DataLoadError) as caught:
        load_patient_data()
    assert "Patient IDs must be unique" in str(caught.value.__cause__)


def test_optional_lists_do_not_share_state():
    first = PatientRecord(patient_id="PATIENT_001")
    second = PatientRecord(patient_id="PATIENT_002")
    first.previous_infections.append(PreviousInfection(
        diagnosis="urinary tract infection", organism="Escherichia coli",
        diagnosed_on="2026-01-01", site="urinary tract",
    ))

    assert second.previous_infections == []
