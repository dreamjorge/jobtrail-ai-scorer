# JobTrail AI Scorer design

## Goal

Provide a public, provider-agnostic Python CLI that scores eligible JobTrail jobs through the existing HTTP API and stores validated recommendations as notes.

## Scope

The first release provides manual CLI execution, a Hermes provider, an OpenAI-compatible provider interface, structured score validation, dry-run support, and marker-based deduplication. Scheduling, database changes, and mandatory LinkedIn description fetching are out of scope.

## Architecture

The package is split into four layers:

- `jobtrail.py` owns the JobTrail API client for listing jobs, retrieving complete jobs, and adding notes.
- `models.py` defines Pydantic models, especially the strict `ScoreResult` schema.
- `providers/` declares a provider protocol and contains Hermes plus an OpenAI-compatible implementation boundary.
- `scoring.py` selects eligible jobs, performs deduplication, requests scores, validates results, and writes notes only after successful validation.

`main.py` exposes the `score` CLI. Configuration comes from an ignored local config file with a committed example. Candidate profile content is read from an ignored local path and never embedded in source or prompts tracked by Git.

## Data flow

1. List candidate jobs with `GET /api/jobs`.
2. Skip jobs lacking a non-empty description.
3. Fetch each selected full record with `GET /api/jobs/:id`.
4. Skip existing notes containing `[AI_JOB_SCORE_V1]` or the legacy `[HERMES_JOB_SCORE_V1]`, unless `--force` is supplied.
5. Render a prompt from the job and the local candidate profile.
6. Ask the selected provider for JSON only.
7. Validate JSON as `ScoreResult`.
8. In normal mode, save a marker plus validated structured score to `POST /api/jobs/:id/notes`; in dry-run mode, print without saving.

## Error handling

Provider failures, malformed JSON, schema validation failures, and JobTrail HTTP errors are reported per job. Invalid provider output never creates a note. Processing continues for independent jobs and ends with processed, skipped, and failed totals.

## Testing

Unit tests use fake HTTP/provider boundaries to verify request formatting, marker deduplication, description eligibility, dry-run behavior, score validation, and refusal to save invalid provider output. No test uses a real candidate profile, credential, Hermes executable, or JobTrail deployment.

## Security and privacy

The public repository commits only `config.example.yaml` and `prompts/job-fit.example.md`. `.gitignore` excludes `.env`, `config.yaml`, local YAML variants, and candidate profile files. Runtime commands accept configuration paths but never print secrets or profile content.
