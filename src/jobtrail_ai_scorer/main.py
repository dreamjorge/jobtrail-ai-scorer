"""Command-line entry point for scoring JobTrail jobs."""
import json
import logging
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

import typer

from .config import AppConfig, load_config
from .jobtrail import JobTrailClient
from .automation import AutomationConfig, JobSearchAutomation, JobTrailHTTPClient
from .jobright_manual import normalize_jobright_manual
from .metrics import compute_metrics
from .feedback import feedback_note_body, validate_labels
from .lifecycle import STATES, current_state, lifecycle_history, make_lifecycle_event, parse_lifecycle_note, provenance_from_job
from .run_journal import DEFAULT_RUN_JOURNAL_PATH, iter_runs
from .seen_cache import DEFAULT_SEEN_CACHE_PATH, SeenCache
from ._atomic_json import read_json
from .prompt_budget import LoadStatus, PromptBudget
from .prompt_tokens import estimate_sections, optional_budget_from_env
from .scoring import (
    CURRENT_MARKER,
    PROMPT_INSTRUCTIONS,
    PROMPT_SCHEMA,
    ScoreRunResult,
    score_jobs,
    serialize_job,
)
from .providers import (
    HermesProvider, HermesProviderConfig,
    OpenAICompatibleProvider, OpenAICompatibleConfig,
)

app = typer.Typer(help="Score JobTrail jobs with a configured AI provider.")
logger = logging.getLogger(__name__)


@app.callback()
def _root() -> None:
    """JobTrail AI scorer commands."""

ClientFactory = Callable[[str], object]
ProviderFactory = Callable[[AppConfig], object]


def _make_provider(config: AppConfig) -> object:
    """Build a provider, accepting optional provider-specific config keys."""
    data = config.model_dump()
    if config.provider == "hermes":
        return HermesProvider(HermesProviderConfig(
            executable=data.get("hermes_executable", "hermes"),
            profile=data.get("hermes_profile", "default"),
            timeout_seconds=data.get("provider_timeout_seconds", 60.0),
        ))
    return OpenAICompatibleProvider(OpenAICompatibleConfig(
        endpoint=data.get("openai_endpoint", "http://localhost:8000/v1/chat/completions"),
        model=data.get("openai_model", "default"),
        api_key_env=data.get("openai_api_key_env", "OPENAI_API_KEY"),
        timeout_seconds=data.get("provider_timeout_seconds", 60.0),
    ))


def run_score(*, config_path: Path, limit: int | None = None, job_id: str | None = None,
              dry_run: bool = False, force: bool = False, marker: str | None = None,
              provider_name: str | None = None, base_url: str | None = None,
              client_factory: ClientFactory | None = None,
              provider_factory: ProviderFactory | None = None,
              output_json: bool = False,
              job_payload: dict | None = None) -> ScoreRunResult:
    if output_json and not dry_run:
        raise ValueError("output_json requires dry_run")
    config = load_config(config_path)
    overrides = {}
    if provider_name is not None:
        overrides["provider"] = provider_name
    if base_url is not None:
        # Lets a caller that already resolved the live backend (e.g. the
        # discovery-aware automation launcher) override the static YAML
        # value, instead of every scored job silently targeting whatever
        # jobtrail_base_url happens to be committed in the config file.
        overrides["jobtrail_base_url"] = base_url
    if overrides:
        config = type(config).model_validate({**config.model_dump(), **overrides})
    prompt_budget = PromptBudget.from_env()
    profile, profile_status = prompt_budget.load_optional_text(
        config.candidate_profile_path, prompt_budget.profile_budget_chars
    )
    _warn_if_unavailable("profile", profile_status)
    cv = ""
    cv_status = None
    if config.candidate_cv_path is not None:
        cv, cv_status = prompt_budget.load_optional_text(
            config.candidate_cv_path, prompt_budget.cv_budget_chars
        )
        _warn_if_unavailable("CV", cv_status)
        if cv_status in ("loaded", "truncated"):
            profile += f"\n\nCandidate CV:\n{cv}"
    profile_only = _split_profile(profile, cv)
    client = (client_factory or (lambda url: JobTrailClient(url)))(str(config.jobtrail_base_url))
    provider = (provider_factory or _make_provider)(config)
    try:
        result = score_jobs(client, provider, profile, job_id=job_id, limit=limit,
                            force=force, dry_run=dry_run, marker=marker or config.marker,
                            emit_status=False, output_json=output_json,
                            job_payload=job_payload)
        # Must run before the client closes below: with --job-id this samples
        # the live job via client.get_job, and a closed client makes that
        # call fail silently, falling back to a tiny stub payload that
        # undercounts the estimate and can hide a genuinely oversized prompt.
        if not output_json:
            _emit_prompt_tokens_estimate(client, profile_only=profile_only, cv=cv,
                                          job_id=job_id, job_payload=job_payload)
    finally:
        close = getattr(client, "close", None)
        if close:
            close()
    if output_json:
        typer.echo(result.json_text)
        return result
    for outcome in result.outcomes:
        if outcome.status == "skipped":
            typer.echo(f"SKIP {outcome.job_id}: {outcome.reason}")
        elif outcome.status == "saved":
            typer.echo(f"SAVED {outcome.job_id}")
        elif outcome.status == "dry_run":
            typer.echo(f"SCORE {outcome.job_id}: dry-run")
        elif outcome.status == "failed":
            typer.echo(f"FAIL {outcome.job_id}: {outcome.reason}")
    typer.echo(f"processed={result.processed} skipped={result.skipped} failed={result.failed}")
    return result


