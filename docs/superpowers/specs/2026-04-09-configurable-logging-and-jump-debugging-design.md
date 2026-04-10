# Configurable Logging And Jump Debugging Design

## Goal

Add a project-wide logging system with a configurable log level in `~/.config/linux-agent-island/settings.json`, and use it to diagnose why clicking a session sometimes fails to open or focus the target terminal.

## Chosen Approach

Introduce a small shared logging bootstrap used by both frontend and backend, and extend the existing settings loader so one config file controls both UI settings and logging level. Logging will default to a non-noisy level and can be raised to `DEBUG` when debugging jump failures.

The first implementation pass will keep scope narrow: initialize logging once at process startup, then add targeted logs to the frontend click path, the backend D-Bus `JumpToSession` handler, and the runtime process inspection and jump logic in `linux_agent_island/runtime/processes.py`.

## Alternatives Considered

### Backend-only logging

This would cover most jump failures inside `SessionProcessInspector`, but it would not show whether the frontend ever sent the D-Bus request or whether the request failed before reaching the jump logic.

### Ad hoc `print()` debugging

This is faster in the moment but creates inconsistent output, no central level control, and no durable debugging path for future failures.

## Scope

- Extend `~/.config/linux-agent-island/settings.json` to support `log_level` alongside `top_bar_gap`.
- Normalize and validate configured log levels, with safe fallback when the value is missing or invalid.
- Add a shared logging initialization helper so frontend and backend use the same format and level parsing.
- Initialize logging early in both entrypoints before normal runtime work starts.
- Add targeted logs for frontend jump clicks, backend `JumpToSession` handling, and runtime jump decision branches.
- Log enough detail to explain common failure cases such as missing `pid`, unmatched tmux pane or client, missing window, command execution failure, or final unsuccessful jump result.

## Out Of Scope

- Log file rotation or dedicated on-disk log storage.
- A custom logging framework or logger subclass hierarchy.
- Broad logging coverage across every module.
- Changing jump behavior itself beyond adding observability.
- New frontend UI for viewing logs or toggling log level interactively.

## Configuration

The shared settings file will continue to live at `~/.config/linux-agent-island/settings.json`. The new shape will be:

```json
{
  "top_bar_gap": 8,
  "log_level": "INFO"
}
```

Accepted levels will be standard Python logging names such as `DEBUG`, `INFO`, `WARNING`, and `ERROR`, treated case-insensitively. Invalid values will fall back to the default level instead of failing startup.

## Logging Coverage

### Frontend

- Log when the user clicks the jump button, including `provider`, `session_id`, and current `pid`.
- Log before issuing the D-Bus `JumpToSession` call.
- Log the boolean jump result returned from the backend.
- Log D-Bus call failures as warnings.

### Backend

- Log receipt of `JumpToSession(provider, session_id)`.
- Log when the requested session cannot be found.
- Log the selected session context before attempting the jump, including `cwd`, `pid`, and `tty` when available.
- Log the final jump result.

### Runtime Process Inspector

- Log entry into `jump_to_session()`.
- Log when jump cannot proceed because `session.pid` is missing.
- Log counts or presence for process tree, windows, tmux panes, and tmux clients used during the decision.
- Log which jump path is attempted: tmux path, direct window activation path, or no viable path.
- Log subprocess command arguments and return codes for `wmctrl` and `tmux` calls.
- Log when all candidate activation paths fail.

## Testing

- Add config tests covering valid `log_level`, missing `log_level`, and invalid `log_level` fallback.
- Add focused tests for log-level normalization helpers.
- Add process inspector tests covering key jump failure branches and success branches without changing existing behavior.
- Keep frontend and backend test scope lightweight unless a helper is extracted that benefits from direct unit coverage.

## Manual Verification

1. Set `"log_level": "DEBUG"` in `~/.config/linux-agent-island/settings.json`.
2. Restart the frontend and backend processes.
3. Click a session jump button.
4. Confirm the logs show the full path from frontend click to backend jump result.
5. Validate that a failure can be attributed to a concrete branch such as missing session, missing pid, missing window match, tmux selection failure, or window activation failure.
