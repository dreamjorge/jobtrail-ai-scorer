"""Bounded, one-way JobTrail to n8n delivery boundary."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
import time
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .notify import FORBIDDEN_TOKENS


_EVENT_TYPE = "automation.run.completed"
_SELECTED_FIELDS = (
    "title", "company", "location", "score", "recommendation",
    "recommendationLabel", "jobUrl", "jobTrailLink", "source", "sourceJobId",
)
_MAX_TEXT = 200
_MAX_FAILURE_LABEL = 128
_MAX_FAILURES = 5
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class N8nConfig:
    enabled: bool = False
    endpoint: str = ""
    timeout_seconds: float = 5.0
    retry_attempts: int = _MAX_ATTEMPTS
    auth_header: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "N8nConfig":
        values = os.environ if env is None else env
        truthy = values.get("N8N_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
        if not truthy:
            return cls(enabled=False)
        timeout = float(values.get("N8N_TIMEOUT_SECONDS", "5"))
        attempts = int(values.get("N8N_RETRY_ATTEMPTS", str(_MAX_ATTEMPTS)))
        if timeout <= 0 or attempts < 1 or attempts > _MAX_ATTEMPTS:
            raise ValueError("n8n timeout and retries must be bounded positive values")
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


def _selected_summary(selected: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not selected:
        return None
    result: dict[str, Any] = {}
    for key in _SELECTED_FIELDS:
        if key in selected and selected[key] is not None:
            value = selected[key]
            if isinstance(value, str):
                result[key] = _redact_text(value)
            elif key == "score" and isinstance(value, (int, float)) and not isinstance(value, bool):
                result[key] = value
    return result


def build_envelope(
    *, run_id: str, occurred_at: str, searched: int, imported: int, scored: int,
    failures: tuple[str, ...] | list[str] = (), selected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the closed, bounded payload sent to n8n."""
    safe_selected = _selected_summary(selected)
    identity = (str(run_id), "run")
    if safe_selected:
        identity = (str(safe_selected.get("source", "")), str(safe_selected.get("sourceJobId", "")))
    event_id = sha256(f"jobtrail-n8n-v1|{run_id}|{identity[0]}|{identity[1]}".encode()).hexdigest()[:32]
    safe_failures = [str(label or "")[:_MAX_FAILURE_LABEL] for label in list(failures)[:_MAX_FAILURES]]
    return {
        "schema_version": 1,
        "event_id": event_id,
        "run_id": str(run_id),
        "event_type": _EVENT_TYPE,
        "occurred_at": str(occurred_at),
        "result": {
            "searched": max(0, int(searched)), "imported": max(0, int(imported)),
            "scored": max(0, int(scored)), "failure_count": len(failures),
            "failures": safe_failures,
        },
        "selected": safe_selected,
    }


class N8nOutboundAdapter:
    """Optional HTTP adapter; it is intentionally not a generic webhook client."""

    def __init__(self, config: N8nConfig, *, transport: httpx.BaseTransport | None = None, sleep=time.sleep) -> None:
        self.config = config
        self._transport = transport
        self._sleep = sleep

    def send(self, envelope: Mapping[str, Any], *, dry_run: bool = False) -> DeliveryResult:
        event_id = str(envelope.get("event_id", ""))
        if dry_run or not self.config.enabled or not self.config.endpoint:
            return DeliveryResult("disabled", 0, event_id, "disabled")
        attempts = 0
        try:
            with httpx.Client(transport=self._transport, timeout=self.config.timeout_seconds) as client:
                for attempt in range(1, self.config.retry_attempts + 1):
                    attempts = attempt
                    try:
                        response = client.post(
                            self.config.endpoint, json=dict(envelope),
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
