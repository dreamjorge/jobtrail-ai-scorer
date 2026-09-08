"""Safe orchestration boundary for JobTrail search, scoring, and notification."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
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
    normalize_jobspy_job,
)
from .notify import NotificationBuilder, recommendation_label


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
    notify_enabled: bool = False
    notify_on_failure: bool = False
    whatsapp_command: str = ""
    discover_container: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AutomationConfig":
        e = os.environ if env is None else env

        def split(key: str, default: str, separator: str) -> tuple[str, ...]:
            return tuple(
                x.strip() for x in e.get(key, default).split(separator) if x.strip()
            )

        def truthy(key: str, default: str = "0") -> bool:
            return e.get(key, default).strip().lower() in {"1", "true", "yes", "on"}

        discover_container = e.get("JOBTRAIL_DISCOVER_CONTAINER", "").strip() or None
        search_profiles = (
            _parse_search_profiles(e["JOB_SEARCH_PROFILES"])
            if "JOB_SEARCH_PROFILES" in e
            else ()
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
            notify_enabled=truthy("WHATSAPP_NOTIFY_ENABLED", "0"),
            notify_on_failure=truthy("WHATSAPP_NOTIFY_ON_FAILURE", "0"),
            whatsapp_command=e.get("WHATSAPP_NOTIFY_COMMAND", ""),
            discover_container=discover_container,
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
        self.source_adapters = (
            (JobSpySourceAdapter(gateway),) if source_adapters is None else source_adapters
        )

    def _score(self, job_id: str, config_path: str) -> None:
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
        subprocess.run(
            [
                *shlex.split(self._scorer_command),
                "score",
                "--config",
                config_path,
                "--job-id",
                job_id,
                "--force",
                "--base-url",
                self.base_url,
            ],
            check=True,
        )

    def _notify(self, message: str) -> None:
        if not self._whatsapp_command:
            raise ValueError(
                "WHATSAPP_NOTIFY_COMMAND is required when notifications are enabled"
            )
        subprocess.run(
            shlex.split(self._whatsapp_command), input=message, text=True, check=True
        )

    def run(self, *, config: AutomationConfig) -> AutomationRun:
        if not config.scorer_config_path:
            raise ValueError("SCORER_CONFIG_PATH is required")
        failures: list[str] = []
        ids: list[str] = []
        scored_ids: list[str] = []
        self._scorer_command = config.scorer_command
        self._whatsapp_command = config.whatsapp_command
        self.base_url = config.base_url
        searched = imported = scored = 0
        for adapter in self.source_adapters:
            for request in source_search_requests(config):
                try:
                    jobs = adapter.search(request)
                    searched += len(jobs)
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
                                if self._is_cached(
                                    job,
                                    hours_old=request.hours_old,
                                    failures=failures,
                                ):
                                    continue
                                result = self.gateway.import_job(job.to_import_payload())
                                job_id = result.get("id")
                                if job_id is not None and str(job_id) not in ids:
                                    ids.append(str(job_id))
                                    imported += 1
                                self._record_seen(job, failures=failures)
                        except Exception as exc:
                            failures.append(_format_failure("import", exc))
                except Exception as exc:
                    failures.append(_format_failure("search", exc))
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
            except Exception as exc:
                failures.append(_format_failure("read", exc, job_id=job_id))

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
        return AutomationRun(searched, imported, scored, tuple(failures), best)

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
            match_body = json.dumps(
                build_notification_summary(
                    best_job or {},
                    best_score or {},
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
        if not isinstance(source, str) or not source:
            return None
        if not isinstance(source_job_id, str) or not source_job_id:
            return None
        return source, source_job_id

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
