#!/usr/bin/env python3
"""Operator entry point for automated JobTrail search and scoring.

This intentionally performs real scorer note writes when invoked; it never applies jobs.

The backend URL is resolved on every run by
``jobtrail_ai_scorer.discover.resolve_backend_url`` with explicit precedence:

  1. ``http://127.0.0.1:8000`` (the published host port), if reachable.
  2. ``http://<docker-ip>:8000`` from ``docker inspect <container>``, if reachable.
  3. Fail closed: clear error and non-zero exit.

Discovery is on by default. ``--container NAME`` overrides the Docker container
(default ``jobtrail-backend-1``). ``--no-discover`` falls back to the static
``JOBTRAIL_BASE_URL`` from the environment. ``--base-url URL`` bypasses
discovery and uses the URL verbatim (highest priority).
"""

import argparse
import json
import os
import sys

from jobtrail_ai_scorer.automation import (
    AutomationConfig,
    BackendDiscoveryError,
    DISCOVER_DEFAULT_CONTAINER,
    JobSearchAutomation,
    JobTrailHTTPClient,
    SimulationScenario,
    build_planned_operations,
    merge_resolved_base_url,
    resolve_automation_base_url,
)
from jobtrail_ai_scorer.sources import NormalizedJob
from jobtrail_ai_scorer.seen_cache import DEFAULT_SEEN_CACHE_PATH, SeenCache


_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _parse_truthy(value: str | None) -> bool | None:
    """Return a tristate bool for an env-style value.

    ``True``/``False`` reflect the recognised truthy/falsy tokens; ``None``
    signals an unparseable value so the caller can fail closed without
    silently accepting the input.
    """

    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    return None


def _resolve_automation_dry_run(args: argparse.Namespace) -> bool | None:
    """Return whether the automation dry-run is requested, or ``None`` on bad input.

    Precedence: ``--automation-dry-run`` CLI flag >
    ``JOBTRAIL_AUTOMATION_DRY_RUN`` environment variable. ``False`` means
    "explicitly disabled", ``True`` means "enabled", ``None`` means
    "unparseable input; fail closed".
    """

    if getattr(args, "automation_dry_run", False):
        return True
    raw = os.environ.get("JOBTRAIL_AUTOMATION_DRY_RUN")
    if raw is None or raw.strip() == "":
        return False
    return _parse_truthy(raw)


