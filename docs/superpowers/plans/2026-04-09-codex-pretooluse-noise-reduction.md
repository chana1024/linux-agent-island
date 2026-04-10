# Codex PreToolUse Noise Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Linux Agent Island's Codex `PreToolUse` and `PostToolUse` hook noise while keeping session lifecycle updates driven by `SessionStart`, `UserPromptSubmit`, and `Stop`.

**Architecture:** Change Codex hook installation so the provider only manages the three desired events and actively removes this project's previously installed `PreToolUse` and `PostToolUse` commands from `~/.codex/hooks.json`. Keep unrelated user-defined hooks untouched, and prove the behavior with test-first updates in the Codex provider test file.

**Tech Stack:** Python 3, pytest, JSON hook config management

---

## File Structure

- Modify: `linux_agent_island/providers/codex.py`
  Responsibility: define the managed Codex hook event set, merge required hooks, and prune this project's old managed hook commands for events that are no longer desired.
- Modify: `tests/test_codex_provider.py`
  Responsibility: update expectations for required Codex hooks and add regression coverage for pruning managed `PreToolUse` and `PostToolUse` commands while preserving unrelated hooks.

### Task 1: Update Codex provider tests to describe the new hook contract

**Files:**
- Modify: `tests/test_codex_provider.py`
- Test: `tests/test_codex_provider.py`

- [ ] **Step 1: Write the failing test updates**

Update `tests/test_codex_provider.py` so the existing merge test stops expecting `PreToolUse` and `PostToolUse`, and add a regression test that proves old managed commands are removed while user hooks survive.

```python
def test_codex_provider_merges_required_hooks(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/existing/stop.sh",
                                    "timeout": 10,
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-island/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    assert "SessionStart" in payload["hooks"]
    assert "UserPromptSubmit" in payload["hooks"]
    assert "Stop" in payload["hooks"]
    assert "PreToolUse" not in payload["hooks"]
    assert "PostToolUse" not in payload["hooks"]


def test_codex_provider_removes_managed_pre_and_post_hooks_but_keeps_unrelated_hooks(
    tmp_path: Path,
) -> None:
    hooks_path = tmp_path / "hooks.json"
    managed_pre = "/usr/bin/python3 /opt/linux-agent-island/codex-hook.py PreToolUse"
    managed_post = "/usr/bin/python3 /opt/linux-agent-island/codex-hook.py PostToolUse"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": managed_pre,
                                    "timeout": 10,
                                },
                                {
                                    "type": "command",
                                    "command": "/custom/pre.sh",
                                    "timeout": 10,
                                },
                            ]
                        }
                    ],
                    "PostToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": managed_post,
                                    "timeout": 10,
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    provider = CodexProvider(
        state_db_path=tmp_path / "state.sqlite",
        history_path=tmp_path / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=Path("/opt/linux-agent-island/codex-hook.py"),
    )

    provider.install_hooks()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    assert payload["hooks"]["PreToolUse"] == [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "/custom/pre.sh",
                    "timeout": 10,
                }
            ]
        }
    ]
    assert "PostToolUse" not in payload["hooks"]
```

- [ ] **Step 2: Run the targeted tests to verify they fail for the right reason**

Run: `cd /home/lzn/.openclaw/workspace/coder-space/claude-island/linux-agent-island && /usr/bin/python3 -m pytest tests/test_codex_provider.py -q`

Expected: FAIL because `install_hooks()` still adds `PreToolUse` and `PostToolUse`, and it does not prune previously managed commands.

- [ ] **Step 3: Commit the failing test change**

```bash
cd /home/lzn/.openclaw/workspace/coder-space/claude-island
git add linux-agent-island/tests/test_codex_provider.py
git commit -m "test: define Codex hook noise reduction behavior"
```

### Task 2: Implement Codex hook pruning and reduced installation set

**Files:**
- Modify: `linux_agent_island/providers/codex.py`
- Test: `tests/test_codex_provider.py`

- [ ] **Step 1: Write the minimal implementation**

Refactor `linux_agent_island/providers/codex.py` so it manages only the desired events and prunes this project's stale `PreToolUse` and `PostToolUse` commands.

