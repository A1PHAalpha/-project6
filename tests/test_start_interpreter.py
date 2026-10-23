import start


def test_project_interpreter_detection_uses_sys_prefix(tmp_path, monkeypatch):
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    monkeypatch.setattr(start, "VENV_DIR", venv_dir)

    monkeypatch.setattr(start.sys, "prefix", str(venv_dir))
    assert start._is_project_interpreter() is True

    monkeypatch.setattr(start.sys, "prefix", str(tmp_path))
    assert start._is_project_interpreter() is False
