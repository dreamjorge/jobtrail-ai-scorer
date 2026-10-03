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
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            address = address.ipv4_mapped
        elif address.packed[0] == 0xfe and (address.packed[1] & 0xc0) == 0xc0:
            # Python reports fec0::/10 (site-local, deprecated) as is_global=True.
            raise ValueError("blocked address")
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


class _ByteBudgetExceeded(Exception):
    pass


class _ByteBudget:
    """One response's HTTP wire bytes, charged once to the shared run."""
    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.used = 0

    def allowance(self, size):
        size = min(size, MAX_RESPONSE_BYTES - self.used,
                   MAX_TOTAL_BYTES - self.fetcher._bytes)
        if size <= 0:
            raise _ByteBudgetExceeded()
        return size

    def received(self, size):
        self.used += size
        self.fetcher._bytes += size


class _DeadlineReader(io.RawIOBase):
    """Bound every TLS recv, including slow trickled HTTP headers/chunk framing."""
    def __init__(self, owner):
        self.owner = owner

    def readable(self):
        return True

    def readinto(self, buffer):
        self.owner._set_read_timeout()
        budget = self.owner._byte_budget
        view = memoryview(buffer)
        if budget is not None:
            view = view[:budget.allowance(len(view))]
        size = self.owner._tls_sock.recv_into(view)
        if budget is not None:
            budget.received(size)
        self.owner._remaining()
        return size


