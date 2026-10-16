import os
import sys
from collections.abc import Callable
from time import sleep

from pydantic import ValidationError
from pydantic_settings import SettingsError

from infectious_disease_assistant.config import ContainerDatabaseSettings, RagSettings, StartupSettings
from infectious_disease_assistant.fhir.client import FhirClient
from infectious_disease_assistant.fhir.errors import FhirError
from infectious_disease_assistant.event_logging import LOGGER, configure_logging
from infectious_disease_assistant.rag.database import GuidelineStore
from infectious_disease_assistant.rag.errors import RagError
from infectious_disease_assistant.workflow.database import WorkflowStore
from infectious_disease_assistant.workflow.errors import WorkflowDatabaseError

class StartupError(RuntimeError):
    pass


DEPENDENCY_ERRORS = (FhirError, RagError, WorkflowDatabaseError, StartupError)


def retry(operation: Callable[[], None], settings: StartupSettings) -> None:
    for attempt in range(settings.startup_attempts):
        try:
            operation()
            return
        except DEPENDENCY_ERRORS:
            LOGGER.warning("dependency_not_ready")
            if attempt + 1 < settings.startup_attempts:
                sleep(settings.startup_delay_seconds)
    raise StartupError("Required local services did not become ready") from None


def migrate() -> None:
    GuidelineStore(RagSettings()).initialize()
    WorkflowStore().initialize()


def check_dependencies() -> None:
    with FhirClient() as client:
        client.reference_date()
    with WorkflowStore().connection() as connection:
        row = connection.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
        if row is None:
            raise StartupError("Guideline database migrations have not been initialized")
        connection.execute("SELECT 1 FROM ida_investigations LIMIT 0")
        connection.execute("SELECT 1 FROM ida_guideline_chunks LIMIT 0")


def main() -> int:
    args = sys.argv[1:] or ["api"]
    try:
        settings = StartupSettings()
        configure_logging(settings.log_level)
        if args != ["fhir"] and not os.environ.get("DATABASE_URL") and os.environ.get("POSTGRES_PASSWORD"):
            os.environ["DATABASE_URL"] = ContainerDatabaseSettings().connection_url()
        if args == ["migrate"]:
            retry(migrate, settings)
            LOGGER.info("database_migrations_completed")
            return 0
        if args == ["check"]:
            retry(check_dependencies, settings)
            LOGGER.info("local_dependencies_ready")
            return 0
        if args == ["api"]:
            retry(check_dependencies, settings)
            args = [sys.executable, "-m", "uvicorn", "infectious_disease_assistant.api:app",
                    "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
        elif args == ["fhir"]:
            args = [sys.executable, "-m", "infectious_disease_assistant.fhir.server", "--host", "0.0.0.0"]
        os.execvpe(args[0], args, os.environ)
    except (ValidationError, SettingsError, *DEPENDENCY_ERRORS, OSError):
        LOGGER.error("local_startup_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
