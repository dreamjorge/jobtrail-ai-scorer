import pytest
from pydantic import ValidationError

from jobtrail_ai_scorer.config import load_config


def test_load_config_requires_jobtrail_url_and_profile_path(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("provider: hermes\n")

    with pytest.raises(ValidationError):
        load_config(config_path)
