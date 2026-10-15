import json
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(autouse=True)
def configure_synthetic_data(monkeypatch):
    monkeypatch.setenv("IDA_DATA_DIR", str(DATA_DIR))


@pytest.fixture
def synthetic_payload():
    return json.loads((DATA_DIR / "patients.json").read_text(encoding="utf-8"))


@pytest.fixture
def write_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("IDA_DATA_DIR", str(tmp_path))

    def write(payload):
        path = tmp_path / "patients.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    return write
