# Answer Request Interception Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Codex and Claude Code answer-request interception so answer-needed hooks update Linux Agent Island sessions to `waiting_answer`.

**Architecture:** Reuse the existing runtime event model: answer requests become `event_type="question_asked"` with `phase="waiting_answer"` and a `question_prompt` payload. Codex and Claude providers both install an `AnswerRequest` hook and map the hook payload into the same question prompt shape. No frontend redesign, response transport, or permission-flow changes are included.

**Tech Stack:** Python 3, pytest, provider hook JSON configuration, existing `linux_agent_island.runtime.agent_events` and `linux_agent_island.core.models` event/session types.

---

## File Structure

- Modify `linux_agent_island/providers/utils.py`: add a shared `question_prompt_from_payload(payload)` helper used by both providers.
- Modify `linux_agent_island/providers/codex.py`: install `AnswerRequest` and map it to `question_asked` / `waiting_answer`.
- Modify `linux_agent_island/providers/claude.py`: install `AnswerRequest`, map it to `question_asked` / `waiting_answer`, and use the shared helper instead of the local `_question_prompt_from_payload`.
- Modify `tests/test_hooks.py`: add hook event mapping tests for Codex and Claude `AnswerRequest`.
- Modify `tests/test_codex_provider.py`: add/update install-hook expectations for Codex `AnswerRequest`.
- Modify `tests/test_claude_provider.py`: add/update install-hook expectations for Claude `AnswerRequest`.
- Modify `docs/desktop-app.md`: update the managed hook event lists.

## Task 1: Codex AnswerRequest Hook Event Mapping

**Files:**
- Modify: `tests/test_hooks.py`
- Modify: `linux_agent_island/providers/utils.py`
- Modify: `linux_agent_island/providers/codex.py`

- [ ] **Step 1: Write the failing Codex hook mapping test**

Add this test near the existing Codex hook tests in `tests/test_hooks.py`:

```python
def test_codex_answer_request_hook_maps_to_waiting_answer(monkeypatch) -> None:
    class Completed:
        stdout = "pts/7\n"
        stderr = ""

    monkeypatch.setattr(hooks.os, "getppid", lambda: 4321)
    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: Completed())

    event = hooks._build_codex_event(  # noqa: SLF001
        "AnswerRequest",
        {
            "session_id": "session-1",
            "cwd": "/tmp/demo",
            "message": "Choose a deployment target",
            "options": [
                {"label": "staging", "description": "Deploy to staging"},
                "production",
            ],
        },
    )

    assert event["phase"] == "waiting_answer"
    assert event["event_type"] == "question_asked"
    assert event["event_source"] == "AnswerRequest"
    assert event["last_message_preview"] == "Choose a deployment target"
    assert event["question_prompt"]["title"] == "Choose a deployment target"
    assert event["question_prompt"]["options"] == [
        {"label": "staging", "description": "Deploy to staging"},
        {"label": "production", "description": ""},
    ]
```

- [ ] **Step 2: Run the Codex hook test and verify RED**

Run:

```bash
PYTHONPATH=. pytest tests/test_hooks.py::test_codex_answer_request_hook_maps_to_waiting_answer -q
```

Expected: FAIL because `CodexProvider.build_event("AnswerRequest", ...)` currently returns `event_type="activity_updated"` and does not include `question_prompt`.

- [ ] **Step 3: Add the shared question prompt helper**

In `linux_agent_island/providers/utils.py`, add this helper near the other payload extraction helpers:

```python
def question_prompt_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("message") or payload.get("question") or payload.get("prompt") or "").strip()
    if not title:
        title = "Input required"
    raw_options = payload.get("options", [])
    options: list[dict[str, str]] = []
    if isinstance(raw_options, list):
        for item in raw_options:
            if isinstance(item, dict):
                label = str(item.get("label", "")).strip()
                description = str(item.get("description", "")).strip()
            else:
                label = str(item).strip()
                description = ""
            if label:
                options.append({"label": label, "description": description})
    return {
        "title": title,
        "options": options,
    }
```

If `Any` is not already imported in `utils.py`, import it:

```python
from typing import Any
```

- [ ] **Step 4: Implement Codex AnswerRequest mapping**

In `linux_agent_island/providers/codex.py`, import the helper:

```python
from .utils import (
    current_timestamp,
    extract_prompt_title,
    fallback_session_title,
    get_process_metadata,
    question_prompt_from_payload,
)
```

Update `build_event()` so `AnswerRequest` maps to question state:

