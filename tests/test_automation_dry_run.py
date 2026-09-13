"""Offline automation dry-run seam (issue #36).

The ``JobTrailAutomation`` constructor accepts an optional ``simulation``
keyword that swaps the production pipeline for a deterministic, hermetic
replay built from a :class:`SimulationScenario`. This module pins that
contract:

* No live network, no Docker, no subprocess, no private-profile read.
* No ``gateway.import_job`` / ``gateway.get_job`` calls.
* The pipeline still produces an :class:`AutomationRun`-shaped object that
  callers (and the launcher) can introspect, plus a
  :class:`PlannedOperations` envelope summarising what would have happened.
* Output is deterministic for a fixed clock/seed/scenario.
"""

from __future__ import annotations

from typing import Any

import pytest

import jobtrail_ai_scorer.automation as _automation

_REQUIRED_SYMBOLS: tuple[str, ...] = (
    "AutomationConfig",
    "AutomationGateway",
    "AutomationRun",
    "JobTrailAutomation",
)

# Optional (slice-specific) symbols. They are looked up lazily inside the
# helper functions below so module collection succeeds in RED phase and the
# per-test ``pytest.fail`` reports the missing names with context.
_OPTIONAL_SYMBOLS: tuple[str, ...] = (
    "PlannedOperations",
    "SimulationScenario",
    "build_planned_operations",
)


_automation_state: dict[str, Any] = {}
_missing_optional: list[str] = []


def _bootstrap_automation_symbols() -> None:
    """Resolve the simulation helpers from the automation module.

    When the slice is RED, the slice-specific symbols do not exist yet.
    Instead of failing module collection (which would block the entire
    test session), the adapter records the missing names and exposes
    ``None`` placeholders. Individual tests call :func:`require_optional`
    to obtain the helper and receive a meaningful ``pytest.fail`` listing
    every missing symbol.
    """

    for name in _REQUIRED_SYMBOLS:
        value = getattr(_automation, name, None)
        if value is None:
            raise RuntimeError(
                f"required automation symbol {name!r} unexpectedly missing"
            )
        _automation_state[name] = value

    for name in _OPTIONAL_SYMBOLS:
        value = getattr(_automation, name, None)
        if value is None:
            _missing_optional.append(name)
        _automation_state[name] = value


def require_optional(name: str) -> Any:
    """Return the optional symbol ``name`` or fail RED-style."""

    value = _automation_state.get(name)
    if value is None:
        pytest.fail(
            "Automation dry-run RED step missing helpers: "
            + ", ".join(_missing_optional or [name])
        )
    return value


_bootstrap_automation_symbols()

AutomationConfig = _automation_state["AutomationConfig"]
AutomationGateway = _automation_state["AutomationGateway"]
AutomationRun = _automation_state["AutomationRun"]
JobTrailAutomation = _automation_state["JobTrailAutomation"]

from jobtrail_ai_scorer.scoring import CURRENT_MARKER  # noqa: E402
from jobtrail_ai_scorer.sources import NormalizedJob  # noqa: E402


# --- Gateway stubs ------------------------------------------------------------


