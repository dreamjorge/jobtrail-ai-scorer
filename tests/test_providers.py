"""Provider boundary tests."""

import subprocess
from types import SimpleNamespace

import pytest

from jobtrail_ai_scorer.providers import (
    HermesProvider,
    HermesProviderConfig,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    ProviderConfigurationError,
    ProviderError,
    ProviderProcessError,
)


def test_hermes_provider_returns_raw_model_text(monkeypatch):
    """Hermes passes the prompt safely to its configured executable."""
    calls = []

    def fake_hermes_process(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"score": 80}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_hermes_process)
    config = HermesProviderConfig(executable="hermes", profile="local-profile")

    assert HermesProvider(config).score("prompt") == '{"score": 80}'
    assert calls == [
        (
            (["hermes", "--profile", "local-profile"],),
            {
                "input": "prompt",
                "capture_output": True,
                "check": False,
                "text": True,
                "timeout": 60.0,
            },
        )
    ]


def test_hermes_provider_wraps_nonzero_process_failure(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout="", stderr="bad profile"),
    )

    with pytest.raises(ProviderProcessError, match="exit code 2") as error:
        HermesProvider(HermesProviderConfig(executable="hermes", profile="test")).score(
            "private candidate prompt"
        )

    assert "bad profile" not in str(error.value)
    assert "private candidate prompt" not in str(error.value)


def test_hermes_provider_wraps_timeout(monkeypatch):
    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="hermes", timeout=60)

    monkeypatch.setattr(subprocess, "run", time_out)

    with pytest.raises(ProviderProcessError, match="timed out"):
        HermesProvider(HermesProviderConfig(executable="hermes", profile="test")).score("prompt")


def test_openai_compatible_provider_uses_explicit_configured_boundary(monkeypatch):
    monkeypatch.setenv("SCORER_API_KEY", "test-key")
    calls = []

    def transport(endpoint, *, headers, json):
        calls.append((endpoint, headers, json))
        return {"choices": [{"message": {"content": '{"score": 80}'}}]}

    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            endpoint="https://example.test/v1/chat/completions",
            model="test-model",
            api_key_env="SCORER_API_KEY",
        ),
        transport=transport,
    )

    assert provider.score("prompt") == '{"score": 80}'
    assert calls == [
        (
            "https://example.test/v1/chat/completions",
            {"Authorization": "Bearer test-key", "Content-Type": "application/json"},
            {"model": "test-model", "messages": [{"role": "user", "content": "prompt"}]},
        )
    ]


def test_openai_compatible_provider_requires_configured_api_key(monkeypatch):
    monkeypatch.delenv("SCORER_API_KEY", raising=False)
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            endpoint="https://example.test/v1/chat/completions",
            model="test-model",
            api_key_env="SCORER_API_KEY",
        )
    )

    with pytest.raises(ProviderConfigurationError, match="SCORER_API_KEY"):
        provider.score("prompt")


def test_hermes_provider_wraps_process_start_failure(monkeypatch):
    def missing_executable(*args, **kwargs):
        raise FileNotFoundError("hermes not found")

    monkeypatch.setattr(subprocess, "run", missing_executable)

    with pytest.raises(ProviderProcessError, match="Unable to start"):
        HermesProvider(HermesProviderConfig(executable="hermes", profile="test")).score("prompt")


@pytest.mark.parametrize("content", [None, ""])
def test_openai_compatible_provider_rejects_missing_or_empty_model_content(monkeypatch, content):
    monkeypatch.setenv("SCORER_API_KEY", "test-key")
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            endpoint="https://example.test/v1/chat/completions",
            model="test-model",
            api_key_env="SCORER_API_KEY",
        ),
        transport=lambda *args, **kwargs: {"choices": [{"message": {"content": content}}]},
    )

    with pytest.raises(ProviderError, match="invalid response"):
        provider.score("prompt")


def test_openai_compatible_provider_wraps_malformed_json(monkeypatch):
    monkeypatch.setenv("SCORER_API_KEY", "test-key")
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            endpoint="https://example.test/v1/chat/completions",
            model="test-model",
            api_key_env="SCORER_API_KEY",
        ),
        transport=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("invalid JSON")),
    )

    with pytest.raises(ProviderError, match="invalid response"):
        provider.score("prompt")


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_hermes_config_requires_positive_finite_timeout(timeout):
    with pytest.raises(ValueError, match="positive finite"):
        HermesProviderConfig(executable="hermes", profile="test", timeout_seconds=timeout)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_openai_config_requires_positive_finite_timeout(timeout):
    with pytest.raises(ValueError, match="positive finite"):
        OpenAICompatibleConfig(
            endpoint="https://example.test/v1/chat/completions",
            model="test-model",
            api_key_env="SCORER_API_KEY",
            timeout_seconds=timeout,
        )


def test_hermes_config_rejects_profile_that_could_be_an_option():
    with pytest.raises(ValueError, match="must not start with a dash"):
        HermesProviderConfig(executable="hermes", profile="--malicious")
