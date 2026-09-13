"""Explicit configuration; importing this module never reads files or starts clients."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: Literal["gpt-5.4-mini"] = "gpt-5.4-mini"
    timezone: str = "America/New_York"
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    timeout_seconds: float = Field(default=60.0, gt=0, le=300, allow_inf_nan=False)
    max_output_tokens: int = Field(default=12000, ge=256, le=32000)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Expected an IANA timezone") from exc
        return value

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, env_file: Path | None = None
    ) -> Self:
        # .env loading is opt-in, has no global side effects, and never overrides env.
        values = dict(dotenv_values(env_file)) if env_file is not None else {}
        values.update(os.environ if env is None else env)
        fields = {
            "LOOPGRAPH_MODEL": "model",
            "LOOPGRAPH_TIMEZONE": "timezone",
            "LOOPGRAPH_LLM_TIMEOUT_SECONDS": "timeout_seconds",
            "LOOPGRAPH_LLM_MAX_OUTPUT_TOKENS": "max_output_tokens",
        }
        config = {target: values[key] for key, target in fields.items() if key in values}
        key = values.get("OPENAI_API_KEY")
        if key and key.strip():
            config["api_key"] = SecretStr(key.strip())
        return cls.model_validate(config)
