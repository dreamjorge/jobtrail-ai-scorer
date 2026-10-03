"""Hermetic public destination tests: never use real DNS or sockets."""
from datetime import datetime, timezone
import io
from types import SimpleNamespace

import pytest

from jobtrail_ai_scorer import public_http as http


@pytest.fixture(autouse=True)
def deny_real_network(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        pytest.fail("real network is forbidden")
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "socket", denied)


class Response:
    def __init__(self, body=b"hello", status=200, **headers):
        self.status = status
        self.headers = {"content-type": "text/plain", **headers}
        self.body = io.BytesIO(body)
        self.header_wire = (f"HTTP/1.1 {status} Synthetic\r\n" + "".join(
            f"{key}: {value}\r\n" for key, value in self.headers.items()) + "\r\n").encode()
        self.budget = None

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read1(self, size):
        data = self.body.read(self.budget.allowance(size))
        self.budget.received(len(data))
        return data


class Connection:
    def __init__(self, response):
        self.response = response
        self.sock = SimpleNamespace(settimeout=lambda value: None)
        self.requests = []
        self.closed = False

    def connect(self):
        pass

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def set_byte_budget(self, budget):
        self.response.budget = budget

    def getresponse(self):
        remaining = len(self.response.header_wire)
        while remaining:
            size = self.response.budget.allowance(remaining)
            self.response.budget.received(size)
            remaining -= size
        return self.response

    def close(self):
        self.closed = True


def make_fetcher(responses=None, addresses=None, **kwargs):
    calls, dns, connections = [], [], []
    responses = iter(responses or [Response()])
    def resolver(host, timeout):
        dns.append((host, timeout))
        return addresses or ["8.8.8.8"]
    def factory(host, ip, timeout):
        calls.append((host, ip, timeout))
        conn = Connection(next(responses))
        connections.append(conn)
        return conn
    fetcher = http.PublicFetcher(resolver=resolver, connection_factory=factory,
                                 utcnow=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
                                 **kwargs)
    return fetcher, calls, dns, connections


def test_fetch_pins_validated_address_and_keeps_original_hostname():
    fetcher, calls, dns, connections = make_fetcher()
    result = fetcher.fetch("https://public.example/jobs")
    assert result.status == "ok"
    assert result.text == "hello"
    assert calls == [("public.example", "8.8.8.8", 3.0)]
    assert len(dns) == 1
    assert result.checked_at == "2026-01-01T00:00:00Z"
    assert connections[0].closed
    assert connections[0].requests[0][1]["headers"]["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("url", [
    "http://public.example", "https://user:secret@public.example",
    "https://public.example:444", "https://localhost", "https://x.local",
    "https://169.254.169.254", "https://[::1]", "https://[::ffff:127.0.0.1]",
    "https://224.0.0.1", "https://public.example/?api_key=secret",
])
def test_unsafe_urls_never_resolve_or_connect(url):
    fetcher, calls, dns, _ = make_fetcher()
    result = fetcher.fetch(url)
    assert result.status == "blocked"
    assert result.url is None
    assert not calls and not dns


@pytest.mark.parametrize("addresses", [
    ["8.8.8.8", "10.0.0.1"], ["fc00::1"], ["fe80::1"], ["ff02::1"],
    ["::"], ["::ffff:192.168.1.1"], ["not-an-ip"],
])
def test_all_dns_answers_must_be_public(addresses):
    fetcher, calls, _, _ = make_fetcher(addresses=addresses)
    assert fetcher.fetch("https://public.example").status == "blocked"
    assert not calls


@pytest.mark.parametrize("location", ["http://public.example", "https://127.0.0.1",
    "https://x.example:444", "https://u:p@x.example", "https://x.example/?token=secret"])
def test_malicious_redirect(location):
    fetcher, calls, _, _ = make_fetcher([Response(status=302, location=location)])
    assert fetcher.fetch("https://public.example").status == "blocked"
    assert len(calls) == 1


def test_relative_redirect_resolves_and_pins_again():
    fetcher, calls, dns, _ = make_fetcher([Response(status=302, location="/next"), Response()])
    answers = iter([["8.8.8.8"], ["1.1.1.1"]])
    fetcher._resolver = lambda host, timeout: next(answers)
    result = fetcher.fetch("https://public.example/start")
    assert result.url == "https://public.example/next"
    assert [c[:2] for c in calls] == [("public.example", "8.8.8.8"), ("public.example", "1.1.1.1")]


