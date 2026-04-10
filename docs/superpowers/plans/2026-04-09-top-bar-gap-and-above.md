# Top Bar Gap And Above Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a config-backed top bar gap and request always-on-top stacking for the floating island.

**Architecture:** Introduce a tiny frontend settings loader with defaults in `config.py`, then pass `top_bar_gap` into the pure position helper. Apply X11 "above" state after the GTK window surface is ready, while keeping existing move fallback behavior.

**Tech Stack:** Python, GTK4, GDK4, GdkX11, pytest, wmctrl

---

### Task 1: Add failing settings tests

**Files:**
- Create: `tests/test_config.py`
- Modify: `linux_agent_island/core/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
def test_load_frontend_settings_reads_top_bar_gap(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"top_bar_gap": 14}', encoding="utf-8")

    settings = load_frontend_settings(settings_path)

    assert settings.top_bar_gap == 14
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_config.py -q`
Expected: FAIL because the settings loader does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class FrontendSettings:
    top_bar_gap: int = 8
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_config.py linux_agent_island/core/config.py
git commit -m "feat: add frontend settings loader"
```

### Task 2: Apply the gap and above state

**Files:**
- Modify: `linux_agent_island/app/frontend.py`
- Modify: `tests/test_frontend_helpers.py`
- Modify: `README.md`

- [ ] **Step 1: Write the failing test**

```python
def test_compute_window_position_applies_top_bar_gap() -> None:
    assert compute_window_position(
        monitor_x=0,
        monitor_y=0,
        monitor_width=1920,
        top_bar_offset=36,
        top_bar_gap=8,
        expanded=False,
    ) == (220, 850, 44)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_frontend_helpers.py -q`
Expected: FAIL because the helper does not accept `top_bar_gap` yet.

- [ ] **Step 3: Write minimal implementation**

```python
y = monitor_y + max(0, top_bar_offset) + max(0, top_bar_gap)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_frontend_helpers.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_frontend_helpers.py linux_agent_island/app/frontend.py README.md
git commit -m "fix: add top bar gap and above hint"
```
