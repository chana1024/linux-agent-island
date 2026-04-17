# Session Completion Auto Reveal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Focus, auto-expand, scroll to, and temporarily highlight sessions that transition from running to completed in the Linux Agent Island island.

**Architecture:** Keep all behavior in the GTK frontend. Add pure helper functions for completion detection and highlight expiry, then connect those helpers to the existing `ListSessions` and `SessionsChanged` update flow. Use a frontend-only timeout map plus a pending scroll target so the UI can reveal the latest completed session without backend changes, and activate the island window when completion is detected.

**Tech Stack:** Python, GTK4, GLib, pytest

---

### Task 1: Add failing helper tests

**Files:**
- Modify: `linux-agent-island/tests/test_frontend_helpers.py`
- Modify: `linux-agent-island/linux_agent_island/app/frontend.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_detect_completed_sessions_tracks_running_to_completed_transitions() -> None:
    previous = {("codex", "a"): SessionPhase.RUNNING}
    sessions = [
        AgentSession(
            provider="codex",
            session_id="a",
            cwd="/tmp/a",
            title="A",
            phase=SessionPhase.COMPLETED,
            model=None,
            sandbox=None,
            approval_mode=None,
            updated_at=50,
            completed_at=50,
            is_process_alive=True,
        )
    ]

    completed = detect_completed_sessions(previous, sessions)

    assert [session.session_id for session in completed] == ["a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest linux-agent-island/tests/test_frontend_helpers.py -q`
Expected: FAIL because the completion helper does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def detect_completed_sessions(
    previous_phases: dict[SessionKey, SessionPhase],
    sessions: list[AgentSession],
) -> list[AgentSession]:
    return [
        session
        for session in sessions
        if previous_phases.get(session_key(session)) is SessionPhase.RUNNING
        and session.phase is SessionPhase.COMPLETED
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest linux-agent-island/tests/test_frontend_helpers.py -q`
Expected: PASS for the new helper tests.

- [ ] **Step 5: Commit**

```bash
git add linux-agent-island/tests/test_frontend_helpers.py linux-agent-island/linux_agent_island/app/frontend.py
git commit -m "test: cover session completion reveal helpers"
```

### Task 2: Wire auto-reveal into the frontend

**Files:**
- Modify: `linux-agent-island/linux_agent_island/app/frontend.py`
- Modify: `linux-agent-island/tests/test_frontend_helpers.py`

- [ ] **Step 1: Add the next failing tests**

```python
def test_refresh_completion_highlights_uses_latest_completed_session_as_target() -> None:
    highlighted, target = refresh_completion_highlights(
        highlighted_until={},
        completed_sessions=[older, newer],
        now_ts=100,
    )

    assert target == ("codex", "newer")
    assert highlighted[("codex", "older")] == 400
    assert highlighted[("codex", "newer")] == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest linux-agent-island/tests/test_frontend_helpers.py -q`
Expected: FAIL because highlight refresh logic does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def refresh_completion_highlights(...):
    updated = dict(highlighted_until)
    target = None
    for session in completed_sessions:
        key = session_key(session)
        updated[key] = now_ts + HIGHLIGHT_DURATION_SECONDS
        if target is None or (session.completed_at or session.updated_at) >= target_ts:
            target = key
    return updated, target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest linux-agent-island/tests/test_frontend_helpers.py -q`
Expected: PASS for the helper tests.

- [ ] **Step 5: Commit**

```bash
git add linux-agent-island/tests/test_frontend_helpers.py linux-agent-island/linux_agent_island/app/frontend.py
git commit -m "feat: auto reveal completed sessions in frontend"
```

### Task 3: Connect GTK rendering, click clearing, and timeout cleanup

**Files:**
- Modify: `linux-agent-island/linux_agent_island/app/frontend.py`

- [ ] **Step 1: Add the UI wiring**

```python
self.highlighted_until = prune_expired_highlights(self.highlighted_until, now_ts)
if completed_sessions:
    self.highlighted_until, self.pending_scroll_session = refresh_completion_highlights(
        highlighted_until=self.highlighted_until,
        completed_sessions=completed_sessions,
        now_ts=now_ts,
    )
    if not self.expanded:
        self.expanded = True
```

- [ ] **Step 2: Add scroll targeting and highlight clearing**

```python
toggle.connect("clicked", lambda *_args: self._on_session_clicked(session))

if session_key(session) == self.pending_scroll_session:
    adjustment.set_value(target_value)
```

- [ ] **Step 3: Run focused tests**

Run: `/usr/bin/python3 -m pytest linux-agent-island/tests/test_frontend_helpers.py -q`
Expected: PASS

- [ ] **Step 4: Run the full linux-agent-island test suite**

Run: `/usr/bin/python3 -m pytest linux-agent-island/tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add linux-agent-island/linux_agent_island/app/frontend.py linux-agent-island/tests/test_frontend_helpers.py
git commit -m "feat: highlight completed sessions in island"
```