def test_redirect_rebinding_is_blocked():
    fetcher, calls, _, _ = make_fetcher([Response(status=302, location="/next")])
    answers = iter([["8.8.8.8"], ["10.0.0.1"]])
    fetcher._resolver = lambda host, timeout: next(answers)
    assert fetcher.fetch("https://public.example").status == "blocked"
    assert len(calls) == 1


@pytest.mark.parametrize("locations,attempts", [(["/", "/"], 1), (["/a", "/b", "/c"], 3)])
def test_loops_and_redirect_hops_bounded(locations, attempts):
    fetcher, calls, _, _ = make_fetcher([Response(status=302, location=x) for x in locations])
    assert fetcher.fetch("https://public.example").status == "blocked"
    assert len(calls) == attempts


@pytest.mark.parametrize("headers,error", [
    ({"content-encoding": "gzip"}, "unsupported_encoding"),
    ({"content-type": "image/png"}, "unsupported_mime"),
    ({"content-type": "text/plain; charset=latin1"}, "unsupported_charset"),
])
def test_unsupported_responses(headers, error):
    fetcher, _, _, _ = make_fetcher([Response(**headers)])
    result = fetcher.fetch("https://public.example")
    assert (result.status, result.error, result.text) == ("unsupported", error, None)


def test_invalid_utf8():
    fetcher, _, _, _ = make_fetcher([Response(b"\xff")])
    assert fetcher.fetch("https://public.example").error == "invalid_utf8"


def test_response_budget():
    fetcher, _, _, _ = make_fetcher([Response(b"x" * (http.MAX_RESPONSE_BYTES + 1))])
    assert fetcher.fetch("https://public.example").status == "budget_exhausted"
    assert fetcher._bytes == http.MAX_RESPONSE_BYTES


def test_aggregate_includes_discarded_and_redirect_bodies():
    overhead = len(Response(status=404).header_wire)
    body = b"x" * (http.MAX_RESPONSE_BYTES - overhead - 1)
    fetcher, calls, _, _ = make_fetcher([Response(body, status=404) for _ in range(6)] +
                                     [Response(b"redirect", status=302, location="/next")])
    for _ in range(6):
        assert fetcher.fetch("https://public.example").status == "failed"
    assert fetcher.fetch("https://public.example").status == "budget_exhausted"
    assert fetcher._bytes == http.MAX_TOTAL_BYTES
    assert len(calls) == 7


def test_attempt_budget_counts_failures_and_redirects():
    fetcher, calls, _, _ = make_fetcher([Response(status=404) for _ in range(12)])
    for _ in range(12):
        assert fetcher.fetch("https://public.example").status == "failed"
    assert fetcher.fetch("https://public.example").status == "budget_exhausted"
    assert len(calls) == 12


@pytest.mark.parametrize("name,max_value", [("connect_seconds", 3), ("read_seconds", 5), ("total_seconds", 90)])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "1", 91])
def test_timeout_config_finite_positive_bounded(name, max_value, value):
    with pytest.raises(ValueError):
        http.FetchConfig(**{name: value})


def test_deadline_before_connect_and_after_read():
    now = [0.0]
    fetcher, calls, _, _ = make_fetcher(clock=lambda: now[0])
    now[0] = 91
    assert fetcher.fetch("https://public.example").status == "timed_out"
    assert not calls
    now[0] = 0
    response = Response()
    original = response.read1
    def slow_read(size):
        now[0] = 91
        return original(size)
    response.read1 = slow_read
    fetcher, _, _, _ = make_fetcher([response], clock=lambda: now[0])
    assert fetcher.fetch("https://public.example").status == "timed_out"
    assert fetcher._bytes == len(response.header_wire) + 5  # Discarded bytes count.


def test_remaining_timeout_passed_to_dns_and_connection():
    now = [0.0]
    fetcher, calls, dns, _ = make_fetcher(clock=lambda: now[0])
    now[0] = 89
    assert fetcher.fetch("https://public.example").status == "ok"
    assert dns[0][1] == calls[0][2] == 1


def test_generic_tls_failure_consumes_attempt():
    import ssl
    fetcher, _, _, _ = make_fetcher()
    def fail(*args):
        raise ssl.SSLCertVerificationError("secret exception")
    fetcher._factory = fail
    result = fetcher.fetch("https://public.example")
    assert result.error == "tls_failure"
    assert "secret" not in repr(result)
    assert fetcher._attempts == 1


@pytest.mark.parametrize("response", [Response(status="200"), Response(status=302), Response(status=999)])
def test_malformed_response(response):
    fetcher, _, _, _ = make_fetcher([response])
    assert fetcher.fetch("https://public.example").status == "failed"


