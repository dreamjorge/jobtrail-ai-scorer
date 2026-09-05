from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_distribution_metadata_and_runtime_files_exist():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "jobtrail-ai-scorer = \"jobtrail_ai_scorer.main:app\"" in pyproject
    assert (ROOT / "README.md").is_file()
    assert (ROOT / "LICENSE").is_file()
    assert (ROOT / "docker-compose.yml").is_file()
    assert (ROOT / "Dockerfile").is_file()
