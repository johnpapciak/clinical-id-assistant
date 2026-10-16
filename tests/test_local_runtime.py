from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from infectious_disease_assistant import local_runtime
from infectious_disease_assistant.config import ContainerDatabaseSettings, Settings, StartupSettings
from infectious_disease_assistant.fhir.errors import FhirTransportError
from infectious_disease_assistant.workflow.errors import WorkflowDatabaseError


def test_compose_fhir_host_is_a_narrow_opt_in(monkeypatch):
    monkeypatch.setenv('IDA_FHIR_BASE_URL', 'http://synthetic-fhir:8765/fhir')
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.setenv('IDA_FHIR_ALLOW_COMPOSE_HOST', 'true')
    assert Settings(_env_file=None).fhir_base_url == 'http://synthetic-fhir:8765/fhir'


@pytest.mark.parametrize('url', [
    'http://other-service:8765/fhir', 'http://synthetic-fhir:8766/fhir',
    'https://synthetic-fhir:8765/fhir', 'http://user:secret@synthetic-fhir:8765/fhir',
    'http://synthetic-fhir:8765/fhir?secret=x', 'http://10.0.0.2:8765/fhir',
])
def test_compose_opt_in_does_not_allow_arbitrary_endpoints(monkeypatch, url):
    monkeypatch.setenv('IDA_FHIR_ALLOW_COMPOSE_HOST', 'true')
    monkeypatch.setenv('IDA_FHIR_BASE_URL', url)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_database_password_is_encoded_without_exposing_it(monkeypatch):
    monkeypatch.setenv('POSTGRES_PASSWORD', 'synthetic:p@ss/word?#%')
    settings = ContainerDatabaseSettings(_env_file=None)
    url = settings.connection_url()
    assert urlsplit(url).hostname == 'guidelines-db'
    assert urlsplit(url).password == 'synthetic%3Ap%40ss%2Fword%3F%23%25'
    assert 'synthetic:p@ss/word?#%' not in repr(settings)


def test_retry_recovers_from_transient_dependency_failures(monkeypatch):
    operation = Mock(side_effect=[WorkflowDatabaseError('secret-password'), FhirTransportError('secret-response'), None])
    pause = Mock()
    monkeypatch.setattr(local_runtime, 'sleep', pause)
    local_runtime.retry(operation, StartupSettings(startup_attempts=3, startup_delay_seconds=0.1))
    assert operation.call_count == 3
    assert pause.call_count == 2


def test_retry_is_bounded_and_does_not_log_secrets(monkeypatch, caplog):
    operation = Mock(side_effect=WorkflowDatabaseError('secret-password'))
    pause = Mock()
    monkeypatch.setattr(local_runtime, 'sleep', pause)
    with pytest.raises(local_runtime.StartupError):
        local_runtime.retry(operation, StartupSettings(startup_attempts=2))
    assert operation.call_count == 2 and pause.call_count == 1
    assert 'secret-password' not in caplog.text


def test_programming_errors_are_not_retried(monkeypatch):
    operation = Mock(side_effect=TypeError('programming error'))
    with pytest.raises(TypeError):
        local_runtime.retry(operation, StartupSettings())
    assert operation.call_count == 1


def test_both_migration_sets_run_without_an_embedding_call(monkeypatch, rag_settings):
    guideline, workflow = Mock(), Mock()
    monkeypatch.setattr(local_runtime, 'RagSettings', lambda: rag_settings)
    monkeypatch.setattr(local_runtime, 'GuidelineStore', lambda settings: guideline)
    monkeypatch.setattr(local_runtime, 'WorkflowStore', lambda: workflow)
    local_runtime.migrate()
    guideline.initialize.assert_called_once_with()
    workflow.initialize.assert_called_once_with()


def test_api_startup_checks_dependencies_and_executes_without_shell(monkeypatch):
    monkeypatch.setattr('sys.argv', ['local_runtime', 'api'])
    monkeypatch.setenv('POSTGRES_PASSWORD', 'synthetic-secret')
    monkeypatch.setenv('DATABASE_URL', '')
    ready, execute = Mock(), Mock()
    monkeypatch.setattr(local_runtime, 'check_dependencies', ready)
    monkeypatch.setattr(local_runtime.os, 'execvpe', execute)
    assert local_runtime.main() == 0
    ready.assert_called_once_with()
    arguments = execute.call_args.args
    assert arguments[1][1:4] == ['-m', 'uvicorn', 'infectious_disease_assistant.api:app']
    assert arguments[2]['DATABASE_URL'].startswith('postgresql://ida_demo:')
    assert '--no-access-log' in arguments[1]


def test_explicit_database_configuration_is_preserved(monkeypatch):
    monkeypatch.setattr('sys.argv', ['local_runtime', 'python', '-m', 'infectious_disease_assistant.rag', 'init'])
    monkeypatch.setenv('POSTGRES_PASSWORD', 'ignored-secret')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://explicit/db')
    execute = Mock()
    monkeypatch.setattr(local_runtime.os, 'execvpe', execute)
    assert local_runtime.main() == 0
    assert execute.call_args.args[2]['DATABASE_URL'] == 'postgresql://explicit/db'


def test_failed_startup_does_not_expose_configuration(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['local_runtime', 'migrate'])
    monkeypatch.setattr(local_runtime, 'migrate', Mock(side_effect=WorkflowDatabaseError('secret-password')))
    monkeypatch.setenv('IDA_STARTUP_ATTEMPTS', '1')
    assert local_runtime.main() == 1
    assert 'secret-password' not in capsys.readouterr().err


def test_fhir_process_receives_no_database_configuration(monkeypatch):
    monkeypatch.setattr('sys.argv', ['local_runtime', 'fhir'])
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('POSTGRES_PASSWORD', raising=False)
    execute = Mock()
    monkeypatch.setattr(local_runtime.os, 'execvpe', execute)
    assert local_runtime.main() == 0
    assert 'DATABASE_URL' not in execute.call_args.args[2]
    assert execute.call_args.args[1][-2:] == ['--host', '0.0.0.0']
