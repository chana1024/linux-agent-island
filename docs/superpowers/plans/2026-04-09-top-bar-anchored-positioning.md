# Top Bar Anchored Positioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the floating island top-centered and directly below the desktop top bar in collapsed and expanded states.

**Architecture:** Extract window geometry calculation into a pure helper that can be unit tested. Use that helper inside the frontend positioning path, preferring X11-backed absolute movement and keeping the current GTK anchor behavior as fallback.

**Tech Stack:** Python, GTK4, GDK4, GdkX11, pytest

---

### Task 1: Add the failing coordinate tests

**Files:**
- Modify: `linux_agent_shell/tests/test_frontend_helpers.py`
- Test: `linux_agent_shell/tests/test_frontend_helpers.py`

- [ ] **Step 1: Write the failing test**

```python
def test_compute_window_position_returns_top_centered_collapsed_bounds() -> None:
    assert compute_window_position(
        monitor_x=0,
        monitor_y=0,
        monitor_width=1920,
        top_bar_offset=36,
        expanded=False,
    ) == (220, 850, 36)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_frontend_helpers.py -q`
Expected: FAIL because `compute_window_position` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def compute_window_position(...):
    width = 360 if expanded else 220
    x = monitor_x + (monitor_width - width) // 2
    y = monitor_y + top_bar_offset
    return width, x, y
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_frontend_helpers.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_frontend_helpers.py linux_agent_shell/frontend.py
git commit -m "fix: anchor island below top bar"
```

### Task 2: Use X11 positioning with fallback

**Files:**
- Modify: `linux_agent_shell/frontend.py`
- Test: `tests/test_frontend_helpers.py`

- [ ] **Step 1: Write the failing test**

```python
def test_compute_window_position_returns_top_centered_expanded_bounds() -> None:
    assert compute_window_position(
        monitor_x=100,
        monitor_y=24,
        monitor_width=2560,
        top_bar_offset=48,
        expanded=True,
    ) == (360, 1200, 72)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_frontend_helpers.py -q`
Expected: FAIL until the helper handles the expanded width.

- [ ] **Step 3: Write minimal implementation**

```python
width, x, y = compute_window_position(...)
if not self._move_surface_x11(x, y):
    self._move_surface_fallback(width, x, y)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_frontend_helpers.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_frontend_helpers.py linux_agent_shell/frontend.py
git commit -m "fix: use stable x11 island positioning"
```
