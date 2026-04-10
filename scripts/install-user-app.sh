#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ID="linux-agent-island"
SERVICE_NAME="$APP_ID.service"
APP_NAME="Linux Agent Island"
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_ID"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"
CONFIG_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
SERVICE_PATH="$CONFIG_SYSTEMD_DIR/$SERVICE_NAME"
DESKTOP_PATH="$APPLICATIONS_DIR/$APP_ID.desktop"
ICON_PATH="$ICON_DIR/$APP_ID.svg"
WRAPPER_PATH="$BIN_DIR/$APP_ID"
PYTHON="${PYTHON:-/usr/bin/python3}"

missing=()
for command in "$PYTHON" wmctrl xprop systemctl gapplication; do
  if ! command -v "$command" >/dev/null 2>&1; then
    missing+=("$command")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "missing required commands: ${missing[*]}" >&2
  exit 1
fi

"$PYTHON" - <<'PY'
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkX11", "4.0")
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
except ValueError as exc:
    raise SystemExit(str(exc))
PY

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$CONFIG_SYSTEMD_DIR" "$APPLICATIONS_DIR" "$ICON_DIR"
"$PYTHON" -m venv --system-site-packages "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install "$ROOT_DIR"

ln -sf "$VENV_DIR/bin/linux-agent-island" "$WRAPPER_PATH"

cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=$APP_NAME
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=$WRAPPER_PATH daemon
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
EOF

cat >"$DESKTOP_PATH" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Desktop island for local coding agents
Exec=$WRAPPER_PATH open
Icon=$APP_ID
Terminal=false
Categories=Utility;Development;
StartupNotify=false
EOF

cat >"$ICON_PATH" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect x="16" y="30" width="96" height="68" rx="22" fill="#111318"/>
  <rect x="16" y="30" width="96" height="68" rx="22" fill="none" stroke="#7fb6ff" stroke-width="6"/>
  <circle cx="44" cy="64" r="8" fill="#3ddc84"/>
  <circle cx="64" cy="64" r="8" fill="#ffb020"/>
  <circle cx="84" cy="64" r="8" fill="#7fb6ff"/>
</svg>
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
gtk-update-icon-cache "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" >/dev/null 2>&1 || true

LINUX_AGENT_ISLAND_HOOK_COMMAND_PREFIX="$VENV_DIR/bin/python -m linux_agent_island.hooks" \
  "$WRAPPER_PATH" install-hooks

cat <<EOF
Installed $APP_NAME.

Start now:
  systemctl --user start $SERVICE_NAME

Open:
  $WRAPPER_PATH open

Status:
  $WRAPPER_PATH status
  journalctl --user -u $SERVICE_NAME -f

If the service starts without DISPLAY/X11 access, run this once in your desktop session:
  systemctl --user import-environment DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_CURRENT_DESKTOP
  dbus-update-activation-environment --systemd DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_CURRENT_DESKTOP
EOF