def _split_profile(profile: str, cv: str) -> str:
    """Recover the original (CV-free) profile text from the concatenated prompt.

    Returns ``profile`` unchanged when no CV was appended, so callers that
    never configured ``candidate_cv_path`` see exactly what they loaded.
    """

    if not cv:
        return profile
    sentinel = f"\n\nCandidate CV:\n{cv}"
    if profile.endswith(sentinel):
        return profile[: -len(sentinel)]
    return profile


def _emit_prompt_tokens_estimate(client: object, *, profile_only: str, cv: str,
                                 job_id: str | None,
                                 job_payload: dict | None = None) -> None:
    """Print one ``prompt_tokens_estimate=`` line plus an optional budget warning.

    The breakdown uses the raw profile / cv values (before the inline CV
    concatenation that is sent to the provider) so each section is tracked
    separately. The job section reuses ``scoring.serialize_job`` so its size
    matches the JSON the provider will see.
    """

    job_payload = _sample_job_payload(client, job_id, job_payload)
    breakdown = estimate_sections(
        profile=profile_only,
        cv=cv,
        job=job_payload,
        schema=PROMPT_SCHEMA,
        instructions=PROMPT_INSTRUCTIONS,
    )
    typer.echo(f"prompt_tokens_estimate={json.dumps(breakdown, separators=(',', ':'), sort_keys=True)}")
    budget = optional_budget_from_env()
    if budget is not None and breakdown["total"] > budget:
        warning = json.dumps({"budget": budget, "total": breakdown["total"]},
                             separators=(",", ":"), sort_keys=True)
        typer.echo(f"prompt_token_budget={warning}")


def _sample_job_payload(client: object, job_id: str | None,
                        job_payload: dict | None = None) -> str:
    """Pick a deterministic sample job for the size estimate.

    When ``job_id`` is provided we read the same job the provider will see so
    the estimate reflects the live payload. Otherwise we fall back to an
    empty dict so the per-run line stays bounded when no job was scored.
    """

    if job_payload is not None:
        return serialize_job(job_payload)
    if job_id is None:
        return serialize_job({})
    get_job = getattr(client, "get_job", None)
    if not callable(get_job):
        return serialize_job({"id": job_id})
    try:
        job = get_job(job_id)
    except Exception:  # noqa: BLE001 - estimate must never fail the run.
        return serialize_job({"id": job_id})
    if not isinstance(job, dict):
        return serialize_job({"id": job_id})
    return serialize_job(job)


def _warn_if_unavailable(section: str, status: LoadStatus) -> None:
    if status in ("missing", "unreadable"):
        logger.warning(
            "Unable to load candidate %s (%s); continuing without it",
            section,
            status,
        )


