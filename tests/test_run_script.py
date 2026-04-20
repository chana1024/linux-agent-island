from pathlib import Path


def test_dev_run_script_starts_backend_and_frontend_with_system_python() -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run-dev.sh"

    assert script_path.exists()

    content = script_path.read_text(encoding="utf-8")
    assert 'log_level="INFO"' in content
    assert "LINUX_AGENT_ISLAND_HOOK_COMMAND_PREFIX" in content
    assert '--log-level "$log_level"' in content
    assert "/usr/bin/python3 -m linux_agent_island.backend --log-level" in content
    assert "/usr/bin/python3 -m linux_agent_island.frontend --log-level" in content
    assert "trap cleanup EXIT" in content


def test_install_script_exposes_lai_alias() -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "install-user-app.sh"

    assert script_path.exists()

    content = script_path.read_text(encoding="utf-8")
    assert 'ALIAS_WRAPPER_PATH="$BIN_DIR/lai"' in content
    assert 'ln -sf "$VENV_DIR/bin/lai" "$ALIAS_WRAPPER_PATH"' in content


def test_pyproject_exposes_lai_console_script() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"

    assert pyproject_path.exists()

    content = pyproject_path.read_text(encoding="utf-8")
    assert 'linux-agent-island = "linux_agent_island.cli:main"' in content
    assert 'lai = "linux_agent_island.cli:main"' in content
