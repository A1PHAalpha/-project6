"""Project-local .env loading must not search parent directories."""
import os

from project_config import load_project_env


def test_load_project_env_uses_only_the_given_file(tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    parent_env = tmp_path / ".env"
    project_env = project_dir / ".env"
    parent_env.write_text("NETOPS_PARENT_ONLY=bad\n", encoding="utf-8")
    project_env.write_text("NETOPS_PROJECT_ONLY=good\n", encoding="utf-8")

    monkeypatch.delenv("NETOPS_PARENT_ONLY", raising=False)
    monkeypatch.delenv("NETOPS_PROJECT_ONLY", raising=False)
    load_project_env(dotenv_path=project_env, override=True)

    assert os.getenv("NETOPS_PROJECT_ONLY") == "good"
    assert os.getenv("NETOPS_PARENT_ONLY") is None
