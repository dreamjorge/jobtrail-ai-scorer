"""Synchronous public-only HTTPS retrieval; returned text is untrusted data.

One fetcher is one sequential run. Injection contracts are trusted: resolver(host,
seconds) returns a finite list of numeric addresses; factory(host, ip, seconds)
returns an http.client-compatible connection that never re-resolves the host.
Connections must implement set_byte_budget(budget), bounding every underlying
HTTP read with budget.allowance(size) and charging budget.received(size) before
any subsequent deadline check or parsing. This includes headers and framing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
import io
import ipaddress
import math
import re
import socket
import ssl
import threading
import time
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

from . import public_search

MAX_RESPONSE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 3 * 1024 * 1024
MAX_ATTEMPTS = 12
MAX_REDIRECTS = 2
# Same narrow credential-key policy as public_search; no runtime imports.
_SECRET_QUERY_KEYS = frozenset({
    "token", "api_key", "apikey", "access_token", "password", "client_secret",
    "secret", "secretkey", "secret_key", "app_id", "app_key",
})


@dataclass(frozen=True)
class FetchConfig:
    connect_seconds: float = 3.0
    read_seconds: float = 5.0
    total_seconds: float = 90.0

    def __post_init__(self):
        for name, maximum in (("connect_seconds", 3), ("read_seconds", 5),
                              ("total_seconds", 90)):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 < value <= maximum):
                raise ValueError("invalid fetch timeout")


@dataclass(frozen=True)
class FetchResult:
    status: str
    url: str | None
    checked_at: str
    content_type: str | None = None
    text: str | None = None
    error: str | None = None


def _public_ip(value):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if (not address.is_global or address.is_multicast or address.is_reserved
            or address.is_unspecified or address.is_loopback or address.is_link_local):
        raise ValueError("blocked address")
    return str(address)


def _safe_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    decoded = unquote(value)
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in decoded) or "\\" in decoded:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if (parts.scheme != "https" or not host or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443)):
            return None
        host = host.lower().rstrip(".")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if (len(host) > 253 or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host)
                    or any(len(label) > 63 or label.startswith("-") or label.endswith("-")
                           for label in host.split("."))
                    or host.rsplit(".", 1)[-1].isdigit()
                    or "localhost" in host.split(".")
                    or host.endswith((".local", ".localdomain", ".internal", ".lan", ".home", ".invalid"))):
                return None
        else:
            _public_ip(host)
        if any(key.lower() in _SECRET_QUERY_KEYS
               for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
            return None
        if (public_search._credential_fields(parts.query, 0)
                or public_search._credential_fields(parts.fragment, 0, fragment=True)):
            return None
        netloc = f"[{host}]" if ":" in host else host
        # ASCII request targets only; callers must percent-encode Unicode paths.
        target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        target.encode("ascii")
        return urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError):
        return None


class DNSBusy(Exception):
    pass


# Global bound, including abandoned lookups from other fetcher instances. Daemon
# workers cannot cancel libc DNS, but never block process shutdown or exceed 2.
_DNS_SLOTS = threading.BoundedSemaphore(2)


def bounded_resolve(host, timeout):
    if not _DNS_SLOTS.acquire(blocking=False):
        raise DNSBusy()
    done = threading.Event()
    outcome = []
    def lookup():
        try:
            outcome.append([item[4][0] for item in
                            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)])
        except Exception:
            outcome.append(None)
        finally:
            _DNS_SLOTS.release()
            done.set()
    try:
        threading.Thread(target=lookup, daemon=True, name="public-http-dns").start()
    except Exception:
        _DNS_SLOTS.release()
        raise
    if not done.wait(timeout):
        raise TimeoutError()
    if outcome[0] is None:
        raise OSError()
    return outcome[0]
