from pathlib import Path


def test_run_script_starts_backend_and_frontend_with_system_python() -> None:
    script_path = Path(__file__).resolve().parents[1] / "run.sh"

    assert script_path.exists()

    content = script_path.read_text(encoding="utf-8")
    assert 'log_level="INFO"' in content
    assert '--log-level "$log_level"' in content
    assert "/usr/bin/python3 -m linux_agent_shell.backend --log-level" in content
    assert "/usr/bin/python3 -m linux_agent_shell.frontend --log-level" in content
    assert "trap cleanup EXIT" in content