def _build_default_scenario() -> SimulationScenario:
    """Return a deterministic, hermetic scenario for the offline dry-run.

    The launcher never reads the operator profile, CV, environment
    variables beyond the ones listed in :mod:`scripts.automated-job-search`,
    or runtime state. The scenario data is hard-coded so every invocation
    produces the same envelope, which the launcher emits as JSON.
    """

    from jobtrail_ai_scorer.scoring import CURRENT_MARKER

    def _note(score: int) -> str:
        body = json.dumps(
            {
                "score": score,
                "recommendation": "APPLY",
                "strengths": [],
                "gaps": [],
                "needs_confirmation": [],
                "hard_requirements_missing": [],
                "career_value": "Medium",
                "reasoning": "dry-run scenario",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"{CURRENT_MARKER}\n{body}"

    def _job(source: str, source_job_id: str, score: int, title: str) -> NormalizedJob:
        return NormalizedJob(
            source=source,
            source_job_id=source_job_id,
            title=title,
            company="DryRunCo",
            description="Synthetic scenario job for offline dry-run.",
            source_url=f"https://example.test/dry-run/{source_job_id}",
            location="Remote",
            metadata={"score_note": _note(score)},
        )

    return SimulationScenario(
        name="built-in",
        jobs=(
            _job("synthetic", "1", 78, "Backend Engineer"),
            _job("synthetic", "2", 91, "Senior Backend Engineer"),
            _job("synthetic", "3", 55, "Junior Engineer"),
        ),
    )


def _run_automation_dry_run(config: AutomationConfig) -> int:
    """Run the offline simulation and emit the bounded envelope on stdout.

    The dry-run path never instantiates ``JobTrailHTTPClient``,
    ``SeenCache`` or the production ``JobSearchAutomation``. The
    :func:`build_planned_operations` helper produces a deterministic
    envelope which the launcher serialises as JSON.
    """

    scenario = _build_default_scenario()
    planned = build_planned_operations(scenario, config)
    envelope = planned.to_envelope()
    summary = {
        "mode": "automation-dry-run",
        "searched": envelope["searched"],
        "planned_imports": envelope["planned_imports"],
        "planned_scores": envelope["planned_scores"],
        "would_notify": envelope["would_notify"],
        "selected": envelope["best"],
        "envelope": envelope,
        "failures": [],
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


def _resolve_reset_request(args: argparse.Namespace) -> bool:
    """Return True if the operator asked to reset the seen cache.

    Honors both the explicit CLI flag and the JOBTRAIL_RESET_SEEN_CACHE env var.
    """

    if getattr(args, "reset_seen_cache", False):
        return True
    return os.environ.get("JOBTRAIL_RESET_SEEN_CACHE", "").strip().lower() in _TRUTHY


def _build_seen_cache(args: argparse.Namespace) -> SeenCache | None:
    """Construct the seen cache honoring reset/bypass requests.

    The cache is always constructed from the operator-controlled path so the
    cache state is observable on disk. The launcher swallows any construction
    failure so a missing runtime directory never aborts the run; the run
    proceeds without deduplication and a warning is emitted on stderr.
    """

    override = os.environ.get("JOBTRAIL_SEEN_CACHE_PATH", "").strip()
    cache_path = override or DEFAULT_SEEN_CACHE_PATH
    try:
        cache = SeenCache(cache_path)
    except Exception as exc:  # pragma: no cover - defensive guard
        print(f"seen cache unavailable at {cache_path}: {exc}", file=sys.stderr)
        return None
    if _resolve_reset_request(args):
        try:
            cache.reset()
            print(f"seen cache reset at {cache.path}", file=sys.stderr)
        except Exception as exc:
            print(f"seen cache reset failed at {cache.path}: {exc}", file=sys.stderr)
    return cache


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="override SCORER_CONFIG_PATH")
    parser.add_argument(
        "--notify", action="store_true", help="enable configured WhatsApp helper"
    )
    parser.add_argument(
        "--container",
        default=None,
        help=(
            "Docker container name to inspect as fallback when the published port "
            "is unreachable. Defaults to JOBTRAIL_DISCOVER_CONTAINER if set, else "
            f"{DISCOVER_DEFAULT_CONTAINER!r}. Discovery is on by default; pass "
            "--no-discover to use JOBTRAIL_BASE_URL instead."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=(
            "Bypass discovery and use this URL verbatim (highest priority; overrides "
            "JOBTRAIL_BASE_URL and --container)"
        ),
    )
    parser.add_argument(
        "--no-discover",
        action="store_true",
        help="Use JOBTRAIL_BASE_URL without probing the published port or Docker container",
    )
    parser.add_argument(
        "--reset-seen-cache",
        action="store_true",
        help=(
            "Clear the seen cache before this run so every offer is "
            "re-imported (also set by JOBTRAIL_RESET_SEEN_CACHE=1)."
        ),
    )
    parser.add_argument(
        "--automation-dry-run",
        action="store_true",
        help=(
            "Run the offline automation dry-run instead of the production"
            " pipeline. Uses an in-process SimulationScenario; never touches"
            " the gateway, the scorer subprocess, the WhatsApp helper or the"
            " runtime seen cache. Also enabled by JOBTRAIL_AUTOMATION_DRY_RUN."
        ),
    )
    args = parser.parse_args()

    # Offline automation dry-run short-circuit (issue #36). The flag/env
    # MUST be evaluated before any gateway, cache or production-state
    # construction so the forbidden boundary is respected. Fail closed
    # when the env var carries an unrecognised value.
    dry_run_choice = _resolve_automation_dry_run(args)
    if dry_run_choice is None:
        print(
            "JOBTRAIL_AUTOMATION_DRY_RUN must be one of 1/true/yes/on or 0/false/no/off",
            file=sys.stderr,
        )
        return 2
    if dry_run_choice:
        config = AutomationConfig.from_env()
        print(
            "automation-dry-run: offline simulation enabled; "
            "no gateway, scorer or notifier will be invoked",
            file=sys.stderr,
        )
        return _run_automation_dry_run(config)

    config = AutomationConfig.from_env()
    if not config.whatsapp_command:
        overrides = {
            **config.__dict__,
            "whatsapp_command": "./notify-whatsapp-via-hermes.local.sh",
        }
        config = AutomationConfig(**overrides)
    if args.config or args.notify:
        config = AutomationConfig(
            **{
                **config.__dict__,
                "scorer_config_path": args.config or config.scorer_config_path,
                "notify_enabled": args.notify or config.notify_enabled,
            }
        )

    # Resolve the backend URL.
    # Precedence: --base-url > --no-discover (use JOBTRAIL_BASE_URL) > discovery.
    if args.base_url:
        base_url = args.base_url
        source = "cli"
    elif args.no_discover:
        base_url = config.base_url
        source = "static"
    else:
        # Only the explicit CLI flag should override JOBTRAIL_DISCOVER_CONTAINER;
        # argparse's own default must never clobber an env-configured value
        # that the operator relied on when omitting --container.
        container = args.container or config.discover_container or DISCOVER_DEFAULT_CONTAINER
        discovery_config = AutomationConfig(
            **{**config.__dict__, "discover_container": container}
        )
        try:
            base_url, source = resolve_automation_base_url(
                discovery_config, container_name=container
            )
        except BackendDiscoveryError as error:
            print(f"backend discovery failed: {error}", file=sys.stderr)
            return 2
    print(f"backend: {base_url} (source={source})", file=sys.stderr)
    config = merge_resolved_base_url(config, base_url)

    gateway = JobTrailHTTPClient(config.base_url)
    seen_cache = _build_seen_cache(args)
    try:
        result = JobSearchAutomation(
            gateway, seen_cache=seen_cache, ats_boards=config.ats_boards
        ).run(config=config)
    finally:
        gateway.close()
    print(
        {
            "searched": result.searched,
            "imported": result.imported,
            "scored": result.scored,
            "selected": result.selected is not None,
            "failures": list(result.failures),
            "profile_counts": result.profile_counts,
        }
    )
    return 1 if result.failures else 0


if __name__ == "__main__":
    sys.exit(main())