```python
        question_prompt = None
        if hook_name == "Stop":
            event_type = "session_completed"
            phase = "completed"
        elif hook_name == "SessionStart":
            event_type = "session_started"
            title = fallback_session_title(payload)
        elif hook_name == "UserPromptSubmit":
            phase = "running"
            title = extract_prompt_title(payload)
        elif hook_name == "AnswerRequest":
            event_type = "question_asked"
            phase = "waiting_answer"
            question_prompt = question_prompt_from_payload(payload)
```

Include `question_prompt` and message fallback in the returned event:

```python
            "summary": payload.get("last_assistant_message") or payload.get("message", ""),
            "last_message_preview": payload.get("last_assistant_message") or payload.get("message", ""),
            "question_prompt": question_prompt,
```

- [ ] **Step 5: Run the Codex hook test and verify GREEN**

Run:

```bash
PYTHONPATH=. pytest tests/test_hooks.py::test_codex_answer_request_hook_maps_to_waiting_answer -q
```

Expected: PASS.

## Task 2: Claude AnswerRequest Hook Event Mapping

**Files:**
- Modify: `tests/test_hooks.py`
- Modify: `linux_agent_island/providers/claude.py`

- [ ] **Step 1: Write the failing Claude hook mapping test**

Add this test near the existing Claude hook tests in `tests/test_hooks.py`:

```python
def test_claude_answer_request_hook_maps_to_waiting_answer(monkeypatch) -> None:
    monkeypatch.setattr(hooks.time, "time", lambda: 123)

    event = hooks._build_claude_event(  # noqa: SLF001
        "AnswerRequest",
        {
            "session_id": "claude-1",
            "cwd": "/tmp/demo",
            "message": "Need your answer",
            "options": ["Yes", {"label": "No", "description": "Decline"}],
            "model": "claude-sonnet",
        },
    )

    assert event["phase"] == "waiting_answer"
    assert event["event_type"] == "question_asked"
    assert event["event_source"] == "AnswerRequest"
    assert event["last_message_preview"] == "Need your answer"
    assert event["question_prompt"]["title"] == "Need your answer"
    assert event["question_prompt"]["options"] == [
        {"label": "Yes", "description": ""},
        {"label": "No", "description": "Decline"},
    ]
    assert event["claude_metadata"]["model"] == "claude-sonnet"
```

- [ ] **Step 2: Run the Claude hook test and verify RED**

Run:

```bash
PYTHONPATH=. pytest tests/test_hooks.py::test_claude_answer_request_hook_maps_to_waiting_answer -q
```

Expected: FAIL because `ClaudeProvider.build_event("AnswerRequest", ...)` currently falls through to a generic completed/activity mapping or lacks explicit `question_asked` handling.

- [ ] **Step 3: Implement Claude AnswerRequest mapping**

In `linux_agent_island/providers/claude.py`, add the shared helper import:

```python
from .utils import (
    current_timestamp,
    extract_prompt_title,
    fallback_session_title,
    get_process_metadata,
    question_prompt_from_payload,
)
```

Add `AnswerRequest` to `HOOK_EVENTS`:

```python
HOOK_EVENTS = (
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "Notification",
    "AnswerRequest",
    "Stop",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
)
```

Update the default status mapping:

```python
                "AnswerRequest": "waiting_for_input",
```

Update event type mapping:

```python
        elif hook_name in {"Notification", "AnswerRequest"}:
            event_type = "question_asked"
```

Update the question prompt assignment:

```python
        question_prompt = question_prompt_from_payload(payload) if event_type == "question_asked" else None
```

Remove the local `_question_prompt_from_payload()` function from `claude.py` after all call sites use `question_prompt_from_payload`.

- [ ] **Step 4: Run the Claude hook test and verify GREEN**

Run:

```bash
PYTHONPATH=. pytest tests/test_hooks.py::test_claude_answer_request_hook_maps_to_waiting_answer -q
```

Expected: PASS.

## Task 3: Hook Installation Coverage

**Files:**
- Modify: `tests/test_codex_provider.py`
- Modify: `tests/test_claude_provider.py`
- Modify: `linux_agent_island/providers/codex.py`
- Modify: `linux_agent_island/providers/claude.py`

- [ ] **Step 1: Write the failing Codex install test**

Add this test near the Codex install-hook tests in `tests/test_codex_provider.py`:

```python
def test_codex_provider_installs_answer_request_hook(tmp_path: Path) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"
    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-island/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    answer_request_commands = [
        hook["command"]
        for entry in payload["hooks"]["AnswerRequest"]
        for hook in entry["hooks"]
    ]

    assert "/usr/bin/python3 /opt/linux-agent-island/codex-hook.py AnswerRequest" in answer_request_commands
```

