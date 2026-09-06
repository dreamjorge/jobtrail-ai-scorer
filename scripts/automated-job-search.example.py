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
import sys

from jobtrail_ai_scorer.automation import (
    AutomationConfig,
    BackendDiscoveryError,
    DISCOVER_DEFAULT_CONTAINER,
    JobSearchAutomation,
    JobTrailHTTPClient,
    merge_resolved_base_url,
    resolve_automation_base_url,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="override SCORER_CONFIG_PATH")
    parser.add_argument(
        "--notify", action="store_true", help="enable configured WhatsApp helper"
    )
    parser.add_argument(
        "--container",
        default=DISCOVER_DEFAULT_CONTAINER,
        help=(
            "Docker container name to inspect as fallback when the published port "
            "is unreachable (default: %(default)s). Discovery is on by default; "
            "pass --no-discover to use JOBTRAIL_BASE_URL instead."
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
    args = parser.parse_args()
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
        discovery_config = AutomationConfig(
            **{**config.__dict__, "discover_container": args.container}
        )
        try:
            base_url, source = resolve_automation_base_url(
                discovery_config, container_name=args.container
            )
        except BackendDiscoveryError as error:
            print(f"backend discovery failed: {error}", file=sys.stderr)
            return 2
    print(f"backend: {base_url} (source={source})", file=sys.stderr)
    config = merge_resolved_base_url(config, base_url)

    gateway = JobTrailHTTPClient(config.base_url)
    try:
        result = JobSearchAutomation(gateway).run(config=config)
    finally:
        gateway.close()
    print(
        {
            "searched": result.searched,
            "imported": result.imported,
            "scored": result.scored,
            "selected": result.selected is not None,
            "failures": list(result.failures),
        }
    )
    return 1 if result.failures else 0


if __name__ == "__main__":
    sys.exit(main())
