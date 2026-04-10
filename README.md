# Linux Agent Island

Linux v1 desktop island for local coding agents.

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
/usr/bin/python3 -m linux_agent_island.backend
/usr/bin/python3 -m linux_agent_island.backend --log-level DEBUG
```

Frontend:

```bash
/usr/bin/python3 -m linux_agent_island.frontend
/usr/bin/python3 -m linux_agent_island.frontend --log-level DEBUG
```

Frontend settings:

```json
~/.config/linux-agent-island/settings.json
{
  "top_bar_gap": 8
}
```

Logs:

- `~/.local/state/linux-agent-island/logs/backend.log`
- `~/.local/state/linux-agent-island/logs/frontend.log`

## Code Layout

- `linux_agent_island/app/`: D-Bus backend service and GTK frontend
- `linux_agent_island/core/`: configuration, logging, session models, and store
- `linux_agent_island/providers/`: Claude Code and Codex CLI hook/session adapters
- `linux_agent_island/runtime/`: event socket, process inspection, and session cache
- `bin/`: hook shims installed into Claude Code and Codex config

Tests:

```bash
cd linux-agent-island
/usr/bin/python3 -m pytest
```
