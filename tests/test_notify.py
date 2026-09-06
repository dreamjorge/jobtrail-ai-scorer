"""Behavior tests for the bounded WhatsApp notification builder.

The :class:`NotificationBuilder` assembles a single summary dict from a
``ScoreResult``-like mapping and a JobTrail job mapping. The contract under
test is:

* only an explicit allowlist of fields appears in the rendered summary;
* the recommendation is one of ``PRIORITY_APPLY|APPLY|REVIEW|SKIP`` and
  defaults to ``APPLY`` when missing or unknown;
* the run id follows ``YYYY-MM-DD-HHMM-<short hash>``;
* the JobTrail link is built from the configured base and may be optionally
  rewritten through ``WHATSAPP_SHORT_URL_BASE``;
* forbidden CV/profile/prompt/credential sentinel substrings are never
  present in the rendered output.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Mapping

import pytest

from jobtrail_ai_scorer.notify import (
    ALLOWED_FIELDS,
    DEFAULT_RECOMMENDATION,
    FORBIDDEN_TOKENS,
    RECOMMENDATIONS,
    NotificationBuilder,
    build_job_trail_link,
    build_run_id,
    default_job_url_builder,
    normalize_recommendation,
    recommendation_label,
    short_url_for,
)


RUNID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}-[a-f0-9]{6}$")


# --- helpers -----------------------------------------------------------------


def _score(**overrides: Any) -> dict[str, Any]:
    """Return a ScoreResult-like mapping, overrideable per test."""

    base: dict[str, Any] = {
        "score": 91,
        "recommendation": "PRIORITY_APPLY",
        "strengths": ["Python"],
        "gaps": ["None"],
    }
    base.update(overrides)
    return base


def _job(**overrides: Any) -> dict[str, Any]:
    """Return a JobTrail job-like mapping, overrideable per test."""

    base: dict[str, Any] = {
        "id": "j1",
        "position": "Python Engineer",
        "company": "Acme",
        "location": "Queretaro",
        "jobUrl": "https://jobs.test/1",
    }
    base.update(overrides)
    return base


def _builder(
    *,
    base_url: str = "http://127.0.0.1:8000",
    short_url_base: str = "",
    clock=None,
    job_url_builder=None,
) -> NotificationBuilder:
    """Return a builder with deterministic defaults."""

    return NotificationBuilder(
        base_url=base_url,
        short_url_base=short_url_base,
        clock=clock,
        job_url_builder=job_url_builder,
    )


def _fixed_clock(moment: datetime):
    def _now() -> datetime:
        return moment

    return _now


# --- existence / shape of new fields -----------------------------------------


def test_notification_includes_three_new_fields():
    """The summary exposes jobTrailLink, recommendationLabel and runId."""

    body = _builder().build(score=_score(), job=_job())
    assert "jobTrailLink" in body
    assert "recommendationLabel" in body
    assert "runId" in body


def test_rendered_summary_keys_are_exactly_the_allowlist_when_id_present():
    """The allowlist is closed: every key in the rendered dict is allowlisted."""

    body = _builder().build(score=_score(), job=_job())
    extra = set(body) - ALLOWED_FIELDS
    assert extra == set()


# --- run id format -----------------------------------------------------------


def test_runid_format_matches_yyyy_mm_dd_hhmm_short_hash():
    """RunId follows ``YYYY-MM-DD-HHMM-<short hex>`` exactly."""

    fixed = datetime(2025, 4, 19, 9, 30)
    body = _builder(clock=_fixed_clock(fixed)).build(score=_score(), job=_job())
    assert RUNID_PATTERN.match(body["runId"]), body["runId"]
    assert body["runId"].startswith("2025-04-19-0930-")


def test_runid_is_stable_for_same_inputs():
    """Identical stamp + seed produce the same RunId across calls."""

    fixed = datetime(2025, 1, 2, 3, 4)
    builder = _builder(clock=_fixed_clock(fixed))
    first = builder.build(score=_score(), job=_job())["runId"]
    second = builder.build(score=_score(), job=_job())["runId"]
    assert first == second


def test_runid_differs_across_distinct_minutes():
    """Different clock minutes yield different RunIds."""

    a = _builder(clock=_fixed_clock(datetime(2025, 1, 1, 0, 0))).build(
        score=_score(), job=_job()
    )["runId"]
    b = _builder(clock=_fixed_clock(datetime(2025, 1, 1, 0, 1))).build(
        score=_score(), job=_job()
    )["runId"]
    assert a != b


# --- recommendation normalization -------------------------------------------


def test_recommendation_one_of_for_each_allowed_value():
    """Every allowed value passes through unchanged."""

    for value in RECOMMENDATIONS:
        body = _builder().build(score=_score(recommendation=value), job=_job())
        assert body["recommendation"] == value


def test_recommendation_defaults_to_apply_when_missing():
    """A missing recommendation becomes the default ``APPLY``."""

    score = _score()
    score.pop("recommendation")
    body = _builder().build(score=score, job=_job())
    assert body["recommendation"] == DEFAULT_RECOMMENDATION


def test_recommendation_defaults_to_apply_when_unknown():
    """An unknown recommendation is normalized to ``APPLY``."""

    body = _builder().build(score=_score(recommendation="MAYBE"), job=_job())
    assert body["recommendation"] == DEFAULT_RECOMMENDATION


def test_recommendation_label_matches_each_value():
    """Each recommendation carries a stable human-readable label."""

    expected = {
        "PRIORITY_APPLY": "Priority Apply",
        "APPLY": "Apply",
        "REVIEW": "Review",
        "SKIP": "Skip",
    }
    for value in RECOMMENDATIONS:
        body = _builder().build(score=_score(recommendation=value), job=_job())
        assert body["recommendationLabel"] == expected[value]


def test_recommendation_label_uses_default_when_input_unknown():
    """An unknown recommendation maps to the ``APPLY`` label."""

    body = _builder().build(score=_score(recommendation=""), job=_job())
    assert body["recommendationLabel"] == recommendation_label("")


# --- redaction tests ---------------------------------------------------------


def test_summary_redacts_cv_token():
    """Forbidden CV sentinel substrings never reach the rendered output."""

    job = _job(
        position="RESUME_SENTINEL-Python Engineer",
        candidate_cv="RESUME_SENTINEL raw curriculum body",
        description="RESUME_SENTINEL in raw description",
    )
    score = _score(strengths=["Excellent Python (RESUME_SENTINEL)"])
    body = _builder().build(score=score, job=job)
    rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
    assert "RESUME_SENTINEL" not in rendered


def test_summary_redacts_profile_token():
    """Forbidden profile sentinel substrings never reach the rendered output."""

    job = _job(candidate_profile="PROFILE_SENTINEL private profile body")
    score = _score(gaps=["PROFILE_SENTINEL exposed"])
    body = _builder().build(score=score, job=job)
    rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
    assert "PROFILE_SENTINEL" not in rendered


def test_summary_redacts_prompt_token():
    """Forbidden raw-prompt sentinel substrings never reach the rendered output."""

    job = _job(prompt="PROMPT_SENTINEL raw prompt text")
    score = _score(reasoning="PROMPT_SENTINEL raw reasoning")
    body = _builder().build(score=score, job=job)
    rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
    assert "PROMPT_SENTINEL" not in rendered


def test_summary_redacts_credential_token():
    """Forbidden credential sentinel substrings never reach the rendered output."""

    job = _job(api_key="CREDENTIAL_SENTINEL value")
    score = _score(notes=["CREDENTIAL_SENTINEL token"])
    body = _builder().build(score=score, job=job)
    rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
    assert "CREDENTIAL_SENTINEL" not in rendered


def test_summary_redacts_private_runtime_path_tokens():
    """Private runtime path tokens are scrubbed from rendered output."""

    job = _job(position="Engineer at /DATA/AppData/jobtrail")
    score = _score(strengths=["Located in /DATA/AppData/..."])
    body = _builder().build(score=score, job=job)
    rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
    for sensitive in ("/DATA/", "/AppData/"):
        assert sensitive not in rendered


def test_forbidden_tokens_constant_is_importable_and_contains_known_sentinels():
    """Operators can introspect the FORBIDDEN_TOKENS constant."""

    assert isinstance(FORBIDDEN_TOKENS, tuple)
    assert "RESUME_SENTINEL" in FORBIDDEN_TOKENS
    assert "PROFILE_SENTINEL" in FORBIDDEN_TOKENS
    assert "PROMPT_SENTINEL" in FORBIDDEN_TOKENS
    assert "CREDENTIAL_SENTINEL" in FORBIDDEN_TOKENS


# --- no extra fields --------------------------------------------------------


def test_no_extra_fields_in_rendered_summary():
    """The rendered summary contains no fields beyond the allowlist."""

    job = _job(
        description="PROMPT_SENTINEL raw description",
        notes="CREDENTIAL_SENTINEL raw notes",
        candidate_profile="PROFILE_SENTINEL private body",
        api_key="CREDENTIAL_SENTINEL token",
        prompt="PROMPT_SENTINEL raw prompt",
        candidate_cv="RESUME_SENTINEL private cv",
        secret="CREDENTIAL_SENTINEL secret",
        reasoning="FORBIDDEN reasoning",
    )
    body = _builder().build(score=_score(), job=job)
    assert set(body) <= set(ALLOWED_FIELDS)


def test_summary_excludes_sensitive_named_inputs_even_when_present():
    """Sensitive named fields are never copied into the rendered summary."""

    sensitive_payload = {
        "description": "long job description",
        "candidate_profile": "private profile body",
        "candidate_cv": "private cv body",
        "prompt": "raw provider prompt",
        "notes": "raw notes",
        "reasoning": "free-form reasoning",
        "api_key": "sk-LIVE-secret",
        "secret": "AWS-ACCESS-KEY example",
    }
    body = _builder().build(score=_score(**sensitive_payload), job=_job(**sensitive_payload))
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "long job description",
        "private profile body",
        "private cv body",
        "raw provider prompt",
        "free-form reasoning",
        "sk-LIVE-secret",
        "AWS-ACCESS-KEY example",
    ):
        assert forbidden not in serialized


# --- URL shortener opt-in ---------------------------------------------------


def test_url_shortener_optional_keeps_original_when_unset(monkeypatch):
    """Without WHATSAPP_SHORT_URL_BASE, the JobTrail link stays as-is."""

    monkeypatch.delenv("WHATSAPP_SHORT_URL_BASE", raising=False)
    builder = NotificationBuilder.from_env(
        base_url="http://127.0.0.1:8000",
    )
    body = builder.build(score=_score(), job=_job())
    assert body["jobTrailLink"] == "http://127.0.0.1:8000/jobs/j1"


def test_url_shortener_rewrites_when_env_is_set(monkeypatch):
    """WHATSAPP_SHORT_URL_BASE replaces the host while preserving the path."""

    monkeypatch.setenv("WHATSAPP_SHORT_URL_BASE", "https://sho.rt")
    builder = NotificationBuilder.from_env(base_url="http://127.0.0.1:8000")
    body = builder.build(score=_score(), job=_job())
    assert body["jobTrailLink"].startswith("https://sho.rt/")
    assert body["jobTrailLink"].endswith("/jobs/j1")
    assert "127.0.0.1" not in body["jobTrailLink"]


def test_url_shortener_tolerates_trailing_slash(monkeypatch):
    """A trailing slash on WHATSAPP_SHORT_URL_BASE does not double the path."""

    monkeypatch.setenv("WHATSAPP_SHORT_URL_BASE", "https://sho.rt/")
    builder = NotificationBuilder.from_env(base_url="http://127.0.0.1:8000")
    body = builder.build(score=_score(), job=_job())
    assert body["jobTrailLink"] == "https://sho.rt/jobs/j1"


def test_job_trail_link_omitted_when_no_job_id():
    """When the job has no id, the JobTrailLink field is omitted entirely."""

    body = _builder().build(score=_score(), job=_job(id=""))
    assert "jobTrailLink" not in body


def test_job_trail_link_url_encodes_special_characters():
    """Special characters in the job id are percent-encoded by the builder."""

    body = _builder().build(score=_score(), job=_job(id="j/1"))
    assert body["jobTrailLink"].endswith("/jobs/j%2F1")


def test_default_job_url_builder_uses_configured_base():
    """The default URL builder respects the base_url it was constructed with."""

    builder = default_job_url_builder("http://jobtrail.example.com")
    assert builder("abc") == "http://jobtrail.example.com/jobs/abc"
    assert builder("") == "http://jobtrail.example.com"


def test_build_job_trail_link_helper_normalizes_slashes():
    """The helper trims trailing slashes before composing the link."""

    assert build_job_trail_link("http://jobtrail.example.com/", "j1") == (
        "http://jobtrail.example.com/jobs/j1"
    )
    assert build_job_trail_link("", "j1") == "/jobs/j1"


def test_short_url_for_is_passthrough_when_short_base_empty():
    """An empty shortener base returns the original URL unchanged."""

    assert short_url_for("http://jobtrail.example.com/jobs/j1", "") == (
        "http://jobtrail.example.com/jobs/j1"
    )


# --- module-level helper smoke tests ---------------------------------------


def test_normalize_recommendation_accepts_known_values():
    for value in RECOMMENDATIONS:
        assert normalize_recommendation(value) == value


def test_normalize_recommendation_rejects_unknown():
    assert normalize_recommendation(None) == DEFAULT_RECOMMENDATION
    assert normalize_recommendation(42) == DEFAULT_RECOMMENDATION
    assert normalize_recommendation("") == DEFAULT_RECOMMENDATION
    assert normalize_recommendation("maybe") == DEFAULT_RECOMMENDATION


def test_recommendation_label_helper_is_stable():
    assert recommendation_label("PRIORITY_APPLY") == "Priority Apply"
    assert recommendation_label("APPLY") == "Apply"
    assert recommendation_label("REVIEW") == "Review"
    assert recommendation_label("SKIP") == "Skip"
    assert recommendation_label(None) == "Apply"


def test_build_run_id_is_deterministic_for_seed():
    """The same stamp and seed produce a stable RunId value."""

    a = build_run_id(datetime(2025, 5, 6, 7, 8), seed="alpha")
    b = build_run_id(datetime(2025, 5, 6, 7, 8), seed="alpha")
    c = build_run_id(datetime(2025, 5, 6, 7, 8), seed="beta")
    assert a == b
    assert a != c


# --- parametrized explicit allowlist contract ------------------------------


@pytest.mark.parametrize(
    "forbidden_input",
    [
        {"candidate_profile": "PROFILE_SENTINEL body", "description": "PROMPT_SENTINEL desc"},
        {"candidate_cv": "RESUME_SENTINEL cv", "notes": "CREDENTIAL_SENTINEL note"},
        {"prompt": "PROMPT_SENTINEL prompt", "secret": "CREDENTIAL_SENTINEL secret"},
    ],
)
def test_redaction_blocks_multiple_sensitive_areas(forbidden_input: Mapping[str, Any]):
    """A mix of sensitive inputs in different fields is always scrubbed."""

    job = _job(**forbidden_input)
    score = _score(**forbidden_input)
    body = _builder().build(score=score, job=job)
    rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
    for sentinel in (
        "RESUME_SENTINEL",
        "PROFILE_SENTINEL",
        "PROMPT_SENTINEL",
        "CREDENTIAL_SENTINEL",
    ):
        assert sentinel not in rendered
