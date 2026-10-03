"""Direct public guards; no destination fetching in this snapshot."""
from types import SimpleNamespace
import io
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



@pytest.mark.parametrize("name,max_value", [("connect_seconds", 3), ("read_seconds", 5), ("total_seconds", 90)])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "1", 91])
def test_timeout_config_finite_positive_bounded(name, max_value, value):
    with pytest.raises(ValueError):
        http.FetchConfig(**{name: value})


@pytest.mark.parametrize("url", [
    "http://public.example", "https://user:secret@public.example",
    "https://public.example:444", "https://localhost", "https://x.local",
    "https://169.254.169.254", "https://[::1]", "https://[::ffff:127.0.0.1]",
    "https://224.0.0.1", "https://public.example/?api_key=secret",
])
def test_unsafe_urls_never_resolve_or_connect(url):
    assert http._safe_url(url) is None


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"])
def test_direct_address_guard(ip):
    with pytest.raises(ValueError):
        http._public_ip(ip)

def test_direct_public_url():
    assert http._safe_url("https://public.example/jobs#x") == "https://public.example/jobs"


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



def test_direct_pinned_transport_parser_and_wire_budget():
    wire = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
    conn = WireConnection(wire)
    owner = SimpleNamespace(_bytes=0)
    budget = http._ByteBudget(owner)
    conn.set_byte_budget(budget)
    conn.connect()
    conn.request("GET", "/")
    response = conn.getresponse()
    assert response.read() == b"hello"
    assert owner._bytes == conn.received == len(wire)
    response.close()
    conn.close()
    assert conn.closed

def test_direct_wire_cap(monkeypatch):
    monkeypatch.setattr(http, "MAX_RESPONSE_BYTES", 2)
    owner = SimpleNamespace(_bytes=0)
    budget = http._ByteBudget(owner)
    assert budget.allowance(100) == 2
    budget.received(2)
    with pytest.raises(http._ByteBudgetExceeded):
        budget.allowance(1)
    assert owner._bytes == 2


def test_injected_connection_charges_headers_and_body():
    owner = SimpleNamespace(_bytes=0)
    response = Response()
    conn = Connection(response)
    conn.set_byte_budget(http._ByteBudget(owner))
    assert conn.getresponse() is response
    assert response.read1(8) == b"hello"
    assert owner._bytes == len(response.header_wire) + 5
    conn.close()
    assert conn.closed
