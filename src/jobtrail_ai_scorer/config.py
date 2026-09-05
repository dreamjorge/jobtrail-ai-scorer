"""Configuration loading and validation for the scorer."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import AnyHttpUrl, BaseModel


class AppConfig(BaseModel):
    """Validated application configuration."""

    jobtrail_base_url: AnyHttpUrl
    candidate_profile_path: Path
    provider: Literal["hermes", "openai_compatible"] = "hermes"
    marker: str = "[AI_JOB_SCORE_V1]"


def load_config(config_path: Path) -> AppConfig:
    """Load and validate YAML configuration from ``config_path``."""

    data = yaml.safe_load(config_path.read_text()) or {}
    return AppConfig.model_validate(data)
