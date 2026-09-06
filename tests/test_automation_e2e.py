"""Hermetic end-to-end tests for ``JobTrailAutomation.run``.

These tests exercise the full search → import → score → notify pipeline
through the real orchestration code, replacing every external dependency
with an in-process stub:

* :class:`stubs.stub_jobtrail.StubJobTrailServer` runs an in-process
  :class:`http.server.BaseHTTPRequestHandler` that imitates
  ``/api/discover/search``, ``/api/discover/import``, and
  ``/api/jobs/<id>`` on a random localhost port;
* :class:`stubs.stub_scorer.StubScorer` writes a fixed ``[AI_JOB_SCORE_V1]``
  note into the JobTrail state so ``parse_score_note`` returns a valid
  score;
* :class:`stubs.stub_whatsapp.StubWhatsApp` captures the WhatsApp helper
  message body to an in-memory buffer;
* :class:`stubs.stub_jobspy.StubJobSpy` produces JobSpy-style search
  listings that the JobTrail server forwards verbatim.

The test suite covers five invariant contracts required by Issue #11:

1. Happy path — the full pipeline runs end-to-end with correct counts,
   the notes are persisted, and the captured WhatsApp message contains
   only allowlisted fields.
2. Dedup-skip-second-search — a second run with the same
   :class:`SeenCache` skips offers already imported within the TTL window.
3. Partial failure — a transient 5xx on one import is recorded on the
   run summary while the remaining offers still complete.
4. Redaction — sentinel substrings in strengths/gaps are scrubbed before
   they reach the WhatsApp buffer.
5. Single-notification — multiple scored jobs above the threshold still
   produce exactly one best-match notification.

No Docker, systemd, real network calls, or real CV/profile content is
required; the suite is suitable for CI.
"""

from __future__ import annotations

import json
import re
from typing import Iterator
from urllib.parse import unquote

import httpx
import pytest

from jobtrail_ai_scorer.automation import (
    AutomationConfig,
    AutomationRun,
    JobSearchAutomation,
    JobTrailHTTPClient,
)
from jobtrail_ai_scorer.notify import ALLOWED_FIELDS
from jobtrail_ai_scorer.retry import RetryPolicy
from jobtrail_ai_scorer.seen_cache import SeenCache

from stubs.stub_jobspy import StubJobSpy
from stubs.stub_jobtrail import StubJobTrailServer, StubJobTrailState
from stubs.stub_scorer import StubScorer
from stubs.stub_whatsapp import StubWhatsApp


pytestmark = pytest.mark.e2e


# --- Fixtures ----------------------------------------------------------------


_LISTING_ALPHA: dict = {
    "site": "indeed",
    "id": "alpha-1",
    "title": "Python Engineer",
    "company": "Acme",
    "description": "Build Python services.",
    "job_url": "https://jobs.test/alpha",
    "location": "Queretaro",
    "is_remote": False,
}

_LISTING_BETA: dict = {
    "site": "linkedin",
    "id": "beta-2",
    "title": "C++ Backend Engineer",
    "company": "Beta Inc",
    "description": "Build backend systems.",
    "job_url": "https://jobs.test/beta",
    "location": "remote",
    "is_remote": True,
}


# A bounded retry policy keeps the partial-failure test fast (the production
# default sleeps ~1.5s across three attempts). The base/max delays stay
# non-negative so the production ``RetryPolicy`` validator is satisfied.
_FAST_RETRY = RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0)


def _fast_retry_sleep(_seconds: float) -> None:  # pragma: no cover - trivial
    """No-op sleep so retries are exercised without slowing the suite."""


@pytest.fixture
def jobspy() -> StubJobSpy:
    """Two well-formed JobSpy listings used by every test."""

    return StubJobSpy([dict(_LISTING_ALPHA), dict(_LISTING_BETA)])


@pytest.fixture
def state(jobspy: StubJobSpy) -> StubJobTrailState:
    """Shared state bound to the JobSpy listings."""

    return StubJobTrailState(jobspy=jobspy)


@pytest.fixture
def server(state: StubJobTrailState) -> Iterator[StubJobTrailServer]:
    """In-process JobTrail HTTP server bound to a random localhost port."""

    with StubJobTrailServer(state) as srv:
        yield srv


def _make_config(
    server: StubJobTrailServer,
    **overrides,
) -> AutomationConfig:
    """Return an :class:`AutomationConfig` wired to the in-process server.

    The default fixtures pin a single location so the assertions on
    ``searched``/``imported`` counts are stable. Tests that need
    notification enabled override ``whatsapp_command`` so the production
    ``_notify`` fallback would still succeed; the e2e suite always passes a
    stub notifier instead, so the command value is irrelevant.
    """

    defaults: dict = dict(
        base_url=server.base_url,
        scorer_config_path="safe/config.yaml",
        locations=("Queretaro",),
        results_wanted=10,
        hours_old=72,
        max_score=10,
        score_threshold=80,
        notify_enabled=True,
        notify_on_failure=False,
        whatsapp_command="ignored",
        scorer_command="ignored",
    )
    defaults.update(overrides)
    return AutomationConfig(**defaults)


