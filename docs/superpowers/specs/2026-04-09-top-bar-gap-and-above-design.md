# Top Bar Gap And Above Design

## Goal

Allow the floating island to keep a configurable gap below the desktop top bar and request always-on-top stacking so it stays visible over normal application windows.

## Chosen Approach

Store lightweight frontend preferences in a JSON file at `~/.config/linux-agent-island/settings.json`. Add a `top_bar_gap` integer with a default of `8`. Use the existing workarea top offset plus this gap when computing the island Y position.

For stacking, request the most reliable X11 behavior available in this project: mark the surface as skipped from taskbar and pager, and use `wmctrl` to add the `_NET_WM_STATE_ABOVE` state once the window surface exists.

## Scope

- Add `top_bar_gap` config loading with sane defaults and invalid-value fallback.
- Apply the gap in both collapsed and expanded states.
- Request always-on-top behavior on startup and after the window is mapped.
- Do not build a settings UI in this change.
- Do not attempt Wayland-specific stacking behavior.

## Testing

- Add unit tests for loading frontend preferences.
- Extend window-position tests to include gap application.
- Keep the change isolated to config and frontend behavior.
