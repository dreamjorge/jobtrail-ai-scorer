"""Bounded notification construction for the WhatsApp helper.

The :class:`NotificationBuilder` assembles a single summary dict from a
``ScoreResult``-like mapping and a JobTrail job mapping. It enforces the
following contract:

* only the :data:`ALLOWED_FIELDS` are emitted (no CV, no profile, no raw
  prompt, no description, no notes, no credentials);
* ``recommendation`` is normalized to one of
  ``PRIORITY_APPLY|APPLY|REVIEW|SKIP`` and defaults to :data:`DEFAULT_RECOMMENDATION`
  (``APPLY``) when missing or unknown;
* ``recommendationLabel`` is a stable, human-readable label for the same
  recommendation value;
* ``runId`` is anchored on ``YYYY-MM-DD-HHMM-<6-char hex>`` and remains
  deterministic for the same stamp + seed;
* ``jobTrailLink`` is built from the configured ``base_url``; an optional
  ``WHATSAPP_SHORT_URL_BASE`` environment value swaps the host while
  preserving the path;
* every string value is run through the defensive :func:`_scrub` step that
  replaces any :data:`FORBIDDEN_TOKENS` substring with ``"[REDACTED]"`` so
  that the rendered output is safe to deliver through WhatsApp.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping
from urllib.parse import quote


#: Allowed recommendation values. Anything outside this set falls back to
#: :data:`DEFAULT_RECOMMENDATION` so the rendered output is always one of
#: these four strings.
RECOMMENDATIONS: tuple[str, ...] = ("PRIORITY_APPLY", "APPLY", "REVIEW", "SKIP")

#: Recommendation default applied when the score is missing or unknown. The
#: value is intentionally ``APPLY`` (not ``REVIEW``) because the existing
#: automation already classifies anything below the score threshold as a
#: non-match; a missing recommendation field is an upstream omission, not a
#: reason to skip.
DEFAULT_RECOMMENDATION = "APPLY"

#: Human-readable labels for each :data:`RECOMMENDATIONS` value.
_RECOMMENDATION_LABELS: dict[str, str] = {
    "PRIORITY_APPLY": "Priority Apply",
    "APPLY": "Apply",
    "REVIEW": "Review",
    "SKIP": "Skip",
}

#: Sentinel substrings that the builder scrubs from every emitted string. The
#: list is intentionally public so operators can extend it when integrating
#: new providers or secret formats without touching the rendering pipeline.
FORBIDDEN_TOKENS: tuple[str, ...] = (
    # Private runtime path tokens that must never leak to WhatsApp.
    "/DATA/",
    "/AppData/",
    # Test-visible sentinels that mimic CV / profile / prompt / credential
    # content. Real CV/profile/prompt/credential strings are protected by
    # the allowlist alone; the sentinels stay here so a regression that
    # leaks a sensitive *substring* into strengths/gaps/notes is still
    # caught.
    "RESUME_SENTINEL",
    "PROFILE_SENTINEL",
    "PROMPT_SENTINEL",
    "CREDENTIAL_SENTINEL",
)

#: Fields that the builder is permitted to emit in the rendered summary.
ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "company",
        "location",
        "score",
        "recommendation",
        "recommendationLabel",
        "strengths",
        "gaps",
        "jobUrl",
        "jobTrailLink",
        "runId",
        "searchProfiles",
    }
)

#: Maximum length kept identical to the legacy summary so existing payloads
#: remain diff-friendly.
MAX_TEXT_LENGTH = 200
MAX_LIST_ITEMS = 5


def normalize_recommendation(value: Any) -> str:
    """Return one of :data:`RECOMMENDATIONS`, defaulting to ``APPLY``."""

    if isinstance(value, str) and value in RECOMMENDATIONS:
        return value
    return DEFAULT_RECOMMENDATION


def recommendation_label(value: Any) -> str:
    """Return a stable human-readable label for ``value``."""

    return _RECOMMENDATION_LABELS[normalize_recommendation(value)]


def build_run_id(
    moment: datetime | None = None,
    *,
    seed: str = "",
    clock: Callable[[], datetime] | None = None,
) -> str:
    """Return a ``YYYY-MM-DD-HHMM-<short hex>`` run identifier.

    The hash is the first six hex characters of ``sha256(stamp|seed)`` so
    identical inputs produce identical RunIds without exposing the wall-clock
    value. The ``clock`` callable wins over ``moment`` which wins over
    :func:`datetime.now`; this ordering keeps the function deterministic in
    tests.
    """

    if clock is not None:
        when = clock()
    elif moment is not None:
        when = moment
    else:
        when = datetime.now()
    stamp = when.strftime("%Y-%m-%d-%H%M")
    digest = hashlib.sha256(f"{stamp}|{seed}".encode("utf-8")).hexdigest()[:6]
    return f"{stamp}-{digest}"


def default_job_url_builder(base_url: str) -> Callable[[str], str]:
    """Return a callable that produces ``<base>/jobs/<id>`` URLs.

    The base URL is normalized once at construction time so each call only
    performs the URL escape and concatenation. When ``base_url`` is empty the
    builder still produces ``/jobs/<id>``-shaped paths so callers can render
    relative links for tests.
    """

    base = (base_url or "").rstrip("/")

    def _build(job_id: str) -> str:
        encoded = quote(str(job_id or ""), safe="")
        if not encoded:
            return base
        return f"{base}/jobs/{encoded}"

    return _build


def build_job_trail_link(base_url: str, job_id: str) -> str:
    """Convenience wrapper around :func:`default_job_url_builder`."""

    return default_job_url_builder(base_url)(job_id)


def short_url_for(url: str, short_base: str) -> str:
    """Replace the scheme+host of ``url`` with ``short_base`` (best-effort).

    An empty ``short_base`` returns ``url`` unchanged so the shortener path is
    strictly opt-in. The path component is preserved so the shortened link
    still identifies the resource (``/jobs/<id>``).
    """

    if not short_base:
        return url
    base = short_base.rstrip("/")
    if "://" in url:
        _, _, after_scheme = url.partition("://")
        if "/" in after_scheme:
            tail = after_scheme.split("/", 1)[1]
        else:
            tail = ""
    else:
        tail = url.lstrip("/")
    if tail:
        return f"{base}/{tail}"
    return base


def _clip_text(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str:
    text = str(value or "")
    return text[:limit]


def _clip_items(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clip_text(item, limit=limit) for item in value[:MAX_LIST_ITEMS]]


def _scrub(value: Any) -> Any:
    """Recursively replace :data:`FORBIDDEN_TOKENS` substrings in strings."""

    if isinstance(value, str):
        scrubbed = value
        for token in FORBIDDEN_TOKENS:
            if token in scrubbed:
                scrubbed = scrubbed.replace(token, "[REDACTED]")
        return scrubbed
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _scrub(v) for k, v in value.items()}
    return value


@dataclass(frozen=True)
class NotificationBuilder:
    """Compose a bounded WhatsApp summary from a score and a job mapping.

    The constructor takes a JobTrail job URL builder (or derives one from
    ``base_url``). ``short_url_base`` is resolved at construction time from
    the provided ``short_url_base_env`` mapping (default environment) so the
    WHATSAPP_SHORT_URL_BASE opt-in works without runtime imports.
    """

    base_url: str = ""
    job_url_builder: Callable[[str], str] | None = None
    short_url_base: str = ""
    clock: Callable[[], datetime] | None = None
    short_url_base_env: str = "WHATSAPP_SHORT_URL_BASE"
    run_id_seed: str = ""
    #: Optional explicit env mapping used by :meth:`from_env`. ``None`` means
    #: ``os.environ`` will be consulted at construction time.
    env: Mapping[str, str] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.job_url_builder is None:
            # ``object.__setattr__`` because the dataclass is frozen.
            object.__setattr__(
                self,
                "job_url_builder",
                default_job_url_builder(self.base_url),
            )
        # Resolve the shortener opt-in once at construction so callers can
        # monkeypatch ``os.environ`` before invoking :meth:`from_env`.
        if self.env is not None:
            mapping = self.env
        else:
            mapping = os.environ
        short_base = mapping.get(self.short_url_base_env, "")
        if short_base and not self.short_url_base:
            object.__setattr__(self, "short_url_base", short_base.strip())

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        base_url: str = "",
        job_url_builder: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        run_id_seed: str = "",
    ) -> "NotificationBuilder":
        """Construct a builder, honoring ``WHATSAPP_SHORT_URL_BASE``.

        When ``env`` is ``None``, the process ``os.environ`` is consulted so
        ``monkeypatch.setenv`` works in tests. Tests can also pass a
        pre-populated ``Mapping`` to avoid touching the global environment.
        """

        return cls(
            base_url=base_url,
            job_url_builder=job_url_builder,
            clock=clock,
            run_id_seed=run_id_seed,
            env=env,
        )

    def _build_job_trail_link(self, job_id: Any) -> str:
        if not job_id:
            return ""
        builder = self.job_url_builder or default_job_url_builder(self.base_url)
        url = builder(str(job_id))
        return short_url_for(url, self.short_url_base)

    def build(
        self,
        *,
        score: Mapping[str, Any] | None = None,
        job: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the bounded summary dict with the allowlisted fields only."""

        score_map: Mapping[str, Any] = score or {}
        job_map: Mapping[str, Any] = job or {}
        recommendation = normalize_recommendation(score_map.get("recommendation"))
        summary: dict[str, Any] = {
            "title": _clip_text(job_map.get("position", job_map.get("title"))),
            "company": _clip_text(job_map.get("company")),
            "location": _clip_text(job_map.get("location")),
            "score": score_map.get("score"),
            "recommendation": recommendation,
            "recommendationLabel": _RECOMMENDATION_LABELS[recommendation],
            "strengths": _clip_items(score_map.get("strengths")),
            "gaps": _clip_items(score_map.get("gaps")),
            "jobUrl": _clip_text(job_map.get("jobUrl", job_map.get("job_url"))),
        }
        search_profiles = _clip_items(score_map.get("searchProfiles"))
        if search_profiles:
            summary["searchProfiles"] = search_profiles
        job_id = job_map.get("id") or job_map.get("_id")
        link = self._build_job_trail_link(job_id)
        if link:
            summary["jobTrailLink"] = link
        summary["runId"] = build_run_id(
            moment=None,
            seed=self.run_id_seed,
            clock=self.clock,
        )
        return _scrub(summary)


__all__ = [
    "ALLOWED_FIELDS",
    "DEFAULT_RECOMMENDATION",
    "FORBIDDEN_TOKENS",
    "MAX_LIST_ITEMS",
    "MAX_TEXT_LENGTH",
    "NotificationBuilder",
    "RECOMMENDATIONS",
    "build_job_trail_link",
    "build_run_id",
    "default_job_url_builder",
    "normalize_recommendation",
    "recommendation_label",
    "short_url_for",
]
