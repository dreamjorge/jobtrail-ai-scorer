"""Command-line entry point for scoring JobTrail jobs."""
import logging
from pathlib import Path
from typing import Callable

import typer

from .config import AppConfig, load_config
from .jobtrail import JobTrailClient
from .prompt_budget import LoadStatus, PromptBudget
from .scoring import ScoreRunResult, score_jobs
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
              provider_name: str | None = None, client_factory: ClientFactory | None = None,
              provider_factory: ProviderFactory | None = None) -> ScoreRunResult:
    config = load_config(config_path)
    if provider_name:
        config = config.model_copy(update={"provider": provider_name})
    prompt_budget = PromptBudget.from_env()
    profile, profile_status = prompt_budget.load_optional_text(
        config.candidate_profile_path, prompt_budget.profile_budget_chars
    )
    _warn_if_unavailable("profile", profile_status)
    if config.candidate_cv_path is not None:
        cv, cv_status = prompt_budget.load_optional_text(
            config.candidate_cv_path, prompt_budget.cv_budget_chars
        )
        _warn_if_unavailable("CV", cv_status)
        if cv_status in ("loaded", "truncated"):
            profile += f"\n\nCandidate CV:\n{cv}"
    client = (client_factory or (lambda url: JobTrailClient(url)))(str(config.jobtrail_base_url))
    provider = (provider_factory or _make_provider)(config)
    try:
        result = score_jobs(client, provider, profile, job_id=job_id, limit=limit,
                            force=force, dry_run=dry_run, marker=marker or config.marker,
                            emit_status=False)
    finally:
        close = getattr(client, "close", None)
        if close:
            close()
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


def _warn_if_unavailable(section: str, status: LoadStatus) -> None:
    if status in ("missing", "unreadable"):
        logger.warning(
            "Unable to load candidate %s (%s); continuing without it",
            section,
            status,
        )


@app.command()
def score(limit: int | None = typer.Option(None), job_id: str | None = typer.Option(None),
          dry_run: bool = typer.Option(False, "--dry-run"), force: bool = typer.Option(False),
          marker: str | None = typer.Option(None), provider: str | None = typer.Option(None),
          config: Path = typer.Option(Path("config.yaml"), "--config")) -> None:
    """Score eligible jobs and save validated score notes."""
    result = run_score(config_path=config, limit=limit, job_id=job_id, dry_run=dry_run,
                       force=force, marker=marker, provider_name=provider)
    if result.failed:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