class _RecordingGateway(AutomationGateway):
    """In-process gateway that records every call it receives.

    The dry-run contract states that the gateway MUST NOT be touched when
    ``simulation=`` is supplied. The tests below use the absence of any
    recorded call as evidence that the forbidden boundary was respected.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def import_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("import_job", (payload,)))
        return {"id": "live-id"}

    def get_job(self, job_id: str) -> dict[str, Any]:
        self.calls.append(("get_job", (job_id,)))
        return {"id": job_id, "notes": []}

    def list_jobs(self) -> list[dict[str, Any]]:
        self.calls.append(("list_jobs", ()))
        return []

    def add_note(self, job_id: str, body: str) -> None:
        self.calls.append(("add_note", (job_id, body)))


# --- Helpers ------------------------------------------------------------------


def _score_note(score: int, recommendation: str = "APPLY") -> str:
    return (
        f'{CURRENT_MARKER}\n'
        f'{{"score":{score},"recommendation":"{recommendation}",'
        f'"strengths":[],"gaps":[],"needs_confirmation":[],'
        f'"hard_requirements_missing":[],"career_value":"Medium",'
        f'"reasoning":"ok"}}'
    )


def _job(
    *,
    source: str = "synthetic",
    source_job_id: str = "1",
    title: str = "Engineer",
    company: str = "Acme",
    description: str = "Build things.",
    score: int | None = None,
) -> NormalizedJob:
    metadata: dict[str, Any] = {}
    if score is not None:
        metadata["score_note"] = _score_note(score)
    return NormalizedJob(
        source=source,
        source_job_id=source_job_id,
        title=title,
        company=company,
        description=description,
        source_url=f"https://example.com/jobs/{source_job_id}",
        location="Remote",
        metadata=metadata,
    )


def _base_config() -> AutomationConfig:
    return AutomationConfig(
        scorer_config_path="unused.yaml",
        scorer_command="unused",
        score_threshold=70,
        max_score=10,
        notify_enabled=False,
        whatsapp_command="",
        base_url="https://example.test",
    )


# --- Tests --------------------------------------------------------------------


def test_simulation_seam_is_optional_and_does_not_affect_constructor() -> None:
    gateway = _RecordingGateway()
    JobTrailAutomation(gateway)
    # Constructor did not call into the gateway.
    assert gateway.calls == []


def test_simulation_seam_does_not_call_gateway_when_provided() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    gateway = _RecordingGateway()
    scenario = SimulationScenario(
        name="two-jobs",
        jobs=(_job(source_job_id="1", score=80), _job(source_job_id="2", score=55)),
    )
    automation = JobTrailAutomation(gateway, simulation=scenario)
    result = automation.run(config=_base_config())
    assert isinstance(result, AutomationRun)
    assert gateway.calls == []


def test_simulation_seam_does_not_invoke_default_scorer_subprocess() -> None:
    """``self.scorer`` must remain unused when the simulation supplies scores."""

    SimulationScenario = require_optional("SimulationScenario")
    captured: list[tuple[str, str]] = []

    def forbidden_scorer(job_id: str, config_path: str) -> None:
        captured.append((job_id, config_path))

    gateway = _RecordingGateway()
    scenario = SimulationScenario(
        name="single",
        jobs=(_job(source_job_id="1", score=85),),
    )
    automation = JobTrailAutomation(
        gateway,
        scorer=forbidden_scorer,
        simulation=scenario,
    )
    automation.run(config=_base_config())
    assert captured == []


def test_simulation_does_not_invoke_notifier_even_when_enabled() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    sent: list[str] = []

    def notifier(message: str) -> None:
        sent.append(message)

    gateway = _RecordingGateway()
    scenario = SimulationScenario(
        name="match",
        jobs=(_job(source_job_id="1", score=90, title="Top"),),
    )
    automation = JobTrailAutomation(gateway, notifier=notifier, simulation=scenario)
    config = AutomationConfig(
        scorer_config_path="unused.yaml",
        scorer_command="unused",
        score_threshold=70,
        max_score=10,
        notify_enabled=True,  # even with notify on, no message is sent
        whatsapp_command="unused",
        base_url="https://example.test",
    )
    automation.run(config=config)
    assert sent == []


def test_simulation_picks_best_job_above_threshold() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    gateway = _RecordingGateway()
    scenario = SimulationScenario(
        name="three",
        jobs=(
            _job(source_job_id="1", score=80, title="A"),
            _job(source_job_id="2", score=95, title="B"),
            _job(source_job_id="3", score=42, title="C"),
        ),
    )
    automation = JobTrailAutomation(gateway, simulation=scenario)
    result = automation.run(config=_base_config())
    assert result.selected is not None
    assert result.selected["title"] == "B"
    assert result.selected["score"] == 95
    assert result.searched == 3
    assert result.imported == 0  # no live imports
    assert result.scored == 0  # scoring happened inside the scenario


def test_simulation_filters_below_threshold() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    gateway = _RecordingGateway()
    scenario = SimulationScenario(
        name="all-low",
        jobs=(
            _job(source_job_id="1", score=30),
            _job(source_job_id="2", score=50),
        ),
    )
    automation = JobTrailAutomation(gateway, simulation=scenario)
    result = automation.run(config=_base_config())
    assert result.selected is None


def test_simulation_respects_cap_at_zero() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    gateway = _RecordingGateway()
    scenario = SimulationScenario(
        name="one",
        jobs=(_job(source_job_id="1", score=90),),
    )
    automation = JobTrailAutomation(gateway, simulation=scenario)
    result = automation.run(config=AutomationConfig(score_threshold=70, max_score=0))
    assert result.selected is None
    assert result.scored == 0


def test_simulation_deduplicates_jobs_inside_the_scenario() -> None:
    """Scenario-only dedup: same identity only counts once."""

    SimulationScenario = require_optional("SimulationScenario")
    gateway = _RecordingGateway()
    scenario = SimulationScenario(
        name="dup",
        jobs=(
            _job(source="jobspy", source_job_id="42", score=70),
            _job(source="jobspy", source_job_id="42", score=88),
        ),
    )
    automation = JobTrailAutomation(gateway, simulation=scenario)
    result = automation.run(config=_base_config())
    assert result.selected is not None
    # The later (highest) score wins per the parse_score_note convention.
    assert result.selected["score"] == 88
    assert result.envelope["planned_imports"] == 1


def test_simulation_caps_discovery_order_before_ranking() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    build_planned_operations = require_optional("build_planned_operations")
    scenario = SimulationScenario(
        name="cap",
        jobs=(
            _job(source_job_id="78", score=80, title="First"),
            _job(source_job_id="91", score=99, title="Higher"),
        ),
    )
    config = AutomationConfig(score_threshold=70, max_score=1)

    envelope = build_planned_operations(
        scenario, config, clock_iso="fixed"
    ).to_envelope()

    assert envelope["planned_imports"] == 2
    assert envelope["planned_scores"] == 1
    assert envelope["best"]["sourceJobId"] == "78"


def test_simulation_breaks_ties_by_input_order() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    gateway = _RecordingGateway()
    scenario = SimulationScenario(
        name="ties",
        jobs=(
            _job(source_job_id="1", score=80, title="First"),
            _job(source_job_id="2", score=80, title="Second"),
        ),
    )
    automation = JobTrailAutomation(gateway, simulation=scenario)
    result = automation.run(config=_base_config())
    assert result.selected is not None
    assert result.selected["title"] == "Second"


def test_simulation_handles_empty_discovery() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    gateway = _RecordingGateway()
    scenario = SimulationScenario(name="empty", jobs=())
    automation = JobTrailAutomation(gateway, simulation=scenario)
    result = automation.run(config=_base_config())
    assert result.selected is None
    assert result.searched == 0


def test_simulation_emits_planned_operations_envelope() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    PlannedOperations = require_optional("PlannedOperations")
    build_planned_operations = require_optional("build_planned_operations")
    scenario = SimulationScenario(
        name="envelope",
        jobs=(
            _job(source_job_id="1", score=80),
            _job(source_job_id="2", score=55),
        ),
    )
    config = AutomationConfig(
        scorer_config_path="unused.yaml",
        scorer_command="unused",
        score_threshold=70,
        max_score=10,
        notify_enabled=True,  # required for would_notify to be True
        whatsapp_command="unused",
        base_url="https://example.test",
    )
    planned = build_planned_operations(scenario, config)
    assert isinstance(planned, PlannedOperations)
    envelope = planned.to_envelope()
    assert envelope["scenario"] == "envelope"
    assert envelope["searched"] == 2
    assert envelope["planned_imports"] == 2
    assert envelope["planned_scores"] == 2
    assert envelope["would_notify"] is True
    assert envelope["best"]["score"] == 80
    # Envelope must be JSON-serialisable.
    import json

    json.dumps(envelope)


def test_simulation_envelope_is_deterministic_with_fixed_clock() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    build_planned_operations = require_optional("build_planned_operations")
    scenario = SimulationScenario(
        name="deterministic",
        jobs=(_job(source_job_id="1", score=80),),
    )
    fixed_clock = "2025-01-01T00:00:00+00:00"
    a = build_planned_operations(scenario, _base_config(), clock_iso=fixed_clock)
    b = build_planned_operations(scenario, _base_config(), clock_iso=fixed_clock)
    assert a.to_envelope() == b.to_envelope()


def test_simulation_envelope_redacts_sensitive_substrings() -> None:
    """``PlannedOperations.to_envelope`` MUST scrub private markers."""

    SimulationScenario = require_optional("SimulationScenario")
    build_planned_operations = require_optional("build_planned_operations")
    scenario = SimulationScenario(
        name="scenario RESUME_SENTINEL /DATA/private",
        jobs=(
            _job(
                source_job_id="x",
                score=70,
                title="Engineer at /DATA/AppData/private",
                description="mentions RESUME_SENTINEL token",
            ),
        ),
    )
    planned = build_planned_operations(scenario, _base_config())
    envelope = planned.to_envelope()
    rendered = str(envelope)
    assert "/DATA/AppData" not in rendered
    assert "RESUME_SENTINEL" not in rendered


def test_simulation_with_unknown_scenario_type_raises() -> None:
    gateway = _RecordingGateway()
    with pytest.raises(ValueError, match="simulation"):
        JobTrailAutomation(gateway, simulation="not-a-scenario")


def test_simulation_with_none_is_equivalent_to_no_simulation() -> None:
    gateway = _RecordingGateway()
    JobTrailAutomation(gateway, simulation=None)
    # No gateway call expected from the constructor.
    assert gateway.calls == []


def test_simulation_envelope_attaches_to_run() -> None:
    """The dry-run result MUST expose the planned-operations envelope."""

    SimulationScenario = require_optional("SimulationScenario")
    gateway = _RecordingGateway()
    scenario = SimulationScenario(
        name="attach",
        jobs=(_job(source_job_id="1", score=80, title="Hello"),),
    )
    automation = JobTrailAutomation(gateway, simulation=scenario)
    config = AutomationConfig(
        scorer_config_path="unused.yaml",
        scorer_command="unused",
        score_threshold=70,
        max_score=10,
        notify_enabled=True,
        whatsapp_command="unused",
        base_url="https://example.test",
    )
    result = automation.run(config=config)
    assert getattr(result, "envelope", None) is not None
    assert result.envelope["scenario"] == "attach"
    assert result.envelope["best"]["title"] == "Hello"


def test_simulation_filters_jobs_without_score_note() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    gateway = _RecordingGateway()
    scenario = SimulationScenario(
        name="partial",
        jobs=(
            _job(source_job_id="1", score=85),
            _job(source_job_id="2"),  # no score_note
            _job(source_job_id="3", score=72),
        ),
    )
    automation = JobTrailAutomation(gateway, simulation=scenario)
    result = automation.run(config=_base_config())
    assert result.selected is not None
    assert result.selected["score"] == 85
    assert result.envelope["planned_scores"] == 2


def test_simulation_rejects_invalid_scenario_name_type() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    with pytest.raises(ValueError):
        SimulationScenario(name=123)  # type: ignore[arg-type]


def test_simulation_rejects_non_tuple_jobs() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    with pytest.raises(ValueError):
        SimulationScenario(name="bad", jobs=[_job()])  # type: ignore[arg-type]


def test_simulation_clock_defaults_to_utc_iso_when_unspecified() -> None:
    """When ``clock_iso`` is ``None``, the envelope still carries a parseable timestamp."""

    SimulationScenario = require_optional("SimulationScenario")
    build_planned_operations = require_optional("build_planned_operations")
    scenario = SimulationScenario(name="clock", jobs=(_job(source_job_id="1", score=80),))
    planned = build_planned_operations(scenario, _base_config())
    envelope = planned.to_envelope()
    assert isinstance(envelope["clock"], str)
    # ISO-8601 with timezone offset.
    import re

    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", envelope["clock"])


def test_simulation_envelope_does_not_leak_credential_substrings() -> None:
    """URLs that embed query credentials MUST NOT appear in the envelope."""

    SimulationScenario = require_optional("SimulationScenario")
    build_planned_operations = require_optional("build_planned_operations")
    scenario = SimulationScenario(
        name="leak",
        jobs=(
            _normalised_with_url(
                "https://api.example.com/v1/search?app_id=SECRET_ID&app_key=SECRET_KEY"
            ),
        ),
    )
    planned = build_planned_operations(scenario, _base_config())
    rendered = str(planned.to_envelope())
    assert "SECRET_ID" not in rendered
    assert "SECRET_KEY" not in rendered


def test_simulation_caps_deduplicated_discovery_and_scrubs_complete_envelope() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    build_planned_operations = require_optional("build_planned_operations")
    scenario = SimulationScenario(
        name="scenario RESUME_SENTINEL /DATA/private",
        jobs=(
            _job(source_job_id="78", score=80, title="First"),
            _job(source_job_id="78", score=81, title="First latest"),
            _job(source_job_id="91", score=99, title="Higher"),
        ),
    )
    config = AutomationConfig(score_threshold=70, max_score=1)

    envelope = build_planned_operations(
        scenario,
        config,
        clock_iso="2025-01-01T00:00:00+00:00?secret=SECRET_QUERY",
    ).to_envelope()
    rendered = str(envelope)

    assert envelope["planned_imports"] == 2
    assert envelope["planned_scores"] == 1
    assert envelope["best"]["sourceJobId"] == "78"
    assert envelope["best"]["title"] == "First"
    assert "RESUME_SENTINEL" not in rendered
    assert "/DATA/" not in rendered
    assert "SECRET_QUERY" not in rendered
    assert envelope["clock"] == "2025-01-01T00:00:00+00:00"


def test_simulation_cap_includes_unscored_discovery_identity() -> None:
    SimulationScenario = require_optional("SimulationScenario")
    build_planned_operations = require_optional("build_planned_operations")
    scenario = SimulationScenario(
        name="unscored-first",
        jobs=(
            _job(source_job_id="78"),
            _job(source_job_id="91", score=99),
        ),
    )

    envelope = build_planned_operations(
        scenario,
        AutomationConfig(score_threshold=70, max_score=1),
        clock_iso="fixed",
    ).to_envelope()

    assert envelope["planned_imports"] == 2
    assert envelope["planned_scores"] == 0
    assert envelope["best"] is None


def _normalised_with_url(url: str) -> NormalizedJob:
    return NormalizedJob(
        source="adzuna",
        source_job_id="1",
        title="Engineer",
        company="Acme",
        description="Build things.",
        source_url=url,
        location="Remote",
        metadata={"score_note": _score_note(75)},
    )
