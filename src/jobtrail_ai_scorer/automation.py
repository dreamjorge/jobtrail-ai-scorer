"""Safe orchestration boundary for JobTrail search, scoring, and notification."""

from __future__ import annotations

import contextlib
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import shlex
import subprocess
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

import httpx

from .discover import (  # noqa: F401  (re-exported on purpose)
    BackendDiscoveryError,
    DEFAULT_DOCKER_CONTAINER as DISCOVER_DEFAULT_CONTAINER,
)
from .retry import RetryPolicy, classify_retryable, retry_call
from .seen_cache import SeenCache
from .sources import (
    JobSpySourceAdapter,
    NormalizedJob,
    SourceAdapter,
    SourceSearchRequest,
    build_ats_adapters,
    normalize_jobspy_job,
)
from .notify import NotificationBuilder, recommendation_label
from .run_journal import record_run


DEFAULT_TERMS = (
    "Python C++ MATLAB backend APIs databases automation CI/CD Docker LLMs agents"
)


@dataclass(frozen=True)
class SearchProfile:
    name: str
    search_terms: str | None = None
    sites: tuple[str, ...] | None = None
    locations: tuple[str, ...] | None = None
    results_wanted: int | None = None
    hours_old: int | None = None


_SEARCH_PROFILE_KEYS = {
    "name",
    "search_terms",
    "sites",
    "locations",
    "results_wanted",
    "hours_old",
}


def _optional_strings(value: Any, *, key: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"JOB_SEARCH_PROFILES {key} must be a list")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"JOB_SEARCH_PROFILES {key} entries must be strings")
    return tuple(item.strip() for item in value if item.strip())


