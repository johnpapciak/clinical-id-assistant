import json
from pathlib import Path
from unittest.mock import AsyncMock

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


@pytest.fixture
def agent_model(monkeypatch, tmp_path):
    from agents import OpenAIResponsesModel

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    model = AsyncMock(side_effect=AssertionError("Live model calls are disabled in tests"))
    monkeypatch.setattr(OpenAIResponsesModel, "get_response", model)
    return model