class _SocketView:
    def __init__(self, owner):
        self.owner = owner

    def makefile(self, mode):
        return io.BufferedReader(_DeadlineReader(self.owner))

    def sendall(self, data):
        self.owner._set_read_timeout()
        self.owner._tls_sock.sendall(data)
        self.owner._remaining()

    def close(self):
        self.owner._tls_sock.close()


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Numeric TCP connect, original-host SNI and certificate identity, no DNS."""
    def __init__(self, host, ip, timeout):
        super().__init__(host, port=443, timeout=timeout,
                         context=ssl.create_default_context())
        self._ip = _public_ip(ip)
        self._clock = time.monotonic
        self._deadline = self._clock() + timeout
        self._read_seconds = timeout
        self._tls_sock = None
        self._byte_budget = None

    def set_byte_budget(self, budget):
        self._byte_budget = budget

    def set_deadline(self, deadline, clock, read_seconds):
        self._deadline, self._clock, self._read_seconds = deadline, clock, read_seconds

    def _remaining(self):
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise TimeoutError()
        return remaining

    def _set_read_timeout(self):
        # During HTTP I/O self.sock is a view; retain the underlying TLS socket.
        self._tls_sock.settimeout(min(self._read_seconds, self._remaining()))

    def connect(self):
        # Direct numeric socket.connect avoids even getaddrinfo(numeric_ip).
        raw = socket.socket(socket.AF_INET6 if ":" in self._ip else socket.AF_INET,
                            socket.SOCK_STREAM)
        try:
            raw.settimeout(min(self.timeout, self._remaining()))
            raw.connect((self._ip, 443))
            tls = self._context.wrap_socket(raw, server_hostname=self.host,
                                            do_handshake_on_connect=False)
            self._tls_sock = tls
            tls.settimeout(min(self.timeout, self._remaining()))
            tls.do_handshake()
            self._remaining()
            self.sock = tls
        except Exception:
            if self._tls_sock is not None:
                self._tls_sock.close()
            raw.close()
            raise

    def close(self):
        # http.client detaches a Connection: close response before its body is
        # consumed. Our custom file retains TLS until explicit final cleanup.
        if getattr(self, "_reading_headers", False):
            self.sock = None
            super().close()
            return
        super().close()
        if self._tls_sock is not None:
            self._tls_sock.close()

    def getresponse(self):
        # HTTPResponse reads via deadline-aware makefile, not plain socket IO.
        self.sock = _SocketView(self)
        self._reading_headers = True
        try:
            return super().getresponse()
        finally:
            self._reading_headers = False
            if self.sock is not None:
                self.sock = self._tls_sock


class PublicFetcher:
    """No retries, proxies, ambient credentials, cookies, execution or claims.

    Use a new object per run; not thread-safe. Clock is monotonic seconds;
    utcnow supplies a datetime for retrieval timestamps. Trusted injected IO
    must honor its timeout; production IO also enforces the absolute deadline.
    """
    def __init__(self, config=FetchConfig(), *, resolver=bounded_resolve,
                 connection_factory=PinnedHTTPSConnection, clock=time.monotonic,
                 utcnow=lambda: datetime.now(timezone.utc)):
        self.config = config
        self._resolver, self._factory = resolver, connection_factory
        self._clock, self._utcnow = clock, utcnow
        self._deadline = clock() + config.total_seconds
        self._attempts = self._bytes = 0

    def _remaining(self):
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise TimeoutError()
        return remaining

    def fetch(self, url):
        stamp = self._utcnow()
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        checked_at = stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        current = _safe_url(url)
        def result(status, error=None, text=None, mime=None):
            # Failure URLs omitted: never echo malicious input or redirects.
            return FetchResult(status, current if status == "ok" else None,
                               checked_at, mime, text, error)
        if current is None:
            return result("blocked", "unsafe_url")
        seen = set()
        for hop in range(MAX_REDIRECTS + 1):
            conn = response = None
            try:
                self._remaining()
                if self._attempts >= MAX_ATTEMPTS or self._bytes >= MAX_TOTAL_BYTES:
                    return result("budget_exhausted", "run_budget")
                if current in seen:
                    return result("blocked", "redirect_loop")
                seen.add(current)
                parts = urlsplit(current)
                host = parts.hostname
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    answers = self._resolver(host, min(self.config.connect_seconds, self._remaining()))
                else:
                    answers = [host]
                self._remaining()
                if not isinstance(answers, (list, tuple)) or not answers or len(answers) > 256:
                    return result("blocked", "unsafe_dns")
                try:
                    ips = [_public_ip(address) for address in answers]
                except (ValueError, TypeError):
                    return result("blocked", "unsafe_dns")
                self._attempts += 1
                conn = self._factory(host, ips[0], min(self.config.connect_seconds, self._remaining()))
                conn.set_byte_budget(_ByteBudget(self))
                if isinstance(conn, PinnedHTTPSConnection):
                    conn.set_deadline(self._deadline, self._clock, self.config.read_seconds)
                conn.connect()
                self._remaining()
                conn.sock.settimeout(min(self.config.read_seconds, self._remaining()))
                target = parts.path + ("?" + parts.query if parts.query else "")
                conn.request("GET", target, headers={"Host": parts.netloc,
                             "Accept-Encoding": "identity", "Connection": "close"})
                self._remaining()
                response = conn.getresponse()
                self._remaining()
                if type(response.status) is not int or not 100 <= response.status <= 599:
                    return result("failed", "bad_response")
                length = response.getheader("content-length")
                transfer = response.getheader("transfer-encoding")
                if transfer is not None and (not isinstance(transfer, str)
                        or transfer.strip().lower() != "chunked" or length is not None):
                    return result("failed", "bad_framing")
                declared_length = None
                if length is not None:
                    if (not isinstance(length, str) or len(length) > 10
                            or not re.fullmatch(r"[0-9]+", length)):
                        return result("failed", "bad_framing")
                    declared_length = int(length)
                encoding = response.getheader("content-encoding", "identity")
                if not isinstance(encoding, str) or encoding.strip().lower() != "identity":
                    return result("unsupported", "unsupported_encoding")
                body = bytearray()
                while True:
                    self._remaining()
                    if conn.sock is not None:
                        conn.sock.settimeout(min(self.config.read_seconds, self._remaining()))
                    # Wire bytes (including prefetch) are charged below the
                    # parser, not again here. Keep the decoded body bounded too.
                    allowance = min(8192, MAX_RESPONSE_BYTES - len(body))
                    if allowance <= 0:
                        return result("budget_exhausted", "body_budget")
                    chunk = response.read1(allowance)
                    if not isinstance(chunk, bytes) or len(chunk) > allowance:
                        return result("failed", "bad_response")
                    self._remaining()
                    if not chunk:
                        if getattr(response, "length", None) not in (None, 0):
                            return result("failed", "truncated_response")
                        break
                    body.extend(chunk)
                if declared_length is not None and len(body) != declared_length:
                    return result("failed", "truncated_response")
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("location")
                    if not isinstance(location, str) or not location:
                        return result("failed", "bad_redirect")
                    candidate = _safe_url(urljoin(current, location))
                    if candidate is None:
                        return result("blocked", "unsafe_redirect")
                    if hop == MAX_REDIRECTS:
                        return result("blocked", "redirect_limit")
                    current = candidate
                    continue
                if response.status != 200:
                    return result("failed", "http_status")
                content_type = response.getheader("content-type", "")
                if not isinstance(content_type, str):
                    return result("failed", "bad_response")
                mime = content_type.split(";", 1)[0].strip().lower()
                if mime not in ("text/html", "text/plain", "application/json"):
                    return result("unsupported", "unsupported_mime")
                for parameter in content_type.split(";")[1:]:
                    if parameter.strip().lower().startswith("charset="):
                        if parameter.split("=", 1)[1].strip().strip('"').lower() not in ("utf-8", "utf8"):
                            return result("unsupported", "unsupported_charset")
                return result("ok", text=body.decode("utf-8"), mime=mime)
            except _ByteBudgetExceeded:
                return result("budget_exhausted", "body_budget")
            except DNSBusy:
                return result("failed", "dns_busy")
            except TimeoutError:
                return result("timed_out", "deadline")
            except ssl.SSLError:
                return result("failed", "tls_failure")
            except UnicodeError:
                return result("unsupported", "invalid_utf8")
            except (OSError, ValueError, TypeError, http.client.HTTPException):
                return result("failed", "transport_or_response")
            finally:
                if response is not None and hasattr(response, "close"):
                    response.close()
                if conn is not None:
                    conn.close()
        return result("blocked", "redirect_limit")
