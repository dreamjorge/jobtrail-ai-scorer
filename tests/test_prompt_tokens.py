"""Tests for deterministic prompt token estimation and budget warnings."""

import json

import pytest

typer = pytest.importorskip("typer")  # noqa: E402

from jobtrail_ai_scorer import main  # noqa: E402
from jobtrail_ai_scorer.prompt_tokens import (  # noqa: E402
    DEFAULT_ESTIMATOR,
    PROMPT_TOKEN_BUDGET_ENV,
    PROMPT_TOKEN_ESTIMATOR_ENV,
    PromptTokenEstimator,
    estimate_sections,
    estimate_tokens,
    optional_budget_from_env,
)


VALID_SCORE = {
    "score": 80,
    "recommendation": "APPLY",
    "strengths": [],
    "gaps": [],
    "needs_confirmation": [],
    "hard_requirements_missing": [],
    "career_value": "High",
    "reasoning": "matches",
}


# --- estimate_tokens ----------------------------------------------------------


def test_estimate_tokens_chars4_default_divides_and_rounds_up():
    # 7 characters => ceil(7/4) = 2
    assert estimate_tokens("abcdefg") == 2
    # 8 characters => ceil(8/4) = 2
    assert estimate_tokens("abcdefgh") == 2
    # Empty string => 0
    assert estimate_tokens("") == 0


def test_estimate_tokens_words_mode_splits_on_whitespace():
    text = "one two three four five"
    # 5 words
    assert estimate_tokens(text, estimator=PromptTokenEstimator.WORDS) == 5


def test_estimate_tokens_words_mode_handles_irregular_whitespace():
    text = "  one\ttwo\nthree   four\r\nfive "
    assert estimate_tokens(text, estimator=PromptTokenEstimator.WORDS) == 5


def test_estimate_tokens_words_mode_empty_string_returns_zero():
    assert estimate_tokens("", estimator=PromptTokenEstimator.WORDS) == 0
    assert estimate_tokens("   ", estimator=PromptTokenEstimator.WORDS) == 0


def test_estimate_tokens_default_estimator_is_chars4():
    assert DEFAULT_ESTIMATOR == PromptTokenEstimator.CHARS4
    # 12 chars => 3 tokens under chars4
    assert estimate_tokens("a" * 12) == 3


def test_estimate_tokens_rejects_unknown_estimator():
    with pytest.raises(ValueError):
        estimate_tokens("hello", estimator="bytes")  # type: ignore[arg-type]


def test_estimate_tokens_rejects_non_string_text():
    with pytest.raises(TypeError):
        estimate_tokens(123)  # type: ignore[arg-type]


# --- estimate_sections --------------------------------------------------------


def test_estimate_sections_returns_dict_with_total_equal_to_sum():
    profile = "a" * 16  # chars4 -> 4
    cv = "b" * 32  # chars4 -> 8
    job_json = json.dumps({"id": "j1", "description": "Role"}, sort_keys=True)
    schema = "schema text"
    instructions = "instructions text"

    breakdown = estimate_sections(
        profile=profile,
        cv=cv,
        job=job_json,
        schema=schema,
        instructions=instructions,
    )

    assert set(breakdown.keys()) == {"profile", "cv", "job", "schema", "instructions", "total"}
    expected_total = (
        estimate_tokens(profile)
        + estimate_tokens(cv)
        + estimate_tokens(job_json)
        + estimate_tokens(schema)
        + estimate_tokens(instructions)
    )
    assert breakdown["total"] == expected_total
    assert breakdown["profile"] == estimate_tokens(profile)
    assert breakdown["cv"] == estimate_tokens(cv)
    assert breakdown["job"] == estimate_tokens(job_json)
    assert breakdown["schema"] == estimate_tokens(schema)
    assert breakdown["instructions"] == estimate_tokens(instructions)


def test_estimate_sections_respects_words_estimator_override(monkeypatch):
    monkeypatch.setenv(PROMPT_TOKEN_ESTIMATOR_ENV, "words")
    breakdown = estimate_sections(
        profile="one two",
        cv="three four five",
        job="six",
        schema="seven eight",
        instructions="nine ten eleven twelve",
    )
    # 2 + 3 + 1 + 2 + 4 = 12
    assert breakdown["profile"] == 2
    assert breakdown["cv"] == 3
    assert breakdown["job"] == 1
    assert breakdown["schema"] == 2
    assert breakdown["instructions"] == 4
    assert breakdown["total"] == 12


def test_estimate_sections_rejects_unknown_estimator_override(monkeypatch):
    monkeypatch.setenv(PROMPT_TOKEN_ESTIMATOR_ENV, "bogus")
    with pytest.raises(ValueError):
        estimate_sections(profile="x", cv="", job="", schema="", instructions="")


# --- budget warning -----------------------------------------------------------


def test_optional_budget_from_env_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv(PROMPT_TOKEN_BUDGET_ENV, raising=False)
    assert optional_budget_from_env() is None


def test_optional_budget_from_env_returns_positive_integer(monkeypatch):
    monkeypatch.setenv(PROMPT_TOKEN_BUDGET_ENV, "1234")
    assert optional_budget_from_env() == 1234