@app.command()
def metrics(
    period: str = typer.Option("today", "--period"),
    json_output: bool = typer.Option(False, "--json"),
    journal_path: Path | None = typer.Option(None, "--journal-path"),
    seen_cache_path: Path | None = typer.Option(None, "--seen-cache-path"),
    base_url: str = typer.Option("http://127.0.0.1:8000", "--base-url"),
) -> None:
    """Render privacy-safe funnel metrics without mutating JobTrail."""
    if period not in {"today", "7d", "30d"}:
        raise typer.BadParameter("must be one of: today, 7d, 30d", param_hint="--period")
    journal = journal_path or Path(os.environ.get("JOBTRAIL_RUN_JOURNAL_PATH", "") or DEFAULT_RUN_JOURNAL_PATH)
    cache = seen_cache_path or Path(os.environ.get("JOBTRAIL_SEEN_CACHE_PATH", "") or DEFAULT_SEEN_CACHE_PATH)
    now = time.time()
    if period == "today":
        from datetime import datetime, timezone
        start = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        since, until = start, start + 86400
    else:
        since, until = now - (7 if period == "7d" else 30) * 86400, now
    runs = list(iter_runs(journal, since=since, until=until))
    seen_entries = _read_seen_entries(cache)
    jobs: list[dict] = []
    missing: list[str] = []
    client = None
    try:
        client = JobTrailClient(base_url)
        list_jobs = getattr(client, "list_jobs", None)
        if callable(list_jobs):
            jobs = [job for job in list_jobs() if isinstance(job, dict)]
        else:
            missing.append("scored_jobs")
    except Exception:
        missing.append("scored_jobs")
    finally:
        if client is not None and callable(getattr(client, "close", None)):
            client.close()
    view = compute_metrics(runs=runs, seen_entries=seen_entries, scored_jobs=jobs, period=period, now=now)
    if missing:
        view = replace(view, missing_data=tuple(sorted(set(view.missing_data) | set(missing))))
    if json_output:
        typer.echo(json.dumps(view.to_dict(), sort_keys=True, separators=(",", ":")))
    else:
        typer.echo(f"period={view.period} searched={view.searched} imported={view.imported} scored={view.scored} notifications={view.notifications} missing_data={','.join(view.missing_data) or 'none'}")


def _read_seen_entries(path: Path) -> list[dict]:
    payload = read_json(path, default={})
    entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
    result = []
    for key, value in entries.items() if isinstance(entries, dict) else ():
        if not isinstance(key, str) or not isinstance(value, dict) or "\x1f" not in key:
            continue
        source, source_job_id = key.split("\x1f", 1)
        result.append({"source": source, "sourceJobId": source_job_id, "first_seen": value.get("first_seen")})
    return result


