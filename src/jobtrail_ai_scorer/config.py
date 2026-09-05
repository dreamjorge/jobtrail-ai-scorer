"""Configuration loading and validation for the scorer."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import AnyHttpUrl, BaseModel, field_validator


class AppConfig(BaseModel):
    """Validated application configuration."""

    jobtrail_base_url: AnyHttpUrl
    candidate_profile_path: Path
    provider: Literal["hermes", "openai_compatible"] = "hermes"
    hermes_executable: str = "hermes"
    hermes_profile: str = "default"
    openai_endpoint: str = "http://localhost:8000/v1/chat/completions"
    openai_model: str = "default"
    openai_api_key_env: str = "OPENAI_API_KEY"
    provider_timeout_seconds: float = 60.0
    marker: str = "[AI_JOB_SCORE_V1]"

    @field_validator("marker")
    @classmethod
    def require_nonempty_marker(cls, value: str) -> str:
        """Reject markers that would match every note body."""

        marker = value.strip()
        if not marker:
            raise ValueError("marker must not be empty or whitespace")
        return marker


def load_config(config_path: Path) -> AppConfig:
    """Load and validate YAML configuration from ``config_path``."""

    data = yaml.safe_load(config_path.read_text()) or {}
    return AppConfig.model_validate(data)
