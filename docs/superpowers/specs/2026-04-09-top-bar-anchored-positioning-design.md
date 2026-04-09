# Top Bar Anchored Positioning Design

## Goal

Make the floating island appear at the top center of the active monitor with its top edge directly below the desktop top bar in both collapsed and expanded states.

## Current Problem

The current frontend computes a top offset from `_NET_WORKAREA`, but the final placement is delegated to a generic surface anchor call. On X11 desktops this can be ignored or reinterpreted by the window manager, which leaves the island effectively centered instead of anchored below the top bar.

## Chosen Approach

Use a pure helper to compute the target width and absolute `(x, y)` coordinates from monitor geometry and the detected top-bar offset. Apply those coordinates through an X11-specific move path when possible, and keep the existing GTK-based move call as a fallback.

## Scope

- Keep the island horizontally centered in both collapsed and expanded states.
- Keep the island top edge aligned to `monitor.y + top_bar_offset`.
- Preserve the existing width choices for collapsed and expanded states.
- Do not add user-facing configuration for extra top gaps in this change.
- Do not add Wayland-specific behavior.

## Testing

- Add unit tests for the pure coordinate helper.
- Keep the existing `_NET_WORKAREA` parsing tests.
- Run the frontend helper test file after implementation.
