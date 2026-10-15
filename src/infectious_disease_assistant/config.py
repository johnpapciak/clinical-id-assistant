from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IDA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    data_dir: Path = Path("data")


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