```python
class CodexProvider:
    REQUIRED_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")
    MANAGED_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")

    def install_hooks(self) -> None:
        payload: dict[str, object] = {}
        if self.hooks_config_path.exists():
            payload = json.loads(self.hooks_config_path.read_text(encoding="utf-8"))
        hooks = payload.setdefault("hooks", {})
        for event in self.MANAGED_HOOK_EVENTS:
            entries = self._remove_managed_hook_entries(hooks.get(event, []), event)
            if event in self.REQUIRED_HOOK_EVENTS:
                entries = self._merge_hook_entries(entries, event)
            if entries:
                hooks[event] = entries
            else:
                hooks.pop(event, None)
        self.hooks_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.hooks_config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _managed_command(self, event_name: str) -> str:
        return f"/usr/bin/python3 {self.hook_script_path} {event_name}"

    def _remove_managed_hook_entries(self, existing: object, event_name: str) -> list[dict[str, object]]:
        entries = list(existing) if isinstance(existing, list) else []
        managed_command = self._managed_command(event_name)
        filtered_entries: list[dict[str, object]] = []
        for entry in entries:
            hooks = [
                hook
                for hook in entry.get("hooks", [])
                if hook.get("command") != managed_command
            ]
            if hooks:
                filtered_entry = dict(entry)
                filtered_entry["hooks"] = hooks
                filtered_entries.append(filtered_entry)
        return filtered_entries

    def _merge_hook_entries(self, existing: object, event_name: str) -> list[dict[str, object]]:
        entries = list(existing) if isinstance(existing, list) else []
        command = self._managed_command(event_name)
        for entry in entries:
            for hook in entry.get("hooks", []):
                if hook.get("command") == command:
                    return entries
        entries.append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": command,
                        "timeout": 10,
                    }
                ]
            }
        )
        return entries
```

- [ ] **Step 2: Run the targeted tests to verify they pass**

Run: `cd /home/lzn/.openclaw/workspace/coder-space/claude-island/linux-agent-island && /usr/bin/python3 -m pytest tests/test_codex_provider.py -q`

Expected: PASS with all tests in `tests/test_codex_provider.py` green.

- [ ] **Step 3: Run the adjacent hook and store regression tests**

Run: `cd /home/lzn/.openclaw/workspace/coder-space/claude-island/linux-agent-island && /usr/bin/python3 -m pytest tests/test_hooks.py tests/test_store.py -q`

Expected: PASS, confirming the reduced Codex hook set does not break hook event translation or session store lifecycle behavior.

- [ ] **Step 4: Commit the implementation**

```bash
cd /home/lzn/.openclaw/workspace/coder-space/claude-island
git add linux-agent-island/linux_agent_island/providers/codex.py linux-agent-island/tests/test_codex_provider.py
git commit -m "feat: remove Codex tool hook noise"
```

### Task 3: Verify working tree behavior for existing users

**Files:**
- Modify: none
- Test: `linux_agent_island/providers/codex.py`

- [ ] **Step 1: Run the provider installation path against a temporary hooks file**

Run:

```bash
cd /home/lzn/.openclaw/workspace/coder-space/claude-island/linux-agent-island
/usr/bin/python3 - <<'PY'
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from linux_agent_island.providers.codex import CodexProvider

with TemporaryDirectory() as tmp:
    hooks_path = Path(tmp) / "hooks.json"
    hook_script = Path("/opt/linux-agent-island/codex-hook.py")
    hooks_path.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [{"hooks": [{"type": "command", "command": f"/usr/bin/python3 {hook_script} PreToolUse", "timeout": 10}]}],
            "PostToolUse": [{"hooks": [{"type": "command", "command": f"/usr/bin/python3 {hook_script} PostToolUse", "timeout": 10}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "/custom/stop.sh", "timeout": 10}]}],
        }
    }), encoding="utf-8")
    provider = CodexProvider(
        state_db_path=Path(tmp) / "state.sqlite",
        history_path=Path(tmp) / "history.jsonl",
        hooks_config_path=hooks_path,
        hook_script_path=hook_script,
    )
    provider.install_hooks()
    print(hooks_path.read_text(encoding="utf-8"))
PY
```

Expected: output JSON contains `SessionStart`, `UserPromptSubmit`, and `Stop`; removes managed `PreToolUse` and `PostToolUse`; preserves `/custom/stop.sh`.

- [ ] **Step 2: Commit nothing if verification is clean**

Run: `cd /home/lzn/.openclaw/workspace/coder-space/claude-island && git status --short`

Expected: no unexpected file modifications beyond the planned source and test changes.

## Self-Review

- Spec coverage:
  - reduced Codex hook set is implemented in Task 2
  - regression coverage for hook installation is implemented in Task 1
  - existing-user cleanup of stale managed Pre/Post hooks is covered in Task 2 and verified in Task 3
- Placeholder scan:
  - no `TODO`, `TBD`, or implied "write tests later" steps remain
- Type consistency:
  - the plan uses `REQUIRED_HOOK_EVENTS`, `MANAGED_HOOK_EVENTS`, `_managed_command(...)`, and `_remove_managed_hook_entries(...)` consistently across implementation and verification
