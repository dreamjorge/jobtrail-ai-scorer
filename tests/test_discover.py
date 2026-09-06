"""Tests for the dynamic JobTrail backend URL discovery helper."""

from __future__ import annotations

import pytest

from jobtrail_ai_scorer import discover
from jobtrail_ai_scorer.discover import (
    DEFAULT_DOCKER_CONTAINER,
    DEFAULT_PUBLISHED_URL,
    BackendDiscoveryError,
    resolve_backend_url,
)

# RFC 5737 / 6890 reserved documentation ranges; safe to commit as test data.
_DOC_IP_1 = "198.51.100.42"
_DOC_IP_2 = "203.0.113.7"


class FakeProbe:
    """Records probe requests and returns scripted reachability results."""

    def __init__(self, results: dict[str, bool]) -> None:
        self.calls: list[str] = []
        self._results = results

    def __call__(self, url: str) -> bool:
        self.calls.append(url)
        return self._results.get(url, False)


class FakeInspect:
    """Records docker-inspect invocations and returns scripted outputs."""

    def __init__(self, outputs: dict[str, object]) -> None:
        self.calls: list[str] = []
        self._outputs = outputs

    def __call__(self, container_name: str) -> object:
        self.calls.append(container_name)
        return self._outputs.get(container_name)


def test_default_constants_are_public_and_redacted():
    # Must not bake a private host or Docker IP into defaults.
    assert DEFAULT_PUBLISHED_URL == "http://127.0.0.1:8000"
    assert DEFAULT_DOCKER_CONTAINER == "jobtrail-backend-1"
    for value in (DEFAULT_PUBLISHED_URL, DEFAULT_DOCKER_CONTAINER):
        assert "0.0.0.0" not in value


def test_resolve_backend_url_prefers_published_port():
    probe = FakeProbe({DEFAULT_PUBLISHED_URL: True})
    inspect = FakeInspect({})

    url, source = resolve_backend_url(
        probe=probe, inspect=inspect, container_name=DEFAULT_DOCKER_CONTAINER
    )

    assert url == DEFAULT_PUBLISHED_URL
    assert source == "published-port"
    assert probe.calls == [DEFAULT_PUBLISHED_URL]
    assert inspect.calls == []


def test_resolve_backend_url_falls_back_to_docker_ip_when_published_unreachable():
    docker_ip = _DOC_IP_1
    probe = FakeProbe({DEFAULT_PUBLISHED_URL: False, f"http://{docker_ip}:8000": True})
    inspect = FakeInspect({DEFAULT_DOCKER_CONTAINER: docker_ip})

    url, source = resolve_backend_url(
        probe=probe, inspect=inspect, container_name=DEFAULT_DOCKER_CONTAINER
    )

    assert url == f"http://{docker_ip}:8000"
    assert source == "docker-container"
    assert probe.calls == [DEFAULT_PUBLISHED_URL, f"http://{docker_ip}:8000"]
    assert inspect.calls == [DEFAULT_DOCKER_CONTAINER]


def test_resolve_backend_url_fails_closed_when_both_unreachable():
    probe = FakeProbe({})
    inspect = FakeInspect({})

    with pytest.raises(BackendDiscoveryError) as exc:
        resolve_backend_url(
            probe=probe, inspect=inspect, container_name=DEFAULT_DOCKER_CONTAINER
        )

    message = str(exc.value)
    assert DEFAULT_PUBLISHED_URL in message
    assert DEFAULT_DOCKER_CONTAINER in message
    # Should not have probed any docker URL because container lookup failed.
    assert not any(call.startswith(f"http://{_DOC_IP_1}") for call in probe.calls)


def test_resolve_backend_url_fails_closed_when_docker_inspect_returns_empty():
    probe = FakeProbe({DEFAULT_PUBLISHED_URL: False})
    inspect = FakeInspect({DEFAULT_DOCKER_CONTAINER: ""})

    with pytest.raises(BackendDiscoveryError):
        resolve_backend_url(
            probe=probe, inspect=inspect, container_name=DEFAULT_DOCKER_CONTAINER
        )


