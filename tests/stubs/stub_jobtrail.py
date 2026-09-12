"""In-process JobTrail HTTP server stub used by the hermetic e2e suite.

This module replaces the production JobTrail backend with a
:class:`http.server.BaseHTTPRequestHandler` bound to a random localhost
port. The handler imitates the three endpoints the orchestration touches:

* ``POST /api/discover/search`` — returns the listings produced by the
  bound :class:`stubs.stub_jobspy.StubJobSpy` (or a 503 response while
  ``state.fail_search`` is positive, to exercise the retry path);
* ``POST /api/discover/import`` — returns ``{"id": "<source>:<sourceJobId>"}``
  for each successful import, or 503 when the import's ``sourceJobId`` is
  in ``state.fail_import_ids``;
* ``GET /api/jobs/<urlencoded id>`` — returns the stored job record plus
  any pre-seeded notes from :meth:`StubJobTrailState.set_notes`.

The state object exposes the underlying maps so tests can assert on the
exact calls the orchestrator made. The server runs on a daemon thread so
the test process can shut it down via the context-manager protocol.

Only the standard library (``http.server``, ``threading``, ``json``,
``urllib.parse``) is used. ``httpx`` is already a runtime dependency of
the package and is used implicitly by the orchestrator's HTTP client; this
stub does not depend on it.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from .stub_jobspy import StubJobSpy


class StubJobTrailState:
    """Mutable, lock-protected state shared by the server and the stubs.

    The HTTP handler reads and writes :attr:`jobs` from its own thread; the
    stub scorer writes :meth:`set_notes` from the orchestrator's thread. A
    single ``threading.Lock`` guards every mutation so the partial-failure
    and redaction tests stay deterministic regardless of scheduling.
    """

    def __init__(self, jobspy: StubJobSpy | None = None) -> None:
        self.lock = threading.Lock()
        self.jobspy = jobspy or StubJobSpy()
        self.searches: list[dict[str, Any]] = []
        self.imported: list[dict[str, Any]] = []
        self.jobs: dict[str, dict[str, Any]] = {}
        # Failure injection knobs.
        self.fail_search: int = 0  # number of search attempts that 503 first
        self.fail_import_ids: set[str] = set()  # sourceJobIds that 503 on import

    @staticmethod
    def id_for(source: str, source_job_id: str) -> str:
        """Return the stable JobTrail id used by the orchestrator."""

        return f"{source}:{source_job_id}"

    def set_notes(self, job_id: str, notes: list[dict[str, Any]]) -> None:
        """Attach ``notes`` to ``job_id`` (or stage them if the job is unknown)."""

        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id]["notes"] = list(notes)
                return
            # Stage notes so the GET handler can attach them when the job
            # arrives (only relevant when the scorer runs before import,
            # which never happens with the production orchestrator order).
            pending = getattr(self, "_pending_notes", None)
            if pending is None:
                self._pending_notes = {}
                pending = self._pending_notes
            pending[job_id] = list(notes)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the state for assertions."""

        with self.lock:
            return {
                "searches": [dict(item) for item in self.searches],
                "imported": [dict(item) for item in self.imported],
                "jobs": {key: dict(value) for key, value in self.jobs.items()},
            }


def _make_handler(state: StubJobTrailState) -> type[BaseHTTPRequestHandler]:
    """Build a ``BaseHTTPRequestHandler`` subclass bound to ``state``."""

    class _Handler(BaseHTTPRequestHandler):
        # Silence stderr access logging so the test output stays clean.
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        # --- helpers ---------------------------------------------------------

        def _write_json(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        # --- POST endpoints --------------------------------------------------

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            payload = self._read_json()

            if parsed.path == "/api/discover/search":
                with state.lock:
                    if state.fail_search > 0:
                        state.fail_search -= 1
                        self._write_json(
                            503,
                            {"error": "transient", "stage": "search"},
                        )
                        return
                    state.searches.append(dict(payload))
                listings = state.jobspy.search(payload)
                self._write_json(200, {"results": listings})
                return

            if parsed.path == "/api/discover/import":
                source = str(payload.get("source", "site"))
                source_job_id = str(payload.get("sourceJobId", ""))
                with state.lock:
                    if source_job_id in state.fail_import_ids:
                        self._write_json(
                            503,
                            {"error": "transient", "stage": "import"},
                        )
                        return
                    job_id = StubJobTrailState.id_for(source, source_job_id)
                    state.imported.append(dict(payload))
                    job_record: dict[str, Any] = dict(payload)
                    job_record["id"] = job_id
                    pending = getattr(state, "_pending_notes", None)
                    seeded_notes = (
                        list(pending.get(job_id, [])) if pending else []
                    )
                    job_record["notes"] = seeded_notes
                    state.jobs[job_id] = job_record
                self._write_json(200, {"id": job_id})
                return

            self._write_json(404, {"error": "not found", "path": parsed.path})

        # --- GET endpoints ---------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = parsed.path.split("/")
            if parsed.path == "/api/jobs":
                with state.lock:
                    self._write_json(200, list(state.jobs.values()))
                return
            # /api/jobs/<id> — ``id`` may itself contain URL-encoded slashes
            if len(parts) >= 4 and parts[1] == "api" and parts[2] == "jobs":
                job_id = unquote("/".join(parts[3:]))
                with state.lock:
                    job = state.jobs.get(job_id)
                    if job is None:
                        self._write_json(404, {"error": "not found", "id": job_id})
                        return
                    snapshot = dict(job)
                    snapshot["notes"] = list(job.get("notes", []))
                self._write_json(200, snapshot)
                return
            self._write_json(404, {"error": "not found", "path": parsed.path})

    return _Handler


class StubJobTrailServer:
    """Context-managed HTTP server that imitates the JobTrail backend."""

    def __init__(self, state: StubJobTrailState | None = None) -> None:
        self.state = state or StubJobTrailState()
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", 0), _make_handler(self.state)
        )
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        """Return ``http://127.0.0.1:<port>`` for the bound socket."""

        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        """Start the daemon thread that serves ``base_url``."""

        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="stub-jobtrail",
        )
        self._thread.start()

    def stop(self) -> None:
        """Shut down the server and join its thread."""

        self._server.shutdown()
        self._server.server_close()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._thread = None

    # Context-manager protocol -------------------------------------------------

    def __enter__(self) -> "StubJobTrailServer":
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.stop()