def _run(
    server: StubJobTrailServer,
    *,
    scorer: StubScorer,
    notifier: StubWhatsApp,
    seen_cache: SeenCache | None = None,
    config_overrides: dict | None = None,
) -> AutomationRun:
    """Drive ``JobTrailAutomation.run`` against the in-process server."""

    overrides = dict(config_overrides or {})
    config = _make_config(server, **overrides)
    client = JobTrailHTTPClient(
        server.base_url,
        retry_policy=_FAST_RETRY,
        retry_sleep=_fast_retry_sleep,
    )
    try:
        automation = JobSearchAutomation(
            client,
            scorer=scorer,
            notifier=notifier,
            seen_cache=seen_cache,
            retry_policy=_FAST_RETRY,
            retry_sleep=_fast_retry_sleep,
        )
        return automation.run(config=config)
    finally:
        client.close()


def _score_notes(
    *,
    score: int = 91,
    recommendation: str = "PRIORITY_APPLY",
    strengths: list[str] | None = None,
    gaps: list[str] | None = None,
) -> list[dict]:
    """Build ``[AI_JOB_SCORE_V1]`` notes with a fixed score payload."""

    body = json.dumps(
        {
            "score": score,
            "recommendation": recommendation,
            "strengths": list(strengths if strengths is not None else ["Python"]),
            "gaps": list(gaps if gaps is not None else ["None"]),
        }
    )
    return [{"body": f"[AI_JOB_SCORE_V1]\n{body}"}]


# --- Tests -------------------------------------------------------------------


def test_happy_path_drives_full_pipeline(server, state):
    """Search → import → score → notify works end-to-end with real HTTP."""

    scorer = StubScorer(state, default_score=91, recommendation="PRIORITY_APPLY")
    whatsapp = StubWhatsApp()

    result = _run(server, scorer=scorer, notifier=whatsapp)

    assert isinstance(result, AutomationRun)
    assert result.failures == ()
    assert result.searched == 2  # both listings returned for the single location
    assert result.imported == 2
    assert result.scored == 2
    assert result.selected is not None
    assert result.selected["score"] == 91
    assert result.selected["company"] in {"Acme", "Beta Inc"}

    # Notes were persisted through the real HTTP boundary.
    job_id_alpha = state.id_for("indeed", "alpha-1")
    assert any(
        "[AI_JOB_SCORE_V1]" in note["body"]
        for note in state.jobs[job_id_alpha]["notes"]
    )

    # Exactly one best-match WhatsApp message with only allowlisted fields.
    assert len(whatsapp.messages) == 1
    payload = json.loads(whatsapp.messages[0])
    assert set(payload) == set(ALLOWED_FIELDS)
    assert payload["score"] == 91
    assert payload["recommendation"] == "PRIORITY_APPLY"
    # The link is percent-encoded; decode it before comparing to the raw id.
    assert unquote(payload["jobTrailLink"]).endswith(f"/jobs/{job_id_alpha}")
    rendered = json.dumps(payload).lower()
    for forbidden in (
        "description",
        "candidate",
        "profile",
        "prompt",
        "credential",
        "resume",
        "secret",
    ):
        assert forbidden not in rendered


def test_dedup_skip_second_search(tmp_path, server, state):
    """A second run with the same ``SeenCache`` skips cached offers."""

    cache = SeenCache(tmp_path / "seen.json")
    scorer = StubScorer(state, default_score=85, recommendation="APPLY")
    whatsapp = StubWhatsApp()

    run_one = _run(server, scorer=scorer, notifier=whatsapp, seen_cache=cache)
    assert run_one.imported == 2
    assert run_one.scored == 2
    assert cache.size == 2
    assert whatsapp.messages  # first run delivered a best-match notification

    # Second run: fresh scorer/notifier, same cache. The cache filters both
    # offers at import time, so no second import or score is recorded.
    scorer_two = StubScorer(state, default_score=85, recommendation="APPLY")
    whatsapp_two = StubWhatsApp()
    run_two = _run(server, scorer=scorer_two, notifier=whatsapp_two, seen_cache=cache)

    assert run_two.searched == 2  # search still runs; cache filters at import
    assert run_two.imported == 0
    assert run_two.scored == 0
    assert run_two.failures == ()
    assert whatsapp_two.messages == []


