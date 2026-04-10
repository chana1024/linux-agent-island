from pathlib import Path


def test_user_app_installer_generates_desktop_app_and_graphical_user_unit() -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "install-user-app.sh"

    assert script_path.exists()

    content = script_path.read_text(encoding="utf-8")
    assert 'SERVICE_NAME="$APP_ID.service"' in content
    assert "-m venv --system-site-packages" in content
    assert "After=graphical-session.target" in content
    assert "PartOf=graphical-session.target" in content
    assert "WantedBy=graphical-session.target" in content
    assert "ExecStart=$WRAPPER_PATH daemon" in content
    assert "Exec=$WRAPPER_PATH open" in content
    assert "linux_agent_island.hooks" in content
    assert "systemctl --user daemon-reload" in content
    assert 'systemctl --user enable "$SERVICE_NAME"' in content
    assert "systemctl --user import-environment DISPLAY XAUTHORITY" in content
