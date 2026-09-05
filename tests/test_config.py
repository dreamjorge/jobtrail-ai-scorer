import pytest
from pydantic import ValidationError

from jobtrail_ai_scorer.config import AppConfig, load_config


def test_load_config_requires_jobtrail_url_and_profile_path(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("provider: hermes\n")

    with pytest.raises(ValidationError):
        load_config(config_path)


@pytest.mark.parametrize("marker", ["", " \t\n "])
def test_app_config_rejects_empty_or_whitespace_marker(marker):
    with pytest.raises(ValidationError, match="marker must not be empty or whitespace"):
        AppConfig(
            jobtrail_base_url="https://jobs.test",
            candidate_profile_path="candidate-profile.md",
            marker=marker,
        )
