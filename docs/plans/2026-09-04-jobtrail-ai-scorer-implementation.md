# JobTrail AI Scorer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a public Python CLI that scores eligible JobTrail jobs through its HTTP API and saves only validated, deduplicated AI score notes.

**Architecture:** Keep HTTP integration, score validation, provider invocation, and command orchestration separate. The first provider is Hermes; an OpenAI-compatible interface is included without coupling source code to private candidate data or local Docker paths.

**Tech Stack:** Python 3.11+, Typer, httpx, Pydantic v2, PyYAML, pytest, pytest-httpx.

---

### Task 1: Bootstrap the package and safe configuration

**Files:**
- Create: `pyproject.toml`
- Create: `src/jobtrail_ai_scorer/__init__.py`
- Create: `src/jobtrail_ai_scorer/config.py`
- Create: `config.example.yaml`
- Create: `.gitignore`
- Test: `tests/test_config.py`

**Step 1: Write the failing config test**

```python
def test_load_config_requires_jobtrail_url_and_profile_path(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("provider: hermes\n")

    with pytest.raises(ValidationError):
        load_config(config_path)
```

**Step 2: Run it to verify it fails**

Run: `pytest tests/test_config.py -v`

Expected: FAIL because `load_config` does not exist.

**Step 3: Implement the minimal configuration model**

```python
class AppConfig(BaseModel):
    jobtrail_base_url: AnyHttpUrl
    candidate_profile_path: Path
    provider: Literal["hermes", "openai_compatible"] = "hermes"
    marker: str = "[AI_JOB_SCORE_V1]"
```

Add `.env`, `config.yaml`, `*.local.yaml`, and `candidate-profile.md` to `.gitignore`; commit only a secret-free example config.

**Step 4: Run the config test**

Run: `pytest tests/test_config.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add pyproject.toml src/jobtrail_ai_scorer config.example.yaml .gitignore tests/test_config.py
git commit -m "feat: add scorer configuration"
```

### Task 2: Define and validate the score schema

**Files:**
- Create: `src/jobtrail_ai_scorer/models.py`
- Test: `tests/test_scoring_validation.py`

**Step 1: Write failing validation tests**

```python
def test_rejects_score_outside_zero_to_one_hundred():
    with pytest.raises(ValidationError):
        ScoreResult.model_validate({"score": 101, ...})

def test_rejects_unknown_recommendation():
    with pytest.raises(ValidationError):
        ScoreResult.model_validate({"recommendation": "MAYBE", ...})
```

**Step 2: Run the tests**

Run: `pytest tests/test_scoring_validation.py -v`

Expected: FAIL because `ScoreResult` does not exist.

**Step 3: Implement the minimal schema**

Use Pydantic fields enforcing an integer score from 0 through 100, `PRIORITY_APPLY|APPLY|REVIEW|SKIP`, `High|Medium|Low`, string lists, and non-empty `reasoning`.

**Step 4: Run the tests**

Run: `pytest tests/test_scoring_validation.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/jobtrail_ai_scorer/models.py tests/test_scoring_validation.py
git commit -m "feat: validate structured job scores"
```

### Task 3: Add a typed JobTrail HTTP client

**Files:**
- Create: `src/jobtrail_ai_scorer/jobtrail.py`
- Test: `tests/test_jobtrail.py`

**Step 1: Write failing request/response tests**

```python
def test_add_note_posts_body(httpx_mock):
    httpx_mock.add_response(method="POST", url="https://jobs.test/api/jobs/j1/notes", status_code=201)
    client.add_note("j1", "[AI_JOB_SCORE_V1]\n...")
```

Add tests for `list_jobs()` and `get_job(job_id)` with API failures raising a domain-specific error.

**Step 2: Run the tests**

Run: `pytest tests/test_jobtrail.py -v`

Expected: FAIL because the client does not exist.

**Step 3: Implement the client**

Use `httpx.Client` with an explicit timeout. Implement only `GET /api/jobs`, `GET /api/jobs/:id`, and `POST /api/jobs/:id/notes`; do not access Prisma or Docker directly.

**Step 4: Run the tests**

Run: `pytest tests/test_jobtrail.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/jobtrail_ai_scorer/jobtrail.py tests/test_jobtrail.py
git commit -m "feat: add JobTrail API client"
```

### Task 4: Add provider abstractions and Hermes provider

**Files:**
- Create: `src/jobtrail_ai_scorer/providers/__init__.py`
- Create: `src/jobtrail_ai_scorer/providers/base.py`
- Create: `src/jobtrail_ai_scorer/providers/hermes.py`
- Create: `src/jobtrail_ai_scorer/providers/openai_compatible.py`
- Create: `prompts/job-fit.example.md`
- Test: `tests/test_providers.py`

**Step 1: Write the failing provider contract test**

```python
def test_hermes_provider_returns_raw_model_text(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_hermes_process)
    assert HermesProvider(config).score("prompt") == '{"score": 80}'
```

**Step 2: Run the test**

Run: `pytest tests/test_providers.py -v`

Expected: FAIL because `HermesProvider` does not exist.

