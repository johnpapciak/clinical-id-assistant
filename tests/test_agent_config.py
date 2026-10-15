import pytest
from pydantic import ValidationError

from infectious_disease_assistant.config import AgentSettings, Settings


def test_deterministic_settings_need_no_openai_configuration(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    assert Settings(_env_file=None).log_level == "INFO"


def test_agent_configuration_uses_environment(agent_model):
    settings = AgentSettings()

    assert settings.openai_model == "test-model"
    assert settings.openai_api_key.get_secret_value() == "test-key-not-real"
    assert "test-key-not-real" not in repr(settings)
    assert "test-key-not-real" not in settings.model_dump_json()


@pytest.mark.parametrize("variable", ["OPENAI_MODEL", "OPENAI_API_KEY"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_agent_configuration(agent_model, monkeypatch, variable, value):
    if value is None:
        monkeypatch.delenv(variable)
    else:
        monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        AgentSettings()


def test_dotenv_and_environment_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_MODEL=dotenv-model\nOPENAI_API_KEY=fabricated-local-key\n")

    assert AgentSettings(_env_file=env_file).openai_model == "dotenv-model"
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    settings = AgentSettings(_env_file=env_file)
    assert settings.openai_model == "environment-model"
    assert settings.openai_api_key.get_secret_value() == "fabricated-local-key"


def test_configuration_errors_do_not_expose_input_secrets(agent_model, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "")

    with pytest.raises(ValidationError) as caught:
        AgentSettings()
    assert "test-key-not-real" not in str(caught.value)