def test_partial_failure_continues_run(server, state):
    """A transient 5xx on one import is recorded without stopping the run."""

    # Mark listing alpha's source id so the import endpoint returns 503 on
    # every attempt; the retry helper still exhausts its policy.
    state.fail_import_ids.add("alpha-1")

    scorer = StubScorer(state, default_score=88, recommendation="APPLY")
    whatsapp = StubWhatsApp()
    result = _run(
        server,
        scorer=scorer,
        notifier=whatsapp,
        config_overrides={
            "notify_enabled": False,
            "notify_on_failure": True,
        },
    )

    # The failing import was retried (3 attempts by the bounded policy) and
    # surfaced as an exhausted failure; the second offer still completed.
    assert result.searched == 2
    assert result.imported == 1
    assert result.scored == 1
    # Beta cleared the threshold and was selected as the best match; alpha's
    # import failure is reported separately.
    assert result.selected is not None
    assert result.selected["company"] == "Beta Inc"
    assert result.selected["score"] == 88
    assert any(
        failure.startswith("import:exhausted") and "HTTPStatusError" in failure
        for failure in result.failures
    ), result.failures

    # When ``notify_on_failure`` is set, the failure summary is delivered
    # even though ``notify_enabled`` is off.
    assert len(whatsapp.messages) == 1
    payload = json.loads(whatsapp.messages[0])
    assert payload["kind"] == "failure_summary"
    assert payload["failure_count"] >= 1
    for label in payload["failures"]:
        assert isinstance(label, str)
        assert "alpha-1" not in label  # labels are abstract
        assert "description" not in label.lower()


def test_redaction_strips_forbidden_tokens_from_notification(server, state):
    """Strengths/gaps containing sentinels are scrubbed before delivery."""

    # Configure the stub scorer so the notes it writes contain sentinel
    # substrings in the strengths/gaps arrays. The orchestrator overwrites
    # any pre-seeded notes with the scorer output, so the redaction test
    # must exercise the path through the scorer stub.
    scorer = StubScorer(
        state,
        default_score=92,
        recommendation="PRIORITY_APPLY",
        strengths=["PROMPT_SENTINEL exposed", "good"],
        gaps=["RESUME_SENTINEL exposed"],
    )
    whatsapp = StubWhatsApp()

    _run(server, scorer=scorer, notifier=whatsapp)

    assert len(whatsapp.messages) == 1
    body = whatsapp.messages[0]
    for sentinel in (
        "RESUME_SENTINEL",
        "PROFILE_SENTINEL",
        "PROMPT_SENTINEL",
        "CREDENTIAL_SENTINEL",
    ):
        assert sentinel not in body
    assert "[REDACTED]" in body

    # The parsed payload still exposes the strengths/gaps arrays but with
    # the sentinels replaced by the redacted placeholder.
    payload = json.loads(body)
    flat = json.dumps(payload)
    for sentinel in (
        "RESUME_SENTINEL",
        "PROFILE_SENTINEL",
        "PROMPT_SENTINEL",
        "CREDENTIAL_SENTINEL",
    ):
        assert sentinel not in flat
    assert "[REDACTED]" in flat


def test_get_job_endpoint_serves_persisted_notes(server, state):
    """The GET endpoint returns the score notes persisted by the scorer.

    Triangulation: the orchestrator reads job details through
    ``/api/jobs/<id>`` to assemble the WhatsApp notification. This test
    issues an out-of-band GET against the in-process server and confirms the
    notes written by the stub scorer are reachable through the HTTP boundary.
    """

    scorer = StubScorer(state, default_score=85, recommendation="APPLY")
    whatsapp = StubWhatsApp()

    _run(server, scorer=scorer, notifier=whatsapp)

    job_id = state.id_for("indeed", "alpha-1")
    response = httpx.get(f"{server.base_url}/api/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == job_id
    assert any("[AI_JOB_SCORE_V1]" in note["body"] for note in body["notes"])
    # The note body is the raw JSON that ``parse_score_note`` will read; verify
    # the shape is what the orchestrator expects.
    note_body = next(
        note["body"]
        for note in body["notes"]
        if "[AI_JOB_SCORE_V1]" in note["body"]
    )
    payload = json.loads(note_body.split("[AI_JOB_SCORE_V1]", 1)[1].strip())
    assert payload["score"] == 85
    assert payload["recommendation"] == "APPLY"


def test_single_notification_invariant_above_threshold(server, state):
    """Multiple scored jobs above the threshold produce one notification."""

    # Both alpha and beta score 92 with PRIORITY_APPLY; both clear the 80
    # threshold, but the orchestration must select exactly one best match
    # and deliver exactly one WhatsApp message.
    scorer = StubScorer(state, default_score=92, recommendation="PRIORITY_APPLY")
    whatsapp = StubWhatsApp()

    _run(server, scorer=scorer, notifier=whatsapp)

    assert len(whatsapp.messages) == 1
    payload = json.loads(whatsapp.messages[0])
    assert payload["score"] == 92
    assert payload["recommendation"] == "PRIORITY_APPLY"
    assert set(payload) == set(ALLOWED_FIELDS)
    # The run id matches the documented ``YYYY-MM-DD-HHMM-<6 hex>`` shape.
    assert re.match(r"^\d{4}-\d{2}-\d{2}-\d{4}-[a-f0-9]{6}$", payload["runId"])
    # Exactly one of the two jobs is referenced by the rendered link.
    decoded_link = unquote(payload["jobTrailLink"])
    expected_ids = {
        state.id_for("indeed", "alpha-1"),
        state.id_for("linkedin", "beta-2"),
    }
    assert any(decoded_link.endswith(f"/jobs/{job_id}") for job_id in expected_ids)