**Step 3: Implement the provider boundary**

Define a `ScoreProvider` protocol returning raw text. Hermes invokes a configured executable/profile without embedding local defaults. The OpenAI-compatible class exists behind the same protocol but requires explicit endpoint and API-key environment variable configuration.

**Step 4: Run the test**

Run: `pytest tests/test_providers.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/jobtrail_ai_scorer/providers prompts/job-fit.example.md tests/test_providers.py
git commit -m "feat: add scoring provider interface"
```

### Task 5: Implement eligibility, deduplication, and score orchestration

**Files:**
- Create: `src/jobtrail_ai_scorer/scoring.py`
- Test: `tests/test_dedupe.py`
- Test: `tests/test_scoring.py`

**Step 1: Write failing behavior tests**

```python
def test_skips_empty_description():
    result = score_jobs(...)
    assert result.skipped == 1

def test_skips_legacy_and_current_markers_unless_forced():
    assert should_score(job_with_note("[HERMES_JOB_SCORE_V1]"), force=False) is False
    assert should_score(job_with_note("[AI_JOB_SCORE_V1]"), force=False) is False

def test_invalid_provider_json_does_not_save_note():
    provider.score.return_value = "not json"
    score_jobs(...)
    client.add_note.assert_not_called()
```

**Step 2: Run the tests**

Run: `pytest tests/test_dedupe.py tests/test_scoring.py -v`

Expected: FAIL because the orchestrator does not exist.

**Step 3: Implement the minimal workflow**

Fetch full records before inspecting notes, accept `--job-id` and `--limit` selection, render the prompt with the local profile, parse provider JSON, validate `ScoreResult`, and serialize a marker plus canonical JSON into the note. Continue after independent failures and collect counters.

**Step 4: Run the tests**

Run: `pytest tests/test_dedupe.py tests/test_scoring.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/jobtrail_ai_scorer/scoring.py tests/test_dedupe.py tests/test_scoring.py
git commit -m "feat: score and deduplicate eligible jobs"
```

### Task 6: Expose the CLI and dry-run behavior

**Files:**
- Create: `src/jobtrail_ai_scorer/main.py`
- Test: `tests/test_cli.py`

**Step 1: Write failing CLI tests**

```python
def test_dry_run_never_saves_notes(runner):
    result = runner.invoke(app, ["score", "--dry-run", "--limit", "1"])
    assert result.exit_code == 0
    assert "SAVED" not in result.output
```

Add tests for `--job-id`, `--force`, `--marker`, `--provider`, and summary output.

**Step 2: Run the tests**

Run: `pytest tests/test_cli.py -v`

Expected: FAIL because the CLI does not exist.

**Step 3: Implement the command**

Use Typer to expose `jobtrail-ai-scorer score`. Print one clear line per skip, score, save, or failure and a final processed/skipped/failed summary. Return non-zero when one or more jobs fail.

**Step 4: Run the tests**

Run: `pytest tests/test_cli.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/jobtrail_ai_scorer/main.py tests/test_cli.py
git commit -m "feat: add scorer command line interface"
```

### Task 7: Add documentation and final project verification

**Files:**
- Create: `README.md`
- Create: `LICENSE`
- Create: `docker-compose.yml`
- Modify: `pyproject.toml`
- Test: all `tests/`

**Step 1: Write a failing packaging smoke test**

```python
def test_console_script_is_declared():
    project = tomllib.loads(Path("pyproject.toml").read_text())
    assert project["project"]["scripts"]["jobtrail-ai-scorer"]
```

**Step 2: Run the test**

Run: `pytest tests/test_packaging.py -v`

Expected: FAIL until the console script is declared.

**Step 3: Implement minimal distribution documentation**

Document installation, safe config setup, all CLI commands, dry-run first use, marker behavior, Hermes setup variables, and the rule against committing profiles or secrets. The compose file must only reference examples and environment variables, never deployment-specific paths.

**Step 4: Run final verification**

Run: `pytest -q && python -m build`

Expected: all tests pass and a source/wheel distribution is produced.

**Step 5: Commit**

```bash
git add README.md LICENSE docker-compose.yml pyproject.toml tests/test_packaging.py
git commit -m "docs: document scorer setup and usage"
```

### Task 8: Publish the reviewed public repository

**Files:**
- Modify: Git remote configuration only

**Step 1: Review public content**

Run: `git grep -nE '/DATA/AppData|HERMES_|SOUL\.md|api[_-]?key|token|candidate profile'`

Expected: no private path, credential, or profile content is committed; template references are acceptable.

**Step 2: Create and push the public repository**

Run: `gh repo create dreamjorge/jobtrail-ai-scorer --public --source . --push --description 'Provider-agnostic AI scoring CLI for JobTrail'`

Expected: a public GitHub repository with `main` pushed.

**Step 3: Verify the repository**

Run: `gh repo view dreamjorge/jobtrail-ai-scorer --json url,isPrivate,defaultBranchRef`

Expected: `isPrivate` is `false` and default branch is `main`.

**Step 4: Commit**

No additional code commit; report the URL and verification evidence.
