#!/usr/bin/env python3
"""Operator entry point for automated JobTrail search and scoring.

This intentionally performs real scorer note writes when invoked; it never applies jobs.
"""
import argparse
import sys

from jobtrail_ai_scorer.automation import AutomationConfig, JobSearchAutomation, JobTrailHTTPClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="override SCORER_CONFIG_PATH")
    parser.add_argument("--notify", action="store_true", help="enable configured WhatsApp helper")
    args = parser.parse_args()
    config = AutomationConfig.from_env()
    # This helper accepts the JSON summary on stdin; notifications remain opt-in.
    if not config.whatsapp_command:
        config = AutomationConfig(**{**config.__dict__, "whatsapp_command": "./notify-whatsapp-via-hermes.local.sh"})
    if args.config or args.notify:
        config = AutomationConfig(**{**config.__dict__, "scorer_config_path": args.config or config.scorer_config_path,
                                     "notify_enabled": args.notify or config.notify_enabled})
    gateway = JobTrailHTTPClient(config.base_url)
    try:
        result = JobSearchAutomation(gateway).run(config=config)
    finally:
        gateway.close()
    print({"searched": result.searched, "imported": result.imported,
           "scored": result.scored, "selected": result.selected is not None,
           "failures": list(result.failures)})
    return 1 if result.failures else 0


if __name__ == "__main__":
    sys.exit(main())