def _manual_input(*, url: str | None, title: str | None, company: str | None,
                  location: str | None, description: str | None,
                  input_text: str | None) -> object:
    """Resolve and validate manual fields before constructing any client."""
    values: dict[str, object] = {}
    if input_text is not None:
        try:
            parsed = json.loads(input_text)
        except json.JSONDecodeError as exc:
            raise ValueError("input must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("input must be a JSON object")
        values.update(parsed)
    for key, value in (("url", url), ("title", title), ("company", company),
                       ("location", location), ("description", description)):
        if value is not None:
            values[key] = value
    required_missing = False
    for key, label in (("url", "Jobright URL"), ("title", "Title"),
                       ("company", "Company"), ("location", "Location")):
        if key not in values:
            required_missing = True
            values[key] = typer.prompt(label)
    if "description" not in values:
        values["description"] = (
            typer.prompt("Description (optional)", default="")
            if required_missing else ""
        )
    return normalize_jobright_manual(values)


def _safe_duplicate_record(record: object) -> dict[str, str]:
    """Return bounded, non-sensitive details for a duplicate response."""
    if not isinstance(record, dict):
        return {}
    values = {
        "id": record.get("id"),
        "source": record.get("source"),
        "sourceJobId": record.get("sourceJobId"),
        "title": record.get("title") or record.get("position"),
        "company": record.get("company"),
        "location": record.get("location"),
        "url": record.get("url") or record.get("sourceUrl"),
    }
    return {
        key: str(value)[:512]
        for key, value in values.items()
        if value is not None and not isinstance(value, (dict, list, tuple, set))
    }


def run_import_jobright(*, config_path: Path, url: str | None = None,
                        title: str | None = None, company: str | None = None,
                        location: str | None = None, description: str | None = None,
                        input_text: str | None = None, confirm: bool = False,
                        dry_run: bool = False, base_url: str | None = None,
                        client_factory: ClientFactory | None = None,
                        cache_factory: Callable[[Path], object] | None = None) -> dict[str, object]:
    """Preview a manual Jobright record and optionally import it once."""
    if input_text == "-":
        input_text = sys.stdin.read()
    job = _manual_input(url=url, title=title, company=company, location=location,
                        description=description, input_text=input_text)
    preview = {"source": job.source, "sourceJobId": job.source_job_id,
               "url": job.source_url, "title": job.title,
               "company": job.company, "location": job.location,
               "scoring_will_run": bool(confirm and not dry_run)}
    typer.echo(json.dumps(preview, sort_keys=True, separators=(",", ":")))
    if not confirm or dry_run:
        typer.echo("dry-run: no import" if dry_run else "preview: use --confirm to import")
        return preview
    config = load_config(config_path)
    client = (client_factory or (lambda base: JobTrailHTTPClient(base)))(base_url or str(config.jobtrail_base_url))
    try:
        list_jobs = getattr(client, "list_jobs", None)
        existing = list_jobs() if callable(list_jobs) else []
        duplicate = next(
            (
                item for item in existing
                if isinstance(item, dict)
                and item.get("source") == job.source
                and str(item.get("sourceJobId")) == job.source_job_id
            ),
            None,
        )
        if duplicate is not None:
            typer.echo("duplicate: import skipped")
            return {
                **preview,
                "duplicate": True,
                "existing": _safe_duplicate_record(duplicate),
            }
        result = client.import_job(job.to_import_payload())
        cache_path = Path(os.environ.get("JOBTRAIL_SEEN_CACHE_PATH", "") or DEFAULT_SEEN_CACHE_PATH)
        cache = (cache_factory or (lambda path: SeenCache(path)))(cache_path)
        cache.mark_seen(job.source, job.source_job_id)
        job_id = result.get("id") if isinstance(result, dict) else result
        typer.echo(f"IMPORTED {job_id}")
        scoring_config = AutomationConfig.from_env({
            **os.environ,
            "SCORER_CONFIG_PATH": str(config_path),
            "JOBTRAIL_AUTOMATION_DRY_RUN": "0",
            "JOBTRAIL_BASE_URL": base_url or str(config.jobtrail_base_url),
        })
        scoring = JobSearchAutomation(client).score_imported_job(
            str(job_id), job.scorer_input, config=scoring_config
        )
        response = {**preview, "id": job_id, **scoring}
        if not scoring.get("scored"):
            typer.echo("scoring: suppressed")
        elif scoring.get("notification_sent"):
            typer.echo("notification: sent")
        return response
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


@app.command()
def import_jobright(
    url: str | None = typer.Option(None, "--url"),
    title: str | None = typer.Option(None, "--title"),
    company: str | None = typer.Option(None, "--company"),
    location: str | None = typer.Option(None, "--location"),
    description: str | None = typer.Option(None, "--description"),
    input_text: str | None = typer.Option(None, "--input", help="JSON object or '-' for stdin."),
    confirm: bool = typer.Option(False, "--confirm"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    base_url: str | None = typer.Option(None, "--base-url"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Preview and confirmation-gated import of a manually copied Jobright posting."""
    try:
        run_import_jobright(config_path=config, url=url, title=title, company=company,
                            location=location, description=description, input_text=input_text,
                            confirm=confirm, dry_run=dry_run, base_url=base_url)
    except Exception as error:
        # Never expose backend response bodies or imported descriptions.
        raise typer.BadParameter(
            f"import failed ({type(error).__name__})"
        ) from error


def run_feedback(*, config_path: Path, job_id: str, labels: list[str], comment: str | None = None,
                 client_factory: ClientFactory | None = None) -> dict:
    """Fetch a scored job, then append one validated feedback note."""
    config = load_config(config_path)
    client = (client_factory or (lambda url: JobTrailClient(url)))(str(config.jobtrail_base_url))
    try:
        job = client.get_job(job_id)
        if not _has_valid_score_marker(job, marker=config.marker):
            raise ValueError("job has no valid score marker")
        body = feedback_note_body(labels, comment=comment, job=job)
        client.add_note(job_id, body)
        return {"job_id": job_id, "labels": validate_labels(labels)}
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _has_valid_score_marker(job: object, *, marker: str = CURRENT_MARKER) -> bool:
    if not isinstance(job, dict) or not isinstance(job.get("notes"), list):
        return False
    from .scoring import LEGACY_MARKER, parse_score_note
    markers = tuple(dict.fromkeys((marker, CURRENT_MARKER, LEGACY_MARKER)))
    for note in reversed(job["notes"]):
        body = note.get("body") if isinstance(note, dict) else None
        for score_marker in markers:
            payload = parse_score_note(body, marker=score_marker) if isinstance(body, str) else None
            score = payload.get("score") if payload else None
            if isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 100:
                return True
    return False


@app.command()
def feedback(
    job_id: str = typer.Option(..., "--job-id"),
    label: list[str] = typer.Option(..., "--label", help="Feedback label(s), repeat or comma-separate."),
    comment: str | None = typer.Option(None, "--comment"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Attach structured feedback to a previously scored job."""
    labels = [part.strip() for item in label for part in item.split(",") if part.strip()]
    try:
        run_feedback(config_path=config, job_id=job_id, labels=labels, comment=comment)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"FEEDBACK {job_id}")


@app.command()
@app.command("seguimiento")
def track(
    job_id: str = typer.Option(..., "--job-id"),
    to: str | None = typer.Option(None, "--to"),
    note: str | None = typer.Option(None, "--note"),
    source: str | None = typer.Option(None, "--source"),
    confirm: bool = typer.Option(False, "--confirm"),
    json_output: bool = typer.Option(False, "--json"),
    base_url: str | None = typer.Option(None, "--base-url"),
config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """Show lifecycle status/history, optionally appending one event."""
    if to is not None and to not in STATES:
        raise typer.BadParameter("must be a valid lifecycle state", param_hint="--to")
    if confirm and to is None:
        raise typer.BadParameter("--confirm requires --to applied")
    app_config = load_config(config)
    resolved_base_url = base_url or str(app_config.jobtrail_base_url)
    client = JobTrailClient(resolved_base_url)
    try:
        job = client.get_job(job_id)
        previous = current_state(job)
        if to is not None:
            if confirm and to != "applied":
                raise typer.BadParameter("--confirm is only valid with --to applied")
            provenance = provenance_from_job(job)
            event_source = source or provenance.get("source", "cli")
            event_source_job_id = provenance.get("source_job_id", job_id)
            if source is not None:
                provenance = {**provenance, "source": event_source}
            body = make_lifecycle_event(
                previous, to, source=event_source, source_job_id=event_source_job_id, note=note,
                confirm=True if confirm else None, source_url=provenance.get("source_url"),
                provenance=provenance,
            )
            client.add_note(job_id, body)
            history = lifecycle_history(job) + [parse_lifecycle_note(body)]
            status = to
        else:
            history = lifecycle_history(job)
            status = previous
        safe_history = [
            {key: event[key] for key in ("timestamp", "previous_state", "new_state", "source", "source_job_id", "confirmed") if key in event}
            for event in history if event is not None
        ]
        payload = {"job_id": job_id, "current_state": status, "history": safe_history}
        if json_output:
            typer.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        else:
            typer.echo(f"status={status}")
            for event in safe_history:
                typer.echo(f"{event['timestamp']} {event['previous_state']} -> {event['new_state']} ({event['source']})")
    finally:
        client.close()


@app.command()
def score(limit: int | None = typer.Option(None), job_id: str | None = typer.Option(None),
          dry_run: bool = typer.Option(False, "--dry-run"), force: bool = typer.Option(False),
          json_output: bool = typer.Option(False, "--json"),
          job_json: str | None = typer.Option(None, "--job-json", hidden=True),
          marker: str | None = typer.Option(None), provider: str | None = typer.Option(None),
          base_url: str | None = typer.Option(
              None, "--base-url",
              help="Override the config's jobtrail_base_url (e.g. with a "
                   "discovery-resolved URL) instead of using the YAML value.",
          ),
          config: Path = typer.Option(Path("config.yaml"), "--config")) -> None:
    """Score eligible jobs and save validated score notes."""
    job_payload = None
    if job_json == "-":
        job_json = sys.stdin.read()
    if job_json is not None:
        try:
            parsed = json.loads(job_json)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter("must be valid JSON", param_hint="--job-json") from exc
        if not isinstance(parsed, dict):
            raise typer.BadParameter("must be a JSON object", param_hint="--job-json")
        job_payload = parsed
    result = run_score(config_path=config, limit=limit, job_id=job_id, dry_run=dry_run,
                       force=force, marker=marker, provider_name=provider,
                       base_url=base_url, output_json=json_output,
                       job_payload=job_payload)
    if result.failed:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
