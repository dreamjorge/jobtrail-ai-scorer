"""Command-line entry point for scoring JobTrail jobs."""
from pathlib import Path
from typing import Callable

import typer

from .config import AppConfig, load_config
from .jobtrail import JobTrailClient
from .scoring import ScoreRunResult, score_jobs
from .providers import (
    HermesProvider, HermesProviderConfig,
    OpenAICompatibleProvider, OpenAICompatibleConfig,
)

app = typer.Typer(help="Score JobTrail jobs with a configured AI provider.")

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
    profile = config.candidate_profile_path.read_text()
    client = (client_factory or (lambda url: JobTrailClient(url)))(str(config.jobtrail_base_url))
    provider = (provider_factory or _make_provider)(config)
    try:
        result = score_jobs(client, provider, profile, job_id=job_id, limit=limit,
                            force=force, dry_run=dry_run, marker=marker or config.marker)
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
