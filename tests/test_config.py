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


def test_app_config_trims_surrounding_marker_whitespace():
    config = AppConfig(
        jobtrail_base_url="https://jobs.test",
        candidate_profile_path="candidate-profile.md",
        marker=" [CUSTOM] ",
    )

    assert config.marker == "[CUSTOM]"


def test_load_config_preserves_hermes_provider_settings(tmp_path):
    config_path = tmp_path / "config.yaml"
    profile_path = tmp_path / "profile.md"
    config_path.write_text(
        f"jobtrail_base_url: https://jobs.test\n"
        f"candidate_profile_path: {profile_path}\n"
        "provider: hermes\n"
        "hermes_executable: hermes-test\n"
        "hermes_profile: test-profile\n"
        "provider_timeout_seconds: 12.5\n"
    )

    config = load_config(config_path)

    assert config.hermes_executable == "hermes-test"
    assert config.hermes_profile == "test-profile"
    assert config.provider_timeout_seconds == 12.5


def test_load_config_preserves_openai_compatible_provider_settings(tmp_path):
    config_path = tmp_path / "config.yaml"
    profile_path = tmp_path / "profile.md"
    config_path.write_text(
        f"jobtrail_base_url: https://jobs.test\n"
        f"candidate_profile_path: {profile_path}\n"
        "provider: openai_compatible\n"
        "openai_endpoint: https://api.example.test/v1/chat/completions\n"
        "openai_model: scoring-model\n"
        "openai_api_key_env: JOBTRAIL_TEST_API_KEY\n"
    )

    config = load_config(config_path)

    assert str(config.openai_endpoint) == "https://api.example.test/v1/chat/completions"
    assert config.openai_model == "scoring-model"
    assert config.openai_api_key_env == "JOBTRAIL_TEST_API_KEY"
