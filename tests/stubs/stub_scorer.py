"""Fake scorer provider used by the hermetic e2e suite.

The production scorer is invoked as ``SCORER_COMMAND score --config CFG
--job-id ID --force`` (see ``JobTrailAutomation._score``). The orchestrator
treats the scorer as a ``Callable[[str, str], Any]`` so tests can inject a
stub without spawning a subprocess or talking to a provider.

This stub writes a fixed ``[AI_JOB_SCORE_V1]`` note into the supplied
:class:`stubs.stub_jobtrail.StubJobTrailState` so the orchestrator's
``parse_score_note`` returns a valid score, which in turn lets the
``NotificationBuilder`` assemble a best-match summary. The stub also records
every ``(job_id, config_path)`` pair it was asked to score so tests can
assert the orchestrator invoked it the expected number of times.
"""

from __future__ import annotations

import json
from typing import Any

from .stub_jobtrail import StubJobTrailState


class StubScorer:
    """Write a fixed score note to the stub JobTrail state on every call."""

    def __init__(
        self,
        state: StubJobTrailState,
        *,
        default_score: int = 85,
        recommendation: str = "APPLY",
        strengths: list[str] | None = None,
        gaps: list[str] | None = None,
    ) -> None:
        self.state = state
        self.default_score = default_score
        self.default_recommendation = recommendation
        self.default_strengths: list[str] = list(
            strengths if strengths is not None else ["Python"]
        )
        self.default_gaps: list[str] = list(
            gaps if gaps is not None else ["None"]
        )
        self.calls: list[tuple[str, str]] = []

    def __call__(self, job_id: str, config_path: str) -> None:
        """Write a ``[AI_JOB_SCORE_V1]`` note for ``job_id`` into ``state``."""

        self.calls.append((job_id, config_path))
        payload: dict[str, Any] = {
            "score": self.default_score,
            "recommendation": self.default_recommendation,
            "strengths": list(self.default_strengths),
            "gaps": list(self.default_gaps),
        }
        notes = [{"body": f"[AI_JOB_SCORE_V1]\n{json.dumps(payload)}"}]
        self.state.set_notes(job_id, notes)
