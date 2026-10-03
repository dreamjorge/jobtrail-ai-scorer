"""Bounded, one-way JobTrail to n8n delivery boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
import time
from numbers import Real
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .notify import FORBIDDEN_TOKENS
from .opportunity_cards import validated_cards


_EVENT_TYPE = "automation.run.completed"
_SELECTED_FIELDS = (
    "title", "company", "location", "score", "recommendation",
    "recommendationLabel", "jobUrl", "jobTrailLink", "source", "sourceJobId",
)
_MAX_TEXT = 200
_MAX_FAILURE_LABEL = 128
_MAX_FAILURES = 5
_MAX_ATTEMPTS = 3
# Keep individual n8n requests bounded even when configuration is supplied directly.
_MAX_TIMEOUT_SECONDS = 60.0
_MAX_RESULT_COUNT = 1_000_000
_MAX_LABELS = 5
_ACTION_EXPIRY = timedelta(days=1)
_SCORE_FIELDS = ("fit_score", "coverage_score")
_LABEL_FIELDS = ("strengths", "evidence_labels", "gaps", "gap_labels")
_EVIDENCE_LABELS = {"direct", "equivalent", "inferred", "missing"}


@dataclass(frozen=True)
class N8nConfig:
    enabled: bool = False
    endpoint: str = ""
    timeout_seconds: float = 5.0
    retry_attempts: int = _MAX_ATTEMPTS
    auth_header: str = ""

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, Real)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("n8n timeout and retries must be bounded positive values")
        if (
            isinstance(self.retry_attempts, bool)
            or not isinstance(self.retry_attempts, int)
            or not 1 <= self.retry_attempts <= _MAX_ATTEMPTS
        ):
            raise ValueError("n8n timeout and retries must be bounded positive values")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "N8nConfig":
        values = os.environ if env is None else env
        truthy = values.get("N8N_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
        if not truthy:
            return cls(enabled=False)
        timeout = float(values.get("N8N_TIMEOUT_SECONDS", "5"))
        raw_attempts = values.get("N8N_RETRY_ATTEMPTS", str(_MAX_ATTEMPTS))
        if isinstance(raw_attempts, bool) or not isinstance(raw_attempts, (str, int)):
            raise ValueError("n8n timeout and retries must be bounded positive values")
        attempts = int(raw_attempts)
        return cls(
            enabled=truthy,
            endpoint=values.get("N8N_ENDPOINT", "").strip(),
            timeout_seconds=timeout,
            retry_attempts=attempts,
            auth_header=values.get("N8N_AUTH_HEADER", ""),
        )


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    attempts: int = 0
    event_id: str = ""
    classification: str = ""
    detail: str = ""


def _clip(value: Any) -> str:
    return str(value or "")[:_MAX_TEXT]


_CREDENTIAL_QUERY_KEYS = {
    "token", "api_key", "apikey", "access_token", "password", "client_secret",
    "secret", "app_id", "app_key",
}


def _redact_text(value: str) -> str:
    cleaned = value
    for token in FORBIDDEN_TOKENS:
        cleaned = cleaned.replace(token, "[redacted]")
    if "?" not in cleaned or "=" not in cleaned:
        return cleaned[:_MAX_TEXT]
    parts = urlsplit(cleaned)
    query = [
        (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _CREDENTIAL_QUERY_KEYS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))[:_MAX_TEXT]


def _bounded_score(value: Any) -> int | float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or isinstance(value, float) and not math.isfinite(value)
    ):
        return None
    return max(0, min(100, value))


def _bounded_labels(value: Any) -> list[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    return [_redact_text(item) for item in list(value)[:_MAX_LABELS] if isinstance(item, str)]


def _bounded_evidence(value: Any) -> list[dict[str, str]] | None:
    """Preserve only labelled evidence; never serialize arbitrary objects."""
    if not isinstance(value, (list, tuple)):
        return None
    result = []
    for item in value[:_MAX_LABELS]:
        if not isinstance(item, Mapping):
            continue
        label, text = item.get("label"), item.get("text")
        if (
            not isinstance(label, str) or label not in _EVIDENCE_LABELS
            or not isinstance(text, str) or not text.strip()
        ):
            continue
        result.append({"label": label, "text": _redact_text(text)})
    return result


def _bounded_count(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(_MAX_RESULT_COUNT, count))


def _safe_occurred_at(value: Any) -> str:
    """Keep the timestamp bounded while preserving ordinary ISO strings."""

    return _redact_text(_clip(value))


def _identity_digest(identity: Mapping[str, str]) -> str:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return sha256(encoded).hexdigest()[:32]


def _valid_identity(selected: Mapping[str, Any]) -> tuple[str, str] | None:
    source = selected.get("source")
    source_job_id = selected.get("sourceJobId")
    if not isinstance(source, str) or not source.strip():
        return None
    if not isinstance(source_job_id, str) or not source_job_id.strip():
        return None
    return source.strip(), source_job_id.strip()


def _selected_summary(selected: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not selected:
        return None
    result: dict[str, Any] = {}
    for key in _SELECTED_FIELDS:
        if key in selected and selected[key] is not None:
            value = selected[key]
            if isinstance(value, str):
                result[key] = _redact_text(value)
            elif key == "score":
                bounded = _bounded_score(value)
                if bounded is not None:
                    result[key] = bounded
    if isinstance(selected.get("classification"), str):
        result["classification"] = _redact_text(selected["classification"])
    for key in _SCORE_FIELDS:
        value = _bounded_score(selected.get(key))
        if value is not None:
            result[key] = value
    evidence = _bounded_evidence(selected.get("evidence"))
    if evidence is not None:
        result["evidence"] = evidence
    for key in _LABEL_FIELDS:
        labels = _bounded_labels(selected.get(key))
        if labels is not None:
            result[key] = labels
    return result


def _feedback_actions(*, event_id: str, run_id: str, selected: Mapping[str, Any], occurred_at: str) -> list[dict[str, str]]:
    try:
        expiry = datetime.fromisoformat(occurred_at.replace("Z", "+00:00")) + _ACTION_EXPIRY
        expires_at = expiry.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        # Invalid timestamps cannot produce an unbounded lifetime; use a
        # deterministic already-expired value rather than omit the bound.
        expires_at = "1970-01-02T00:00:00+00:00"
    job_identity = _valid_identity(selected)
    if job_identity is None:
        return []
    actions = []
    for action in ("applied", "dismissed", "interesting"):
        # Action identifiers bind to the validated event identity and action
        # type.  The event id itself is derived from raw identity in
        # ``build_envelope`` (before output clipping), so long identities stay
        # collision-resistant without transmitting their private values.
        action_id = _identity_digest({
            "action": action,
            "event_id": event_id,
            "version": "jobtrail-feedback-v1",
        })
        token_id = _identity_digest({"action_id": action_id, "version": "jobtrail-feedback-token-v1"})
        actions.append({"action": action, "action_id": action_id, "token_id": token_id, "expires_at": expires_at})
    return actions


def build_envelope(
    *, run_id: str, occurred_at: str, searched: int, imported: int, scored: int,
    failures: tuple[str, ...] | list[str] = (), selected: Mapping[str, Any] | None = None,
    feedback_actions: bool = False, opportunities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the closed, bounded payload sent to n8n."""
    raw_run_id = run_id if isinstance(run_id, str) else str(run_id)
    safe_run_id = _redact_text(_clip(raw_run_id))
    cards = validated_cards(opportunities) if opportunities is not None else None
    version = 2 if cards else 1
    safe_selected = _selected_summary(selected)
    if cards and safe_selected:
        # Preserve the selected compatibility summary, not CV-match evidence.
        safe_selected = {key: value for key, value in safe_selected.items()
                         if key in _SELECTED_FIELDS + ('classification',) + _SCORE_FIELDS}
    identity = _valid_identity(selected or {}) or (raw_run_id, "run")
    event_id = _identity_digest({
        "job_id": identity[1],
        "run_id": raw_run_id,
        "source": identity[0],
        "version": f"jobtrail-n8n-v{version}",
    })
    safe_occurred_at = _safe_occurred_at(occurred_at)
    actions = (
        _feedback_actions(
            event_id=event_id,
            run_id=raw_run_id,
            selected=selected or {},
            occurred_at=safe_occurred_at,
        )
        if feedback_actions and safe_selected
        else []
    )
    safe_failures = [
        _redact_text(str(label or ""))[:_MAX_FAILURE_LABEL]
        for label in list(failures)[:_MAX_FAILURES]
    ]
    return {
        "schema_version": version,
        "event_id": event_id,
        "run_id": safe_run_id,
        "event_type": _EVENT_TYPE,
        "occurred_at": safe_occurred_at,
        "result": {
            "searched": _bounded_count(searched), "imported": _bounded_count(imported),
            "scored": _bounded_count(scored), "failure_count": _bounded_count(len(safe_failures)),
            "failures": safe_failures,
        },
        "selected": safe_selected,
        **({"actions": actions} if actions else {}),
        **({"opportunities": cards} if cards else {}),
    }


