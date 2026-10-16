import hashlib
from importlib.resources import files
from typing import Any

import psycopg
from psycopg import sql

DatabaseConnection = psycopg.Connection[dict[str, Any]]


class MigrationChangedError(RuntimeError):
    pass


def apply_migrations(connection: DatabaseConnection, *, package: str, ledger: str, lock_id: int) -> None:
    connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))
    table = sql.Identifier(ledger)
    connection.execute(sql.SQL("""
        CREATE TABLE IF NOT EXISTS {} (
            version text PRIMARY KEY, checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
    """).format(table))
    migrations = files(package).joinpath("sql")
    for migration in sorted(migrations.iterdir(), key=lambda item: item.name):
        if not migration.name.endswith(".sql"):
            continue
        source = migration.read_text(encoding="utf-8")
        checksum = hashlib.sha256(source.encode()).hexdigest()
        previous = connection.execute(
            sql.SQL("SELECT checksum FROM {} WHERE version = %s").format(table), (migration.name,),
        ).fetchone()
        if previous:
            if previous["checksum"] != checksum:
                raise MigrationChangedError("An applied migration has changed")
            continue
        connection.execute(source)
        connection.execute(
            sql.SQL("INSERT INTO {} (version, checksum) VALUES (%s, %s)").format(table),
            (migration.name, checksum),
        )
