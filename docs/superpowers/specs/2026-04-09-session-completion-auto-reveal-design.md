# Session Completion Auto Reveal Design

## Goal

When a visible session transitions from `running` to `completed`, automatically reveal it in the expanded Linux Agent Island island, scroll it into view, and highlight it for up to five minutes unless the user clicks that session first.

## Chosen Approach

Implement the behavior entirely in `linux_agent_island/app/frontend.py`. The GTK frontend already receives the full session list through `ListSessions` and `SessionsChanged`, so it can compare the previous and current phases locally without changing the backend or D-Bus payloads.

The frontend will keep three pieces of transient UI state: the last known phase per session, highlight expiration timestamps per session, and the latest completed session that still needs to be scrolled into view. When a `running -> completed` transition is detected, the frontend will expand the island if needed, scroll to the newest completed session, and apply a temporary highlight style to every newly completed session.

## Scope

- Detect only real `running -> completed` transitions.
- Auto-expand the island when it is collapsed, without stealing focus.
- Scroll to the newest completed session when one or more sessions complete.
- Keep completed sessions highlighted for five minutes, or clear the highlight immediately when the user clicks that session card.
- Preserve existing per-session detail expansion behavior.

## Out Of Scope

- Backend or D-Bus protocol changes.
- Auto-expanding per-session details.
- Sound effects, notifications, or jump-to-terminal behavior changes.
- Persisting highlight state across frontend restarts.

## Testing

- Add pure helper tests for transition detection and highlight expiry.
- Add helper coverage for choosing the latest completed session as the scroll target.
- Keep rendering changes localized to `frontend.py`.