def _validated_envelope(envelope: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a canonical closed-envelope copy, or reject the mapping."""

    if not isinstance(envelope, Mapping):
        return None
    version = envelope.get('schema_version')
    if type(version) is not int or version not in (1, 2):
        return None
    cards = validated_cards(envelope.get('opportunities')) if version == 2 else None
    fields = set(envelope) - ({'opportunities'} if version == 2 else set())
    if fields not in (
        {"schema_version", "event_id", "run_id", "event_type", "occurred_at", "result", "selected"},
        {"schema_version", "event_id", "run_id", "event_type", "occurred_at", "result", "selected", "actions"},
    ):
        return None
    if envelope.get("event_type") != _EVENT_TYPE:
        return None
    event_id = envelope.get("event_id")
    run_id = envelope.get("run_id")
    occurred_at = envelope.get("occurred_at")
    if (
        not isinstance(event_id, str) or len(event_id) != 32 or any(c not in "0123456789abcdef" for c in event_id)
        or not isinstance(run_id, str) or len(run_id) > _MAX_TEXT
        or run_id != _redact_text(run_id)
        or not isinstance(occurred_at, str) or len(occurred_at) > _MAX_TEXT
        or occurred_at != _redact_text(occurred_at)
    ):
        return None
    result = envelope.get("result")
    if not isinstance(result, Mapping) or set(result) != {"searched", "imported", "scored", "failure_count", "failures"}:
        return None
    counters = (result.get("searched"), result.get("imported"), result.get("scored"), result.get("failure_count"))
    if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_RESULT_COUNT for value in counters):
        return None
    failures = result.get("failures")
    if not isinstance(failures, list) or len(failures) > _MAX_FAILURES or result["failure_count"] != len(failures):
        return None
    if any(
        not isinstance(label, str)
        or len(label) > _MAX_FAILURE_LABEL
        or label != _redact_text(label)
        for label in failures
    ):
        return None
    selected = envelope.get("selected")
    if selected is not None and (not isinstance(selected, Mapping) or _selected_summary(selected) != dict(selected)):
        return None
    if version == 2 and selected is not None and set(selected) - set(
            _SELECTED_FIELDS + ('classification',) + _SCORE_FIELDS):
        return None
    canonical: dict[str, Any] = {
        "schema_version": version, "event_id": event_id, "run_id": run_id,
        "event_type": _EVENT_TYPE, "occurred_at": occurred_at,
        "result": {key: result[key] for key in ("searched", "imported", "scored", "failure_count")}
        | {"failures": list(failures)},
        "selected": None if selected is None else dict(selected),
    }
    if "actions" in envelope:
        actions = envelope["actions"]
        if (
            not isinstance(actions, list)
            or [action.get("action") for action in actions if isinstance(action, Mapping)]
            != ["applied", "dismissed", "interesting"]
        ):
            return None
        parsed_occurred_at: datetime | None = None
        if actions:
            try:
                parsed_occurred_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError):
                return None
            if parsed_occurred_at.tzinfo is None:
                return None
        for action in actions:
            if not isinstance(action, Mapping) or set(action) != {"action", "action_id", "token_id", "expires_at"}:
                return None
            action_id = action.get("action_id")
            token_id = action.get("token_id")
            expires_at = action.get("expires_at")
            try:
                parsed_expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError):
                return None
            action_type = action.get("action")
            expected_action_id = _identity_digest({
                "action": action_type,
                "event_id": event_id,
                "version": "jobtrail-feedback-v1",
            }) if action_type in {"applied", "dismissed", "interesting"} else ""
            expected_token_id = _identity_digest({
                "action_id": expected_action_id,
                "version": "jobtrail-feedback-token-v1",
            })
            if (
                action_type not in {"applied", "dismissed", "interesting"}
                or action_id != expected_action_id
                or token_id != expected_token_id
                or not isinstance(expires_at, str)
                or len(expires_at) > _MAX_TEXT
                or expires_at != _redact_text(expires_at)
                or parsed_expiry.tzinfo is None
                or parsed_occurred_at is None
                or parsed_expiry != parsed_occurred_at + _ACTION_EXPIRY
            ):
                return None
        canonical["actions"] = [dict(action) for action in actions]
    if cards:
        canonical['opportunities'] = cards
        # 3 cards x (3 citations + 3 cited claims), with Unicode/JSON escaping
        # headroom. v1 bounds/bytes remain unchanged.
        if len(json.dumps(canonical, ensure_ascii=True).encode()) > 262_144:
            return None
    return canonical


class N8nOutboundAdapter:
    """Optional HTTP adapter; it is intentionally not a generic webhook client."""

    def __init__(self, config: N8nConfig, *, transport: httpx.BaseTransport | None = None, sleep=time.sleep) -> None:
        self.config = config
        self._transport = transport
        self._sleep = sleep

    def send(self, envelope: Mapping[str, Any], *, dry_run: bool = False) -> DeliveryResult:
        try:
            safe_envelope = _validated_envelope(envelope)
        except Exception:
            # A caller-controlled Mapping must never turn validation failure
            # into an outbound request (or an adapter exception).
            safe_envelope = None
        if safe_envelope is None:
            return DeliveryResult("failed", 0, "", "terminal")
        event_id = safe_envelope["event_id"]
        if dry_run or not self.config.enabled or not self.config.endpoint:
            return DeliveryResult("disabled", 0, event_id, "disabled")
        attempts = 0
        try:
            with httpx.Client(transport=self._transport, timeout=self.config.timeout_seconds) as client:
                for attempt in range(1, self.config.retry_attempts + 1):
                    attempts = attempt
                    try:
                        response = client.post(
                            self.config.endpoint, json=safe_envelope,
                            headers={"Authorization": self.config.auth_header} if self.config.auth_header else None,
                        )
                        if 200 <= response.status_code < 300:
                            return DeliveryResult("accepted", attempts, event_id)
                        if 400 <= response.status_code < 500:
                            return DeliveryResult("failed", attempts, event_id, "terminal")
                        if attempt < self.config.retry_attempts:
                            self._sleep(0)
                            continue
                        return DeliveryResult("failed", attempts, event_id, "exhausted")
                    except (httpx.TimeoutException, httpx.TransportError):
                        if attempt < self.config.retry_attempts:
                            self._sleep(0)
                            continue
                        return DeliveryResult("failed", attempts, event_id, "uncertain")
        except (httpx.TimeoutException, httpx.TransportError):
            return DeliveryResult("failed", attempts, event_id, "uncertain")
        return DeliveryResult("failed", attempts, event_id, "uncertain")


__all__ = ["DeliveryResult", "N8nConfig", "N8nOutboundAdapter", "build_envelope"]