def test_resolve_backend_url_fails_closed_when_docker_inspect_raises():
    def raising_inspect(container_name: str) -> str:
        raise discover.DockerInspectError("docker not available")

    probe = FakeProbe({DEFAULT_PUBLISHED_URL: False})

    with pytest.raises(BackendDiscoveryError) as exc:
        resolve_backend_url(
            probe=probe,
            inspect=raising_inspect,
            container_name=DEFAULT_DOCKER_CONTAINER,
        )
    assert "docker not available" in str(exc.value)


def test_resolve_backend_url_uses_custom_container_name_when_published_unreachable():
    custom_name = "jobtrail-backend-qa"
    docker_ip = _DOC_IP_1
    probe = FakeProbe({DEFAULT_PUBLISHED_URL: False, f"http://{docker_ip}:8000": True})
    inspect = FakeInspect({custom_name: docker_ip})

    url, source = resolve_backend_url(
        probe=probe, inspect=inspect, container_name=custom_name
    )

    assert url == f"http://{docker_ip}:8000"
    assert source == "docker-container"
    assert inspect.calls == [custom_name]


def test_resolve_backend_url_does_not_call_docker_when_published_is_reachable():
    # Defensive: docker should never be touched when the published port works.
    def fail_inspect(container_name: str) -> str:
        raise AssertionError(
            "docker should not be consulted when the published port responds"
        )

    probe = FakeProbe({DEFAULT_PUBLISHED_URL: True})
    url, source = resolve_backend_url(probe=probe, inspect=fail_inspect)

    assert url == DEFAULT_PUBLISHED_URL
    assert source == "published-port"


def test_resolve_backend_url_real_probe_uses_urllib(monkeypatch):
    # Verifies that the default probe delegates to urllib.request, not httpx,
    # which keeps the helper dependency-free.
    import urllib.request

    captured: list[tuple[str, float]] = []

    def fake_urlopen(request, *, timeout):  # type: ignore[no-untyped-def]
        captured.append((request.full_url, timeout))
        raise AssertionError("network must not be used in tests")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(BackendDiscoveryError):
        resolve_backend_url(
            timeout=1.5,
            container_name=DEFAULT_DOCKER_CONTAINER,
        )

    # We should have probed the published URL at least once via urllib.
    assert any(url.startswith(DEFAULT_PUBLISHED_URL) for url, _ in captured)


def test_probe_url_returns_true_on_success(monkeypatch):
    import urllib.request

    class _Resp:
        status = 200

    def fake_urlopen(request, *, timeout):
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert discover._probe_url(DEFAULT_PUBLISHED_URL, timeout=1.0) is True


def test_probe_url_returns_false_on_urllib_error(monkeypatch):
    import urllib.error
    import urllib.request

    def fake_urlopen(request, *, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert discover._probe_url(DEFAULT_PUBLISHED_URL, timeout=1.0) is False


def test_docker_inspect_ip_extracts_ip_from_docker_output(monkeypatch):
    fake_output = f"{_DOC_IP_2}\n"

    def fake_run(args, *, capture_output, text, timeout, check):  # type: ignore[no-untyped-def]
        class _Result:
            stdout = fake_output
            returncode = 0

        return _Result()

    monkeypatch.setattr(discover.subprocess, "run", fake_run)

    assert (
        discover._docker_inspect_ip(DEFAULT_DOCKER_CONTAINER, timeout=1.0) == _DOC_IP_2
    )


def test_docker_inspect_ip_rejects_empty(monkeypatch):
    def fake_run(args, *, capture_output, text, timeout, check):
        class _Result:
            stdout = "   \n"
            returncode = 0

        return _Result()

    monkeypatch.setattr(discover.subprocess, "run", fake_run)

    with pytest.raises(discover.DockerInspectError):
        discover._docker_inspect_ip(DEFAULT_DOCKER_CONTAINER, timeout=1.0)


def test_module_does_not_expose_a_hard_coded_private_ip():
    # Sanity guard: defaults must never contain private IPs from runtime hosts.
    for name in dir(discover):
        value = getattr(discover, name)
        if isinstance(value, str):
            # Reject anything that looks like a 10.x / 192.168.x / 172.16-31.x
            # host baked into constants. Public loopback (127.0.0.1) is fine.
            assert not _looks_like_private_ip(value), (
                f"discover.{name} contains a private IP constant: {value!r}"
            )


def _looks_like_private_ip(value: str) -> bool:
    import re

    return bool(
        re.search(
            r"\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[0-1])\.\d+\.\d+)",
            value,
        )
    )
