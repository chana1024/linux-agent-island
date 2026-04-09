# Codex PreToolUse Noise Reduction Design

## Context

The current Linux Agent Shell backend installs Codex hooks for `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, and `Stop`.

For Codex sessions, `PreToolUse` and `PostToolUse` are both translated into generic `activity_updated` events with `phase="running"`. Those events do not currently capture tool identity or tool-specific UI state. They still trigger the full runtime update path:

- runtime socket event emission
- `SessionStore.apply(...)`
- session cache persistence
- D-Bus `SessionsChanged`
- frontend `_render()` and window reposition scheduling

This creates high-frequency UI churn without adding proportional user value.

## Problem

The current behavior has two visible costs:

1. Every tool invocation produces extra hook output and extra session refreshes.
2. The session card often re-renders even when the visible state has not meaningfully changed.

`SessionStart` alone cannot replace `PreToolUse` because it only fires once at session creation. A session can later transition from a completed turn back into active work without a second `SessionStart`.

## Goals

- Preserve accurate session lifecycle visibility.
- Keep Codex sessions able to move back to `running` when a new turn starts.
- Reduce unnecessary refreshes caused by tool-level hook events.
- Avoid changing the Claude lifecycle in this task.

## Non-Goals

- Displaying the currently running tool name.
- Adding per-tool progress UI.
- Refactoring the overall session lifecycle model.
- Changing frontend layout or visuals.

## Options Considered

### Option 1: Remove Codex `PreToolUse` and `PostToolUse`, keep `SessionStart`, `UserPromptSubmit`, and `Stop`

Pros:

- Largest reduction in hook noise and redundant refreshes.
- Keeps session activation responsive because `UserPromptSubmit` still marks active work before tool execution.
- Minimal code change.

Cons:

- If Codex ever performs tool work without a preceding `UserPromptSubmit`, the UI will not receive a tool-start activity signal.

### Option 2: Keep Codex `PreToolUse` and `PostToolUse`, but suppress no-op updates in the backend

Pros:

- Preserves full hook coverage.
- Can reduce some redundant refreshes.

Cons:

- More complex because the backend must define and maintain no-op equivalence rules.
- Still leaves terminal hook noise visible because the hooks continue firing.

### Option 3: Keep `PreToolUse`, drop only `PostToolUse`

Pros:

- Slight reduction in traffic.
- Maintains a tool-start signal.

Cons:

- Retains most of the noise because `PreToolUse` is the main high-frequency event.
- Still does not surface tool-specific information.

## Recommended Design

Use Option 1.

For Codex only:

- install hooks for `SessionStart`, `UserPromptSubmit`, and `Stop`
- stop installing hooks for `PreToolUse` and `PostToolUse`

Rationale:

- `UserPromptSubmit` is the correct event to represent the start of a new user-driven turn.
- `Stop` still marks the turn as completed.
- `SessionStart` still creates the session.
- This preserves the existing lifecycle model while removing the highest-noise events that do not currently provide distinct value.

## Expected Lifecycle After Change

Codex session flow becomes:

1. `SessionStart` creates the live session.
2. `UserPromptSubmit` marks the session active and updates recency.
3. Internal tool execution happens without extra Linux Agent Shell hook traffic.
4. `Stop` marks the turn completed.
5. Process reconciliation continues to determine eventual disappearance from the island.

This means a session can still move from `completed` back to `running` on the next user prompt, which is the main reason `SessionStart` alone is insufficient.

## Implementation Notes

Code changes should be limited to:

- Codex hook installation in `linux_agent_shell/providers/codex.py`
- tests that currently assert `PreToolUse` and `PostToolUse` hook registration

No runtime event schema changes are required.
No frontend changes are required.

## Testing

Add or update tests to verify:

- Codex hook installation includes `SessionStart`, `UserPromptSubmit`, and `Stop`
- Codex hook installation no longer includes `PreToolUse`
- Codex hook installation no longer includes `PostToolUse`
- Existing stop hook merge behavior still works

Regression expectation:

- Codex sessions still appear on start
- Codex sessions still become active on user prompt
- Codex sessions still become completed on stop

## Risks

The main risk is hidden dependence on `PreToolUse` for recency updates in cases where a tool run happens without a preceding user prompt.

This is acceptable for the current implementation because:

- the system already has process-based liveness reconciliation
- `UserPromptSubmit` is the more meaningful activity boundary for the current UI
- the removed hooks do not currently drive distinct visible information

If that risk materializes later, the better fix is not to restore noisy generic events, but to reintroduce tool hooks only together with tool-specific state or backend de-duplication.