- [ ] **Step 2: Write the failing Claude install test**

Add this test near the Claude install-hook tests in `tests/test_claude_provider.py`:

```python
def test_claude_provider_installs_answer_request_hook(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    provider = ClaudeProvider(
        settings_path=settings_path,
        hook_command_prefix="/venv/bin/python -m linux_agent_island.hooks",
        socket_path=tmp_path / "events.sock",
    )

    provider.install_hooks()
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    answer_request_commands = [
        hook["command"]
        for entry in payload["hooks"]["AnswerRequest"]
        for hook in entry["hooks"]
    ]

    assert "/venv/bin/python -m linux_agent_island.hooks claude AnswerRequest" in answer_request_commands
```

- [ ] **Step 3: Run install tests and verify RED**

Run:

```bash
PYTHONPATH=. pytest \
  tests/test_codex_provider.py::test_codex_provider_installs_answer_request_hook \
  tests/test_claude_provider.py::test_claude_provider_installs_answer_request_hook \
  -q
```

Expected: FAIL because `AnswerRequest` is not currently installed by either provider.

- [ ] **Step 4: Add Codex AnswerRequest to required hooks**

In `linux_agent_island/providers/codex.py`, update:

```python
    REQUIRED_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "AnswerRequest", "Stop")
```

- [ ] **Step 5: Confirm Claude HOOK_EVENTS includes AnswerRequest**

This should already be done in Task 2. Verify `linux_agent_island/providers/claude.py` includes:

```python
    "AnswerRequest",
```

- [ ] **Step 6: Run install tests and verify GREEN**

Run:

```bash
PYTHONPATH=. pytest \
  tests/test_codex_provider.py::test_codex_provider_installs_answer_request_hook \
  tests/test_claude_provider.py::test_claude_provider_installs_answer_request_hook \
  -q
```

Expected: PASS.

## Task 4: Regression Tests and Documentation

**Files:**
- Modify: `docs/desktop-app.md`
- Verify: `tests/test_hooks.py`
- Verify: `tests/test_codex_provider.py`
- Verify: `tests/test_claude_provider.py`

- [ ] **Step 1: Update docs managed event lists**

In `docs/desktop-app.md`, update the Claude list to include `AnswerRequest`:

```text
UserPromptSubmit
PreToolUse
PostToolUse
PermissionRequest
Notification
AnswerRequest
Stop
SessionStart
SessionEnd
PreCompact
```

Update the Codex list to include `AnswerRequest`:

```text
SessionStart
UserPromptSubmit
AnswerRequest
Stop
```

- [ ] **Step 2: Run focused hook mapping tests**

Run:

```bash
PYTHONPATH=. pytest \
  tests/test_hooks.py::test_codex_answer_request_hook_maps_to_waiting_answer \
  tests/test_hooks.py::test_claude_answer_request_hook_maps_to_waiting_answer \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run provider install regression tests**

Run:

```bash
PYTHONPATH=. pytest tests/test_codex_provider.py tests/test_claude_provider.py -q
```

Expected: PASS.

- [ ] **Step 4: Run combined relevant regression suite**

Run:

```bash
PYTHONPATH=. pytest tests/test_hooks.py tests/test_codex_provider.py tests/test_claude_provider.py -q
```

Expected: PASS.

- [ ] **Step 5: Review the diff**

Run:

```bash
git diff -- linux_agent_island/providers/utils.py linux_agent_island/providers/codex.py linux_agent_island/providers/claude.py tests/test_hooks.py tests/test_codex_provider.py tests/test_claude_provider.py docs/desktop-app.md
```

Expected: diff only contains `AnswerRequest` interception, shared question prompt helper, tests, and docs.

## Self-Review

- Spec coverage: The plan covers Codex interception, Claude Code interception, hook installation, event mapping, tests, and docs.
- Placeholder scan: No `TBD`, `TODO`, or unbounded "add tests" instructions remain.
- Type consistency: The plan uses existing dict payload conventions, existing `question_prompt` event field, existing `SessionPhase.WAITING_ANSWER` string value, and existing provider hook install patterns.
- Scope check: The plan intentionally excludes UI redesign, answering from the island UI, permission approval changes, and runtime service installation.

## Review Notes

Plan review provider: `gemini-cli`.

Verdict: `APPROVED`.

Minor recommendation adopted in this plan: move the existing Claude question prompt helper into `linux_agent_island/providers/utils.py` and reuse it from both Codex and Claude.