@pytest.mark.parametrize("headers", [
    {"content-length": "garbage"}, {"content-length": "-1"},
    {"content-length": "5, 5"}, {"content-length": "10"},
    {"transfer-encoding": "gzip"},
    {"transfer-encoding": "chunked", "content-length": "5"},
])
def test_malformed_framing_is_rejected(headers):
    fetcher, _, _, _ = make_fetcher([Response(**headers)])
    assert fetcher.fetch("https://public.example").status == "failed"


def test_truncated_response_fails_closed():
    response = Response(b"short")
    response.length = 50  # http.client leaves nonzero length on premature EOF.
    fetcher, _, _, _ = make_fetcher([response])
    assert fetcher.fetch("https://public.example").status == "failed"


@pytest.mark.parametrize("wire", [
    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello",
    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n",
])
def test_production_socket_pinning_tls_and_real_http_parser(monkeypatch, wire):
    import socket
    import ssl
    events = []
    class FakeSocket:
        def __init__(self):
            self.wire = io.BytesIO(wire)
            self.closed = False
        def settimeout(self, value):
            assert 0 < value <= 5
        def connect(self, destination):
            events.append(("connect", destination))
        def do_handshake(self):
            events.append(("handshake",))
        def sendall(self, data):
            assert b"Host: public.example" in data
            assert b"Cookie:" not in data and b"Authorization:" not in data
        def recv_into(self, buffer):
            assert not self.closed
            data = self.wire.read(len(buffer))
            buffer[:len(data)] = data
            return len(data)
        def close(self):
            self.closed = True
    raw = FakeSocket()
    real_context = ssl.create_default_context
    def context():
        actual = real_context()
        assert actual.check_hostname
        assert actual.verify_mode == ssl.CERT_REQUIRED
        def wrap(sock, *, server_hostname, do_handshake_on_connect):
            assert sock is raw
            assert not do_handshake_on_connect
            events.append(("tls", server_hostname))
            return raw
        return SimpleNamespace(wrap_socket=wrap, check_hostname=actual.check_hostname,
                               verify_mode=actual.verify_mode)
    monkeypatch.setattr(socket, "socket", lambda *args: raw)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("second lookup"))
    monkeypatch.setattr(ssl, "create_default_context", context)
    fetcher = http.PublicFetcher(resolver=lambda host, seconds: ["8.8.8.8"])
    result = fetcher.fetch("https://public.example/jobs")
    assert result.status == "ok"
    assert result.text == "hello"
    assert events == [("connect", ("8.8.8.8", 443)), ("tls", "public.example"), ("handshake",)]
    assert raw.closed


