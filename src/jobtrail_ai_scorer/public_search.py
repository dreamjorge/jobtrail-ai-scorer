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
    "secret", "secretkey", "secret_key", "app_id", "app_key",
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


def _url_value(value: str) -> bool:
    return bool(re.match(r"^(?:https?:|[a-z][a-z0-9+.-]*://|//)", value, re.IGNORECASE))


def _decode_component(value: str, *, stop_at_url: bool = False) -> str | None:
    """At most three decode passes; unresolved escapes are not trusted."""
    for _ in range(3):
        if stop_at_url and _url_value(value):
            return value
        decoded = unquote(value, errors="strict")
        if decoded == value:
            return value
        value = decoded
    if stop_at_url and _url_value(value):
        return value
    return None if re.search(r"%[0-9a-f]{2}", value, re.IGNORECASE) else value


def _credential_fields(value: str, depth: int, *, fragment: bool = False) -> bool:
    # Inspect fragment URI envelopes without decoding ordinary query boundaries.
    if fragment or "=" not in value:
        decoded = _decode_component(value, stop_at_url=True)
        if decoded is None:
            return True
        if fragment and (_url_value(decoded) or decoded.startswith("/")):
            return _url_credentials(decoded, depth + 1)
        if "=" not in value:
            value = decoded
    if _url_value(value):
        return _url_credentials(value, depth + 1)
    if value.startswith("/") and "?" in value:
        value = value.partition("?")[2]
    if fragment and "=" not in value:
        return False
    for key, item in parse_qsl(value.lstrip("?"), keep_blank_values=True):
        key = _decode_component(key)
        if key is None or key.casefold() in _SECRET_QUERY_KEYS:
            return True
        item = _decode_component(item, stop_at_url=True)
        if item is None or (_url_value(item) and _url_credentials(item, depth + 1)):
            return True
    return False


def _url_credentials(value: str, depth: int = 0) -> bool:
    """Inspect bounded URL-valued fields without changing the returned URL."""
    if depth > 4:
        return True
    parts = urlsplit(value)
    if depth and (
        (not parts.netloc and not value.startswith("/"))
        or re.search(r"%(?![0-9a-f]{2})", value, re.IGNORECASE)
    ):
        return True
    authority = _decode_component(parts.netloc)
    if authority is None or urlsplit("//" + authority).username is not None:
        return True
    return _credential_fields(parts.query, depth) or _credential_fields(
        parts.fragment, depth, fragment=True,
    )


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
        if _url_credentials(value):
            return None
        netloc = f"[{host}]" if ":" in host else host
        return urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))
    except ValueError:
        return None


class _BorrowedTransport(httpx.BaseTransport):
    """Delegate requests while leaving transport closure to the caller."""

    def __init__(self, transport: httpx.BaseTransport) -> None:
        self._transport = transport

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._transport.handle_request(request)


class BravePublicSearchAdapter:
    """Sequential per-run attempt and candidate budgets; no retries or cookies."""

    def __init__(self, config: PublicSearchConfig = PublicSearchConfig(), *,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self._transport = _BorrowedTransport(transport) if transport is not None else None
        self._attempts = 0
        self._seen: set[str] = set()

    def search(self, request: PublicSearchRequest, *, dry_run: bool = False) -> PublicSearchResult:
        if type(request) is not PublicSearchRequest:
            raise TypeError("expected public search request")
        if type(dry_run) is not bool:
            raise ValueError("invalid dry-run flag")
        if not self.config.enabled:
            return PublicSearchResult("disabled")
        if dry_run:
            return PublicSearchResult("dry_run")
        if not self.config.api_key:
            return PublicSearchResult("provider_unconfigured")
        if self._attempts >= MAX_QUERIES or len(self._seen) >= MAX_CANDIDATES:
            return PublicSearchResult("budget_exhausted")
        self._attempts += 1
        try:
            # A fresh client per query prevents response cookies becoming ambient auth.
            with httpx.Client(transport=self._transport, trust_env=False,
                              follow_redirects=False, timeout=self.config.timeout_seconds) as client:
                with client.stream("GET", BRAVE_SEARCH_URL,
                                   params={"q": request.query, "count": MAX_RESULTS},
                                   headers={"X-Subscription-Token": self.config.api_key,
                                            "Accept": "application/json", "Accept-Encoding": "identity"}) as response:
                    if response.status_code == 429:
                        return PublicSearchResult("quota_exceeded")
                    if 400 <= response.status_code < 500:
                        return PublicSearchResult("provider_error")
                    if response.status_code >= 500:
                        return PublicSearchResult("unavailable")
                    if response.status_code != 200:
                        return PublicSearchResult("bad_response")
                    # Reject compressed bodies rather than decompressing an unbounded bomb.
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        return PublicSearchResult("bad_response")
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            return PublicSearchResult("oversized_response")
                        body.extend(chunk)
            payload = json.loads(body)
        except httpx.TimeoutException:
            return PublicSearchResult("timeout")
        except httpx.TransportError:
            return PublicSearchResult("unavailable")
        except (ValueError, UnicodeError, RecursionError, httpx.DecodingError):
            return PublicSearchResult("bad_response")
        if not isinstance(payload, dict) or not isinstance(payload.get("web"), dict):
            return PublicSearchResult("bad_response")
        results = payload["web"].get("results")
        if not isinstance(results, list):
            return PublicSearchResult("bad_response")
        if not results:
            return PublicSearchResult("not_found")
        leads = []
        for item in results[:MAX_RESULTS]:
            if len(self._seen) >= MAX_CANDIDATES:
                break
            if not isinstance(item, dict):
                continue
            title, snippet = item.get("title"), item.get("description")
            url = _candidate_url(item.get("url"))
            if (
                url is None or not isinstance(title, str) or not title.strip()
                or not isinstance(snippet, str) or len(title) > 300 or len(snippet) > 2000
                or any(_forbidden(v) or self.config.api_key in v for v in (title, snippet, url))
                or url in self._seen
            ):
                continue
            self._seen.add(url)
            leads.append(SearchLead(title, url, snippet))
        return PublicSearchResult("found" if leads else "untrusted", tuple(leads))