def test_optional_budget_from_env_rejects_zero_or_negative(monkeypatch):
    monkeypatch.setenv(PROMPT_TOKEN_BUDGET_ENV, "0")
    with pytest.raises(ValueError):
        optional_budget_from_env()
    monkeypatch.setenv(PROMPT_TOKEN_BUDGET_ENV, "-1")
    with pytest.raises(ValueError):
        optional_budget_from_env()


def test_optional_budget_from_env_rejects_non_integer(monkeypatch):
    monkeypatch.setenv(PROMPT_TOKEN_BUDGET_ENV, "fifty")
    with pytest.raises(ValueError):
        optional_budget_from_env()


# --- run_score wiring ---------------------------------------------------------


def _write_config(tmp_path, *, profile="PROFILE", cv=None):
    profile_path = tmp_path / "profile.md"
    profile_path.write_text(profile)
    config_path = tmp_path / "cfg.yaml"
    config_text = (
        f"jobtrail_base_url: http://localhost:3000\n"
        f"candidate_profile_path: {profile_path}\n"
        f"provider: hermes\nhermes_executable: hermes\nhermes_profile: default\n"
    )
    if cv is not None:
        cv_path = tmp_path / "cv.md"
        cv_path.write_text(cv)
        config_text += f"candidate_cv_path: {cv_path}\n"
    config_path.write_text(config_text)
    return config_path


class _FakeClient:
    def get_job(self, job_id):
        return {"id": job_id, "description": "A job"}

    def close(self):
        pass


class _FakeProvider:
    def score(self, prompt):
        return json.dumps(VALID_SCORE)


def test_run_score_emits_prompt_tokens_estimate_line(tmp_path, capsys):
    config_path = _write_config(tmp_path, profile="PROFILE CONTENT", cv="PRIVATE CV CONTENT")
    main.run_score(
        config_path=config_path,
        job_id="j1",
        dry_run=True,
        client_factory=lambda _: _FakeClient(),
        provider_factory=lambda _: _FakeProvider(),
    )
    captured = capsys.readouterr().out
    assert "prompt_tokens_estimate=" in captured
    # JSON-like payload that includes each section name and a total.
    payload = captured.split("prompt_tokens_estimate=", 1)[1].split()[0]
    data = json.loads(payload)
    for key in ("profile", "cv", "job", "schema", "instructions", "total"):
        assert key in data
    assert data["total"] == (
        data["profile"] + data["cv"] + data["job"] + data["schema"] + data["instructions"]
    )


def test_run_score_emits_only_one_prompt_tokens_estimate_line(tmp_path, capsys):
    config_path = _write_config(tmp_path, profile="PROFILE")
    main.run_score(
        config_path=config_path,
        job_id="j1",
        dry_run=True,
        client_factory=lambda _: _FakeClient(),
        provider_factory=lambda _: _FakeProvider(),
    )
    captured = capsys.readouterr().out
    assert captured.count("prompt_tokens_estimate=") == 1


def test_run_score_warns_when_total_exceeds_budget(tmp_path, capsys, monkeypatch):
    # Use a tiny budget so even the default chars4 estimate triggers the warning.
    monkeypatch.setenv(PROMPT_TOKEN_BUDGET_ENV, "1")
    config_path = _write_config(
        tmp_path, profile="PROFILE CONTENT THAT HAS MANY CHARACTERS", cv="MORE CV CONTENT"
    )
    main.run_score(
        config_path=config_path,
        job_id="j1",
        dry_run=True,
        client_factory=lambda _: _FakeClient(),
        provider_factory=lambda _: _FakeProvider(),
    )
    captured = capsys.readouterr().out
    assert "prompt_token_budget=" in captured
    # The structured payload must be machine-parseable.
    payload = captured.split("prompt_token_budget=", 1)[1].split()[0]
    data = json.loads(payload)
    assert set(data.keys()) == {"budget", "total"}
    assert data["budget"] == 1
    assert data["total"] > 1


def test_run_score_does_not_warn_when_total_is_at_or_below_budget(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(PROMPT_TOKEN_BUDGET_ENV, "1000000")
    config_path = _write_config(tmp_path, profile="PROFILE", cv="CV")
    main.run_score(
        config_path=config_path,
        job_id="j1",
        dry_run=True,
        client_factory=lambda _: _FakeClient(),
        provider_factory=lambda _: _FakeProvider(),
    )
    captured = capsys.readouterr().out
    assert "prompt_token_budget=" not in captured
    assert "prompt_tokens_estimate=" in captured


def test_run_score_does_not_warn_when_budget_unset(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(PROMPT_TOKEN_BUDGET_ENV, raising=False)
    config_path = _write_config(
        tmp_path, profile="PROFILE CONTENT THAT HAS MANY CHARACTERS", cv="MORE CV CONTENT"
    )
    main.run_score(
        config_path=config_path,
        job_id="j1",
        dry_run=True,
        client_factory=lambda _: _FakeClient(),
        provider_factory=lambda _: _FakeProvider(),
    )
    captured = capsys.readouterr().out
    assert "prompt_token_budget=" not in captured
    assert "prompt_tokens_estimate=" in captured