def test_dns_workers_are_capped_and_nonblocking(monkeypatch):
    import socket
    import threading
    entered = threading.Barrier(3)
    release = threading.Event()
    finished = threading.Barrier(3)
    def stuck(*args, **kwargs):
        entered.wait(timeout=2)
        release.wait(timeout=2)
        finished.wait(timeout=2)
        return [(None, None, None, None, ("8.8.8.8", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", stuck)
    try:
        for _ in range(2):
            with pytest.raises(TimeoutError):
                http.bounded_resolve("public.example", 0.01)
        entered.wait(timeout=2)
        with pytest.raises(http.DNSBusy):
            http.bounded_resolve("public.example", 0.01)
    finally:
        release.set()
        finished.wait(timeout=2)


def test_deadline_reader_stops_trickled_headers():
    now = [0.0]
    conn = http.PinnedHTTPSConnection("public.example", "8.8.8.8", 3)
    conn.set_deadline(1, lambda: now[0], 5)
    def recv(buffer):
        buffer[0] = 65
        now[0] = 2
        return 1
    conn._tls_sock = SimpleNamespace(recv_into=recv, settimeout=lambda timeout: None)
    with pytest.raises(TimeoutError):
        http._DeadlineReader(conn).readinto(bytearray(1))


class WireConnection(http.PinnedHTTPSConnection):
    """Production HTTP parser and raw reader, synthetic TLS bytes only."""
    def __init__(self, wire, now=None, delayed=False):
        super().__init__("public.example", "8.8.8.8", 3)
        self.wire = io.BytesIO(wire)
        self.received = 0
        self.closed = False
        def recv(buffer):
            data = self.wire.read(len(buffer))
            buffer[:len(data)] = data
            self.received += len(data)
            if delayed:
                now[0] = 91
            return len(data)
        self._tls_sock = SimpleNamespace(recv_into=recv, settimeout=lambda value: None,
                                        close=lambda: setattr(self, "closed", True))

    def connect(self):
        self.sock = self._tls_sock

    def request(self, *args, **kwargs):
        self._HTTPConnection__state = http.http.client._CS_REQ_SENT


@pytest.mark.parametrize("aggregate", [False, True])
@pytest.mark.parametrize("framing", [b"Content-Length: 1000\r\n", b"Transfer-Encoding: chunked\r\n"])
def test_real_parser_prefetch_obeys_wire_budget(monkeypatch, aggregate, framing):
    wire = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n" + framing + b"\r\n"
    wire += b"3e8\r\n" + b"x" * 1000 + b"\r\n0\r\n\r\n" if b"chunked" in framing else b"x" * 1000
    conn = WireConnection(wire)
    monkeypatch.setattr(http, "MAX_RESPONSE_BYTES", 128)
    fetcher = http.PublicFetcher(resolver=lambda *args: ["8.8.8.8"],
                                 connection_factory=lambda *args: conn)
    initial = http.MAX_TOTAL_BYTES - 96 if aggregate else 0
    fetcher._bytes = initial
    result = fetcher.fetch("https://public.example")
    assert result.status == "budget_exhausted"
    assert conn.received == (96 if aggregate else 128)
    assert fetcher._bytes == initial + conn.received
    assert conn.closed


def test_real_parser_counts_bytes_before_post_recv_deadline():
    now = [0.0]
    wire = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
    conn = WireConnection(wire, now, delayed=True)
    fetcher = http.PublicFetcher(resolver=lambda *args: ["8.8.8.8"],
                                 connection_factory=lambda *args: conn, clock=lambda: now[0])
    assert fetcher.fetch("https://public.example").status == "timed_out"
    assert fetcher._bytes == conn.received == len(wire)
    assert conn.closed


def test_real_parser_counts_delayed_body_receive():
    now = [0.0]
    headers = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\n"
    conn = WireConnection(headers + b"hello")
    def recv(buffer):
        size = len(headers) if conn.received == 0 else len(buffer)
        data = conn.wire.read(min(size, len(buffer)))
        buffer[:len(data)] = data
        conn.received += len(data)
        if conn.received > len(headers):
            now[0] = 91
        return len(data)
    conn._tls_sock.recv_into = recv
    fetcher = http.PublicFetcher(resolver=lambda *args: ["8.8.8.8"],
                                 connection_factory=lambda *args: conn, clock=lambda: now[0])
    assert fetcher.fetch("https://public.example").status == "timed_out"
    assert fetcher._bytes == conn.received == len(headers) + 5
    assert conn.closed


@pytest.mark.parametrize("wire,status", [
    (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\nhello", "ok"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\nX-Trailer: value\r\n\r\n", "ok"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\nshort", "failed"),
    (b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n\r\ndiscarded", "unsupported"),
    (b"HTTP/1.1 302 Found\r\nLocation: https://127.0.0.1\r\nContent-Length: 5\r\n\r\nhello", "blocked"),
    (b"not HTTP\r\n\r\ndiscarded", "failed"),
])
def test_real_parser_charges_all_received_bytes_once(wire, status):
    conn = WireConnection(wire)
    fetcher = http.PublicFetcher(resolver=lambda *args: ["8.8.8.8"],
                                 connection_factory=lambda *args: conn)
    assert fetcher.fetch("https://public.example").status == status
    assert fetcher._bytes == conn.received == len(wire)
    assert conn.closed


@pytest.mark.parametrize("wire", [
    b"HTTP/1.1 200 OK\r\nX-Large: " + b"x" * 1000,
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nX-Trailer: " + b"x" * 1000,
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;extension=" + b"x" * 1000,
])
def test_header_and_chunk_overhead_cannot_escape_wire_cap(monkeypatch, wire):
    monkeypatch.setattr(http, "MAX_RESPONSE_BYTES", 128)
    conn = WireConnection(wire)
    fetcher = http.PublicFetcher(resolver=lambda *args: ["8.8.8.8"],
                                 connection_factory=lambda *args: conn)
    assert fetcher.fetch("https://public.example").status == "budget_exhausted"
    assert fetcher._bytes == conn.received == 128
    assert conn.closed


def test_dns_busy_and_timeout_are_generic():
    fetcher, calls, _, _ = make_fetcher()
    def busy(*args):
        raise http.DNSBusy("private details")
    fetcher._resolver = busy
    assert fetcher.fetch("https://public.example").error == "dns_busy"
    def timeout(*args):
        raise TimeoutError("private details")
    fetcher._resolver = timeout
    assert fetcher.fetch("https://public.example").status == "timed_out"
    assert not calls
