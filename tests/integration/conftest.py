import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from infectious_disease_assistant.config import DatabaseSettings, RagSettings
from infectious_disease_assistant.rag.database import GuidelineStore
from infectious_disease_assistant.workflow.database import WorkflowStore


@pytest.fixture
def isolated_database_url():
    url = os.environ.get("IDA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set IDA_TEST_DATABASE_URL to a disposable PostgreSQL/pgvector database")
    schema = "ida_test_" + uuid4().hex
    try:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            # Keep ledger lookups local even when public already contains initialized tables.
            for ledger in ["ida_guideline_migrations", "ida_workflow_migrations"]:
                connection.execute(sql.SQL("""CREATE TABLE {}.{} (
                    version text PRIMARY KEY, checksum text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )""").format(sql.Identifier(schema), sql.Identifier(ledger)))
        parts = urlsplit(url)
        query = [(key, value) for key, value in parse_qsl(parts.query) if key != "options"]
        query.append(("options", f"-csearch_path={schema},public"))
        yield urlunsplit(parts._replace(query=urlencode(query)))
    finally:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def postgres_settings(isolated_database_url, rag_settings):
    settings = RagSettings(
        _env_file=None, DATABASE_URL=isolated_database_url, OPENAI_EMBEDDING_MODEL="test-embedding",
        embedding_dimensions=3, data_dir=rag_settings.data_dir, chunk_words=40, chunk_overlap=5,
    )
    GuidelineStore(settings).initialize()
    return settings


@pytest.fixture
def workflow_settings(isolated_database_url):
    settings = DatabaseSettings(_env_file=None, DATABASE_URL=isolated_database_url)
    WorkflowStore(settings).initialize()
    return settings
