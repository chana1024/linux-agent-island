# Linux Agent Shell

Linux v1 desktop shell for local coding agents.

Current scope:

- Claude Code session visibility
- Codex CLI session visibility
- Floating GTK4 island window
- Background event service with D-Bus API
- Completion notifications

Current non-goals:

- Codex approval UI
- Wayland support
- Replacing the existing macOS app

## Run

One command:

```bash
./run.sh
./run.sh --log-level DEBUG
```

Backend:

```bash
/usr/bin/python3 -m linux_agent_shell.backend
/usr/bin/python3 -m linux_agent_shell.backend --log-level DEBUG
```

Frontend:

```bash
/usr/bin/python3 -m linux_agent_shell.frontend
/usr/bin/python3 -m linux_agent_shell.frontend --log-level DEBUG
```

Frontend settings:

```json
~/.config/linux-agent-shell/settings.json
{
  "top_bar_gap": 8
}
```

Tests:

```bash
cd linux-agent-shell
/usr/bin/python3 -m pytest
```
