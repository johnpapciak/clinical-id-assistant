from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

from pydantic import Field, SecretStr, StringConstraints, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from infectious_disease_assistant.models.patient import PatientId
from infectious_disease_assistant.models.workflow import ReviewerId


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IDA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    data_dir: Path = Path("data")
    current_user_id: Annotated[str, StringConstraints(pattern=r"^REVIEWER_[0-9]{3}$")] = "REVIEWER_001"
    patient_assignments: dict[ReviewerId, frozenset[PatientId]] = Field(
        default_factory=lambda: {"REVIEWER_001": frozenset({"PATIENT_001"})},
    )
    trace_include_payloads: bool = False
    store_model_responses: bool = False
    fhir_allow_compose_host: bool = False
    fhir_base_url: str = "http://127.0.0.1:8765/fhir"

    @field_validator("fhir_base_url")
    @classmethod
    def validate_local_fhir_url(cls, value: str, info: ValidationInfo) -> str:
        parsed = urlsplit(value)
        parsed.port
        allowed = parsed.hostname in {"localhost", "127.0.0.1", "::1"} or (
            info.data.get("fhir_allow_compose_host") and parsed.hostname == "synthetic-fhir" and parsed.port == 8765
        )
        if (
            parsed.scheme != "http" or not allowed
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/fhir"
        ):
            raise ValueError("FHIR must use a local synthetic HTTP endpoint")
        return value


class ContainerDatabaseSettings(Settings):
    database_host: Literal["guidelines-db"] = "guidelines-db"
    postgres_password: SecretStr = Field(validation_alias="POSTGRES_PASSWORD", min_length=1)

    def connection_url(self) -> str:
        password = quote(self.postgres_password.get_secret_value(), safe="")
        return f"postgresql://ida_demo:{password}@{self.database_host}:5432/ida_guidelines"


class StartupSettings(Settings):
    startup_attempts: int = Field(default=20, ge=1, le=60)
    startup_delay_seconds: float = Field(default=1.0, ge=0, le=5, allow_inf_nan=False)


class AgentSettings(Settings):
    model_config = SettingsConfigDict(hide_input_in_errors=True)

    openai_model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(
        validation_alias="OPENAI_MODEL",
    )
    openai_api_key: SecretStr = Field(validation_alias="OPENAI_API_KEY")

    @field_validator("openai_api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("OPENAI_API_KEY must not be blank")
        return value


class DatabaseSettings(Settings):
    model_config = SettingsConfigDict(hide_input_in_errors=True)

    database_url: SecretStr = Field(validation_alias="DATABASE_URL")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        if urlsplit(value.get_secret_value()).scheme not in {"postgres", "postgresql"}:
            raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
        return value


class RagSettings(DatabaseSettings):
    embedding_model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(
        default="text-embedding-3-small", validation_alias="OPENAI_EMBEDDING_MODEL",
    )
    embedding_dimensions: int = Field(default=1536, ge=1, le=3072)
    chunk_words: int = Field(default=120, ge=10, le=2000)
    chunk_overlap: int = Field(default=20, ge=0)

    @field_validator("chunk_overlap")
    @classmethod
    def validate_overlap(cls, value: int, info: ValidationInfo) -> int:
        if value >= info.data.get("chunk_words", 120):
            raise ValueError("Chunk overlap must be smaller than chunk size")
        return value


class EmbeddingCredentials(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    openai_api_key: SecretStr = Field(validation_alias="OPENAI_API_KEY")

    @field_validator("openai_api_key")
    @classmethod
    def validate_embedding_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("OPENAI_API_KEY must not be blank")
        return value
