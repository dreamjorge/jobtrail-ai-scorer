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


# R5: Bounded regression tests for credential policy alignment and IPv6 ULA


def test_encoded_credential_key_in_query():
    """URL-encoded credential key ?api%20key=VALUE must be rejected."""
    assert http._safe_url("https://public.example/?api%20key=VALUE") is None


def test_encoded_credential_key_in_fragment():
    """URL-encoded credential key in fragment #api%20key=VALUE must be rejected."""
    assert http._safe_url("https://public.example/path#api%20key=VALUE") is None


def test_fragment_with_nested_url_having_credentials():
    """Fragment containing nested URL with credentials must be rejected."""
    assert http._safe_url("https://public.example/#https://evil.com?token=xxx") is None


def test_url_encoded_secret_key_api():
    """URL-encoded secretkey and api key in same query must be rejected."""
    assert http._safe_url("https://public.example/?api%20key=secret&apikey=VALUE") is None


def test_ipv6_ula_fc00_rejected():
    """IPv6 ULA fc00::/8 site-local must be rejected by _public_ip."""
    with pytest.raises(ValueError, match="blocked address"):
        http._public_ip("fc00::1")


def test_ipv6_ula_fd00_rejected():
    """IPv6 ULA fd00::/8 site-local must be rejected by _public_ip."""
    with pytest.raises(ValueError, match="blocked address"):
        http._public_ip("fd00::1")
