"""Hermetic stub package for the JobTrailAutomation end-to-end suite.

The modules in this package imitate the external dependencies that the
production orchestration touches at run time:

* ``stub_jobspy`` produces JobSpy-style search listings;
* ``stub_jobtrail`` runs an in-process HTTP server that imitates the
  JobTrail ``/api/discover/search``, ``/api/discover/import``, and
  ``/api/jobs/<id>`` endpoints;
* ``stub_scorer`` is a fake scorer provider that writes a fixed
  ``[AI_JOB_SCORE_V1]`` note into the stub JobTrail state;
* ``stub_whatsapp`` captures the WhatsApp helper message body to an
  in-memory buffer.

These stubs use only the standard library (plus ``httpx`` and ``yaml``
already declared as runtime dependencies in ``pyproject.toml``) so the e2e
suite runs in CI without Docker, systemd, or any real network service.
"""

from __future__ import annotations
