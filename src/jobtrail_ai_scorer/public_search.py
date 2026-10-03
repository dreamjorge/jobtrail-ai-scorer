"""Bounded Brave discovery. Snippets are untrusted leads, never cited facts.

One adapter belongs to one run; it is deliberately not a runtime integration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import math
import re
from typing import Literal, Mapping
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

import httpx

from .notify import FORBIDDEN_TOKENS

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_QUERIES = 3
MAX_RESULTS = 5
MAX_CANDIDATES = 10
MAX_RESPONSE_BYTES = 256 * 1024
# Mirror n8n_outbound's credential-query policy without importing runtime state.
_SECRET_QUERY_KEYS = frozenset({
    "token", "api_key", "apikey", "access_token", "password", "client_secret",
    "secret", "app_id", "app_key",
})
Status = Literal[
    "disabled", "dry_run", "provider_unconfigured", "budget_exhausted",
    "found", "not_found", "untrusted", "provider_error", "quota_exceeded",
    "timeout", "unavailable", "bad_response", "oversized_response",
]


def _forbidden(value: str) -> bool:
    return any(token.casefold() in value.casefold() for token in FORBIDDEN_TOKENS)


@dataclass(frozen=True)
class PublicSearchRequest:
    """Only caller-approved public identifiers; never pass raw job mappings."""

    company: str
    title: str
    location: str

    def __post_init__(self) -> None:
        for name, limit in (("company", 160), ("title", 200), ("location", 120)):
            value = getattr(self, name)
            if (
                not isinstance(value, str) or not value.strip() or len(value) > limit
                or _forbidden(value)
                or any(not (c.isalnum() or c in " .,&+()-'") for c in value)
            ):
                raise ValueError("invalid public search field")
            object.__setattr__(self, name, " ".join(value.split()))

    @property
    def query(self) -> str:
        return f'"{self.company}" "{self.title}" "{self.location}" jobs'


@dataclass(frozen=True)
class PublicSearchConfig:
    enabled: bool = False
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("invalid search enable flag")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 30
        ):
            raise ValueError("invalid search timeout")
        if self.api_key is not None:
            if not isinstance(self.api_key, str) or len(self.api_key) > 1024:
                raise ValueError("invalid search credential")
            key = self.api_key.strip()
            if any(not 33 <= ord(c) <= 126 for c in key):
                raise ValueError("invalid search credential")
            object.__setattr__(self, "api_key", key or None)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> PublicSearchConfig:
        """Parse an explicitly supplied environment; never read ambient secrets."""
        flag = env.get("OPPORTUNITY_INTELLIGENCE_ENABLED", "false").strip().lower()
        if flag not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
            raise ValueError("invalid search enable flag")
        enabled = flag in ("1", "true", "yes", "on")
        return cls(enabled=enabled, api_key=env.get("BRAVE_SEARCH_API_KEY") if enabled else None)


@dataclass(frozen=True)
class SearchLead:
    title: str
    source_url: str
    snippet: str
    verified: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True)
class PublicSearchResult:
    status: Status
    leads: tuple[SearchLead, ...] = ()


def _candidate_url(value: object) -> str | None:
    """Syntactic filtering only. No DNS or destination fetching in this slice."""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    decoded = unquote(value)
    if _forbidden(decoded) or any(c.isspace() or ord(c) < 32 for c in decoded) or "\\" in decoded:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if (
            parts.scheme != "https" or not host or parts.username is not None
            or parts.password is not None or parts.port not in (None, 443)
        ):
            return None
        host = host.lower().rstrip(".")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if (
                not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host)
                or any(label.startswith("-") or label.endswith("-") for label in host.split("."))
                or host.rsplit(".", 1)[-1].isdigit()
                or "localhost" in host.split(".")
                or host.endswith((".localdomain", ".local", ".internal", ".lan", ".home", ".invalid"))
            ):
                return None
        else:
            if not address.is_global or address.is_multicast or address.is_reserved:
                return None
        if any(k.lower() in _SECRET_QUERY_KEYS
               for k, _ in parse_qsl(parts.query, keep_blank_values=True)):
            return None
        netloc = f"[{host}]" if ":" in host else host
        return urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))
    except ValueError:
        return None
