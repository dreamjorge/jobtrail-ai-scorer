# JobTrail AI Scorer

Provider-agnostic CLI that evaluates JobTrail jobs against a local candidate profile.

## Setup

```sh
python -m pip install .
cp config.example.yaml config.yaml
# edit config.yaml and candidate-profile.md
```

Run `jobtrail-ai-scorer score --config config.yaml --dry-run` to validate scores without writing notes.
Use `--job-id ID` or `--limit N`; `--force` re-scores jobs with an existing marker.

The CLI never stores API keys in configuration. Provider credentials are read from environment variables.
