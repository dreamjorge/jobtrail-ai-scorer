"""Dynamic JobTrail backend URL discovery.

The systemd service previously baked ``JOBTRAIL_BASE_URL=http://<docker-ip>:8000``
into ``/DATA/AppData/jobtrail/search-automation.env`` by running ``docker inspect``
once at start. That left the runtime pointing at a stale IP when the container
was recreated or when a future ``compose.hub.yml`` published the backend on the
host.

This module recomputes the URL on each call. The explicit precedence is:

1. ``http://127.0.0.1:8000`` (the published host port) if reachable.
2. ``http://<docker-container-ip>:8000`` from ``docker inspect <container>`` if reachable.
3. ``BackendDiscoveryError`` — fail closed with a clear message.

The helper is dependency-free (``urllib.request`` + ``subprocess``) so it can be
invoked from any launcher or test without importing ``httpx``.
"""

from __future__ import annotations

import subprocess
import urllib.error
import urllib.request
from typing import Callable


DEFAULT_PUBLISHED_URL = "http://127.0.0.1:8000"
DEFAULT_DOCKER_CONTAINER = "jobtrail-backend-1"
DEFAULT_PORT = 8000
DEFAULT_PROBE_TIMEOUT_SECONDS = 2.0
DEFAULT_INSPECT_TIMEOUT_SECONDS = 5.0

ProbeFn = Callable[[str], bool]
InspectFn = Callable[[str], str]


class BackendDiscoveryError(RuntimeError):
    """Raised when neither the published port nor the Docker container is reachable."""


class DockerInspectError(RuntimeError):
    """Raised when ``docker inspect`` cannot return a usable container IP."""


def resolve_backend_url(
    container_name: str = DEFAULT_DOCKER_CONTAINER,
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    published_url: str = DEFAULT_PUBLISHED_URL,
    inspect_timeout: float = DEFAULT_INSPECT_TIMEOUT_SECONDS,
    probe: ProbeFn | None = None,
    inspect: InspectFn | None = None,
) -> tuple[str, str]:
    """Return ``(url, source)`` for the JobTrail backend, or raise.

    ``source`` is one of ``"published-port"`` or ``"docker-container"`` so callers
    can log which branch served the run.

    Inject ``probe`` (taking a URL and returning True/False) and ``inspect``
    (taking a container name and returning the container IP) to override the
    defaults in tests. Production code should rely on the defaults.
    """
    if probe is not None:
        probe_fn = probe
    else:
        probe_fn = _make_default_probe(timeout)
    if inspect is not None:
        inspect_fn = inspect
    else:
        inspect_fn = _make_default_inspect(inspect_timeout)

    # Branch 1: published host port (preferred).
    try:
        if probe_fn(published_url):
            return published_url, "published-port"
    except Exception as error:  # pragma: no cover - defensive
        # A probe exception means we cannot trust this branch; fall through.
        _publish_probe_diagnostic(error)

    # Branch 2: Docker container IP.
    try:
        docker_ip = inspect_fn(container_name)
    except (DockerInspectError, Exception) as error:
        if isinstance(error, DockerInspectError):
            message = str(error)
        else:
            message = f"{type(error).__name__}: {error}"
        raise BackendDiscoveryError(
            f"Failed to inspect Docker container {container_name!r}: {message}. "
            "Verify that compose.hub.yml is running and the container name is correct."
        ) from error

    docker_ip = (docker_ip or "").strip()
    if docker_ip:
        candidate = _format_container_url(docker_ip, published_url)
        try:
            if probe_fn(candidate):
                return candidate, "docker-container"
        except Exception as error:  # pragma: no cover - defensive
            _publish_probe_diagnostic(error)

    raise BackendDiscoveryError(
        "Unable to reach JobTrail backend at "
        f"{published_url!r} or via Docker container {container_name!r}. "
        "Verify that compose.hub.yml publishes the backend on 127.0.0.1:8000 "
        "or that the jobtrail-backend-1 container is running and bound to port 8000."
    )


def _format_container_url(docker_ip: str, published_url: str) -> str:
    """Build the candidate URL for the container IP, preserving the published port."""
    scheme = published_url.split("://", 1)[0] if "://" in published_url else "http"
    return f"{scheme}://{docker_ip}:{DEFAULT_PORT}"


def _make_default_probe(timeout: float) -> ProbeFn:
    def _probe(url: str) -> bool:
        return _probe_url(url, timeout=timeout)

    return _probe


def _make_default_inspect(timeout: float) -> InspectFn:
    def _inspect(container_name: str) -> str:
        return _docker_inspect_ip(container_name, timeout=timeout)

    return _inspect


def _probe_url(url: str, *, timeout: float) -> bool:
    """Return True iff ``url`` answers an HTTP request within ``timeout`` seconds.

    Uses ``urllib.request`` to keep the helper dependency-free. Any
    ``URLError``/``HTTPError``/timeout/connection issue is treated as
    "unreachable" so the next branch can be tried.
    """
    request = urllib.request.Request(url, method="GET")
    try:
        urllib.request.urlopen(request, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        # The server replied; for discovery purposes an HTTP response means
        # the backend is reachable (e.g. 404 on root is fine — we want any reply).
        return True
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


def _docker_inspect_ip(container_name: str, *, timeout: float) -> str:
    """Run ``docker inspect`` and return the container's bridge IP, or raise."""
    args = [
        "docker",
        "inspect",
        "-f",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        container_name,
    ]
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise DockerInspectError(f"docker inspect failed: {error}") from error

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise DockerInspectError(
            stderr or f"docker inspect exited with status {completed.returncode}"
        )

    output = (completed.stdout or "").strip()
    if not output:
        raise DockerInspectError(
            f"docker inspect returned no IP for container {container_name!r}"
        )
    return output


def _publish_probe_diagnostic(_error: BaseException) -> None:
    """Reserved hook so future diagnostic logging stays in one place."""
    return None


__all__ = [
    "BackendDiscoveryError",
    "DEFAULT_DOCKER_CONTAINER",
    "DEFAULT_PORT",
    "DEFAULT_PROBE_TIMEOUT_SECONDS",
    "DEFAULT_PUBLISHED_URL",
    "DockerInspectError",
    "resolve_backend_url",
]