def _parse_search_profiles(raw: str) -> tuple[SearchProfile, ...]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("JOB_SEARCH_PROFILES must be valid JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError("JOB_SEARCH_PROFILES must be a JSON list")

    profiles: list[SearchProfile] = []
    names: set[str] = set()
    for entry in parsed:
        if not isinstance(entry, dict):
            raise ValueError("JOB_SEARCH_PROFILES entries must be objects")
        unknown = set(entry) - _SEARCH_PROFILE_KEYS
        if unknown:
            raise ValueError("JOB_SEARCH_PROFILES contains unknown keys")
        name = str(entry.get("name", "")).strip()
        if not name:
            raise ValueError("JOB_SEARCH_PROFILES entries require non-blank names")
        if name in names:
            raise ValueError("JOB_SEARCH_PROFILES entries require unique names")
        names.add(name)
        search_terms = entry.get("search_terms")
        if search_terms is not None:
            search_terms = str(search_terms)
        profiles.append(
            SearchProfile(
                name=name,
                search_terms=search_terms,
                sites=_optional_strings(entry.get("sites"), key="sites"),
                locations=_optional_strings(entry.get("locations"), key="locations"),
                results_wanted=(
                    int(entry["results_wanted"])
                    if entry.get("results_wanted") is not None
                    else None
                ),
                hours_old=(
                    int(entry["hours_old"])
                    if entry.get("hours_old") is not None
                    else None
                ),
            )
        )
    return tuple(profiles)


@dataclass(frozen=True)
class AtsBoardConfig:
    """Configuration parsed from the ``JOB_ATS_BOARDS`` env var.

    Empty board lists mean "no ATS adapters for this provider". When
    ``JOB_ATS_BOARDS`` is unset, :attr:`AutomationConfig.ats_boards` is
    ``None`` and no ATS adapters are constructed. ``results_wanted`` is
    bounded to the closed interval ``[1, 200]`` and defaults to ``25``.
    """

    lever_boards: tuple[str, ...] = ()
    greenhouse_boards: tuple[str, ...] = ()
    results_wanted: int = 25


_ATS_BOARD_KEYS = frozenset(
    {"lever_boards", "greenhouse_boards", "results_wanted"}
)
_ATS_DEFAULT_RESULTS = 25
_ATS_MIN_RESULTS = 1
_ATS_MAX_RESULTS = 200


def _validate_ats_board_list(value: Any, *, provider: str) -> tuple[str, ...]:
    """Validate one ``lever_boards``/``greenhouse_boards`` list entry."""

    if not isinstance(value, list):
        raise ValueError(f"JOB_ATS_BOARDS {provider} must be a list")
    seen: set[str] = set()
    boards: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ValueError(
                f"JOB_ATS_BOARDS {provider} entries must be strings"
            )
        token = entry.strip()
        if not token:
            raise ValueError(
                f"JOB_ATS_BOARDS {provider} entries must be non-blank"
            )
        if token in seen:
            raise ValueError(
                f"JOB_ATS_BOARDS {provider} entries must be unique"
            )
        seen.add(token)
        boards.append(token)
    return tuple(boards)


def parse_ats_boards(raw: str) -> AtsBoardConfig:
    """Parse the ``JOB_ATS_BOARDS`` env var into an :class:`AtsBoardConfig`.

    The parser uses a closed allowlist of keys (``lever_boards``,
    ``greenhouse_boards``, ``results_wanted``) and raises ``ValueError``
    for any unknown key, non-object payload, blank or duplicate board
    token, or out-of-bounds ``results_wanted``. ``results_wanted``
    defaults to ``25`` and must satisfy ``1 <= results_wanted <= 200``.
    """

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("JOB_ATS_BOARDS must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("JOB_ATS_BOARDS must be a JSON object")
    unknown = set(parsed) - _ATS_BOARD_KEYS
    if unknown:
        raise ValueError("JOB_ATS_BOARDS contains unknown keys")

    lever_boards = (
        _validate_ats_board_list(parsed["lever_boards"], provider="lever_boards")
        if "lever_boards" in parsed
        else ()
    )
    greenhouse_boards = (
        _validate_ats_board_list(
            parsed["greenhouse_boards"], provider="greenhouse_boards"
        )
        if "greenhouse_boards" in parsed
        else ()
    )

    raw_results = parsed.get("results_wanted", _ATS_DEFAULT_RESULTS)
    # ``bool`` is a subclass of ``int`` in Python; reject it explicitly
    # so True/False cannot satisfy a strict "must be int" contract.
    if isinstance(raw_results, bool) or not isinstance(raw_results, int):
        raise ValueError("JOB_ATS_BOARDS results_wanted must be an int")
    if raw_results < _ATS_MIN_RESULTS or raw_results > _ATS_MAX_RESULTS:
        raise ValueError(
            f"JOB_ATS_BOARDS results_wanted must be between "
            f"{_ATS_MIN_RESULTS} and {_ATS_MAX_RESULTS}"
        )

    return AtsBoardConfig(
        lever_boards=lever_boards,
        greenhouse_boards=greenhouse_boards,
        results_wanted=raw_results,
    )


@dataclass(frozen=True)
class AutomationConfig:
    base_url: str = "http://127.0.0.1:8000"
    sites: tuple[str, ...] = ("linkedin", "indeed")
    search_terms: str = DEFAULT_TERMS
    locations: tuple[str, ...] = ("Queretaro", "remote")
    results_wanted: int = 10
    hours_old: int = 72
    search_profiles: tuple[SearchProfile, ...] = ()
    max_score: int = 10
    score_threshold: int = 80
    scorer_command: str = "jobtrail-ai-scorer"
    scorer_config_path: str = ""
    run_journal_path: str = ""
    notify_enabled: bool = False
    notify_on_failure: bool = False
    whatsapp_command: str = ""
    discover_container: str | None = None
    ats_boards: AtsBoardConfig | None = None
    # Circuit breaker configuration. ``breaker_state_path`` is the sentinel
    # for "breaker not configured" — an empty string means the
    # orchestrator will not build a :class:`CircuitBreaker` at all. Setting
    # ``BREAKER_STATE_PATH`` in the environment (or passing a non-empty
    # value here) wires the breaker to that path. The other breaker_*
    # fields tune the threshold and cooldowns; the underlying defaults
    # are defined in :mod:`jobtrail_ai_scorer.circuit_breaker`.
    breaker_failure_threshold: int = 3
    breaker_cooldown_seconds: float = 3600.0
    breaker_alert_cooldown_seconds: float = 3600.0
    breaker_state_path: str = ""
    dry_run: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AutomationConfig":
        e = os.environ if env is None else env

        def split(key: str, default: str, separator: str) -> tuple[str, ...]:
            return tuple(
                x.strip() for x in e.get(key, default).split(separator) if x.strip()
            )

        def truthy(key: str, default: str = "0") -> bool:
            return e.get(key, default).strip().lower() in {"1", "true", "yes", "on"}

        def _coerce_int(key: str, default: int) -> int:
            raw = e.get(key)
            if raw is None or raw == "":
                return default
            try:
                return int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be an integer") from exc

        def _coerce_float(key: str, default: float) -> float:
            raw = e.get(key)
            if raw is None or raw == "":
                return default
            try:
                return float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be a float") from exc

        discover_container = e.get("JOBTRAIL_DISCOVER_CONTAINER", "").strip() or None
        search_profiles = (
            _parse_search_profiles(e["JOB_SEARCH_PROFILES"])
            if "JOB_SEARCH_PROFILES" in e
            else ()
        )
        ats_boards = (
            parse_ats_boards(e["JOB_ATS_BOARDS"])
            if "JOB_ATS_BOARDS" in e
            else None
        )
        return cls(
            base_url=e.get("JOBTRAIL_BASE_URL", cls.base_url),
            sites=split("JOB_SEARCH_SITES", "linkedin,indeed", ","),
            search_terms=e.get("JOB_SEARCH_TERMS", DEFAULT_TERMS),
            locations=split("JOB_SEARCH_LOCATIONS", "Queretaro;remote", ";"),
            results_wanted=int(e.get("JOB_SEARCH_RESULTS_WANTED", "10")),
            hours_old=int(e.get("JOB_SEARCH_HOURS_OLD", "72")),
            search_profiles=search_profiles,
            max_score=int(e.get("JOB_SEARCH_MAX_SCORE", "10")),
            score_threshold=int(e.get("JOB_SCORE_THRESHOLD", "80")),
            scorer_command=e.get("SCORER_COMMAND", "jobtrail-ai-scorer"),
            scorer_config_path=e.get("SCORER_CONFIG_PATH", ""),
                run_journal_path=e.get("JOBTRAIL_RUN_JOURNAL_PATH", "").strip(),
            notify_enabled=truthy("WHATSAPP_NOTIFY_ENABLED", "0"),
            notify_on_failure=truthy("WHATSAPP_NOTIFY_ON_FAILURE", "0"),
            whatsapp_command=e.get("WHATSAPP_NOTIFY_COMMAND", ""),
            discover_container=discover_container,
            ats_boards=ats_boards,
            breaker_failure_threshold=_coerce_int(
                "BREAKER_FAILURE_THRESHOLD", cls.breaker_failure_threshold
            ),
            breaker_cooldown_seconds=_coerce_float(
                "BREAKER_COOLDOWN_SECONDS", cls.breaker_cooldown_seconds
            ),
            breaker_alert_cooldown_seconds=_coerce_float(
                "BREAKER_ALERT_COOLDOWN_SECONDS", cls.breaker_alert_cooldown_seconds
            ),
            breaker_state_path=e.get("BREAKER_STATE_PATH", "").strip(),
            dry_run=truthy("JOBTRAIL_AUTOMATION_DRY_RUN", "0"),
        )


def resolve_automation_base_url(
    config: AutomationConfig,
    *,
    container_name: str | None = None,
    probe: Any | None = None,
    inspect: Any | None = None,
) -> tuple[str, str]:
    """Return ``(url, source)`` honoring ``config.discover_container`` if set.

    Falls back to ``config.base_url`` (source ``"static"``) when discovery is
    not requested, preserving prior behavior for callers that pass
    ``JOBTRAIL_BASE_URL`` only. The precedence logic lives in
    :mod:`jobtrail_ai_scorer.discover`; this helper just adapts the
    ``AutomationConfig`` contract to it.
    """
    if config.discover_container:
        # Imported lazily so the static-only code path does not pull in the
        # dependency-free discovery helper for callers that never opt in.
        from .discover import resolve_backend_url

        return resolve_backend_url(
            container_name=container_name or config.discover_container,
            probe=probe,
            inspect=inspect,
        )
    return config.base_url, "static"


def merge_resolved_base_url(
    config: AutomationConfig,
    base_url: str,
) -> AutomationConfig:
    """Return a new ``AutomationConfig`` with ``base_url`` replaced by ``base_url``."""

    overrides = {**config.__dict__, "base_url": base_url}
    return AutomationConfig(**overrides)


def source_search_requests(config: AutomationConfig) -> list[SourceSearchRequest]:
    if not config.search_profiles:
        return [
            SourceSearchRequest(
                sites=config.sites,
                search_term=config.search_terms,
                location=location,
                results_wanted=config.results_wanted,
                hours_old=config.hours_old,
                is_remote=location.strip().lower() == "remote",
            )
            for location in config.locations
        ]

    requests: list[SourceSearchRequest] = []
    for profile in config.search_profiles:
        for location in profile.locations or config.locations:
            requests.append(
                SourceSearchRequest(
                    sites=profile.sites or config.sites,
                    search_term=profile.search_terms or config.search_terms,
                    location=location,
                    results_wanted=(
                        profile.results_wanted
                        if profile.results_wanted is not None
                        else config.results_wanted
                    ),
                    hours_old=(
                        profile.hours_old
                        if profile.hours_old is not None
                        else config.hours_old
                    ),
                    is_remote=location.strip().lower() == "remote",
                    profile_name=profile.name,
                )
            )
    return requests


def ats_source_search_requests(config: AutomationConfig) -> list[SourceSearchRequest]:
    """Build one request per configured profile for board-backed ATS sources."""

    results_wanted = (
        config.ats_boards.results_wanted
        if config.ats_boards is not None
        else config.results_wanted
    )
    profiles = config.search_profiles or (SearchProfile(name="default"),)
    requests: list[SourceSearchRequest] = []
    for profile in profiles:
        locations = profile.locations or config.locations
        location = locations[0] if locations else ""
        requests.append(
            SourceSearchRequest(
                sites=profile.sites or config.sites,
                search_term=profile.search_terms or config.search_terms,
                location=location,
                results_wanted=results_wanted,
                hours_old=(
                    profile.hours_old
                    if profile.hours_old is not None
                    else config.hours_old
                ),
                is_remote=location.strip().lower() == "remote",
                profile_name=profile.name,
            )
        )
    return requests


def search_payloads(config: AutomationConfig) -> list[dict[str, Any]]:
    return [request.to_jobspy_payload() for request in source_search_requests(config)]


def map_jobspy_job(job: Mapping[str, Any]) -> dict[str, Any]:
    return normalize_jobspy_job(job).to_import_payload()


def parse_score_note(notes: Any) -> dict[str, Any] | None:
    if not isinstance(notes, list):
        return None
    for note in reversed(notes):
        body = note.get("body") if isinstance(note, dict) else None
        if not isinstance(body, str) or "[AI_JOB_SCORE_V1]" not in body:
            continue
        try:
            value = json.loads(body.split("[AI_JOB_SCORE_V1]", 1)[1].strip())
            if isinstance(value, dict) and isinstance(value.get("score"), (int, float)):
                return value
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return None


def build_notification_summary(
    job: Mapping[str, Any],
    score: Mapping[str, Any],
    *,
    base_url: str = "",
) -> dict[str, Any]:
    """Build a bounded notification from explicitly allowlisted fields.

    The legacy 8-field summary is preserved for callers that do not pass a
    ``base_url``. When ``base_url`` is provided, the new
    :class:`NotificationBuilder` is used so the summary also exposes
    ``jobTrailLink``, ``recommendationLabel``, and ``runId``. The CV/profile/
    prompt/credential redaction contract is enforced inside the builder.
    """

    builder = NotificationBuilder.from_env(base_url=base_url)
    summary = builder.build(score=score, job=job)
    if "recommendationLabel" not in summary:
        # Defensive fallback for the no-base-url path so the legacy callers
        # still get a stable, normalized recommendation label.
        summary["recommendationLabel"] = recommendation_label(score.get("recommendation"))
    return summary


class AutomationGateway(Protocol):
    def search(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...
    def import_job(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_job(self, job_id: str) -> dict[str, Any]: ...


class JobTrailHTTPClient:
    """HTTP boundary kept injectable so orchestration never needs a live service in tests."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=30)
        self._owned = client is None
        # Retry is applied only to idempotent operations (search and GET).
        # Import is a non-idempotent POST and is deliberately never retried.
        self._retry_policy = retry_policy or RetryPolicy()
        self._retry_sleep = retry_sleep

    def close(self) -> None:
        if self._owned:
            self._client.close()

    def _post_once(self, path: str, payload: dict[str, Any]) -> Any:
        response = self._client.post(path, json=payload)
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        return retry_call(
            self._post_once,
            path,
            payload,
            policy=self._retry_policy,
            sleep=self._retry_sleep,
            label=f"POST {path}",
        )

    def search(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        result = self._post("/api/discover/search", payload)
        return (
            result
            if isinstance(result, list)
            else result.get("results", result.get("jobs", []))
        )

    def import_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._post_once("/api/discover/import", payload)
        return result if isinstance(result, dict) else {"id": result}

    def get_job(self, job_id: str) -> dict[str, Any]:
        def _do_get() -> Any:
            response = self._client.get(f"/api/jobs/{quote(job_id, safe='')}")
            response.raise_for_status()
            return response.json()

        return retry_call(
            _do_get,
            policy=self._retry_policy,
            sleep=self._retry_sleep,
            label=f"GET /api/jobs/{job_id}",
        )


@dataclass(frozen=True)
class AutomationRun:
    searched: int = 0
    imported: int = 0
    scored: int = 0
    failures: tuple[str, ...] = ()
    selected: dict[str, Any] | None = None
    profile_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    dry_run: bool = False
    planned_operations: dict[str, int] = field(default_factory=dict)
    notification_preview: dict[str, Any] | None = None


def _format_failure(stage: str, exc: BaseException, *, job_id: str | None = None) -> str:
    """Return a stable failure label that includes retry classification metadata.

    The label format is ``"<stage>[:<job_id>]:<classification>:<exc>"`` where
    ``classification`` is one of ``retryable`` (transient, retried),
    ``exhausted`` (retries failed), or ``terminal`` (immediate failure). The
    exception name is appended so operators can grep for a specific error type
    without leaking the exception message into the run summary.
    """

    classification = getattr(exc, "retry_metadata", {}).get("classification") or classify_retryable(exc)
    prefix = f"{stage}:{job_id}" if job_id else stage
    return f"{prefix}:{classification}:{type(exc).__name__}"


def build_failure_summary(
    failures: tuple[str, ...] | list[str], *, max_items: int = 5
) -> dict[str, Any]:
    """Return a bounded failure summary safe for WhatsApp notifications.

    The summary exposes only the failure count and the first ``max_items``
    abstract labels (which already strip sensitive content). The structure is
    explicit so operators can confirm notifications do not leak descriptions,
    profiles, or prompts.
    """

    items = [str(label) for label in list(failures)[:max_items]]
    return {
        "kind": "failure_summary",
        "failure_count": len(failures),
        "failures": items,
    }


class JobTrailAutomation:
    def __init__(
        self,
        gateway: AutomationGateway,
        *,
        scorer: Callable[[str, str], Any] | None = None,
        notifier: Callable[[str], Any] | None = None,
        seen_cache: SeenCache | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_sleep: Callable[[float], None] | None = None,
        source_adapters: tuple[SourceAdapter, ...] | None = None,
        ats_boards: AtsBoardConfig | None = None,
        preflight_runner: Callable[["AutomationConfig"], Any] | None = None,
        circuit_breaker: Any | None = None,
            run_journal: Callable[..., Any] | None = None,
            clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.gateway, self.scorer, self.notifier = (
            gateway,
            scorer or self._score,
            notifier or self._notify,
        )
        # ``seen_cache`` is opt-in. When provided, the cache is queried
        # before every ``/api/discover/import`` call so we don't re-import
        # offers already seen within the configured TTL window. Cache
        # failures must never crash a run; they are reported as
        # ``seen-cache:...`` entries on ``AutomationRun.failures``.
        self.seen_cache = seen_cache
        self._scorer_command = "jobtrail-ai-scorer"
        self._whatsapp_command = ""
        self._dry_run = False
        self.base_url = AutomationConfig.base_url
        # Scorer subprocess invocations are idempotent (the CLI runs with
        # ``--force``) so retry is safe. The default policy bounds attempts
        # and total backoff so a wedged provider cannot block a run forever.
        self._scorer_retry_policy = retry_policy or RetryPolicy(
            max_attempts=3,
            base_delay=0.5,
            max_delay=8.0,
        )
        self._scorer_retry_sleep = retry_sleep
        # ``preflight_runner`` and ``circuit_breaker`` are opt-in test
        # overrides. The default ``None`` keeps the legacy pipeline
        # unchanged: no preflight is run, and no breaker gates the run.
        # When ``preflight_runner`` is provided, ``run()`` calls it with
        # the resolved :class:`AutomationConfig` and treats a
        # ``should_abort`` report as a hard abort. When ``circuit_breaker``
        # is provided, ``run()`` consults it before the pipeline and
        # records success/failure afterwards; if no override is supplied
        # and the active config has a non-empty ``breaker_state_path``, a
        # default breaker is built from that config (see
        # :meth:`_resolve_breaker`).
        self._preflight_runner = preflight_runner
        self._circuit_breaker = circuit_breaker
        self._run_journal = run_journal or record_run
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # Default ``source_adapters`` preserves the historical
        # ``(JobSpySourceAdapter(gateway),)`` tuple when no ATS boards are
        # configured. When ``ats_boards`` is supplied, the factory appends
        # the configured Lever/Greenhouse adapters while explicit
        # ``source_adapters`` injection remains untouched.
        if source_adapters is None:
            if ats_boards is None:
                source_adapters = (JobSpySourceAdapter(gateway),)
            else:
                source_adapters = (
                    JobSpySourceAdapter(gateway),
                    *build_ats_adapters(ats_boards),
                )
        self.source_adapters = source_adapters

    def _invoke_scorer(self, job_id: str, config_path: str,
                       payload: dict[str, Any] | None = None) -> Any:
        """Call injected scorers with payload when supported."""
        try:
            inspect.signature(self.scorer).bind(job_id, config_path, payload)
        except (TypeError, ValueError):
            return self.scorer(job_id, config_path)
        return self.scorer(job_id, config_path, payload)

    def _score(self, job_id: str, config_path: str,
               payload: dict[str, Any] | None = None) -> Any:
        # No retry here: ``run()`` already wraps every call to ``self.scorer``
        # (default or injected) in a single ``retry_call`` with
        # ``_scorer_retry_policy``. Retrying here too would nest attempts
        # (up to max_attempts**2) and silently exceed the documented policy.
        #
        # ``--base-url`` propagates the URL this run actually searched and
        # imported against (``self.base_url``, set from the resolved
        # ``AutomationConfig.base_url`` in ``run()``) so the scorer targets
        # the same backend instead of falling back to whatever static
        # ``jobtrail_base_url`` is committed in the scorer's own YAML config.
        command = [*shlex.split(self._scorer_command), "score"]
        if self._dry_run:
            result = subprocess.run(
                [
                    *command,
                    "--dry-run",
                    "--json",
                    "--job-json",
                    "-",
                    "--config",
                    config_path,
                ],
                check=True,
                capture_output=True,
                input=json.dumps(
                    payload if payload is not None else {"id": job_id},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                text=True,
            )
            value = json.loads(result.stdout)
            if isinstance(value, list):
                value = next(
                    (
                        item
                        for item in value
                        if isinstance(item, dict)
                        and str(item.get("job_id")) == str(job_id)
                    ),
                    value[0] if len(value) == 1 else None,
                )
            if not isinstance(value, dict) or not isinstance(
                value.get("score"), (int, float)
            ):
                raise ValueError("scorer dry-run output must be a score mapping")
            return value
        subprocess.run([*command, "--config", config_path, "--job-id", job_id, "--force", "--base-url", self.base_url], check=True)
        return None

    def _notify(self, message: str) -> None:
        if not self._whatsapp_command:
            raise ValueError(
                "WHATSAPP_NOTIFY_COMMAND is required when notifications are enabled"
            )
        subprocess.run(
            shlex.split(self._whatsapp_command), input=message, text=True, check=True
        )

    def run(self, *, config: AutomationConfig) -> AutomationRun:
        started_at = self._clock()
        if not config.scorer_config_path:
            raise ValueError("SCORER_CONFIG_PATH is required")
        self._scorer_command = config.scorer_command
        self._whatsapp_command = config.whatsapp_command
        self._dry_run = config.dry_run
        self.base_url = config.base_url

        if config.dry_run:
            if self._preflight_runner is not None:
                report = self._preflight_runner(config)
                if getattr(report, "should_abort", False):
                    return self._empty_run(tuple(self._preflight_failure_labels(report)), breaker=None, dry_run=True)
            return self._run_dry(config=config)

        # Step 0: resolve the circuit breaker.
        #
        # The injection override (constructor) wins; otherwise the breaker
        # is built from ``config.breaker_*`` when the config opts in via
        # a non-empty ``breaker_state_path``. An empty path means "no
        # breaker configured" so the legacy pipeline is preserved.
        breaker = self._resolve_breaker(config)
        if breaker is not None and not breaker.should_attempt():
            # Step 0a: breaker is OPEN. Record a single bounded failure,
            # attempt one alert (gated by the breaker's own cooldown and
            # the operator's notify flags), and return an empty run. The
            # breaker-opened run MUST NOT increment the failure counter
            # so the alert does not extend the cooldown.
            run = self._handle_breaker_open(
                breaker=breaker,
                config=config,
            )

            self._journal_run(config, run, started_at)
            return run

        if self._preflight_runner is not None:
            report = self._preflight_runner(config)
            if getattr(report, "should_abort", False):
                preflight_failures = self._preflight_failure_labels(report)
                run = self._empty_run(tuple(preflight_failures), breaker=breaker)
                self._journal_run(config, run, started_at)
                return run

        failures: list[str] = []
        ids: list[str] = []
        scored_ids: list[str] = []
        searched = imported = scored = 0
        count_profiles = bool(config.search_profiles)
        profile_counts: dict[str, dict[str, int]] = (
            {
                profile.name: {
                    "searched": 0,
                    "imported": 0,
                    "duplicates": 0,
                    "failures": 0,
                }
                for profile in config.search_profiles
            }
            if count_profiles
            else {}
        )
        imported_by_identity: dict[tuple[str, str], str] = {}
        profiles_by_job_id: dict[str, list[str]] = {}
        for adapter in self.source_adapters:
            requests = (
                ats_source_search_requests(config)
                if getattr(adapter, "name", None) in {"lever", "greenhouse"}
                else source_search_requests(config)
            )
            for request in requests:
                profile_name = request.profile_name
                try:
                    jobs = adapter.search(request)
                    searched += len(jobs)
                    if count_profiles:
                        profile_counts[profile_name]["searched"] += len(jobs)
                    for job in jobs:
                        try:
                            # Hold the seen-cache lock across the whole
                            # check/import/mark sequence (not just each call in
                            # isolation) so an overlapping run can never import
                            # the same offer twice or drop this run's mark; see
                            # SeenCache.transaction.
                            cache_txn = (
                                self.seen_cache.transaction()
                                if self.seen_cache is not None
                                else contextlib.nullcontext()
                            )
                            with cache_txn:
                                identity = self._cache_identity(job)
                                if count_profiles and identity in imported_by_identity:
                                    profile_counts[profile_name]["duplicates"] += 1
                                    winning_job_id = imported_by_identity[identity]
                                    self._append_profile_provenance(
                                        profiles_by_job_id,
                                        winning_job_id,
                                        profile_name,
                                    )
                                    continue
                                if self._is_cached(
                                    job,
                                    hours_old=request.hours_old,
                                    failures=failures,
                                ):
                                    continue
                                import_payload = job.to_import_payload()
                                result = self.gateway.import_job(import_payload)
                                job_id = result.get("id")
                                if job_id is not None and str(job_id) not in ids:
                                    job_id_str = str(job_id)
                                    ids.append(job_id_str)
                                    imported += 1
                                    if count_profiles:
                                        profile_counts[profile_name]["imported"] += 1
                                        if identity is not None:
                                            imported_by_identity[identity] = job_id_str
                                        self._append_profile_provenance(
                                            profiles_by_job_id,
                                            job_id_str,
                                            profile_name,
                                        )
                                self._record_seen(job, failures=failures)
                        except Exception as exc:
                            failures.append(_format_failure("import", exc))
                            if count_profiles:
                                profile_counts[profile_name]["failures"] += 1
                except Exception as exc:
                    failures.append(_format_failure("search", exc))
                    if count_profiles:
                        profile_counts[profile_name]["failures"] += 1
        for job_id in ids[: config.max_score]:
            try:
                retry_call(
                    self.scorer,
                    job_id,
                    config.scorer_config_path,
                    policy=self._scorer_retry_policy,
                    sleep=self._scorer_retry_sleep,
                    label=f"scorer score {job_id}",
                )
                scored_ids.append(job_id)
                scored += 1
            except Exception as exc:
                failures.append(_format_failure("score", exc, job_id=job_id))
        best = None
        best_job = None
        best_score = None
        best_job_id = None
        for job_id in scored_ids:
            try:
                job = self.gateway.get_job(job_id)
                score = parse_score_note(job.get("notes"))
                if (
                    score
                    and score.get("score", -1) >= config.score_threshold
                    and (best is None or score["score"] > best["score"])
                ):
                    best = {
                        "title": job.get("position", job.get("title", "")),
                        "company": job.get("company", ""),
                        "location": job.get("location", ""),
                        "score": score["score"],
                        "recommendation": score.get("recommendation", ""),
                        "strengths": score.get("strengths", []),
                        "gaps": score.get("gaps", []),
                        "jobUrl": job.get("jobUrl", job.get("job_url", "")),
                    }
                    best_job = job
                    best_score = score
                    best_job_id = job_id
            except Exception as exc:
                failures.append(_format_failure("read", exc, job_id=job_id))

        if best is not None and best_job_id in profiles_by_job_id:
            best["searchProfiles"] = list(profiles_by_job_id[best_job_id])
        notification_body = self._compose_notification(
            best=best,
            best_job=best_job,
            best_score=best_score,
            failures=tuple(failures),
            notify_enabled=config.notify_enabled,
            notify_on_failure=config.notify_on_failure,
            base_url=self.base_url,
        )
        if notification_body is not None:
            try:
                self.notifier(notification_body)
            except Exception:
                failures.append("notify")
        run = AutomationRun(
            searched,
            imported,
            scored,
            tuple(failures),
            best,
            profile_counts,
        )
        # Step N: record the run outcome on the breaker so it can open
        # after consecutive failures or close after a clean run.
        if breaker is not None:
            if run.failures:
                breaker.record_failure()
            else:
                breaker.record_success()
        self._journal_run(config, run, started_at)
        return run

    def _run_dry(self, *, config: AutomationConfig) -> AutomationRun:
        failures: list[str] = []
        searched = scored = planned_imported = planned_notified = 0
        previews: list[tuple[str, dict[str, Any]]] = []
        identities: dict[tuple[str, str], str] = {}
        profiles_by_id: dict[str, list[str]] = {}
        count_profiles = bool(config.search_profiles)
        profile_counts = ({p.name: {"searched": 0, "imported": 0, "duplicates": 0, "failures": 0}
                          for p in config.search_profiles} if count_profiles else {})
        for adapter in self.source_adapters:
            requests = (ats_source_search_requests(config)
                        if getattr(adapter, "name", None) in {"lever", "greenhouse"}
                        else source_search_requests(config))
            for request in requests:
                try:
                    jobs = adapter.search(request)
                    searched += len(jobs)
                    if count_profiles:
                        profile_counts[request.profile_name]["searched"] += len(jobs)
                    for job in jobs:
                        payload = job.to_import_payload()
                        identity = self._cache_identity(job)
                        if identity is not None and identity in identities:
                            if count_profiles:
                                profile_counts[request.profile_name]["duplicates"] += 1
                            self._append_profile_provenance(profiles_by_id, identities[identity], request.profile_name)
                            continue
                        job_id = identity[1] if identity else str(payload.get("jobUrl") or payload.get("position") or searched)
                        if identity is not None:
                            identities[identity] = job_id
                        previews.append((job_id, payload))
                        planned_imported += 1
                        if count_profiles:
                            profile_counts[request.profile_name]["imported"] += 1
                        self._append_profile_provenance(profiles_by_id, job_id, request.profile_name)
                except Exception as exc:
                    failures.append(_format_failure("search", exc))
                    if count_profiles:
                        profile_counts[request.profile_name]["failures"] += 1
        scored_previews: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for job_id, job in previews[:config.max_score]:
            try:
                score = retry_call(
                    self._invoke_scorer, job_id, config.scorer_config_path, job,
                    policy=self._scorer_retry_policy, sleep=self._scorer_retry_sleep,
                    label=f"scorer score {job_id}",
                )
                if not isinstance(score, Mapping) or not isinstance(score.get("score"), (int, float)):
                    raise ValueError("scorer preview must be a score mapping")
                scored_previews.append((job_id, job, dict(score)))
                scored += 1
            except Exception as exc:
                failures.append(_format_failure("score", exc, job_id=job_id))
        best = best_job = best_score = best_job_id = None
        for job_id, job, score in scored_previews:
            if score["score"] >= config.score_threshold and (best is None or score["score"] > best["score"]):
                best = {"title": job.get("position", job.get("title", "")), "company": job.get("company", ""),
                        "location": job.get("location", ""), "score": score["score"],
                        "recommendation": score.get("recommendation", ""), "strengths": score.get("strengths", []),
                        "gaps": score.get("gaps", []), "jobUrl": job.get("jobUrl", job.get("job_url", ""))}
                best_job, best_score, best_job_id = job, score, job_id
        if best is not None and best_job_id in profiles_by_id:
            best["searchProfiles"] = list(profiles_by_id[best_job_id])
        body = self._compose_notification(best=best, best_job=best_job, best_score=best_score,
                                           failures=tuple(failures), notify_enabled=config.notify_enabled,
                                           notify_on_failure=config.notify_on_failure, base_url=self.base_url)
        notification_preview = None
        if body is not None:
            planned_notified = 1
            try:
                notification_preview = json.loads(body.split("\n", 1)[0])
            except (TypeError, json.JSONDecodeError):
                notification_preview = {"kind": "notification_preview"}
        return AutomationRun(searched, 0, scored, tuple(failures), best, profile_counts, True,
                             {"searched": searched, "imported": planned_imported, "scored": scored,
                              "notified": planned_notified}, notification_preview)

    def _journal_run(self, config: AutomationConfig, run: AutomationRun, started_at: datetime) -> None:
        if not config.run_journal_path:
            return
        try:
            self._run_journal(config.run_journal_path, run, started_at=started_at,
                              finished_at=self._clock(), base_url_source="static")
        except Exception:
            pass

    # --- Breaker / preflight helpers --------------------------------------

    def _resolve_breaker(self, config: "AutomationConfig") -> Any | None:
        """Return the breaker to use for this run, or ``None`` to skip.

        The constructor override wins. When no override is supplied, a
        default :class:`CircuitBreaker` is built from
        ``config.breaker_*`` only when ``config.breaker_state_path`` is
        non-empty (the sentinel for "not configured"). An empty path
        preserves the legacy pipeline so existing callers that construct
        ``AutomationConfig`` without opting in see no behavior change.
        """

        if self._circuit_breaker is not None:
            return self._circuit_breaker
        if not config.breaker_state_path:
            return None
        # Lazy import so callers that never opt in don't pay the cost
        # of the circuit_breaker module's atomic-JSON helper.
        from .circuit_breaker import BreakerConfig, CircuitBreaker

        breaker_config = BreakerConfig(
            failure_threshold=config.breaker_failure_threshold,
            cooldown_seconds=config.breaker_cooldown_seconds,
            alert_cooldown_seconds=config.breaker_alert_cooldown_seconds,
            state_path=config.breaker_state_path,
        )
        return CircuitBreaker(breaker_config)

    def _handle_breaker_open(
        self,
        *,
        breaker: Any,
        config: "AutomationConfig",
    ) -> "AutomationRun":
        """Return an empty run for an OPEN breaker, emitting one alert.

        The breaker-opened run is deliberately short: the legacy
        pipeline never runs, the failure counter is not incremented
        (so the alert does not extend the cooldown), and the notifier
        is called at most once per alert cooldown when notifications
        are enabled via ``notify_enabled`` or ``notify_on_failure``.
        """

        failures: list[str] = ["breaker:open"]
        if (
            breaker.try_alert()
            and (config.notify_enabled or config.notify_on_failure)
        ):
            self._send_breaker_alert(failures=failures)
        return AutomationRun(
            0,
            0,
            0,
            tuple(failures),
            None,
            {},
        )

    def _send_breaker_alert(
        self,
        *,
        failures: list[str],
    ) -> None:
        """Best-effort delivery of the breaker-open alert.

        Failures here must not crash the run; they are appended to the
        ``failures`` list as ``notify`` so the operator can see the
        notifier crashed without losing the breaker-opened state.
        """

        body = json.dumps(
            {
                "kind": "breaker_open",
                "message": "Circuit breaker is open; automation paused.",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            self.notifier(body)
        except Exception:
            failures.append("notify")

    @staticmethod
    def _preflight_failure_labels(report: Any) -> list[str]:
        """Return one ``preflight:unavailable:<name>`` label per failing check."""

        labels: list[str] = []
        required_map = getattr(report, "_required", {}) or {}
        for result in report:
            if result.status == "unavailable" and required_map.get(
                result.name, True
            ):
                labels.append(f"preflight:unavailable:{result.name}")
        return labels

    @staticmethod
    def _empty_run(
        failures: tuple[str, ...],
        *,
        breaker: Any | None,
        dry_run: bool = False,
    ) -> "AutomationRun":
        """Return an empty :class:`AutomationRun` for the short-circuit paths.

        The breaker is recorded as a failure when one or more failures
        are present so a hard preflight-abort can still trip the
        breaker after enough consecutive hard-abort runs. No record is
        made for the breaker-opened path (the run was already inside
        an OPEN state, so recording there is a no-op in the standard
        state machine and would extend the cooldown).
        """

        run = AutomationRun(0, 0, 0, failures, None, {}, dry_run,
                             {"searched": 0, "imported": 0, "scored": 0, "notified": 0} if dry_run else {})
        if breaker is not None and failures:
            breaker.record_failure()
        return run

    @staticmethod
    def _compose_notification(
        *,
        best: dict[str, Any] | None,
        best_job: dict[str, Any] | None,
        best_score: dict[str, Any] | None,
        failures: tuple[str, ...],
        notify_enabled: bool,
        notify_on_failure: bool,
        base_url: str = "",
    ) -> str | None:
        """Assemble the WhatsApp helper message from the run's outcome.

        The body is sent only when there is something to report:
        - ``notify_enabled`` is set and a best match was selected, or
        - ``notify_on_failure`` is set and at least one failure was recorded.
        When both apply, the failure summary is appended on a separate line so
        the best-match JSON remains diff-friendly.

        The best-match payload is now produced by
        :class:`NotificationBuilder` so the daily summary exposes
        ``jobTrailLink``, ``recommendationLabel``, and ``runId``. The
        optional ``WHATSAPP_SHORT_URL_BASE`` env var rewrites the
        JobTrail host while preserving the trailing ``/jobs/<id>`` path.
        """

        match_body: str | None = None
        if best is not None and notify_enabled:
            score_for_notification = dict(best_score or {})
            if best.get("searchProfiles"):
                score_for_notification["searchProfiles"] = best["searchProfiles"]
            match_body = json.dumps(
                build_notification_summary(
                    best_job or {},
                    score_for_notification,
                    base_url=base_url,
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
        failure_body: str | None = None
        if notify_on_failure and failures:
            failure_body = json.dumps(
                build_failure_summary(failures),
                ensure_ascii=False,
                sort_keys=True,
            )
        if match_body is not None and failure_body is not None:
            return f"{match_body}\n{failure_body}"
        return match_body or failure_body

    @staticmethod
    def _normalized_identity(
        source: Any, source_job_id: Any
    ) -> tuple[str, str] | None:
        if not isinstance(source, str) or not source.strip():
            return None
        if not isinstance(source_job_id, str) or not source_job_id.strip():
            return None
        return source.strip().lower(), source_job_id.strip().lower()

    @staticmethod
    def _append_profile_provenance(
        profiles_by_job_id: dict[str, list[str]], job_id: str, profile_name: str
    ) -> None:
        profiles = profiles_by_job_id.setdefault(job_id, [])
        if profile_name not in profiles:
            profiles.append(profile_name)

    # --- seen-cache helpers -----------------------------------------------

    def _cache_identity(
        self, job: NormalizedJob | Mapping[str, Any]
    ) -> tuple[str, str] | None:
        """Return ``(source, sourceJobId)`` for the cache key, or None."""

        if isinstance(job, NormalizedJob):
            source, source_job_id = job.identity
        else:
            # Preserve the legacy raw JobSpy mapping fallback for callers that
            # still pass mapping-shaped jobs through the cache helpers.
            mapped = map_jobspy_job(job)
            source = mapped.get("source")
            source_job_id = mapped.get("sourceJobId")
        return self._normalized_identity(source, source_job_id)

    def _is_cached(
        self,
        job: NormalizedJob | Mapping[str, Any],
        *,
        hours_old: int,
        failures: list[str],
    ) -> bool:
        """Return True when the offer should be skipped due to the cache."""

        if self.seen_cache is None:
            return False
        identity = self._cache_identity(job)
        if identity is None:
            return False
        source, source_job_id = identity
        try:
            return self.seen_cache.should_skip(
                source,
                source_job_id,
                hours_old=hours_old,
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            # Cache failures must never crash a run; surface only the
            # exception type (never str(exc), which can leak local paths)
            # as a ``seen-cache:...`` failure so operators can see the cause.
            failures.append(f"seen-cache:check:{type(exc).__name__}")
            return False

    def _record_seen(
        self,
        job: NormalizedJob | Mapping[str, Any],
        *,
        failures: list[str],
    ) -> None:
        """Persist the (source, sourceJobId) pair to the seen cache."""

        if self.seen_cache is None:
            return
        identity = self._cache_identity(job)
        if identity is None:
            return
        source, source_job_id = identity
        try:
            self.seen_cache.mark_seen(source, source_job_id)
        except Exception as exc:  # pragma: no cover - defensive guard
            # See ``_is_cached``: never surface str(exc) here either.
            failures.append(f"seen-cache:write:{type(exc).__name__}")


JobSearchAutomation = JobTrailAutomation
